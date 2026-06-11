"""
Endpoints de chat para el frontend Streamlit.

Este modulo expone el endpoint `POST /api/chat` que ejecuta la cadena
RAG COMPLETA:

  1) Recibe la pregunta del abogado (autenticado por JWT).
  2) Ofusca la PII (Regla de Oro).
  3) Recupera contexto del MotorVectorial (coleccion del tenant + global).
  4) Llama al LLM externo via AsyncOpenAI (OpenRouter) de forma asincrona.
  5) Devuelve la respuesta junto con las fuentes consultadas.

Adicionalmente expone `GET /api/chat/salud` como healthcheck autenticado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.dependencias import (
    UsuarioActual,
    obtener_orquestador_rag,
    obtener_usuario_actual,
)
from backend.servicios.rag_motor import OrquestadorRAG

router = APIRouter(prefix="/api", tags=["chat-frontend"])


# ============================================================
# Esquemas Pydantic.
# ============================================================

class PreguntaSimpleIn(BaseModel):
    """Payload minimo que envia el frontend Streamlit."""

    pregunta: str = Field(min_length=3, max_length=2000)


class FuenteOut(BaseModel):
    """Un chunk usado como contexto por el LLM."""

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


# ============================================================
# Endpoints.
# ============================================================

@router.post("/chat", response_model=RespuestaSimpleOut)
async def chat_frontend(
    payload: PreguntaSimpleIn,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
) -> RespuestaSimpleOut:
    """
    Ejecuta la cadena RAG completa para una pregunta del abogado.

    El `tenant_id` se extrae del JWT (claims `tenant_id` y `sub`,
    que en este sistema es el ID del abogado). Se usa para acotar
    la busqueda a la coleccion privada del usuario.
    """
    # El orquestador recibe el ID del abogado (usuario_id) como tenant_id:
    # la coleccion se nombra `casos_user_<usuario_id>`.
    tenant_id = str(usuario_actual["usuario_id"])

    resultado = await orquestador.consultar(
        pregunta_cruda=payload.pregunta,
        tenant_id=tenant_id,
    )

    return RespuestaSimpleOut(
        pregunta_original=resultado["pregunta_original"],
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta=resultado["respuesta"],
        modelo=resultado["modelo"],
        tenant_id=usuario_actual["tenant_id"],
        usuario_id=usuario_actual["usuario_id"],
        fuentes=[FuenteOut(**f) for f in resultado["fuentes"]],
    )


@router.get("/chat/salud", tags=["meta"])
def salud_chat_frontend(usuario_actual: UsuarioActual = Depends(obtener_usuario_actual)) -> dict:
    """
    Healthcheck autenticado del modulo de chat.

    Util para que el frontend confirme que su JWT es valido antes
    de permitir al usuario enviar preguntas.
    """
    return {
        "estado": "ok",
        "tenant_id": usuario_actual["tenant_id"],
        "usuario_id": usuario_actual["usuario_id"],
    }
