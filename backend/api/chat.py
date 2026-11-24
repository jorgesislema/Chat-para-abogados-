"""
Endpoints de chat (unificados).

  - POST /api/chat        : cadena RAG completa para el frontend
                            (ofusca + ChromaDB + OpenRouter + auditoria).
  - GET  /api/chat/salud  : healthcheck autenticado (devuelve claims).
  - POST /chat/preguntar   : cadena RAG con contrato compacto
                            (sin `pregunta_original` ni `fuentes`).

Historial: antes existian `chat.py` (POST /chat/preguntar) y
`rutas_chat.py` (POST /api/chat). Se unificaron aqui (Fase 1A).
Los paths y contratos HTTP no cambian.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from backend.api.dependencias import (
    UsuarioActual,
    obtener_orquestador_rag,
    obtener_usuario_actual,
)
from backend.api.esquemas import PreguntaIn, RespuestaChatOut
from backend.servicios.rag_motor import OrquestadorRAG
from backend.servicios.telemetria import (
    formatear_fuentes_para_auditoria,
    guardar_auditoria,
)

router = APIRouter(tags=["chat"])


# ============================================================
# Esquemas Pydantic del endpoint /api/chat.
# ============================================================

class PreguntaSimpleIn(BaseModel):
    """Payload minimo que envia el frontend Streamlit."""

    pregunta: str = Field(min_length=3, max_length=2000)
    # Filtro opcional por categoria documental. Si es None o vacio,
    # el RAG busca en todas las categorias del tenant + la global.
    categoria: str | None = Field(default=None, max_length=64)


class FuenteOut(BaseModel):
    """Un chunk usado como contexto por el LLM."""

    id: str = ""
    texto: str
    metadata: dict
    puntuacion: float
    coleccion_origen: str


class RespuestaSimpleOut(BaseModel):
    """Respuesta final con la version ofuscada y las fuentes."""

    pregunta_original: str
    pregunta_ofuscada: str
    respuesta: str
    modelo: str
    tenant_id: int
    usuario_id: int
    fuentes: list[FuenteOut] = Field(default_factory=list)
    # Metricas de telemetria (Fase 6).
    tokens_prompt: int = 0
    tokens_completion: int = 0
    # Filtro de categoria aplicado (None = "Todas").
    categoria_filtrada: str | None = None


# ============================================================
# Endpoints.
# ============================================================

@router.post("/api/chat", response_model=RespuestaSimpleOut)
async def chat_frontend(
    payload: PreguntaSimpleIn,
    background_tasks: BackgroundTasks,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
) -> RespuestaSimpleOut:
    """
    Ejecuta la cadena RAG completa para una pregunta del abogado
    y encola la auditoria asincrona en BackgroundTasks.

    El `tenant_id` (claims `tenant_id` del JWT, ID de la firma) se usa
    tanto para acotar la busqueda en ChromaDB como para etiquetar la
    auditoria. NOTA: para ChromaDB el orquestador recibe el
    `usuario_id` (claims `sub`, ID del abogado especifico), porque
    cada abogado tiene su coleccion privada `casos_user_<id>`.
    """
    abogado_id = str(usuario_actual["usuario_id"])
    tenant_firma = str(usuario_actual["tenant_id"])

    resultado = await orquestador.consultar(
        pregunta_cruda=payload.pregunta,
        tenant_id=abogado_id,
        categoria=payload.categoria,
    )

    background_tasks.add_task(
        guardar_auditoria,
        tenant_id=tenant_firma,
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta_llm=resultado["respuesta"],
        fuentes_usadas=formatear_fuentes_para_auditoria(resultado["fuentes"]),
        tokens_prompt=resultado.get("tokens_prompt", 0),
        tokens_completion=resultado.get("tokens_completion", 0),
    )

    return RespuestaSimpleOut(
        pregunta_original=resultado["pregunta_original"],
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta=resultado["respuesta"],
        modelo=resultado["modelo"],
        tenant_id=usuario_actual["tenant_id"],
        usuario_id=usuario_actual["usuario_id"],
        fuentes=[FuenteOut(**f) for f in resultado["fuentes"]],
        tokens_prompt=resultado.get("tokens_prompt", 0),
        tokens_completion=resultado.get("tokens_completion", 0),
        categoria_filtrada=resultado.get("categoria_filtrada"),
    )


@router.get("/api/chat/salud", tags=["meta"])
def salud_chat_frontend(
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> dict:
    """
    Healthcheck autenticado del modulo de chat.

    Devuelve los claims del usuario (incluido `rol`) para que el
    frontend pueda renderizar la UI segun RBAC sin necesidad de
    decodificar el JWT en cliente.
    """
    return {
        "estado": "ok",
        "tenant_id": usuario_actual["tenant_id"],
        "usuario_id": usuario_actual["usuario_id"],
        "rol": usuario_actual.get("rol", "abogado"),
        "email": usuario_actual.get("email", ""),
    }


@router.post("/chat/preguntar", response_model=RespuestaChatOut)
async def preguntar(
    payload: PreguntaIn,
    background_tasks: BackgroundTasks,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
) -> RespuestaChatOut:
    """
    Punto de entrada del chat juridico (cadena RAG completa + auditoria).

    Mismo orquestador que `/api/chat`: ofusca, busca contexto en el
    motor vectorial, llama al LLM async y registra la auditoria en
    background. Contrato compacto para clientes que ya tienen
    frontend propio.
    """
    abogado_id = str(usuario_actual["usuario_id"])
    tenant_firma = str(usuario_actual["tenant_id"])

    resultado = await orquestador.consultar(
        pregunta_cruda=payload.pregunta,
        tenant_id=abogado_id,
    )

    background_tasks.add_task(
        guardar_auditoria,
        tenant_id=tenant_firma,
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta_llm=resultado["respuesta"],
        fuentes_usadas=formatear_fuentes_para_auditoria(resultado["fuentes"]),
        tokens_prompt=resultado.get("tokens_prompt", 0),
        tokens_completion=resultado.get("tokens_completion", 0),
    )

    return RespuestaChatOut(
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta=resultado["respuesta"],
    )
