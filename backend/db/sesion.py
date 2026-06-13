"""
Modulo de compatibilidad. La conexion real vive en `backend.db.conexion`.

Este archivo se mantiene para no romper imports previos
(`from backend.db.sesion import ...`). Las nuevas llamadas deben
importar directamente desde `backend.db.conexion`.
"""

from __future__ import annotations

from backend.db.conexion import (  # noqa: F401 - re-exports
    SessionLocal,
    crear_engine,
    engine,
    obtener_sesion_bd,
)

__all__ = ["SessionLocal", "crear_engine", "engine", "obtener_sesion_bd"]
