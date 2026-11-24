"""
Tests Fase 2 (C): roles admin/abogado/cliente y matriz de permisos.

- Servicio puro: base por rol, grants, denies, grupos, documentos.
- Integracion vectorial: el filtro se aplica DENTRO de la busqueda;
  sin permiso la respuesta deniega SIN filtrar ni una frase del
  documento ajeno (y sin llamar al LLM). Las fuentes solo muestran
  documentos permitidos.
- API: solo admin gestiona permisos (403 para abogado/cliente),
  matriz 200, CRUD completo con auditoria [ADMIN].
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
from backend.db.modelos import ROL_ABOGADO, ROL_ADMIN, ROL_CLIENTE, Permiso, RegistroAuditoria, Tenant, Usuario
from backend.db.vectorial import COLECCION_GLOBAL, MotorVectorial, nombre_coleccion_para_tenant
from backend.main import aplicacion
from backend.servicios.permisos import (
    MENSAJE_SIN_ACCESO,
    PermisoRegla,
    resolver_alcance,
)

SECRETO_JUICIO = (
    "Juicio 2026-04567 por despido intempestivo: el fallo ordena pagar "
    "cuarenta y dos mil dolares al extrabajador Segundo Guaman. "
    "Frase secreta de control: QUINUA-ZORRO-7741."
)
TEXTO_PROPIO = "Minuta interna del abogado: preparar alegatos para la audiencia del viernes."
TEXTO_LEY = "Codigo del Trabajo: la jornada ordinaria maxima es de ocho horas diarias."


# ============================================================
# Fixtures y helpers.
# ============================================================

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


def _ids_por_username() -> dict[str, int]:
    bd = obtener_sesion_bd()
    try:
        return {u.username: u.id for u in bd.query(Usuario).all()}
    finally:
        bd.close()


def _bootstrap_con_roles(cliente: TestClient) -> dict[str, str]:
    """Crea admin + abogado + cliente y devuelve sus JWT."""
    r = cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete Fase2",
            "username": "admin_f2",
            "contrasena": "contrasenaSegura123",
        },
    )
    assert r.status_code == 201, r.text
    tokens = {"admin": r.json()["access_token"]}

    bd = obtener_sesion_bd()
    try:
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()

    for username, rol, grupo in [
        ("abogado_f2", "abogado", None),
        ("cliente_f2", "cliente", "grupo-quito"),
    ]:
        r = cliente.post(
            "/api/admin/usuarios",
            headers={"Authorization": f"Bearer {tokens['admin']}"},
            json={
                "username": username,
                "password": "contrasenaSegura123",
                "rol": rol,
                "tenant_id": tenant_id,
                "grupo": grupo,
            },
        )
        assert r.status_code == 201, r.text
        r = cliente.post(
            "/api/auth/token",
            data={"username": username, "password": "contrasenaSegura123"},
        )
        assert r.status_code == 200, r.text
        tokens[username.split("_")[0]] = r.json()["access_token"]
    return tokens


def _cliente_llm_mockeado(texto: str = "Respuesta simulada.") -> MagicMock:
    respuesta_mock = MagicMock()
    respuesta_mock.choices = [MagicMock()]
    respuesta_mock.choices[0].message.content = texto
    respuesta_mock.usage = None
    cliente_mock = MagicMock()
    from unittest.mock import AsyncMock

    cliente_mock.chat.completions.create = AsyncMock(return_value=respuesta_mock)
    return cliente_mock


# ============================================================
# Servicio puro: resolver_alcance.
# ============================================================

def test_admin_ve_todo() -> None:
    alcance = resolver_alcance(
        rol="admin", usuario_id=1, grupo=None, reglas=[],
        colecciones_existentes=["a", "b"],
    )
    assert alcance.colecciones == frozenset({"a", "b"})


def test_abogado_base_propia_y_global() -> None:
    alcance = resolver_alcance(
        rol="abogado", usuario_id=7, grupo=None, reglas=[],
        colecciones_existentes=[COLECCION_GLOBAL, "casos_user_7", "casos_user_9"],
    )
    assert alcance.colecciones == frozenset({COLECCION_GLOBAL, "casos_user_7"})
    assert "casos_user_9" not in alcance.colecciones


def test_cliente_sin_base_solo_concedido() -> None:
    reglas = [
        PermisoRegla(tenant_id=1, usuario_id=5, recurso_tipo="coleccion",
                     recurso_id="casos_user_9")
    ]
    alcance = resolver_alcance(
        rol="cliente", usuario_id=5, grupo=None, reglas=reglas,
        colecciones_existentes=[COLECCION_GLOBAL, "casos_user_5", "casos_user_9"],
    )
    assert alcance.colecciones == frozenset({"casos_user_9"})
    assert COLECCION_GLOBAL not in alcance.colecciones


def test_deny_gana_a_grant() -> None:
    reglas = [
        PermisoRegla(tenant_id=1, usuario_id=7, recurso_tipo="coleccion",
                     recurso_id="casos_user_9", puede_ver=True),
        PermisoRegla(tenant_id=1, usuario_id=7, recurso_tipo="coleccion",
                     recurso_id="casos_user_9", puede_ver=False),
    ]
    alcance = resolver_alcance(
        rol="abogado", usuario_id=7, grupo=None, reglas=reglas,
        colecciones_existentes=["casos_user_7", "casos_user_9", COLECCION_GLOBAL],
    )
    assert "casos_user_9" not in alcance.colecciones


def test_deny_no_quita_base_abogado() -> None:
    reglas = [
        PermisoRegla(tenant_id=1, usuario_id=7, recurso_tipo="coleccion",
                     recurso_id="casos_user_7", puede_ver=False),
    ]
    alcance = resolver_alcance(
        rol="abogado", usuario_id=7, grupo=None, reglas=reglas,
        colecciones_existentes=["casos_user_7", COLECCION_GLOBAL],
    )
    assert "casos_user_7" in alcance.colecciones


def test_permiso_por_grupo() -> None:
    reglas = [
        PermisoRegla(tenant_id=1, grupo="laboral", recurso_tipo="coleccion",
                     recurso_id="casos_user_9"),
    ]
    alcance = resolver_alcance(
        rol="abogado", usuario_id=7, grupo="laboral", reglas=reglas,
        colecciones_existentes=["casos_user_7", "casos_user_9", COLECCION_GLOBAL],
    )
    assert "casos_user_9" in alcance.colecciones
    alcance2 = resolver_alcance(
        rol="abogado", usuario_id=8, grupo="civil", reglas=reglas,
        colecciones_existentes=["casos_user_8", "casos_user_9", COLECCION_GLOBAL],
    )
    assert "casos_user_9" not in alcance2.colecciones


def test_hashes_documento_se_resuelven() -> None:
    reglas = [
        PermisoRegla(tenant_id=1, usuario_id=5, recurso_tipo="documento",
                     recurso_id="abc123"),
    ]
    alcance = resolver_alcance(
        rol="cliente", usuario_id=5, grupo=None, reglas=reglas,
        colecciones_existentes=["casos_user_9"],
    )
    assert alcance.hashes_documentos == frozenset({"abc123"})
    assert alcance.colecciones == frozenset()


# ============================================================
# Integracion: denegacion sin filtrar + fuentes permitidas.
# ============================================================

def _sembrar_vectores(cliente: TestClient, ids: dict[str, int]) -> None:
    from backend.api.dependencias import obtener_motor_vectorial as dep

    motor = aplicacion.dependency_overrides[dep]()
    # Coleccion ajena (otro abogado, id 999) con el juicio secreto.
    motor.ingestar_chunks(
        [SECRETO_JUICIO], "casos_user_999",
        metadata={"titulo": "Juicio ajeno", "categoria": "Expediente",
                  "hash_archivo": "hash_secreto_juicio_1"},
    )
    # Coleccion propia del abogado con contenido propio.
    motor.ingestar_chunks(
        [TEXTO_PROPIO], nombre_coleccion_para_tenant(ids["abogado_f2"]),
        metadata={"titulo": "Minuta propia", "categoria": "Expediente",
                  "hash_archivo": "hash_propio_1"},
    )
    # Global con una ley de otro tema.
    motor.ingestar_chunks(
        [TEXTO_LEY], COLECCION_GLOBAL,
        metadata={"titulo": "Codigo del Trabajo", "categoria": "Leyes",
                  "hash_archivo": "hash_ley_1"},
    )


PREGUNTA_AJENA = (
    "¿Que dice el juicio 2026-04567 por despido intempestivo, "
    "cuanto ordena pagar y cual es la frase de control?"
)


def test_abogado_sin_permiso_recibe_denegacion_sin_filtrar(
    cliente: TestClient,
) -> None:
    tokens = _bootstrap_con_roles(cliente)
    _sembrar_vectores(cliente, _ids_por_username())
    mock = _cliente_llm_mockeado()

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post(
            "/api/chat",
            json={"pregunta": PREGUNTA_AJENA},
            headers={"Authorization": f"Bearer {tokens['abogado']}"},
        )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["acceso_denegado"] is True
    assert cuerpo["respuesta"] == MENSAJE_SIN_ACCESO
    assert "QUINUA-ZORRO-7741" not in cuerpo["respuesta"]
    assert "cuarenta y dos mil" not in cuerpo["respuesta"]
    assert cuerpo["fuentes"] == []
    # El LLM nunca fue llamado: cero riesgo de filtracion.
    assert mock.chat.completions.create.await_count == 0


def test_cliente_sin_permiso_tampoco_ve_juicio_ajeno(
    cliente: TestClient,
) -> None:
    tokens = _bootstrap_con_roles(cliente)
    _sembrar_vectores(cliente, _ids_por_username())
    mock = _cliente_llm_mockeado()

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post(
            "/api/chat",
            json={"pregunta": PREGUNTA_AJENA},
            headers={"Authorization": f"Bearer {tokens['cliente']}"},
        )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["acceso_denegado"] is True
    assert "QUINUA-ZORRO-7741" not in cuerpo["respuesta"]
    assert mock.chat.completions.create.await_count == 0


def test_cliente_con_permiso_documento_ve_solo_ese_doc(
    cliente: TestClient,
) -> None:
    tokens = _bootstrap_con_roles(cliente)
    ids = _ids_por_username()
    _sembrar_vectores(cliente, ids)

    # Admin concede el documento concreto al cliente.
    r = cliente.post(
        "/api/admin/permisos",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
        json={
            "usuario_id": ids["cliente_f2"],
            "recurso_tipo": "documento",
            "recurso_id": "hash_secreto_juicio_1",
        },
    )
    assert r.status_code == 201, r.text

    mock = _cliente_llm_mockeado("El fallo ordena el pago indicado.")
    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post(
            "/api/chat",
            json={"pregunta": PREGUNTA_AJENA},
            headers={"Authorization": f"Bearer {tokens['cliente']}"},
        )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["acceso_denegado"] is False
    assert mock.chat.completions.create.await_count == 1
    # Fuentes: solo el documento concedido.
    assert len(cuerpo["fuentes"]) >= 1
    for f in cuerpo["fuentes"]:
        assert f["metadata"].get("hash_archivo") == "hash_secreto_juicio_1"


def test_abogado_con_grant_de_coleccion_ve_contenido(
    cliente: TestClient,
) -> None:
    tokens = _bootstrap_con_roles(cliente)
    ids = _ids_por_username()
    _sembrar_vectores(cliente, ids)

    r = cliente.post(
        "/api/admin/permisos",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
        json={
            "usuario_id": ids["abogado_f2"],
            "recurso_tipo": "coleccion",
            "recurso_id": "casos_user_999",
        },
    )
    assert r.status_code == 201, r.text

    mock = _cliente_llm_mockeado("Detalles del juicio ajeno.")
    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post(
            "/api/chat",
            json={"pregunta": PREGUNTA_AJENA},
            headers={"Authorization": f"Bearer {tokens['abogado']}"},
        )
    cuerpo = r.json()
    assert cuerpo["acceso_denegado"] is False
    assert any(
        f["coleccion_origen"] == "casos_user_999" for f in cuerpo["fuentes"]
    )


def test_admin_ve_todo_sin_permisos(cliente: TestClient) -> None:
    tokens = _bootstrap_con_roles(cliente)
    _sembrar_vectores(cliente, _ids_por_username())

    mock = _cliente_llm_mockeado("Resumen del juicio.")
    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=mock):
        r = cliente.post(
            "/api/chat",
            json={"pregunta": PREGUNTA_AJENA},
            headers={"Authorization": f"Bearer {tokens['admin']}"},
        )
    cuerpo = r.json()
    assert cuerpo["acceso_denegado"] is False
    assert any(
        "QUINUA-ZORRO-7741" in f["texto"] for f in cuerpo["fuentes"]
    )


# ============================================================
# API de permisos: RBAC + CRUD + matriz + auditoria.
# ============================================================

def test_abogado_no_puede_gestionar_permisos(cliente: TestClient) -> None:
    tokens = _bootstrap_con_roles(cliente)
    r = cliente.post(
        "/api/admin/permisos",
        headers={"Authorization": f"Bearer {tokens['abogado']}"},
        json={"recurso_tipo": "coleccion", "recurso_id": "x"},
    )
    assert r.status_code == 403
    r = cliente.get(
        "/api/admin/permisos/matriz",
        headers={"Authorization": f"Bearer {tokens['cliente']}"},
    )
    assert r.status_code == 403


def test_crud_permisos_y_matriz(cliente: TestClient) -> None:
    tokens = _bootstrap_con_roles(cliente)
    ids = _ids_por_username()
    hdr = {"Authorization": f"Bearer {tokens['admin']}"}

    # Sin usuario ni grupo -> 400.
    r = cliente.post(
        "/api/admin/permisos", headers=hdr,
        json={"recurso_tipo": "coleccion", "recurso_id": "casos_user_999"},
    )
    assert r.status_code == 400

    # Crear.
    r = cliente.post(
        "/api/admin/permisos", headers=hdr,
        json={
            "usuario_id": ids["abogado_f2"],
            "recurso_tipo": "coleccion",
            "recurso_id": "casos_user_999",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["permiso_id"]

    # Listar + matriz.
    r = cliente.get("/api/admin/permisos", headers=hdr)
    assert r.status_code == 200 and len(r.json()) == 1
    r = cliente.get("/api/admin/permisos/matriz", headers=hdr)
    assert r.status_code == 200
    assert any(u["username"] == "cliente_f2" for u in r.json()["usuarios"])

    # Borrar.
    r = cliente.delete(f"/api/admin/permisos/{pid}", headers=hdr)
    assert r.status_code == 204
    r = cliente.get("/api/admin/permisos", headers=hdr)
    assert r.json() == []

    # Auditoria: crear + borrar dejan fila [ADMIN].
    bd = obtener_sesion_bd()
    try:
        filas = (
            bd.query(RegistroAuditoria)
            .filter(RegistroAuditoria.pregunta_ofuscada.like("[ADMIN] %permiso%"))
            .all()
        )
    finally:
        bd.close()
    assert len(filas) >= 2


def test_permiso_a_usuario_de_otra_firma_da_404(cliente: TestClient) -> None:
    tokens = _bootstrap_con_roles(cliente)
    r = cliente.post(
        "/api/admin/permisos",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
        json={
            "usuario_id": 999999,
            "recurso_tipo": "coleccion",
            "recurso_id": "casos_user_1",
        },
    )
    assert r.status_code == 404
