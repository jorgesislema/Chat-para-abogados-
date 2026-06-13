"""
Endpoint de chat RAG completo: `POST /chat/preguntar`.

Equivalente funcional a `POST /api/chat` pero con un contrato de
esquema mas compacto (sin `pregunta_original` ni `fuentes` explicitas).
Pensado para clientes que ya tienen un frontend propio y solo necesitan
la respuesta.

Incluye la escritura asincrona de auditoria via BackgroundTasks
(Fase 6: Telemetria).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

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

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/preguntar", response_model=RespuestaChatOut)
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
    background.
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
