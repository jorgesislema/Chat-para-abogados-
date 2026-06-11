"""
Tests de humo para los endpoints principales de FastAPI.

Usa `TestClient` de Starlette y mockea el cliente AsyncOpenAI para
no depender de red ni de API keys reales. Ademas sobreescribe la
dependencia `obtener_motor_vectorial` con un `EphemeralClient`
para que los tests no contaminen `backend/db/chroma_data/`.
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
from backend.db.sesion import engine
from backend.db.vectorial import MotorVectorial
from backend.main import aplicacion


# ============================================================
# Fixtures.
# ============================================================

@pytest.fixture()
def cliente() -> TestClient:
    """Cliente FastAPI con BD SQL reseteada (sin override de motor)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestClient(aplicacion)


@pytest.fixture()
def cliente_con_motor_aislado(
    cliente: TestClient, embedding_function_compartido
) -> TestClient:
    """
    Cliente FastAPI con el `MotorVectorial` reemplazado por uno sobre
    `EphemeralClient`. Asi los tests de chat no tocan `backend/db/chroma_data/`
    ni dejan basura entre corridas.
    """
    _reset_singletons_for_tests()

    cliente_efimero = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
    cliente_efimero.reset()
    motor_test = MotorVectorial(
        cliente=cliente_efimero,
        embedding_function=embedding_function_compartido,
    )

    aplicacion.dependency_overrides[obtener_motor_vectorial] = lambda: motor_test
    try:
        yield cliente
    finally:
        aplicacion.dependency_overrides.clear()
        _reset_singletons_for_tests()


# ============================================================
# Helpers.
# ============================================================

def _registrar_y_obtener_token(cliente: TestClient) -> str:
    r = cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete Test",
            "email": "test@bufete.com",
            "contrasena": "contrasenaSegura123",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _cliente_openai_mockeado(texto_respuesta: str = "Respuesta simulada del LLM."):
    """Construye un mock de AsyncOpenAI cuya llamada retorna un texto fijo."""
    respuesta_mock = MagicMock()
    respuesta_mock.choices = [MagicMock()]
    respuesta_mock.choices[0].message.content = texto_respuesta

    cliente_mock = MagicMock()
    cliente_mock.chat.completions.create = AsyncMock(return_value=respuesta_mock)
    return cliente_mock


# ============================================================
# Tests del healthcheck.
# ============================================================

def test_healthcheck_devuelve_ok(cliente: TestClient) -> None:
    respuesta = cliente.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"


# ============================================================
# Tests de auth basicos.
# ============================================================

def test_preguntar_sin_token_es_401(cliente: TestClient) -> None:
    respuesta = cliente.post("/chat/preguntar", json={"pregunta": "hola"})
    assert respuesta.status_code == 401


def test_api_chat_sin_token_devuelve_401(cliente: TestClient) -> None:
    respuesta = cliente.post("/api/chat", json={"pregunta": "hola mundo"})
    assert respuesta.status_code == 401


def test_api_chat_salud_sin_token_devuelve_401(cliente: TestClient) -> None:
    respuesta = cliente.get("/api/chat/salud")
    assert respuesta.status_code == 401


def test_registro_y_login_exitosos(cliente: TestClient) -> None:
    payload = {
        "nombre_tenant": "Bufete Demo",
        "email": "demo@bufete.com",
        "contrasena": "contrasenaSegura123",
    }
    r_registro = cliente.post("/auth/registro", json=payload)
    assert r_registro.status_code == 201, r_registro.text
    assert "access_token" in r_registro.json()

    r_login = cliente.post(
        "/auth/login",
        json={"email": payload["email"], "contrasena": payload["contrasena"]},
    )
    assert r_login.status_code == 200
    assert "access_token" in r_login.json()


def test_registro_duplicado_devuelve_409(cliente: TestClient) -> None:
    payload = {
        "nombre_tenant": "Bufete Unico",
        "email": "unico@bufete.com",
        "contrasena": "contrasenaSegura123",
    }
    cliente.post("/auth/registro", json=payload)
    r = cliente.post("/auth/registro", json=payload)
    assert r.status_code == 409


