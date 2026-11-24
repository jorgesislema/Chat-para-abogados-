"""
Motor RAG (Retrieval-Augmented Generation) para LegalRAG_SaaS.

Arquitectura hibrida:
  - Embeddings y busqueda vectorial: 100% local (sentence-transformers + ChromaDB).
  - Razonamiento: externo via OpenRouter (compatible con la API de OpenAI).

La Regla de Oro del proyecto se aplica en `OrquestadorRAG.consultar()`:
la consulta que llega al LLM YA paso por `ofuscar_texto()`. Esta capa
nunca recibe texto crudo del usuario.

Modulos exportados:
  - `FragmentoRecuperado`: dataclass liviana que el orquestador recibe
    del motor vectorial. Tambien re-exportada desde `backend.db.vectorial`.
  - `GeneradorRespuestaOpenRouter`: cliente sincrono (usado por `evals/`).
  - `MotorRAG`: orquestador sincrono legacy (usado por algunos tests).
  - `OrquestadorRAG`: orquestador ASINCRONO usado por los endpoints de
    FastAPI. Aplica la cadena completa: ofusca -> busca contexto ->
    arma prompt -> llama AsyncOpenAI -> retorna dict estructurado.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from openai import (
    APIConnectionError,
    AsyncOpenAI,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.core.configuracion import obtener_configuracion
from backend.db.vectorial import FragmentoRecuperado, MotorVectorial
from backend.servicios.ofuscacion import ofuscar_texto
from backend.servicios.permisos import (
    MENSAJE_SIN_ACCESO,
    AlcancePermitido,
    PermisoRegla,
    resolver_alcance,
)

# Logger del modulo: el orquestador registra degradaciones para que
# operaciones pueda auditar caidas de OpenRouter sin tirar el servidor.
LOGGER = logging.getLogger(__name__)

# Mensaje canonico devuelto al cliente cuando la IA externa falla
# definitivamente (tras agotar reintentos o por error duro tipo 401).
MENSAJE_DEGRADACION_IA: str = (
    "Error: El servicio de IA externa no respondio tras multiples "
    "intentos o presento un fallo. Por favor, reintente en unos minutos."
)


# Plantilla canonica del prompt de sistema. Centralizada para que un cambio
# se propague al OrquestadorRAG y a los evals de forma consistente.
PROMPT_SISTEMA_POR_DEFECTO: str = (
    "Eres un asistente legal estricto. Usa solo el siguiente contexto para "
    "responder. Si no esta en el contexto, indica explicitamente que no "
    "tienes informacion suficiente. No inventes datos personales ni cites "
    "articulos que no aparezcan en el contexto."
)

@dataclass(frozen=True)
class _FragmentoLegacy(FragmentoRecuperado):
    """Alias historico: compatibilidad con el dataclass previo que tenia
    `expediente_id`. Las nuevas instancias usan el dataclass moderno de
    `backend.db.vectorial.FragmentoRecuperado` que incluye `metadata`,
    `puntuacion` y `coleccion_origen`."""


# Re-export para no romper imports previos.
__all__ = [
    "FragmentoRecuperado",
    "GeneradorRespuestaOpenRouter",
    "MotorRAG",
    "OrquestadorRAG",
    "PROMPT_SISTEMA_POR_DEFECTO",
]


# ============================================================
# Generador sincrono (legado, usado por evals/ y algunos tests).
# ============================================================

class GeneradorRespuestaOpenRouter:
    """
    Cliente ligero sobre un endpoint compatible con la API de OpenAI.

    Por defecto apunta al proveedor configurado (`LLM_PROVEEDOR`:
    openrouter | sensenova | hms | custom). Inicializa un `OpenAI`
    cuya `base_url` apunta al endpoint del proveedor.
    La API key se resuelve en este orden:
      1. Argumento explicito al constructor (util para tests).
      2. `LLM_API_KEY` / `SENSENOVA_API_KEY` / `HMS_API_KEY` /
         `OPENROUTER_API_KEY` (ver `backend.core.configuracion`).
      3. Valor cacheado en `backend.core.configuracion`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        modelo: str | None = None,
        base_url: str | None = None,
    ) -> None:
        configuracion = obtener_configuracion()
        clave = (
            api_key
            or os.getenv("LLM_API_KEY")
            or os.getenv("SENSENOVA_API_KEY")
            or os.getenv("HMS_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or configuracion.llm_api_key
        )
        self._modelo = modelo or configuracion.llm_modelo
        self._cliente = OpenAI(
            base_url=base_url or configuracion.llm_base_url,
            api_key=clave,
        )

    @property
    def modelo(self) -> str:
        return self._modelo

    def generar(
        self,
        prompt: str,
        temperatura: float = 0.2,
        max_tokens: int | None = None,
        mensajes_sistema: list[str] | None = None,
    ) -> str:
        """Envia el prompt al LLM sincrono y devuelve el texto de la respuesta."""
        if max_tokens is None:
            max_tokens = obtener_configuracion().llm_max_tokens
        mensajes: list[dict[str, str]] = []
        if mensajes_sistema:
            for msj in mensajes_sistema:
                mensajes.append({"role": "system", "content": msj})
        mensajes.append({"role": "user", "content": prompt})

        respuesta = self._cliente.chat.completions.create(
            model=self._modelo,
            messages=mensajes,  # type: ignore[arg-type]
            temperature=temperatura,
            max_tokens=max_tokens,
        )
        contenido: Any = respuesta.choices[0].message.content
        return contenido if isinstance(contenido, str) else str(contenido)


# ============================================================
# Orquestador RAG sincrono legacy.
# ============================================================

class MotorRAG:
    """
    Orquesta la cadena completa: recuperar contexto local y consultar al LLM
    sincrono. Mantenido por compatibilidad con tests previos; los endpoints
    en produccion deben usar `OrquestadorRAG`.
    """

    def __init__(
        self,
        generador: GeneradorRespuestaOpenRouter | None = None,
        prompt_sistema: str | None = None,
    ) -> None:
        self._generador = generador or GeneradorRespuestaOpenRouter()
        self._prompt_sistema = prompt_sistema or PROMPT_SISTEMA_POR_DEFECTO

    def construir_prompt(
        self,
        consulta_ofuscada: str,
        fragmentos: list[FragmentoRecuperado],
    ) -> str:
        """Une el contexto recuperado y la consulta en un unico prompt."""
        secciones = ["CONTEXTO RELEVANTE:"]
        for idx, frag in enumerate(fragmentos, start=1):
            secciones.append(f"[{idx}] {frag.texto}")
        secciones.append("")
        secciones.append("PREGUNTA DEL ABOGADO (ya ofuscada):")
        secciones.append(consulta_ofuscada)
        return "\n".join(secciones)

    def responder(
        self,
        consulta_ofuscada: str,
        fragmentos: list[FragmentoRecuperado],
    ) -> str:
        """Compone el prompt, llama al LLM y devuelve la respuesta final."""
        prompt_usuario = self.construir_prompt(consulta_ofuscada, fragmentos)
        return self._generador.generar(
            prompt=prompt_usuario,
            mensajes_sistema=[self._prompt_sistema],
        )


# ============================================================
# Orquestador RAG asincrono (USADO POR LOS ENDPOINTS).
# ============================================================

class OrquestadorRAG:
    """
    Cadena RAG completa: ofuscacion -> busqueda vectorial -> LLM async.

    Reglas arquitectonicas que cumple este orquestador:
      1. **Asincronismo**: usa `AsyncOpenAI` para no bloquear el event loop.
      2. **Inyeccion de dependencias**: el `MotorVectorial` se recibe
         por constructor. NO se instancia dentro (un cliente ChromaDB
         por proceso es suficiente y economico en RAM).
      3. **Gobernanza**: la pregunta cruda NUNCA sale al LLM ni se
         vectoriza. Se ofusca primero, y la version ofuscada se usa
         tanto para la busqueda vectorial como para el prompt al LLM.

    Parametros:
        motor_vectorial: instancia compartida de MotorVectorial.
        cliente_llm: cliente AsyncOpenAI pre-construido (util para tests).
            Si es None, se construye uno nuevo apuntando a OpenRouter.
        modelo: identificador del modelo (default: `LLM_MODELO` del .env).
        prompt_sistema: prompt de sistema a inyectar al LLM. Default seguro
            que prohibe inventar informacion.
        temperatura: temperatura de sampling del LLM.
        max_tokens: limite de tokens de la respuesta del LLM.
        top_k: numero de chunks a recuperar del vector store.
        incluir_global: si True, ademas de la coleccion del tenant se
            consulta la coleccion global de leyes publicas.
    """

    def __init__(
        self,
        motor_vectorial: MotorVectorial,
        cliente_llm: AsyncOpenAI | None = None,
        modelo: str | None = None,
        prompt_sistema: str | None = None,
        temperatura: float | None = None,
        max_tokens: int | None = None,
        top_k: int | None = None,
        incluir_global: bool = True,
    ) -> None:
        self._motor = motor_vectorial
        _cfg = obtener_configuracion()
        self._modelo = modelo or _cfg.llm_modelo
        self._prompt_sistema = prompt_sistema or PROMPT_SISTEMA_POR_DEFECTO
        self._temperatura = (
            _cfg.rag_temperatura_citas if temperatura is None else temperatura
        )
        self._max_tokens = (
            _cfg.llm_max_tokens if max_tokens is None else max_tokens
        )
        self._top_k = _cfg.rag_top_k if top_k is None else top_k
        self._incluir_global = incluir_global

        if cliente_llm is not None:
            self._cliente_llm = cliente_llm
        else:
            configuracion = obtener_configuracion()
            clave = configuracion.llm_api_key
            try:
                self._cliente_llm: AsyncOpenAI | None = AsyncOpenAI(
                    base_url=configuracion.llm_base_url,
                    api_key=clave,
                )
            except Exception as exc:  # noqa: BLE001 - degradacion controlada
                # Defensa en profundidad: si el SDK de OpenAI no puede
                # inicializarse (ej. incompatibilidad con la version de
                # httpx instalada: `proxies` vs `proxy`), NO dejamos
                # que el constructor explote y tumbe FastAPI. El
                # orquestador queda en modo "sin LLM": cualquier
                # llamada a `consultar` sera interceptada por el
                # `try/except` de `_llamar_api_con_reintentos` y
                # resultara en el dict de degradacion civilizado.
                LOGGER.error(
                    "OrquestadorRAG: no se pudo inicializar AsyncOpenAI. "
                    "El orquestador funcionara en modo degradado. error=%s",
                    exc,
                )
                self._cliente_llm = None

    @property
    def modelo(self) -> str:
        return self._modelo

    @property
    def motor_vectorial(self) -> MotorVectorial:
        return self._motor

    # --------------------------------------------------------
    # Cadena principal.
    # --------------------------------------------------------

    async def consultar(
        self,
        pregunta_cruda: str,
        tenant_id: str | int,
        categoria: str | None = None,
        rol: str = "abogado",
        usuario_id: str | int | None = None,
        grupo: str | None = None,
        reglas_permiso: list | None = None,
        colecciones_existentes: list[str] | None = None,
        umbral_restringido: float | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta la cadena RAG completa y devuelve un dict estructurado.

        Parametros:
            pregunta_cruda: pregunta del abogado (antes de ofuscar).
            tenant_id: ID del abogado; la busqueda se acota a su
                coleccion privada `casos_user_<id>`.
            categoria: filtro opcional por categoria documental
                ("Leyes", "Jurisprudencia", "Expediente"). Si es None
                o la cadena vacia, no se filtra.
            rol / usuario_id / grupo / reglas_permiso /
            colecciones_existentes: si `reglas_permiso` es None, flujo
                legacy (coleccion del tenant + global, sin filtro de
                permisos). Si se provee, flujo permiso-aware (Fase 2):
                la busqueda se limita a las colecciones permitidas y a
                los documentos (hashes) concedidos; si hay contenido
                relevante SOLO en zona prohibida, se responde con el
                mensaje canned de denegacion SIN llamar al LLM (nunca
                filtra ni una frase del documento ajeno).
            umbral_restringido: puntuacion minima [0,1] para considerar
                que existe contenido restringido (defecto por env
                RAG_UMBRAL_RESTRINGIDO, 0.35).

        Retorna:
            {
                "pregunta_original": str,
                "pregunta_ofuscada": str,
                "respuesta": str,
                "modelo": str,
                "fuentes": list[dict],   # metadata, texto, puntuacion, origen
                "tokens_prompt": int,
                "tokens_completion": int,
                "categoria_filtrada": str | None,
                "acceso_denegado": bool,
                "colecciones_consultadas": list[str],
            }

        Resiliencia:
            La llamada al LLM externo se hace a traves de
            `_llamar_api_con_reintentos`, decorada con `tenacity` para
            tolerar errores temporales (conexion, rate limit, 5xx).
            Si tras agotar los reintentos (o ante un error duro tipo
            400/401) la API sigue sin responder, este metodo NO
            propaga la excepcion: retorna un dict de "degradacion
            elegante" con un mensaje civilizado y `fuentes` intactas
            para que la telemetria registre el incidente sin tirar
            el servidor FastAPI.
        """
        # Paso A: ofuscar ANTES de cualquier otra operacion.
        pregunta_ofuscada = ofuscar_texto(pregunta_cruda)

        # Paso B: buscar contexto (legacy o permiso-aware).
        acceso_denegado = False
        colecciones_consultadas: list[str] = []
        if reglas_permiso is None:
            nombre_coleccion = self._resolver_nombre_coleccion(tenant_id)
            fragmentos = self._motor.buscar_contexto(
                pregunta_ofuscada,
                nombre_coleccion,
                incluir_global=self._incluir_global,
                top_k=self._top_k,
                filtro_categoria=categoria,
            )
            colecciones_consultadas = [nombre_coleccion]
        else:
            fragmentos, acceso_denegado, colecciones_consultadas = (
                self._buscar_con_permisos(
                    pregunta_ofuscada=pregunta_ofuscada,
                    rol=rol,
                    usuario_id=usuario_id if usuario_id is not None else tenant_id,
                    grupo=grupo,
                    reglas_permiso=reglas_permiso,
                    colecciones_existentes=colecciones_existentes or [],
                    categoria=categoria,
                    umbral_restringido=umbral_restringido,
                )
            )
            if acceso_denegado:
                return self._construir_dict_denegacion(
                    pregunta_cruda=pregunta_cruda,
                    pregunta_ofuscada=pregunta_ofuscada,
                    categoria=categoria,
                    colecciones_consultadas=colecciones_consultadas,
                )

        # Paso C: ensamblar el string de contexto legal.
        contexto_legal = self._ensamblar_contexto(fragmentos)

        # Paso D: armar los mensajes para OpenRouter.
        system_prompt = (
            f"{self._prompt_sistema}\n\n"
            f"CONTEXTO:\n{contexto_legal}"
        )
        mensajes: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pregunta_ofuscada},
        ]

        # Paso E: llamada al LLM con reintentos + degradacion elegante.
        try:
            respuesta_llm = await self._llamar_api_con_reintentos(mensajes)
        except Exception as exc:  # noqa: BLE001 - degradacion controlada
            LOGGER.warning(
                "OrquestadorRAG: LLM externo no disponible tras reintentos. "
                "tenant_id=%s error=%s",
                tenant_id,
                exc,
            )
            return self._construir_dict_degradacion(
                pregunta_cruda=pregunta_cruda,
                pregunta_ofuscada=pregunta_ofuscada,
                fragmentos=fragmentos,
                categoria=categoria,
            )

        contenido: Any = respuesta_llm.choices[0].message.content
        texto_llm = contenido if isinstance(contenido, str) else str(contenido)

        # Extraccion defensiva del uso de tokens: el SDK de OpenAI los
        # expone en `respuesta.usage.{prompt,completion}_tokens`. Si el
        # proveedor no los devuelve (o estamos en un mock sin esa
        # propiedad), registramos 0.
        tokens_prompt = 0
        tokens_completion = 0
        usage = getattr(respuesta_llm, "usage", None)
        if usage is not None:
            tokens_prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens_completion = int(getattr(usage, "completion_tokens", 0) or 0)

        return {
            "pregunta_original": pregunta_cruda,
            "pregunta_ofuscada": pregunta_ofuscada,
            "respuesta": texto_llm,
            "modelo": self._modelo,
            "tokens_prompt": tokens_prompt,
            "tokens_completion": tokens_completion,
            "categoria_filtrada": categoria,
            "acceso_denegado": acceso_denegado,
            "colecciones_consultadas": colecciones_consultadas,
            "fuentes": [
                {
                    "id": f.id,
                    "texto": f.texto,
                    "metadata": f.metadata,
                    "puntuacion": f.puntuacion,
                    "coleccion_origen": f.coleccion_origen,
                }
                for f in fragmentos
            ],
        }

    # --------------------------------------------------------
    # Permisos (Fase 2): busqueda acotada + denegacion sin filtrar.
    # --------------------------------------------------------

    def _buscar_con_permisos(
        self,
        pregunta_ofuscada: str,
        rol: str,
        usuario_id: str | int,
        grupo: str | None,
        reglas_permiso: list[PermisoRegla],
        colecciones_existentes: list[str],
        categoria: str | None,
        umbral_restringido: float | None,
    ) -> tuple[list[FragmentoRecuperado], bool, list[str]]:
        """
        Busca solo en el alcance permitido. Retorna
        (fragmentos, acceso_denegado, colecciones_consultadas).

        - Los fragmentos provienen UNICAMENTE de colecciones
          permitidas o de documentos (hashes) concedidos via `where`
          nativo. El texto prohibido nunca llega al LLM.
        - Si no hay fragmentos pero hay contenido relevante en zona
          prohibida (>= umbral), acceso_denegado=True y el caller
          responde con el mensaje canned SIN llamar al LLM.
        """
        try:
            uid = int(usuario_id)
        except (TypeError, ValueError):
            raise ValueError(
                f"usuario_id invalido: {usuario_id!r}. Debe ser convertible a int."
            )
        alcance: AlcancePermitido = resolver_alcance(
            rol=rol,
            usuario_id=uid,
            grupo=grupo,
            reglas=reglas_permiso,
            colecciones_existentes=colecciones_existentes,
        )
        permitidas = sorted(alcance.colecciones)
        prohibidas = [c for c in colecciones_existentes if c not in alcance.colecciones]

        try:
            umbral_relevancia = float(os.getenv("RAG_UMBRAL_RELEVANCIA", "0.55"))
        except (TypeError, ValueError):
            umbral_relevancia = 0.55

        fragmentos = self._motor.buscar_en_colecciones(
            pregunta_ofuscada,
            permitidas,
            top_k=self._top_k,
            filtro_categoria=categoria,
        )

        # Documentos concedidos por hash dentro de colecciones ajenas:
        # filtro `where` nativo (el servidor solo devuelve esos chunks).
        if alcance.hashes_documentos and prohibidas:
            where_hash = {"hash_archivo": {"$in": sorted(alcance.hashes_documentos)}}
            fragmentos.extend(
                self._motor.buscar_en_colecciones(
                    pregunta_ofuscada,
                    prohibidas,
                    top_k=self._top_k,
                    filtro_categoria=categoria,
                    where_por_coleccion={c: where_hash for c in prohibidas},
                )
            )
            fragmentos.sort(key=lambda f: f.puntuacion, reverse=True)
            fragmentos = fragmentos[: self._top_k]

        mejor_permitido = max((f.puntuacion for f in fragmentos), default=0.0)
        relevantes = [f for f in fragmentos if f.puntuacion >= umbral_relevancia]
        if relevantes:
            return relevantes, False, permitidas

        # Sin fragmentos relevantes: ¿hay contenido relevante SOLO en
        # zona prohibida? Debe superar el umbral absoluto Y estar
        # claramente por encima del mejor permitido (margen), para no
        # denegar cuando todo es irrelevante.
        umbral = umbral_restringido
        if umbral is None:
            try:
                umbral = float(os.getenv("RAG_UMBRAL_RESTRINGIDO", "0.35"))
            except (TypeError, ValueError):
                umbral = 0.35
        try:
            margen = float(os.getenv("RAG_MARGEN_RESTRINGIDO", "0.15"))
        except (TypeError, ValueError):
            margen = 0.15
        mejor = self._motor.mejor_puntuacion_en(
            pregunta_ofuscada, prohibidas, filtro_categoria=categoria
        )
        if mejor >= umbral and mejor > mejor_permitido + margen:
            return [], True, permitidas
        return [], False, permitidas

    def _construir_dict_denegacion(
        self,
        pregunta_cruda: str,
        pregunta_ofuscada: str,
        categoria: str | None,
        colecciones_consultadas: list[str],
    ) -> dict[str, Any]:
        """Respuesta canned de denegacion: mismo contrato, sin LLM."""
        return {
            "pregunta_original": pregunta_cruda,
            "pregunta_ofuscada": pregunta_ofuscada,
            "respuesta": MENSAJE_SIN_ACCESO,
            "modelo": self._modelo,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "categoria_filtrada": categoria,
            "acceso_denegado": True,
            "colecciones_consultadas": colecciones_consultadas,
            "fuentes": [],
        }

    # --------------------------------------------------------
    # Resiliencia: llamada al LLM con reintentos exponenciales.
    # --------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (APIConnectionError, RateLimitError, InternalServerError)
        ),
    )
    async def _llamar_api_con_reintentos(
        self,
        mensajes: list[dict[str, str]],
    ) -> Any:
        """
        Invoca `chat.completions.create` con reintentos exponenciales.

        Solo se reintenta ante errores temporales del proveedor:
          - `APIConnectionError` (red, DNS, timeout de conexion).
          - `RateLimitError` (429).
          - `InternalServerError` (5xx del lado del proveedor).

        Errores 4xx duros como `AuthenticationError` (401) o
        `BadRequestError` (400) NO estan en la tupla, por lo que
        `tenacity` los deja pasar al instante (fail-fast): no tiene
        sentido reintentar un token invalido o un prompt mal formado.

        Tras 3 intentos fallidos, la excepcion original se re-lanza
        y es capturada por `consultar` para aplicar la degradacion
        elegante.

        Si el cliente LLM quedo en `None` (constructor degradado por
        incompatibilidad del SDK), lanza `APIConnectionError` para
        que la cadena de reintentos se comporte igual que ante una
        caida de red.
        """
        if self._cliente_llm is None:
            raise APIConnectionError(request=MagicMock())
        return await self._cliente_llm.chat.completions.create(
            model=self._modelo,
            messages=mensajes,  # type: ignore[arg-type]
            temperature=self._temperatura,
            max_tokens=self._max_tokens,
        )

    def _construir_dict_degradacion(
        self,
        pregunta_cruda: str,
        pregunta_ofuscada: str,
        fragmentos: list[FragmentoRecuperado],
        categoria: str | None = None,
    ) -> dict[str, Any]:
        """
        Construye el dict de respuesta cuando la IA externa falla.

        Mantiene el mismo contrato que el camino feliz (claves y tipos)
        para que la capa HTTP/telemetria no se rompa, y conserva los
        IDs de las `fuentes` para que la auditoria registre QUE
        contexto estaba disponible cuando la IA se cayo.
        """
        return {
            "pregunta_original": pregunta_cruda,
            "pregunta_ofuscada": pregunta_ofuscada,
            "respuesta": MENSAJE_DEGRADACION_IA,
            "modelo": self._modelo,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "categoria_filtrada": categoria,
            "fuentes": [
                {
                    "id": f.id,
                    "texto": f.texto,
                    "metadata": f.metadata,
                    "puntuacion": f.puntuacion,
                    "coleccion_origen": f.coleccion_origen,
                }
                for f in fragmentos
            ],
        }

    # --------------------------------------------------------
    # Helpers internos.
    # --------------------------------------------------------

    @staticmethod
    def _resolver_nombre_coleccion(tenant_id: str | int) -> str:
        """
        Acepta `str` o `int` por flexibilidad (el JWT da `int`, el spec
        del orquestador dice `str`). Internamente se usa el `int` para
        nombrar la coleccion (`casos_user_<id>`).
        """
        # Importacion local para evitar ciclo en el modulo de db.
        from backend.db.vectorial import nombre_coleccion_para_tenant

        try:
            tenant_int = int(tenant_id)
        except (TypeError, ValueError):
            raise ValueError(
                f"tenant_id invalido: {tenant_id!r}. Debe ser convertible a int."
            )
        return nombre_coleccion_para_tenant(tenant_int)

    @staticmethod
    def _ensamblar_contexto(fragmentos: list[FragmentoRecuperado]) -> str:
        """Une los chunks recuperados en un unico string con marcas de origen."""
        if not fragmentos:
            return "(sin contexto recuperado para esta pregunta)"
        bloques: list[str] = []
        for idx, frag in enumerate(fragmentos, start=1):
            bloques.append(
                f"[Fragmento {idx} | origen={frag.coleccion_origen} | "
                f"puntuacion={frag.puntuacion:.3f}]\n{frag.texto}"
            )
        return "\n\n".join(bloques)
