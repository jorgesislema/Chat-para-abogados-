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

# Proveedores LLM soportados (todos con API compatible OpenAI).
# Endpoints reales en uso por el bufete:
#   - sensenova: https://token.sensenova.ai/v1
#   - seek:      https://100milionseek.top/v1
#   - openrouter: https://openrouter.ai/api/v1 (defecto historico)
#   - hms:        configurable via LLM_BASE_URL (nube Huawei / gateway propio)
#   - custom:     cualquier endpoint compatible via LLM_BASE_URL
PROVEEDOR_OPENROUTER = "openrouter"
PROVEEDOR_SENSENOVA = "sensenova"
PROVEEDOR_SEEK = "seek"
PROVEEDOR_HMS = "hms"
PROVEEDOR_CUSTOM = "custom"

_URLS_POR_DEFECTO: dict[str, str] = {
    PROVEEDOR_OPENROUTER: "https://openrouter.ai/api/v1",
    PROVEEDOR_SENSENOVA: "https://token.sensenova.ai/v1",
    PROVEEDOR_SEEK: "https://100milionseek.top/v1",
    PROVEEDOR_HMS: "https://api.hms.ai/v1",
    PROVEEDOR_CUSTOM: "",
}


_CLAVE_POR_PROVEEDOR: dict[str, str] = {
    PROVEEDOR_SENSENOVA: "SENSENOVA_API_KEY",
    PROVEEDOR_SEEK: "SEEK_API_KEY",
    PROVEEDOR_HMS: "HMS_API_KEY",
    PROVEEDOR_OPENROUTER: "OPENROUTER_API_KEY",
}


def _resolver_proveedor() -> str:
    """
    LLM_PROVEEDOR manda (AI_PROVIDER se acepta como alias).
    Si no hay ninguno, se deduce de la clave presente, para no mandar por
    error una clave de SenseNova a OpenRouter (error 401).
    """
    valor = (os.getenv("LLM_PROVEEDOR") or os.getenv("AI_PROVIDER") or "").strip().lower()
    if valor in _URLS_POR_DEFECTO:
        return valor
    if os.getenv("OPENROUTER_API_KEY"):
        return PROVEEDOR_OPENROUTER
    for proveedor in (PROVEEDOR_SENSENOVA, PROVEEDOR_SEEK, PROVEEDOR_HMS):
        if os.getenv(_CLAVE_POR_PROVEEDOR[proveedor]):
            return proveedor
    return PROVEEDOR_OPENROUTER


def _resolver_llm_api_key(proveedor: str = "") -> str:
    """Prioridad: LLM_API_KEY > clave del proveedor elegido > cualquier otra."""
    propia = os.getenv(_CLAVE_POR_PROVEEDOR.get(proveedor, ""), "") if proveedor else ""
    return (
        os.getenv("LLM_API_KEY", "")
        or propia
        or os.getenv("SENSENOVA_API_KEY", "")
        or os.getenv("SEEK_API_KEY", "")
        or os.getenv("HMS_API_KEY", "")
        or os.getenv("OPENROUTER_API_KEY", "")
    )


def _resolver_llm_base_url(proveedor: str) -> str:
    """LLM_BASE_URL manda; si no, URL canonica del proveedor."""
    explicita = os.getenv("LLM_BASE_URL", "").strip()
    if explicita:
        return explicita
    return _URLS_POR_DEFECTO.get(proveedor, _URLS_POR_DEFECTO[PROVEEDOR_OPENROUTER])


def _resolver_llm_modelo(proveedor: str) -> str:
    """LLM_MODELO manda; si no, modelo por proveedor.

    Un nombre con "/" (ej. meta-llama/llama-3-8b-instruct) es de OpenRouter:
    otro proveedor lo rechaza con 404, asi que se ignora y se usa el suyo.
    """
    explicito = os.getenv("LLM_MODELO", "").strip()
    if explicito and not (proveedor != PROVEEDOR_OPENROUTER and "/" in explicito):
        return explicito
    if proveedor == PROVEEDOR_SENSENOVA:
        return "sensenova-6.8-flash"
    if proveedor == PROVEEDOR_SEEK:
        return "deepseek-chat"
    return "meta-llama/llama-3-8b-instruct"


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
    llm_proveedor: str
    llm_base_url: str
    llm_api_key: str
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
    proveedor = _resolver_proveedor()

    return Configuracion(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        llm_modelo=_resolver_llm_modelo(proveedor),
        llm_proveedor=proveedor,
        llm_base_url=_resolver_llm_base_url(proveedor),
        llm_api_key=_resolver_llm_api_key(proveedor),
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
