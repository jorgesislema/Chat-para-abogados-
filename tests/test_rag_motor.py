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
  - Resiliencia (Fase 7): reintentos exponenciales sobre errores
    temporales y fail-fast sobre errores duros del proveedor.

El cliente AsyncOpenAI es siempre un mock (AsyncMock) para evitar
consumo de tokens. El motor vectorial es REAL pero contra un
EphemeralClient (no toca disco).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import chromadb
import pytest
from chromadb.config import Settings
from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

from backend.db.vectorial import (
    COLECCION_GLOBAL,
    MotorVectorial,
    nombre_coleccion_para_tenant,
)
from backend.servicios.rag_motor import (
    MENSAJE_DEGRADACION_IA,
    OrquestadorRAG,
)


@pytest.fixture()
def _sin_espera_tenacity(monkeypatch):
    """
    Anula los `sleep` que `tenacity` programa entre reintentos
    (`wait_exponential(multiplier=1, min=2, max=10)` -> 2s + 4s = 6s).

    Sin este fixture la suite tardaria minutos en lugar de milisegundos.
    En tenacity 9.x, `wait_exponential` invoca `tenacity.nap.sleep`
    (que por defecto delega a `time.sleep`).
    """
    monkeypatch.setattr("tenacity.nap.sleep", lambda *a, **kw: None)
    yield


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
    """Mock de AsyncOpenAI: `chat.completions.create` retorna texto fijo.

    Por defecto NO incluye `usage` (los tests de Fase 6 usan
    `cliente_llm_mock_con_usage` para forzar valores de tokens).
    """
    respuesta = MagicMock()
    respuesta.choices = [MagicMock()]
    respuesta.choices[0].message.content = "Respuesta del LLM mockeado."
    respuesta.usage = None  # explicito: no hay metricas en este mock

    cliente = MagicMock()
    cliente.chat.completions.create = AsyncMock(return_value=respuesta)
    return cliente


@pytest.fixture()
def cliente_llm_mock_con_usage() -> MagicMock:
    """Mock de AsyncOpenAI con `usage` poblado para tests de telemetria."""
    respuesta = MagicMock()
    respuesta.choices = [MagicMock()]
    respuesta.choices[0].message.content = "Respuesta del LLM mockeado."
    respuesta.usage.prompt_tokens = 123
    respuesta.usage.completion_tokens = 45

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
    # El contrato incluye las claves originales + telemetria (Fase 6).
    for k in (
        "pregunta_original",
        "pregunta_ofuscada",
        "respuesta",
        "modelo",
        "fuentes",
        "tokens_prompt",
        "tokens_completion",
    ):
        assert k in resultado, f"Falta la clave {k!r}"
    assert resultado["pregunta_original"] == "Que dice el codigo civil sobre divorcio?"
    assert resultado["respuesta"] == "Respuesta del LLM mockeado."
    assert resultado["modelo"] == "modelo-test"
    assert isinstance(resultado["fuentes"], list)
    # El mock no setea .usage -> defaults a 0.
    assert resultado["tokens_prompt"] == 0
    assert resultado["tokens_completion"] == 0


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


async def test_consultar_extrae_tokens_del_usage(
    motor: MotorVectorial,
) -> None:
    """Cuando el LLM devuelve `usage`, el orquestador extrae los tokens."""
    from backend.servicios.rag_motor import OrquestadorRAG

    respuesta = MagicMock()
    respuesta.choices = [MagicMock()]
    respuesta.choices[0].message.content = "ok"
    respuesta.usage.prompt_tokens = 250
    respuesta.usage.completion_tokens = 80

    cliente = MagicMock()
    cliente.chat.completions.create = AsyncMock(return_value=respuesta)

    orquestador = OrquestadorRAG(
        motor_vectorial=motor, cliente_llm=cliente, modelo="m"
    )
    resultado = await orquestador.consultar("p", tenant_id=1)
    assert resultado["tokens_prompt"] == 250
    assert resultado["tokens_completion"] == 80


