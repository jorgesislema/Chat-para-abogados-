"""
Punto de entrada de la aplicacion FastAPI.

Crea la instancia de la app, monta los routers, configura CORS para el
frontend Streamlit y registra el evento de inicio que crea las tablas
de la base de datos relacional.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import admin, auth, chat, rutas_chat
from backend.db.base import Base
from backend.db.sesion import engine


@asynccontextmanager
async def lifespan(aplicacion: FastAPI):
    """Crea las tablas al arrancar y libera recursos al cerrar."""
    Base.metadata.create_all(bind=engine)
    yield
    # Limpieza: SQLAlchemy cierra el engine automaticamente al finalizar el proceso.


aplicacion = FastAPI(
    title="LegalRAG_SaaS API",
    version="0.1.0",
    description="Backend multi-tenant para firmas de abogados con RAG local y OpenRouter.",
    lifespan=lifespan,
)

# CORS abierto solo en desarrollo; en produccion, restringir al dominio del frontend.
aplicacion.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

aplicacion.include_router(auth.router)
aplicacion.include_router(admin.router)
aplicacion.include_router(chat.router)
aplicacion.include_router(rutas_chat.router)


@aplicacion.get("/salud", tags=["meta"])
def salud() -> dict:
    """Endpoint de healthcheck basico para monitoreo."""
    return {"estado": "ok", "servicio": "LegalRAG_SaaS"}
