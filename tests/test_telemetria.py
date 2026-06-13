"""
Tests TDD de la telemetria y auditoria asincrona (Fase 6).

Cubre:
  - La funcion `guardar_auditoria` inserta un RegistroAuditoria
    con todos los campos correctos.
  - `formatear_fuentes_para_auditoria` convierte la lista de fuentes
    del orquestador en un string separado por comas.
  - El endpoint `POST /api/chat` programa la tarea en BackgroundTasks
    y la ejecuta ANTES de que TestClient retorne (Starlette corre
    los background tasks sincronamente al final del response cycle).
  - La pregunta CRUDA NUNCA llega a la BD de auditoria (Regla de Oro).
  - El conteo de tokens se persiste correctamente.
"""

from __future__ import annotations

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
from backend.db.modelos import RegistroAuditoria
from backend.db.vectorial import MotorVectorial
from backend.main import aplicacion
from backend.servicios.telemetria import (
    formatear_fuentes_para_auditoria,
    guardar_auditoria,
)


# ============================================================
# Fixtures.
# ============================================================

@pytest.fixture()
def cliente(embedding_function_compartido) -> TestClient:
    """Cliente FastAPI con BD reseteada y motor vectorial en EphemeralClient."""
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


def _bootstrap_admin(cliente: TestClient) -> str:
    """Crea tenant + admin via /auth/registro y devuelve el JWT."""
    r = cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete Telemetria",
            "username": "admin_telemetria",
            "contrasena": "contrasenaSegura123",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _cliente_llm_mockeado(
    contenido: str = "Respuesta del LLM mockeado.",
    prompt_tokens: int = 200,
    completion_tokens: int = 80,
) -> MagicMock:
    """Construye un mock de AsyncOpenAI con `usage` poblado."""
    respuesta = MagicMock()
    respuesta.choices = [MagicMock()]
    respuesta.choices[0].message.content = contenido
    respuesta.usage.prompt_tokens = prompt_tokens
    respuesta.usage.completion_tokens = completion_tokens

    cliente = MagicMock()
    cliente.chat.completions.create = AsyncMock(return_value=respuesta)
    return cliente


# ============================================================
# Tests unitarios: guardar_auditoria y formatear_fuentes.
# ============================================================

def test_guardar_auditoria_inserta_registro_con_todos_los_campos() -> None:
    """La funcion persiste un RegistroAuditoria y devuelve su id."""
    # Reseteamos para evitar restos de otros tests.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    id_registro = guardar_auditoria(
        tenant_id="42",
        pregunta_ofuscada="Mi cliente [NOMBRE] apelo la sentencia",
        respuesta_llm="Respuesta del LLM",
        fuentes_usadas="leyes_globales_abc123,casos_user_1_def456",
        tokens_prompt=200,
        tokens_completion=80,
    )
    assert isinstance(id_registro, int)
    assert id_registro > 0

    bd = obtener_sesion_bd()
    try:
        registro = bd.get(RegistroAuditoria, id_registro)
    finally:
        bd.close()

    assert registro is not None
    assert registro.tenant_id == "42"
    assert registro.pregunta_ofuscada == "Mi cliente [NOMBRE] apelo la sentencia"
    assert registro.respuesta_llm == "Respuesta del LLM"
    assert registro.fuentes_usadas == "leyes_globales_abc123,casos_user_1_def456"
    assert registro.tokens_prompt == 200
    assert registro.tokens_completion == 80
    assert registro.timestamp is not None


