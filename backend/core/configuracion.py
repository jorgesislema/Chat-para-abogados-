"""
Configuracion centralizada del backend.

Lee las variables desde el entorno o desde un archivo .env en la raiz del proyecto.
Todos los modulos deben importar `configuracion` en lugar de leer `os.environ` directamente.

SEGURIDAD (Fase 1B): `SECRET_KEY_JWT` es obligatoria. Si falta, esta en
blanco o conserva el valor por defecto "cambiar-en-produccion", la app
NO arranca: `obtener_configuracion()` lanza RuntimeError. En tests se
permite clave debil solo con `LEGALRAG_TESTING=1` (ver conftest.py).
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

VALOR_DEFECTO_INSEGURO = "cambiar-en-produccion"
LONGITUD_MINIMA_JWT = 32


def _validar_secret_jwt(valor: str) -> str:
    """Fail-fast: sin secreto valido no se arranca."""
    if os.getenv("LEGALRAG_TESTING") == "1":
        # En tests se permite cualquier valor no vacio (conftest inyecta uno fake).
        return valor or "test-secret-key-for-pytest-only"
    if not valor or valor.strip() == "" or valor == VALOR_DEFECTO_INSEGURO:
        raise RuntimeError(
            "SECRET_KEY_JWT no configurada. Define una clave aleatoria de "
            f"al menos {LONGITUD_MINIMA_JWT} caracteres en .env "
            "(ver .env.ejemplo). La app no arranca sin ella."
        )
    if len(valor) < LONGITUD_MINIMA_JWT:
        raise RuntimeError(
            f"SECRET_KEY_JWT demasiado corta ({len(valor)} caracteres). "
            f"Minimo {LONGITUD_MINIMA_JWT}. Genera una con: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return valor


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
    # RAG (Fase G2): limites configurables sin tocar codigo.
    llm_max_tokens: int
    llm_max_tokens_texto_completo: int
    rag_top_k: int
    rag_temperatura_citas: float


def _entero_env(nombre: str, defecto: int) -> int:
    try:
        return int(os.getenv(nombre, str(defecto)))
    except (TypeError, ValueError):
        return defecto


def _flotante_env(nombre: str, defecto: float) -> float:
    try:
        return float(os.getenv(nombre, str(defecto)))
    except (TypeError, ValueError):
        return defecto


@lru_cache(maxsize=1)
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuracion cargada una sola vez (cacheada)."""

    return Configuracion(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        llm_modelo=os.getenv("LLM_MODELO", "meta-llama/llama-3-8b-instruct"),
        secret_key_jwt=_validar_secret_jwt(os.getenv("SECRET_KEY_JWT", "")),
        algoritmo_jwt=os.getenv("ALGORITMO_JWT", "HS256"),
        expiracion_token_minutos=_entero_env("EXPIRACION_TOKEN_MINUTOS", 30),
        # Default: backend/db/legalrag.db (relativa a la raiz del proyecto).
        cadena_bd=os.getenv("CADENA_BD", "sqlite:///./backend/db/legalrag.db"),
        ruta_chromadb=os.getenv("RUTA_CHROMADB", "./backend/db/chroma_data"),
        modelo_embeddings=os.getenv(
            "MODELO_EMBEDDINGS",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
        llm_max_tokens=_entero_env("LLM_MAX_TOKENS", 2048),
        llm_max_tokens_texto_completo=_entero_env("LLM_MAX_TOKENS_TEXTO_COMPLETO", 4096),
        rag_top_k=_entero_env("RAG_TOP_K", 8),
        rag_temperatura_citas=_flotante_env("RAG_TEMPERATURA_CITAS", 0.1),
    )


def _reset_configuracion_cache() -> None:
    """Solo para tests: invalida el lru_cache de obtener_configuracion."""
    obtener_configuracion.cache_clear()