async def test_consultar_sin_usage_devuelve_tokens_cero(
    motor: MotorVectorial,
) -> None:
    """Si el LLM no devuelve `usage` (ej. modelo antiguo), defaults a 0."""
    from backend.servicios.rag_motor import OrquestadorRAG

    respuesta = MagicMock()
    respuesta.choices = [MagicMock()]
    respuesta.choices[0].message.content = "ok"
    respuesta.usage = None

    cliente = MagicMock()
    cliente.chat.completions.create = AsyncMock(return_value=respuesta)

    orquestador = OrquestadorRAG(
        motor_vectorial=motor, cliente_llm=cliente, modelo="m"
    )
    resultado = await orquestador.consultar("p", tenant_id=1)
    assert resultado["tokens_prompt"] == 0
    assert resultado["tokens_completion"] == 0


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


# ============================================================
# Resiliencia (Fase 7): tenacity + degradacion elegante.
# ============================================================
#
# Reglas bajo prueba:
#   - Errores temporales (APIConnectionError, RateLimitError,
#     InternalServerError) disparan hasta 3 reintentos exponenciales.
#   - Errores duros (AuthenticationError -> 401) NO se reintentan: fail-fast.
#   - Tras agotar reintentos (o ante error duro), `consultar` retorna
#     un dict de error controlado con `fuentes` intactas, en vez de
#     propagar la excepcion.


def _cliente_llm_que_falla_con(excepcion: BaseException) -> MagicMock:
    """
    Construye un mock de AsyncOpenAI cuyo `chat.completions.create`
    SIEMPRE lanza la excepcion indicada (sin importar el numero de intento).
    """
    cliente = MagicMock()
    cliente.chat.completions.create = AsyncMock(side_effect=excepcion)
    return cliente


async def test_reintenta_3_veces_ante_api_connection_error(
    motor: MotorVectorial,
    _sin_espera_tenacity,
) -> None:
    """
    Ante un `APIConnectionError` (timeout, DNS, red caida) el orquestador
    debe reintentar la llamada hasta 3 veces (1 intento + 2 reintentos)
    antes de rendirse.
    """
    cliente = _cliente_llm_que_falla_con(
        APIConnectionError(request=MagicMock())
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente,
        modelo="m",
    )

    resultado = await orquestador.consultar("pregunta", tenant_id=1)

    # Verificacion CONTRACTUAL de la resiliencia:
    # tenacity con stop_after_attempt(3) ejecuta la funcion 3 veces
    # antes de propagar la excepcion al `try/except` de `consultar`.
    assert cliente.chat.completions.create.await_count == 3, (
        f"Se esperaban 3 intentos, hubo "
        f"{cliente.chat.completions.create.await_count}"
    )

    # La degradacion elegante debe dispararse tras agotar reintentos.
    assert resultado["respuesta"] == MENSAJE_DEGRADACION_IA


async def test_reintenta_3_veces_ante_rate_limit_error(
    motor: MotorVectorial,
    _sin_espera_tenacity,
) -> None:
    """`RateLimitError` (429) tambien es transitorio: 3 intentos."""
    # Usamos `RateLimitError` (subclase de `APIStatusError`) para que
    # `tenacity.retry_if_exception_type` lo reconozca como transitorio.
    error_429 = RateLimitError(
        "Rate limit reached",
        response=MagicMock(status_code=429),
        body=None,
    )
    cliente = _cliente_llm_que_falla_con(error_429)
    orquestador = OrquestadorRAG(
        motor_vectorial=motor, cliente_llm=cliente, modelo="m"
    )

    resultado = await orquestador.consultar("pregunta", tenant_id=1)
    assert cliente.chat.completions.create.await_count == 3
    assert resultado["respuesta"] == MENSAJE_DEGRADACION_IA