def test_guardar_auditoria_NO_persiste_pregunta_cruda() -> None:
    """Gobernanza: la BD de auditoria NUNCA contiene la pregunta cruda.

    Esto se valida de extremo a extremo: la pregunta cruda se ofusca
    primero con `ofuscar_texto` (la Regla de Oro), y SOLO la version
    ofuscada llega a `guardar_auditoria`. La funcion de auditoria
    es un sumidero: no hace mas PII-scrubbing que el caller ya hizo.
    """
    from backend.servicios.ofuscacion import ofuscar_texto

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    pregunta_cruda = "Juan Pérez con DNI 12345678Z apeló la sentencia"
    pregunta_limpia = ofuscar_texto(pregunta_cruda)

    # El caller (endpoint) es responsable de pasar la version ofuscada.
    guardar_auditoria(
        tenant_id="1",
        pregunta_ofuscada=pregunta_limpia,
        respuesta_llm="ok",
        fuentes_usadas="",
        tokens_prompt=10,
        tokens_completion=5,
    )

    bd = obtener_sesion_bd()
    try:
        registros = bd.query(RegistroAuditoria).all()
    finally:
        bd.close()

    assert len(registros) == 1
    # La BD no debe contener NADA de la pregunta cruda.
    registro = registros[0]
    assert "Juan Pérez" not in registro.pregunta_ofuscada
    assert "12345678Z" not in registro.pregunta_ofuscada
    assert "[NOMBRE]" in registro.pregunta_ofuscada
    assert "[DNI]" in registro.pregunta_ofuscada


def test_formatear_fuentes_une_ids_por_comas() -> None:
    fuentes = [
        {"id": "leyes_globales_abc", "texto": "..."},
        {"id": "casos_user_1_xyz", "texto": "..."},
    ]
    assert formatear_fuentes_para_auditoria(fuentes) == "leyes_globales_abc,casos_user_1_xyz"


def test_formatear_fuentes_omite_ids_vacios() -> None:
    fuentes = [
        {"id": "id1", "texto": "..."},
        {"id": "", "texto": "..."},
        {"id": "id3", "texto": "..."},
    ]
    assert formatear_fuentes_para_auditoria(fuentes) == "id1,id3"


def test_formatear_fuentes_lista_vacia_devuelve_cadena_vacia() -> None:
    assert formatear_fuentes_para_auditoria([]) == ""


# ============================================================
# Tests de integracion: endpoint /api/chat con BackgroundTasks.
# ============================================================

