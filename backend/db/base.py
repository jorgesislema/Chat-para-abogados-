"""
Base declarativa de SQLAlchemy. Todos los modelos la heredan.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Clase base para todos los modelos ORM del proyecto."""

    pass