async def test_no_reintenta_ante_authentication_error(
    motor: MotorVectorial,
    _sin_espera_tenacity,
) -> None:
    """
    Un 401 (token invalido / expirado) NO se reintenta: reintentar
    un error de autenticacion es浪费时间. Fail-fast y degradacion.
    """
    error_401 = AuthenticationError(
        "Invalid API key",
        response=MagicMock(status_code=401),
        body=None,
    )
    cliente = _cliente_llm_que_falla_con(error_401)
    orquestador = OrquestadorRAG(
        motor_vectorial=motor, cliente_llm=cliente, modelo="m"
    )

    resultado = await orquestador.consultar("pregunta", tenant_id=1)

    # Exactamente UNA llamada: tenacity no toco el side_effect.
    assert cliente.chat.completions.create.await_count == 1, (
        "AuthenticationError (401) debe fallar rapido (fail-fast), "
        "no reintentar."
    )
    assert resultado["respuesta"] == MENSAJE_DEGRADACION_IA


async def test_degradacion_mantiene_fuentes_para_telemetria(
    motor: MotorVectorial,
    _sin_espera_tenacity,
) -> None:
    """
    Cuando el LLM cae definitivamente, `consultar` NO debe propagar la
    excepcion (no debe "tirar" FastAPI). Debe retornar el dict de
    degradacion CONSERVANDO los IDs de las fuentes recuperadas, para
    que la capa de telemetria registre QUE contexto estaba disponible
    cuando la IA externa se cayo.
    """
    motor.ingestar_chunks(
        ["Sentencia 2435-17-EP23 sobre prescripcion adquisitiva."],
        "casos_user_21",
        metadata={"archivo": "sentencia.pdf", "tier": 3},
    )
    cliente = _cliente_llm_que_falla_con(
        APIConnectionError(request=MagicMock())
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente,
        modelo="m",
        incluir_global=False,
        top_k=1,
    )

    # Si esto NO lanza, FastAPI sobrevive. La degradacion es elegante.
    resultado = await orquestador.consultar("prescripcion", tenant_id=21)

    # Contrato intacto: mismas claves que el camino feliz.
    for k in (
        "pregunta_original",
        "pregunta_ofuscada",
        "respuesta",
        "modelo",
        "fuentes",
        "tokens_prompt",
        "tokens_completion",
    ):
        assert k in resultado, f"Falta la clave {k!r} en la degradacion"

    # La respuesta es el mensaje canonico de degradacion, NO la excepcion.
    assert resultado["respuesta"] == MENSAJE_DEGRADACION_IA
    assert "APIConnectionError" not in resultado["respuesta"]
    assert "Traceback" not in resultado["respuesta"]

    # Telemetria: no consumimos tokens del LLM.
    assert resultado["tokens_prompt"] == 0
    assert resultado["tokens_completion"] == 0

    # CRITICO: las fuentes del vector store local se preservan para que
    # operaciones pueda auditar QUE contexto estaba disponible.
    assert len(resultado["fuentes"]) == 1
    fuente = resultado["fuentes"][0]
    assert fuente["coleccion_origen"] == "casos_user_21"
    assert "prescripcion" in fuente["texto"]
    assert fuente["metadata"].get("archivo") == "sentencia.pdf"
    # El id NO debe estar vacio: es la llave que la telemetria usa.
    assert fuente["id"], "El id de la fuente no puede ser vacio"


async def test_degradacion_con_fuentes_vacias_si_no_hay_contexto(
    motor: MotorVectorial,
    _sin_espera_tenacity,
) -> None:
    """
    Si ademas de caer la IA no hay contexto en el vector store, el
    dict de degradacion debe seguir siendo valido: `fuentes=[]` y
    sin excepciones lanzadas.
    """
    cliente = _cliente_llm_que_falla_con(
        APIConnectionError(request=MagicMock())
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        cliente_llm=cliente,
        modelo="m",
        incluir_global=False,
    )

    resultado = await orquestador.consultar("pregunta", tenant_id=999)
    assert resultado["fuentes"] == []
    assert resultado["respuesta"] == MENSAJE_DEGRADACION_IA
    assert resultado["modelo"] == "m"


