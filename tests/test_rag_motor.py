"""
Tests unitarios del OrquestadorRAG (backend.servicios.rag_motor).

Cubre:
  - La cadena completa ofuscar -> buscar -> prompt -> LLM -> respuesta.
  - El contrato del dict retornado (pregunta_original, pregunta_ofuscada,
    respuesta, modelo, fuentes).
  - La Gobernanza: la pregunta que llega al LLM es la version ofuscada.
  - Inyeccion de dependencias: el motor vectorial se inyecta y no se
    crea uno nuevo por accidente.
  - Aislamiento de tenant: la busqueda usa la coleccion `casos_user_<id>`.
  - Sin llamada real a OpenRouter: AsyncOpenAI es un Mock.

El cliente AsyncOpenAI es siempre un mock (AsyncMock) para evitar
consumo de tokens. El motor vectorial es REAL pero contra un
EphemeralClient (no toca disco).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import chromadb
import pytest
from chromadb.config import Settings

from backend.db.vectorial import (
    COLECCION_GLOBAL,
    MotorVectorial,
    nombre_coleccion_para_tenant,
)
from backend.servicios.rag_motor import OrquestadorRAG


# ============================================================
# Fixtures.
# ============================================================

@pytest.fixture()
def cliente_chroma_efimero() -> chromadb.api.ClientAPI:
    """EphemeralClient con reset() para tests aislados."""
    cliente = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
    cliente.reset()
    return cliente


@pytest.fixture()
def motor(cliente_chroma_efimero, embedding_function_compartido) -> MotorVectorial:
    """MotorVectorial real contra EphemeralClient, con EF compartido."""
    return MotorVectorial(
        cliente=cliente_chroma_efimero,
        embedding_function=embedding_function_compartido,
    )


@pytest.fixture()
def cliente_llm_mock() -> MagicMock:
    """Mock de AsyncOpenAI: `chat.completions.create` retorna texto fijo."""
    respuesta = MagicMock()
    respuesta.choices = [MagicMock()]
    respuesta.choices[0].message.content = "Respuesta del LLM mockeado."

    cliente = MagicMock()
    cliente.chat.completions.create = AsyncMock(return_value=respuesta)
    return cliente


@pytest.fixture()
def orquestador(motor: MotorVectorial, cliente_llm_mock: MagicMock) -> OrquestadorRAG:
    """OrquestadorRAG cableado con motor real y LLM mockeado."""
    return OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="modelo-test",
    )


# ============================================================
# Tests basicos de la cadena.
# ============================================================

async def test_consultar_devuelve_dict_estructurado(orquestador: OrquestadorRAG) -> None:
    resultado = await orquestador.consultar(
        pregunta_cruda="Que dice el codigo civil sobre divorcio?",
        tenant_id=42,
    )

    assert isinstance(resultado, dict)
    assert set(resultado.keys()) == {
        "pregunta_original",
        "pregunta_ofuscada",
        "respuesta",
        "modelo",
        "fuentes",
    }
    assert resultado["pregunta_original"] == "Que dice el codigo civil sobre divorcio?"
    assert resultado["respuesta"] == "Respuesta del LLM mockeado."
    assert resultado["modelo"] == "modelo-test"
    assert isinstance(resultado["fuentes"], list)


async def test_consultar_ofusca_la_pregunta(orquestador: OrquestadorRAG) -> None:
    """El dict retornado incluye la pregunta ORIGINAL y la OFUSCADA."""
    resultado = await orquestador.consultar(
        pregunta_cruda="El cliente Juan Pérez apelo la sentencia",
        tenant_id=1,
    )
    assert "Juan Pérez" in resultado["pregunta_original"]
    assert "Juan Pérez" not in resultado["pregunta_ofuscada"]
    assert "[NOMBRE]" in resultado["pregunta_ofuscada"]


async def test_consultar_llama_a_openrouter_async(orquestador: OrquestadorRAG) -> None:
    """El cliente AsyncOpenAI fue invocado exactamente una vez."""
    await orquestador.consultar("Pregunta generica", tenant_id=1)
    cliente_llm = orquestador._cliente_llm  # noqa: SLF001 - test intencional
    cliente_llm.chat.completions.create.assert_awaited_once()


async def test_consultar_pasa_el_modelo_configurado(
    orquestador: OrquestadorRAG,
) -> None:
    await orquestador.consultar("Pregunta", tenant_id=1)
    llamada = orquestador._cliente_llm.chat.completions.create.call_args  # noqa: SLF001
    assert llamada.kwargs["model"] == "modelo-test"


# ============================================================
# Gobernanza: la pregunta al LLM es la ofuscada.
# ============================================================

async def test_llm_recibe_pregunta_ofuscada_en_mensaje_user(
    orquestador: OrquestadorRAG,
) -> None:
    """
    CRITICO: el contenido del mensaje `user` enviado al LLM NO debe
    contener nombres propios. Si los contiene, la Gobernanza PII esta
    rota y la PII del cliente se envia a OpenRouter.
    """
    await orquestador.consultar(
        pregunta_cruda="Mi cliente Juan Pérez (DNI 12345678Z) quiere impugnar",
        tenant_id=1,
    )
    llamada = orquestador._cliente_llm.chat.completions.create.call_args  # noqa: SLF001
    mensajes = llamada.kwargs["messages"]
    contenido_user = next(m["content"] for m in mensajes if m["role"] == "user")
    contenido_system = next(m["content"] for m in mensajes if m["role"] == "system")

    assert "Juan Pérez" not in contenido_user
    assert "12345678Z" not in contenido_user
    assert "[NOMBRE]" in contenido_user
    assert "[DNI]" in contenido_user

    # El system prompt tampoco debe llevar la PII cruda.
    assert "Juan Pérez" not in contenido_system
    assert "12345678Z" not in contenido_system


async def test_llm_recibe_contexto_recuperado_en_system(
    orquestador: OrquestadorRAG,
) -> None:
    """El system prompt debe llevar el contexto del vector store, no la
    pregunta del usuario (que va al user)."""
    motor = orquestador._motor  # noqa: SLF001
    motor.ingestar_chunks(
        ["El articulo 44 del Codigo Civil regula el divorcio."],
        "casos_user_5",
    )
    await orquestador.consultar("divorcio articulo 44", tenant_id=5)
    llamada = orquestador._cliente_llm.chat.completions.create.call_args  # noqa: SLF001
    contenido_system = next(
        m["content"] for m in llamada.kwargs["messages"] if m["role"] == "system"
    )
    # Si hay contexto recuperado, debe aparecer en el system prompt.
    assert "Codigo Civil" in contenido_system or "(sin contexto" in contenido_system


# ============================================================
# Busqueda vectorial.
# ============================================================

async def test_consultar_busca_en_coleccion_del_tenant(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    motor.ingestar_chunks(
        ["Documento del usuario 7 sobre despido laboral."],
        "casos_user_7",
        metadata={"tier": 3, "propietario": "user_7"},
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
    )
    await orquestador.consultar("informacion sobre despido", tenant_id=7)

    llamada = cliente_llm_mock.chat.completions.create.call_args
    contenido_system = next(
        m["content"] for m in llamada.kwargs["messages"] if m["role"] == "system"
    )
    # El chunk del usuario 7 debe aparecer en el contexto enviado al LLM.
    assert "despido laboral" in contenido_system


async def test_consultar_incluye_coleccion_global_si_incluir_global(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    motor.ingestar_chunks(
        ["La Constitucion Espanola garantiza la igualdad en su articulo 14."],
        COLECCION_GLOBAL,
    )
    motor.ingestar_chunks(
        ["Mi caso privado del usuario 9."],
        "casos_user_9",
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
        incluir_global=True,
    )
    await orquestador.consultar("igualdad constitucional", tenant_id=9)

    llamada = cliente_llm_mock.chat.completions.create.call_args
    contenido_system = next(
        m["content"] for m in llamada.kwargs["messages"] if m["role"] == "system"
    )
    assert "Constitucion" in contenido_system
    assert "articulo 14" in contenido_system


async def test_consultar_excluye_coleccion_global_si_incluir_global_false(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    motor.ingestar_chunks(
        ["Ley publica aplicable a todos."],
        COLECCION_GLOBAL,
    )
    motor.ingestar_chunks(
        ["Mi caso privado."],
        "casos_user_3",
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
        incluir_global=False,
    )
    await orquestador.consultar("pregunta", tenant_id=3)

    llamada = cliente_llm_mock.chat.completions.create.call_args
    contenido_system = next(
        m["content"] for m in llamada.kwargs["messages"] if m["role"] == "system"
    )
    # Sin inclusion global, el system prompt no deberia contener la ley publica.
    assert "Ley publica aplicable a todos" not in contenido_system


# ============================================================
# Aislamiento de tenant.
# ============================================================

async def test_no_filtra_datos_de_otros_tenants(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    motor.ingestar_chunks(
        ["Secreto A: solo el usuario 1 ve esto."],
        "casos_user_1",
        metadata={"dueno": "A"},
    )
    motor.ingestar_chunks(
        ["Secreto B: solo el usuario 2 ve esto."],
        "casos_user_2",
        metadata={"dueno": "B"},
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
        incluir_global=False,
    )
    await orquestador.consultar("secreto de un cliente", tenant_id=1)

    llamada = cliente_llm_mock.chat.completions.create.call_args
    contenido_system = next(
        m["content"] for m in llamada.kwargs["messages"] if m["role"] == "system"
    )
    assert "Secreto A" in contenido_system
    assert "Secreto B" not in contenido_system


# ============================================================
# Inyeccion de dependencias.
# ============================================================

async def test_no_instancia_motor_vectorial_nuevo(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    """El motor que se pasa al constructor es el que se usa."""
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
    )
    assert orquestador.motor_vectorial is motor


async def test_cliente_llm_personalizado_se_respeta(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
    )
    assert orquestador._cliente_llm is cliente_llm_mock  # noqa: SLF001


async def test_acepta_tenant_id_como_int_y_str(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    """El orquestador acepta tenant_id como int o str (el JWT da int)."""
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
    )
    motor.ingestar_chunks(
        ["contenido para el usuario 8"],
        nombre_coleccion_para_tenant(8),
    )
    # Como str:
    await orquestador.consultar("pregunta", tenant_id="8")
    # Como int:
    await orquestador.consultar("pregunta", tenant_id=8)
    # Ambas deben haber completado sin lanzar.


async def test_tenant_id_invalido_lanza_value_error(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
    )
    with pytest.raises(ValueError):
        await orquestador.consultar("pregunta", tenant_id="no-es-un-numero")


# ============================================================
# Estructura del retorno.
# ============================================================

async def test_fuentes_contienen_texto_metadata_puntuacion_y_origen(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    motor.ingestar_chunks(
        ["Chunk relevante del usuario 11."],
        "casos_user_11",
        metadata={"archivo": "expediente.pdf", "tier": 3},
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
        incluir_global=False,
        top_k=1,
    )
    resultado = await orquestador.consultar("relevante", tenant_id=11)
    assert len(resultado["fuentes"]) >= 1
    fuente = resultado["fuentes"][0]
    assert "texto" in fuente
    assert "metadata" in fuente
    assert "puntuacion" in fuente
    assert "coleccion_origen" in fuente
    assert fuente["metadata"].get("archivo") == "expediente.pdf"
    assert fuente["coleccion_origen"] == "casos_user_11"


async def test_fuentes_vacias_si_no_hay_contexto(
    motor: MotorVectorial,
    cliente_llm_mock: MagicMock,
) -> None:
    """Tenant sin coleccion propia -> solo leyes globales -> 0 fuentes si
    no se incluyen y no hay global tampoco."""
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente_llm_mock,
        modelo="m",
        incluir_global=False,
    )
    resultado = await orquestador.consultar("pregunta sin contexto", tenant_id=999)
    assert resultado["fuentes"] == []
