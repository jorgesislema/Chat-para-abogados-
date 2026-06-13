"""
Conexion a la base de datos relacional (SQLite por defecto).

Aqui vive el engine de SQLAlchemy y la fabrica de sesiones. Cualquier
modulo que necesite hablar con SQLite debe importar de este archivo.

Ruta por defecto: `backend/db/legalrag.db` (relativa a la raiz del
proyecto). Se sobreescribe con la variable de entorno `CADENA_BD`.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.configuracion import obtener_configuracion

# Ruta canonica del archivo SQLite (para poder crear el directorio).
_RUTA_BD_POR_DEFECTO: Path = (
    Path(__file__).resolve().parent / "legalrag.db"
)


def _asegurar_directorio_sqlite(cadena_bd: str) -> None:
    """Crea el directorio del archivo SQLite si no existe."""
    if not cadena_bd.startswith("sqlite:///"):
        return
    ruta = Path(cadena_bd.replace("sqlite:///", "", 1))
    if ruta.parent and not ruta.parent.exists():
        ruta.parent.mkdir(parents=True, exist_ok=True)


def crear_engine(cadena_bd: str | None = None) -> Engine:
    """Construye un engine de SQLAlchemy para la cadena dada (o la del .env)."""
    cadena = cadena_bd or obtener_configuracion().cadena_bd
    _asegurar_directorio_sqlite(cadena)
    return create_engine(
        cadena,
        connect_args={"check_same_thread": False} if cadena.startswith("sqlite") else {},
        future=True,
    )


# Engine y SessionLocal a nivel de modulo. Un solo engine por proceso
# es la forma recomendada por SQLAlchemy (pool de conexiones reusable).
engine: Engine = crear_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, future=True
)


def obtener_sesion_bd() -> Session:
    """Devuelve una nueva sesion. El caller es responsable de cerrarla."""
    return SessionLocal()


__all__ = ["SessionLocal", "crear_engine", "engine", "obtener_sesion_bd"]
