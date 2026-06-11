"""
Configuracion del engine y la sesion de SQLAlchemy.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.configuracion import obtener_configuracion

_config = obtener_configuracion()
engine = create_engine(
    _config.cadena_bd,
    connect_args={"check_same_thread": False} if _config.cadena_bd.startswith("sqlite") else {},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def obtener_sesion_bd() -> Session:
    """Devuelve una nueva sesion. El caller es responsable de cerrarla."""
    return SessionLocal()
