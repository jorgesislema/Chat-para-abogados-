"""
Configuracion centralizada del backend.

Lee las variables desde el entorno o desde un archivo .env en la raiz del proyecto.
Todos los modulos deben importar `configuracion` en lugar de leer `os.environ` directamente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Carga .env solo si existe; en produccion se espera que las vars ya esten definidas.
_raiz_proyecto = Path(__file__).resolve().parents[2]
_env_path = _raiz_proyecto / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


@dataclass(frozen=True)
class Configuracion:
    """Snapshot inmutable de la configuracion de la aplicacion."""

    openrouter_api_key: str
    llm_modelo: str
    secret_key_jwt: str
    algoritmo_jwt: str
    expiracion_token_minutos: int
    cadena_bd: str
    ruta_chromadb: str
    modelo_embeddings: str


@lru_cache(maxsize=1)
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuracion cargada una sola vez (cacheada)."""

    return Configuracion(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        llm_modelo=os.getenv("LLM_MODELO", "meta-llama/llama-3-8b-instruct"),
        secret_key_jwt=os.getenv("SECRET_KEY_JWT", "cambiar-en-produccion"),
        algoritmo_jwt=os.getenv("ALGORITMO_JWT", "HS256"),
        expiracion_token_minutos=int(os.getenv("EXPIRACION_TOKEN_MINUTOS", "60")),
        # Default: backend/db/legalrag.db (relativa a la raiz del proyecto).
        cadena_bd=os.getenv("CADENA_BD", "sqlite:///./backend/db/legalrag.db"),
        ruta_chromadb=os.getenv("RUTA_CHROMADB", "./chromadb"),
        modelo_embeddings=os.getenv(
            "MODELO_EMBEDDINGS",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
    )
