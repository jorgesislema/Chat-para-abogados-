"""
Endpoint de chat RAG completo: `POST /chat/preguntar`.

Equivalente funcional a `POST /api/chat` pero con un contrato de
esquema mas compacto (sin `pregunta_original` ni `fuentes` explicitas).
Pensado para clientes que ya tienen un frontend propio y solo necesitan
la respuesta.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.dependencias import (
    UsuarioActual,
    obtener_orquestador_rag,
    obtener_usuario_actual,
)
from backend.api.esquemas import PreguntaIn, RespuestaChatOut
from backend.servicios.rag_motor import OrquestadorRAG

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/preguntar", response_model=RespuestaChatOut)
async def preguntar(
    payload: PreguntaIn,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
) -> RespuestaChatOut:
    """
    Punto de entrada del chat juridico (cadena RAG completa).

    Mismo orquestador que `/api/chat`: ofusca, busca contexto en el
    motor vectorial, llama al LLM async y devuelve la respuesta.
    """
    tenant_id = str(usuario_actual["usuario_id"])
    resultado = await orquestador.consultar(
        pregunta_cruda=payload.pregunta,
        tenant_id=tenant_id,
    )
    return RespuestaChatOut(
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta=resultado["respuesta"],
    )