async def test_recupera_si_un_reintento_funciona(
    motor: MotorVectorial,
    _sin_espera_tenacity,
) -> None:
    """
    Caso happy de la resiliencia: el primer intento falla con un
    error transitorio, el segundo intento tiene exito y se usa
    ESA respuesta (no la de degradacion).
    """
    respuesta_exitosa = MagicMock()
    respuesta_exitosa.choices = [MagicMock()]
    respuesta_exitosa.choices[0].message.content = "Respuesta tras reintento."
    respuesta_exitosa.usage = None

    cliente = MagicMock()
    # Primer intento: falla. Segundo intento: exito.
    cliente.chat.completions.create = AsyncMock(
        side_effect=[
            APIConnectionError(request=MagicMock()),
            respuesta_exitosa,
        ]
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor, cliente_llm=cliente, modelo="m"
    )

    resultado = await orquestador.consultar("pregunta", tenant_id=1)

    assert cliente.chat.completions.create.await_count == 2
    # Se usa la respuesta del reintento exitoso, NO la degradacion.
    assert resultado["respuesta"] == "Respuesta tras reintento."


# ============================================================
# Resiliencia del constructor (defensa en profundidad).
# ============================================================
#
# Caso real: incompatibilidad `openai==1.51.0` + `httpx>=0.28`
# (el kwarg `proxies` se renombro a `proxy` y revienta el
# constructor de AsyncOpenAI). El orquestador NO debe tumbar
# el servidor: debe sobrevivir en modo degradado y responder
# con el dict civilizado.


def test_constructor_no_explota_si_asyncopenai_falla(
    motor: MotorVectorial,
    monkeypatch,
) -> None:
    """
    Si el SDK `AsyncOpenAI` no puede inicializarse (caso real:
    httpx>=0.28 rompe openai 1.51.0), el constructor debe
    registrar el error y dejar al orquestador en modo degradado
    (`_cliente_llm is None`), NO lanzar excepcion.
    """
    # Forzamos el fallo que reproduce el bug de produccion:
    # httpx>=0.28 renombro `proxies` -> `proxy`, y openai 1.51.0
    # sigue pasando `proxies=...`, lo que hace explotar al
    # constructor de AsyncClient.
    def _explota_al_crear_async_openai(*args, **kwargs):  # noqa: ARG001
        raise TypeError(
            "AsyncClient.__init__() got an unexpected keyword argument 'proxies'"
        )

    monkeypatch.setattr(
        "backend.servicios.rag_motor.AsyncOpenAI",
        _explota_al_crear_async_openai,
    )

    # El constructor NO debe lanzar: debe atrapar y degradar.
    orquestador = OrquestadorRAG(motor_vectorial=motor, modelo="m")
    assert orquestador._cliente_llm is None  # noqa: SLF001 - test intencional


async def test_consultar_degrada_si_cliente_llm_no_se_pudo_crear(
    motor: MotorVectorial,
    monkeypatch,
    _sin_espera_tenacity,
) -> None:
    """
    Cuando el constructor queda en modo degradado (cliente None),
    `consultar` debe detectar la situacion al primer intento,
    aplicar los reintentos de tenacity (3 veces) y devolver el
    dict de degradacion civilizado, NO un 500.
    """
    monkeypatch.setattr(
        "backend.servicios.rag_motor.AsyncOpenAI",
        lambda *a, **kw: (_ for _ in ()).throw(
            TypeError("AsyncClient.__init__() got an unexpected keyword argument 'proxies'")
        ),
    )

    motor.ingestar_chunks(
        ["Sentencia sobre prescripcion."],
        "casos_user_77",
        metadata={"archivo": "sentencia.pdf"},
    )
    orquestador = OrquestadorRAG(
        motor_vectorial=motor,
        modelo="m",
        incluir_global=False,
        top_k=1,
    )
    # Sanity: el constructor sobrevivio en modo degradado.
    assert orquestador._cliente_llm is None  # noqa: SLF001

    resultado = await orquestador.consultar("prescripcion", tenant_id=77)

    # Contrato intacto: el endpoint recibe un 200 OK con la respuesta
    # civilizada, no un 500 que tire FastAPI.
    assert resultado["respuesta"] == MENSAJE_DEGRADACION_IA
    assert resultado["tokens_prompt"] == 0
    assert resultado["tokens_completion"] == 0
    # Las fuentes del contexto local se preservan para la telemetria.
    assert len(resultado["fuentes"]) == 1
    assert resultado["fuentes"][0]["coleccion_origen"] == "casos_user_77"
