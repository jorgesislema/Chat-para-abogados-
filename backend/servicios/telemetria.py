"""
Telemetria y auditoria asincrona del RAG (Fase 6).

Funciones de este modulo:
  - guardar_auditoria: persiste un RegistroAuditoria en SQLite.
    Pensada para ejecutarse via `BackgroundTasks` de FastAPI: se
    llama DESPUES de que la respuesta se ha enviado al cliente,
    por lo que NO afecta a la latencia percibida.

Reglas de gobernanza que cumple este modulo:
  - Solo se persiste la pregunta OFUSCADA. La cruda NUNCA entra
    en la BD de auditoria.
  - El `tenant_id` se recibe como String para que el registro
    sobreviva a un borrado del tenant (trazabilidad legal).
  - La funcion abre su PROPIA sesion de SQLAlchemy: la sesion
    de la request HTTP ya esta cerrada cuando corre el background
    task.
"""

from __future__ import annotations

from typing import Any

from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import RegistroAuditoria


def guardar_auditoria(
    tenant_id: str,
    pregunta_ofuscada: str,
    respuesta_llm: str,
    fuentes_usadas: str,
    tokens_prompt: int,
    tokens_completion: int,
) -> int:
    """
    Inserta un RegistroAuditoria en SQLite.

    Parametros:
        tenant_id: identificador del tenant como string.
        pregunta_ofuscada: pregunta YA OFUSCA (Regla de Oro).
        respuesta_llm: texto devuelto por el LLM.
        fuentes_usadas: IDs de ChromaDB separados por comas.
        tokens_prompt: tokens consumidos en el prompt.
        tokens_completion: tokens generados por el LLM.

    Retorna:
        El id autoincrement del registro insertado.
    """
    bd = obtener_sesion_bd()
    try:
        registro = RegistroAuditoria(
            tenant_id=tenant_id,
            pregunta_ofuscada=pregunta_ofuscada,
            respuesta_llm=respuesta_llm,
            fuentes_usadas=fuentes_usadas,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
        )
        bd.add(registro)
        bd.commit()
        bd.refresh(registro)
        return int(registro.id)
    finally:
        bd.close()


def formatear_fuentes_para_auditoria(fuentes: list[dict[str, Any]]) -> str:
    """
    Convierte la lista de fuentes del orquestador en un string
    `id1,id2,id3` para almacenarlo en `RegistroAuditoria.fuentes_usadas`.

    Si una fuente no tiene `id` (ej. mocks), se omite silenciosamente.
    """
    ids = [str(f.get("id", "")).strip() for f in fuentes]
    return ",".join(i for i in ids if i)


__all__ = ["formatear_fuentes_para_auditoria", "guardar_auditoria"]
