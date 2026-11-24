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

from backend.api import admin, auth, chat
from backend.db.base import Base
from backend.db.conexion import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Crea las tablas al arrancar y libera recursos al cerrar."""
    Base.metadata.create_all(bind=engine)
    yield
    # Limpieza: SQLAlchemy cierra el engine automaticamente al finalizar el proceso.

# CORS restrictivo: solo permitimos el frontend de Streamlit (puerto 8501).
# Wildcard ["*"] esta PROHIBIDO en produccion: rompe la proteccion
# del navegador contra cross-site request forgery.
app = FastAPI(
    title="LegalRAG_SaaS API",
    version="0.1.0",
    description="Backend multi-tenant para firmas de abogados con RAG local y OpenRouter.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(chat.router)


# Alias en español para compatibilidad con tests y docs (`aplicacion`).
# El objeto canónico es `app` (convención uvicorn `backend.main:app`).
aplicacion = app


@app.get("/salud", tags=["meta"])
def salud() -> dict[str, str]:
    """Endpoint de healthcheck basico para monitoreo."""
    return {"estado": "ok", "servicio": "LegalRAG_SaaS"}