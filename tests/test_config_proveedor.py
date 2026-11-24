"""El proveedor de IA se elige bien aunque el .env venga incompleto o viejo."""

from __future__ import annotations

import pytest

from backend.core import configuracion as cfg

_VARS = ("LLM_PROVEEDOR", "AI_PROVIDER", "LLM_MODELO", "LLM_API_KEY", "LLM_BASE_URL",
         "OPENROUTER_API_KEY", "SENSENOVA_API_KEY", "SEEK_API_KEY", "HMS_API_KEY")


@pytest.fixture(autouse=True)
def _limpio(monkeypatch: pytest.MonkeyPatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    cfg.obtener_configuracion.cache_clear()
    yield
    cfg.obtener_configuracion.cache_clear()


def test_env_viejo_con_ai_provider_y_modelo_de_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "sensenova")
    monkeypatch.setenv("LLM_MODELO", "meta-llama/llama-3-8b-instruct")
    monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova")
    c = cfg.obtener_configuracion()
    assert c.llm_proveedor == "sensenova"
    assert c.llm_base_url == "https://token.sensenova.ai/v1"
    assert c.llm_modelo == "sensenova-6.8-flash"
    assert c.llm_api_key == "sk-sensenova"


def test_sin_proveedor_se_deduce_de_la_clave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova")
    assert cfg.obtener_configuracion().llm_proveedor == "sensenova"


def test_openrouter_conserva_su_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVEEDOR", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova")
    monkeypatch.setenv("LLM_MODELO", "mistralai/mistral-small")
    c = cfg.obtener_configuracion()
    assert c.llm_modelo == "mistralai/mistral-small" and c.llm_api_key == "sk-or-x"
