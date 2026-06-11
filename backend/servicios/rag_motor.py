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

import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAI

from backend.core.configuracion import obtener_configuracion
from backend.db.vectorial import FragmentoRecuperado, MotorVectorial
from backend.servicios.ofuscacion import ofuscar_texto


# Plantilla canonica del prompt de sistema. Centralizada para que un cambio
# se propague al OrquestadorRAG y a los evals de forma consistente.
PROMPT_SISTEMA_POR_DEFECTO: str = (
    "Eres un asistente legal estricto. Usa solo el siguiente contexto para "
    "responder. Si no esta en el contexto, indica explicitamente que no "
    "tienes informacion suficiente. No inventes datos personales ni cites "
    "articulos que no aparezcan en el contexto."
)

_BASE_URL_OPENROUTER: str = "https://openrouter.ai/api/v1"


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
    Cliente ligero sobre la API de OpenRouter usando el SDK de OpenAI (sync).

    Inicializa un `OpenAI` cuya `base_url` apunta al endpoint de OpenRouter.
    La API key se resuelve en este orden:
      1. Argumento explicito al constructor (util para tests).
      2. Variable de entorno OPENROUTER_API_KEY.
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
            or os.getenv("OPENROUTER_API_KEY")
            or configuracion.openrouter_api_key
        )
        self._modelo = modelo or configuracion.llm_modelo
        self._cliente = OpenAI(
            base_url=base_url or _BASE_URL_OPENROUTER,
            api_key=clave,
        )

    @property
    def modelo(self) -> str:
        return self._modelo

    def generar(
        self,
        prompt: str,
        temperatura: float = 0.2,
        max_tokens: int = 512,
        mensajes_sistema: list[str] | None = None,
    ) -> str:
        """Envia el prompt al LLM sincrono y devuelve el texto de la respuesta."""
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
        temperatura: float = 0.2,
        max_tokens: int = 512,
        top_k: int = 5,
        incluir_global: bool = True,
    ) -> None:
        self._motor = motor_vectorial
        self._modelo = modelo or obtener_configuracion().llm_modelo
        self._prompt_sistema = prompt_sistema or PROMPT_SISTEMA_POR_DEFECTO
        self._temperatura = temperatura
        self._max_tokens = max_tokens
        self._top_k = top_k
        self._incluir_global = incluir_global

        if cliente_llm is not None:
            self._cliente_llm = cliente_llm
        else:
            configuracion = obtener_configuracion()
            clave = (
                os.getenv("OPENROUTER_API_KEY")
                or configuracion.openrouter_api_key
            )
            self._cliente_llm = AsyncOpenAI(
                base_url=_BASE_URL_OPENROUTER,
                api_key=clave,
            )

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
    ) -> dict[str, Any]:
        """
        Ejecuta la cadena RAG completa y devuelve un dict estructurado.

        Retorna:
            {
                "pregunta_original": str,
                "pregunta_ofuscada": str,
                "respuesta": str,
                "modelo": str,
                "fuentes": list[dict],   # metadata, texto, puntuacion, origen
            }
        """
        # Paso A: ofuscar ANTES de cualquier otra operacion.
        pregunta_ofuscada = ofuscar_texto(pregunta_cruda)

        # Paso B: buscar contexto en la coleccion del tenant + global.
        nombre_coleccion = self._resolver_nombre_coleccion(tenant_id)
        fragmentos = self._motor.buscar_contexto(
            pregunta_ofuscada,
            nombre_coleccion,
            incluir_global=self._incluir_global,
            top_k=self._top_k,
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

        # Paso E: llamada asincrona al LLM.
        respuesta_llm = await self._cliente_llm.chat.completions.create(
            model=self._modelo,
            messages=mensajes,  # type: ignore[arg-type]
            temperature=self._temperatura,
            max_tokens=self._max_tokens,
        )
        contenido: Any = respuesta_llm.choices[0].message.content
        texto_llm = contenido if isinstance(contenido, str) else str(contenido)

        return {
            "pregunta_original": pregunta_cruda,
            "pregunta_ofuscada": pregunta_ofuscada,
            "respuesta": texto_llm,
            "modelo": self._modelo,
            "fuentes": [
                {
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