def test_endpoint_api_chat_encola_tarea_y_responde_200(
    cliente: TestClient,
) -> None:
    """
    Despues de una llamada exitosa a /api/chat, debe haber UN
    RegistroAuditoria con los tokens correctos y la pregunta ofuscada.
    """
    token = _bootstrap_admin(cliente)
    cliente_llm = _cliente_llm_mockeado(
        contenido="Respuesta RAG simulada.",
        prompt_tokens=200,
        completion_tokens=80,
    )

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        respuesta = cliente.post(
            "/api/chat",
            json={"pregunta": "El cliente Juan Pérez apelo la sentencia"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    # La respuesta HTTP debe llegar ANTES de que se bloquee nada.
    assert "respuesta" in cuerpo
    assert cuerpo["tokens_prompt"] == 200
    assert cuerpo["tokens_completion"] == 80

    # Tras la respuesta, el BackgroundTask ya debio haber corrido
    # (Starlette TestClient los ejecuta sincronamente al final del ciclo).
    bd = obtener_sesion_bd()
    try:
        registros = bd.query(RegistroAuditoria).all()
    finally:
        bd.close()

    assert len(registros) == 1
    registro = registros[0]
    assert registro.tokens_prompt == 200
    assert registro.tokens_completion == 80
    # Gobernanza: la BD no debe contener la pregunta cruda.
    assert "Juan Pérez" not in registro.pregunta_ofuscada
    assert "[NOMBRE]" in registro.pregunta_ofuscada


def test_endpoint_api_chat_no_bloquea_si_falla_la_auditoria(
    cliente: TestClient,
) -> None:
    """
    Si la auditoria falla (ej. BD bloqueada), la respuesta al cliente
    debe seguir siendo 200: el background task NO afecta al usuario.
    """
    token = _bootstrap_admin(cliente)
    cliente_llm = _cliente_llm_mockeado()

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        with patch(
            "backend.servicios.telemetria.guardar_auditoria",
            side_effect=RuntimeError("BD no disponible"),
        ):
            respuesta = cliente.post(
                "/api/chat",
                json={"pregunta": "Pregunta valida"},
                headers={"Authorization": f"Bearer {token}"},
            )

    # La respuesta al cliente sigue siendo 200 aunque la auditoria falle.
    assert respuesta.status_code == 200


def test_endpoint_api_chat_auditoria_tiene_tenant_id_de_la_firma(
    cliente: TestClient,
) -> None:
    """El tenant_id en la auditoria es el de la FIRMA, no el del abogado."""
    token = _bootstrap_admin(cliente)
    cliente_llm = _cliente_llm_mockeado()

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        cliente.post(
            "/api/chat",
            json={"pregunta": "Mi cliente tiene dudas"},
            headers={"Authorization": f"Bearer {token}"},
        )

    bd = obtener_sesion_bd()
    try:
        registros = bd.query(RegistroAuditoria).all()
    finally:
        bd.close()

    assert len(registros) == 1
    # Tras el registro, el tenant_id del admin es 1 (primer tenant creado).
    assert registros[0].tenant_id == "1"


def test_endpoint_chat_preguntar_tambien_encola_auditoria(
    cliente: TestClient,
) -> None:
    """El endpoint legacy /chat/preguntar tambien escribe en auditoria."""
    token = _bootstrap_admin(cliente)
    cliente_llm = _cliente_llm_mockeado(
        contenido="ok", prompt_tokens=50, completion_tokens=20
    )

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        respuesta = cliente.post(
            "/chat/preguntar",
            json={"pregunta": "Pregunta al chat legacy"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert respuesta.status_code == 200

    bd = obtener_sesion_bd()
    try:
        registros = bd.query(RegistroAuditoria).all()
    finally:
        bd.close()

    assert len(registros) == 1
    assert registros[0].tokens_prompt == 50
    assert registros[0].tokens_completion == 20


def test_fuentes_chromadb_se_persisten_en_auditoria(
    cliente: TestClient,
) -> None:
    """
    Si el orquestador recupera chunks de ChromaDB, sus IDs deben quedar
    registrados en el campo `fuentes_usadas` del RegistroAuditoria.

    Para garantizar que el endpoint y el test apuntan al MISMO motor
    vectorial (el inyectado via dependency_overrides), reconstruimos
    un MotorVectorial sobre el mismo `cliente_efimero` y lo volvemos
    a inyectar. Asi el `dependency_overrides[obtener_motor_vectorial]`
    sigue valiendo para el endpoint.
    """
    from backend.db.vectorial import COLECCION_GLOBAL

    token = _bootstrap_admin(cliente)
    cliente_llm = _cliente_llm_mockeado()

    # Ingestamos un documento en la coleccion global a traves del
    # motor que el endpoint va a usar. El truco: dependemos de
    # `obtener_motor_vectorial` para que devuelva la misma instancia
    # que el test acaba de poblar.
    from backend.api.dependencias import obtener_motor_vectorial as dep
    motor_para_test = cliente.app.dependency_overrides[dep]()
    n = motor_para_test.ingestar_chunks(
        ["Ley aplicable al caso de prueba."],
        COLECCION_GLOBAL,
        metadata={"tier": 1},
    )
    assert n == 1

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        cliente.post(
            "/api/chat",
            json={"pregunta": "que ley es aplicable?"},
            headers={"Authorization": f"Bearer {token}"},
        )

    bd = obtener_sesion_bd()
    try:
        registros = bd.query(RegistroAuditoria).all()
    finally:
        bd.close()

    assert len(registros) == 1
    fuentes = registros[0].fuentes_usadas
    # El ID del chunk ingestado debe empezar por "leyes_globales_".
    assert fuentes.startswith("leyes_globales_")
    assert len(fuentes) > 0
