"""
Configuracion de pytest para el proyecto LegalRAG_SaaS.

Asegura que la raiz del proyecto esta en sys.path para resolver
`from backend.servicios.ofuscacion import ...` desde los tests.

Adicionalmente:
  - Expone un fixture de sesion `embedding_function_compartido` que
    carga el modelo de sentence-transformers UNA sola vez por corrida
    y lo reutiliza en todos los tests del motor vectorial.
  - Define una `OPENROUTER_API_KEY` y un `SECRET_KEY_JWT` en el
    entorno para que `AsyncOpenAI` (en el constructor del
    `OrquestadorRAG`) y el firmado de JWTs no fallen en tests que
    luego mockean la llamada real al LLM.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-no-real-calls")
os.environ.setdefault("SECRET_KEY_JWT", "test-secret-key-for-pytest-only")
os.environ["LEGALRAG_TESTING"] = "1"


@pytest.fixture(scope="session", autouse=True)
def _variables_de_entorno_para_tests():
    """Asegura que las variables esten presentes en TODOS los tests."""
    os.environ["OPENROUTER_API_KEY"] = "test-key-no-real-calls"
    os.environ["SECRET_KEY_JWT"] = "test-secret-key-for-pytest-only"
    os.environ["LEGALRAG_TESTING"] = "1"
    yield


@pytest.fixture(scope="session")
def embedding_function_compartido():
    """
    Devuelve un `SentenceTransformerEmbeddingFunction` cargado una sola
    vez para toda la corrida. Tests que necesiten embeddings deben
    pedir este fixture e inyectarlo en su `MotorVectorial`.
    """
    from chromadb.utils import embedding_functions

    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
