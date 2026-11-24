"""
Endpoints del recopilador de logs.

  - POST /api/logs/cliente : el navegador reporta un error (cualquier usuario).
  - GET  /api/admin/logs   : el administrador consulta los eventos.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.api.dependencias import UsuarioActual, obtener_usuario_actual, verificar_admin
from backend.core.registro_logs import leer_eventos, registrar_evento

router = APIRouter(tags=["logs"])


class ErrorCliente(BaseModel):
    mensaje: str = Field(max_length=500)
    origen: str = Field(default="", max_length=200)
    ruta_api: str = Field(default="", max_length=200)
    estado: int | None = None
    id_llamada: str = Field(default="", max_length=40)
    pila: str = Field(default="", max_length=2000)


@router.post("/api/logs/cliente", status_code=204, response_model=None)
def reportar_error_cliente(
    datos: ErrorCliente,
    usuario: UsuarioActual = Depends(obtener_usuario_actual),
) -> None:
    """Guarda un error ocurrido en el navegador (sin texto de preguntas)."""
    registrar_evento("ERROR", {
        "tipo": "error_cliente",
        "usuario_id": usuario["usuario_id"],
        "rol": usuario["rol"],
        **datos.model_dump(),
    })


@router.get("/api/admin/logs", dependencies=[Depends(verificar_admin)])
def listar_logs(
    limite: int = Query(100, ge=1, le=500),
    solo_errores: bool = False,
    q: str = Query("", max_length=100),
) -> dict:
    """Eventos recientes, los mas nuevos primero."""
    items = leer_eventos(limite=limite, solo_errores=solo_errores, texto=q)
    return {"total": len(items), "items": items}