def test_api_chat_salud_con_token_devuelve_tenant(cliente: TestClient) -> None:
    token = _registrar_y_obtener_token(cliente)
    respuesta = cliente.get(
        "/api/chat/salud",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ok"
    assert "tenant_id" in cuerpo
    assert "usuario_id" in cuerpo


# ============================================================
# Tests del flujo RAG con LLM mockeado (AsyncOpenAI).
# ============================================================

def test_chat_aplica_ofuscacion_antes_del_llm(
    cliente_con_motor_aislado: TestClient,
) -> None:
    """
    Verifica el contrato critico de Gobernanza: el nombre 'Juan Perez'
    NUNCA llega al LLM aunque el endpoint /chat/preguntar responda 200.

    Mockeamos `AsyncOpenAI` para no depender de red. El motor vectorial
    esta sobre `EphemeralClient` (inyectado via dependency_overrides).
    """
    token = _registrar_y_obtener_token(cliente_con_motor_aislado)
    cliente_llm = _cliente_openai_mockeado("Respuesta simulada del LLM.")

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        r_chat = cliente_con_motor_aislado.post(
            "/chat/preguntar",
            json={"pregunta": "El cliente Juan Pérez se opone a la demanda"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r_chat.status_code == 200, r_chat.text
    cuerpo = r_chat.json()
    assert "Juan Pérez" not in cuerpo["pregunta_ofuscada"]
    assert "[NOMBRE]" in cuerpo["pregunta_ofuscada"]
    assert cuerpo["respuesta"] == "Respuesta simulada del LLM."


def test_api_chat_ejecuta_cadena_rag_y_ofusca(
    cliente_con_motor_aislado: TestClient,
) -> None:
    """
    El endpoint /api/chat ejecuta la cadena RAG completa: ofusca,
    busca en el motor vectorial y llama al LLM mockeado.
    Verificamos ademas que el `pregunta_ofuscada` se devuelve al frontend
    para que el abogado pueda auditar la Gobernanza PII.
    """
    token = _registrar_y_obtener_token(cliente_con_motor_aislado)
    cliente_llm = _cliente_openai_mockeado("Respuesta RAG simulada.")

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        r = cliente_con_motor_aislado.post(
            "/api/chat",
            json={"pregunta": "El cliente Juan Pérez apelo la sentencia"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert r.status_code == 200, r.text
    cuerpo = r.json()

    # Respuesta real del LLM (no placeholder).
    assert cuerpo["respuesta"] == "Respuesta RAG simulada."
    assert cuerpo["modelo"] != "placeholder-sin-llm"

    # Gobernanza PII.
    assert "Juan Pérez" not in cuerpo["pregunta_ofuscada"]
    assert "[NOMBRE]" in cuerpo["pregunta_ofuscada"]

    # Metadatos del JWT propagados al response.
    assert isinstance(cuerpo["tenant_id"], int)
    assert isinstance(cuerpo["usuario_id"], int)

    # El endpoint debe devolver al menos la lista de fuentes (posiblemente
    # vacia si la coleccion del tenant no tiene contenido).
    assert "fuentes" in cuerpo
    assert isinstance(cuerpo["fuentes"], list)


def test_api_chat_llama_al_LLM_con_la_pregunta_ofuscada(
    cliente_con_motor_aislado: TestClient,
) -> None:
    """
    La pregunta que llega al LLM (en `messages[user]`) debe ser la
    version OFUSCADA. Si llega la cruda, la Gobernanza PII esta rota.
    """
    token = _registrar_y_obtener_token(cliente_con_motor_aislado)
    cliente_llm = _cliente_openai_mockeado("ok")

    with patch("backend.servicios.rag_motor.AsyncOpenAI", return_value=cliente_llm):
        cliente_con_motor_aislado.post(
            "/api/chat",
            json={"pregunta": "Mi cliente Juan Pérez tiene dudas"},
            headers={"Authorization": f"Bearer {token}"},
        )

    # Inspeccionamos la llamada al LLM mockeado.
    llamada = cliente_llm.chat.completions.create.call_args
    mensajes = llamada.kwargs["messages"]
    mensaje_usuario = next(m for m in mensajes if m["role"] == "user")
    contenido_usuario = mensaje_usuario["content"]
    assert "Juan Pérez" not in contenido_usuario
    assert "[NOMBRE]" in contenido_usuario


def test_api_chat_pregunta_demasiado_corta_es_422(
    cliente_con_motor_aislado: TestClient,
) -> None:
    token = _registrar_y_obtener_token(cliente_con_motor_aislado)
    respuesta = cliente_con_motor_aislado.post(
        "/api/chat",
        json={"pregunta": "ab"},  # 2 chars: viola min_length=3
        headers={"Authorization": f"Bearer {token}"},
    )
    assert respuesta.status_code == 422
