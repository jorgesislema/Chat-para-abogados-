"""
Tests Fase D/E/F (backend): conversaciones, casos, metricas, export PDF,
colecciones, borrado de documentos y streaming SSE.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
import pytest
from chromadb.config import Settings
from fastapi.testclient import TestClient

from backend.api.dependencias import (
    _reset_singletons_for_tests,
    obtener_motor_vectorial,
)
from backend.db.base import Base
from backend.db.conexion import engine, obtener_sesion_bd
from backend.db.modelos import RegistroAuditoria, Usuario
from backend.db.vectorial import COLECCION_GLOBAL, MotorVectorial
from backend.main import aplicacion

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def cliente(embedding_function_compartido) -> TestClient:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _reset_singletons_for_tests()

    cliente_efimero = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
    cliente_efimero.reset()
    motor_test = MotorVectorial(
        cliente=cliente_efimero,
        embedding_function=embedding_function_compartido,
    )
    aplicacion.dependency_overrides[obtener_motor_vectorial] = lambda: motor_test
    try:
        yield TestClient(aplicacion)
    finally:
        aplicacion.dependency_overrides.clear()
        _reset_singletons_for_tests()


def _bootstrap(cliente: TestClient) -> dict:
    r = cliente.post(
        "/auth/registro",
        json={"nombre_tenant": "Bufete F", "username": "admin_f",
              "contrasena": "contrasenaSegura123"},
    )
    assert r.status_code == 201, r.text
    tokens = {"admin": r.json()["access_token"]}
    bd = obtener_sesion_bd()
    try:
        from backend.db.modelos import Tenant
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()
    r = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
        json={"username": "abogado_f", "password": "contrasenaSegura123",
              "rol": "abogado", "tenant_id": tenant_id},
    )
    assert r.status_code == 201, r.text
    r = cliente.post(
        "/api/auth/token",
        data={"username": "abogado_f", "password": "contrasenaSegura123"},
    )
    tokens["abogado"] = r.json()["access_token"]
    return tokens


def _h(api_token: str) -> dict:
    return {"Authorization": f"Bearer {api_token}"}


# ---------- conversaciones ----------

def test_crud_conversacion_y_auto_titulo(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    r = cliente.post("/api/conversaciones", headers=_h(tokens["abogado"]), json={})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["titulo"] == "Sin titulo"

    r = cliente.post(
        f"/api/conversaciones/{cid}/mensajes", headers=_h(tokens["abogado"]),
        json={"rol": "user", "contenido": "¿Cual es el plazo para apelar una sentencia civil?"},
    )
    assert r.status_code == 201
    r = cliente.get(f"/api/conversaciones/{cid}", headers=_h(tokens["abogado"]))
    assert r.status_code == 200
    assert "plazo" in r.json()["titulo"].lower()
    assert r.json()["materia"] == "civil"
    assert len(r.json()["mensajes_lista"]) == 1


def test_visibilidad_por_rol(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    cliente.post("/api/conversaciones", headers=_h(tokens["abogado"]), json={"titulo": "Privada"})
    r = cliente.get("/api/conversaciones", headers=_h(tokens["admin"]))
    assert r.json()["total"] == 1
    # Otro abogado no existe; el admin ve todo, el dueño ve la suya.
    r = cliente.get("/api/conversaciones", headers=_h(tokens["abogado"]))
    assert r.json()["total"] == 1


def test_paginacion_y_filtros(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    for i in range(25):
        cliente.post(
            "/api/conversaciones", headers=_h(tokens["abogado"]),
            json={"titulo": f"Consulta laboral {i}", "materia": "laboral"},
        )
    r = cliente.get("/api/conversaciones?page=1&page_size=20", headers=_h(tokens["abogado"]))
    assert r.json()["total"] == 25 and r.json()["pages"] == 2
    assert len(r.json()["items"]) == 20
    r = cliente.get("/api/conversaciones?page=2&page_size=20", headers=_h(tokens["abogado"]))
    assert len(r.json()["items"]) == 5
    r = cliente.get("/api/conversaciones?q=laboral+3", headers=_h(tokens["abogado"]))
    assert r.json()["total"] >= 1
    r = cliente.get("/api/conversaciones?materia=civil", headers=_h(tokens["abogado"]))
    assert r.json()["total"] == 0


def test_renombrar_archivar_borrar(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    cid = cliente.post("/api/conversaciones", headers=_h(tokens["abogado"]), json={}).json()["id"]
    r = cliente.patch(f"/api/conversaciones/{cid}", headers=_h(tokens["abogado"]),
                      json={"titulo": "Caso Perez", "archivada": True})
    assert r.json()["titulo"] == "Caso Perez" and r.json()["archivada"] is True
    r = cliente.get("/api/conversaciones?archivada=true", headers=_h(tokens["abogado"]))
    assert r.json()["total"] == 1
    assert cliente.delete(f"/api/conversaciones/{cid}", headers=_h(tokens["abogado"])).status_code == 204
    assert cliente.get(f"/api/conversaciones/{cid}", headers=_h(tokens["abogado"])).status_code == 404


def test_exportar_pdf(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    cid = cliente.post("/api/conversaciones", headers=_h(tokens["abogado"]),
                       json={"titulo": "Para exportar"}).json()["id"]
    cliente.post(f"/api/conversaciones/{cid}/mensajes", headers=_h(tokens["abogado"]),
                 json={"rol": "user", "contenido": "Hola"})
    r = cliente.get(f"/api/conversaciones/{cid}/exportar.pdf", headers=_h(tokens["abogado"]))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


# ---------- casos ----------

def test_casos_crud_y_visibilidad(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    # Abogado no puede crear.
    r = cliente.post("/api/casos", headers=_h(tokens["abogado"]),
                     json={"numero_proceso": "1-2026", "materia": "civil"})
    assert r.status_code == 403
    r = cliente.post("/api/casos", headers=_h(tokens["admin"]),
                     json={"numero_proceso": "17200-2026-0001", "materia": "laboral",
                           "cliente_nombre": "Cliente Demo", "cliente_username": "abogado_f"})
    assert r.status_code == 201, r.text
    r = cliente.get("/api/casos?materia=laboral", headers=_h(tokens["abogado"]))
    assert r.json()["total"] == 1
    r = cliente.get("/api/casos?q=17200", headers=_h(tokens["abogado"]))
    assert r.json()["total"] == 1


# ---------- metricas / auditoria / colecciones ----------

def test_metricas_y_auditoria(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    bd = obtener_sesion_bd()
    try:
        bd.add(RegistroAuditoria(tenant_id="1", pregunta_ofuscada="[NOMBRE] pregunta",
                                 respuesta_llm="resp", fuentes_usadas="a,b",
                                 tokens_prompt=10, tokens_completion=20))
        bd.commit()
    finally:
        bd.close()
    r = cliente.get("/api/admin/metricas", headers=_h(tokens["admin"]))
    assert r.status_code == 200
    # 1 fila [ADMIN] por crear_usuario en _bootstrap + 1 manual.
    assert r.json()["consultas_totales"] == 2
    assert r.json()["tokens_prompt"] == 10
    r = cliente.get("/api/admin/metricas", headers=_h(tokens["abogado"]))
    assert r.status_code == 403
    r = cliente.get("/api/admin/auditoria", headers=_h(tokens["admin"]))
    assert r.json()["total"] == 2
    r = cliente.get("/api/admin/colecciones", headers=_h(tokens["admin"]))
    assert r.status_code == 200 and isinstance(r.json()["colecciones"], list)


def test_borrar_documento(cliente: TestClient) -> None:
    from backend.api.dependencias import obtener_motor_vectorial as dep

    tokens = _bootstrap(cliente)
    motor = aplicacion.dependency_overrides[dep]()
    motor.ingestar_chunks(["Texto a borrar."], COLECCION_GLOBAL,
                          metadata={"titulo": "Borrable", "categoria": "Leyes",
                                    "hash_archivo": "hash_borrar_1"})
    bd = obtener_sesion_bd()
    try:
        from backend.db.modelos import DocumentoIngestado
        doc_id = None
        # Registro manual (el endpoint de subida ya se prueba en otro modulo).
        bd.add(DocumentoIngestado(hash_archivo="hash_borrar_1", nombre_archivo="b.pdf",
                                  categoria="Leyes", tenant_id_destino="0"))
        bd.commit()
        doc_id = bd.query(DocumentoIngestado).first().id
    finally:
        bd.close()
    r = cliente.delete(f"/api/admin/documentos/{doc_id}", headers=_h(tokens["admin"]))
    assert r.status_code == 204
    r = cliente.get("/api/admin/documentos", headers=_h(tokens["admin"]))
    assert r.json()["items"] == []


# ---------- streaming ----------

def _mock_stream(textos: list[str]) -> MagicMock:
    async def _gen():
        for t in textos:
            m = MagicMock()
            m.choices = [MagicMock()]
            m.choices[0].delta.content = t
            yield m

    mock = MagicMock()
    mock.chat.completions.create = AsyncMock(return_value=_gen())
    return mock


def test_stream_devuelve_tokens_y_fuentes(cliente: TestClient) -> None:
    tokens = _bootstrap(cliente)
    mock = _mock_stream(["Hola ", "mundo"])
    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post("/api/chat/stream", headers=_h(tokens["abogado"]),
                         json={"pregunta": "¿Que dice la ley sobre vacaciones?"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    eventos = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data:")]
    tipos = [e["tipo"] for e in eventos]
    assert "token" in tipos and "hecho" in tipos
    assert "".join(e.get("texto", "") for e in eventos if e["tipo"] == "token") == "Hola mundo"


def test_stream_denegado_sin_llamar_al_llm(cliente: TestClient) -> None:
    from backend.api.dependencias import obtener_motor_vectorial as dep

    tokens = _bootstrap(cliente)
    motor = aplicacion.dependency_overrides[dep]()
    motor.ingestar_chunks(
        ["Juicio secreto 777: el demandado debe pagar nueve mil. CLAVE-ALFA-1."],
        "casos_user_999",
        metadata={"titulo": "Secreto", "categoria": "Expediente", "hash_archivo": "h777"},
    )
    mock = _mock_stream(["nunca"])
    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post("/api/chat/stream", headers=_h(tokens["abogado"]),
                         json={"pregunta": "¿Que dice el juicio secreto 777 con CLAVE-ALFA-1?"})
    eventos = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data:")]
    assert any(e["tipo"] == "denegado" for e in eventos)
    assert "CLAVE-ALFA-1" not in r.text
    assert mock.chat.completions.create.await_count == 0


# ---------- seed demo ----------

def test_seed_demo_200_conversaciones() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "seed.db")
        env = dict(os.environ, CADENA_BD=f"sqlite:///{db}",
                   LEGALRAG_TESTING="1", SECRET_KEY_JWT="x" * 40)
        proc = subprocess.run(
            [sys.executable, str(RAIZ / "scripts" / "sembrar_demo.py"),
             "--sin-vectores", "--seed", "7"],
            capture_output=True, text=True, env=env, timeout=300,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        import sqlite3
        c = sqlite3.connect(db)
        try:
            assert c.execute("select count(*) from usuarios").fetchone()[0] == 19
            assert c.execute("select count(*) from casos").fetchone()[0] == 25
            assert c.execute("select count(*) from conversaciones").fetchone()[0] == 200
            n = c.execute("select count(*) from mensajes_chat").fetchone()[0]
            assert n >= 200 * 3
            assert c.execute(
                "select count(*) from mensajes_chat where rol='assistant' "
                "and contenido like '%No reemplaza el criterio%'").fetchone()[0] > 0
        finally:
            c.close()
