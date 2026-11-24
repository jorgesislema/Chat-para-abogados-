"""
Tests Docker/pagina local (sin Docker corriendo): la pagina profesional
queda en http://localhost:8000 servida por el backend.
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _leer(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


def test_compose_expone_solo_8000() -> None:
    compose = _leer("docker-compose.yml")
    assert ":8000" in compose  # puerto contenedor fijo; host configurable
    assert "PUERTO_HOST" in compose
    assert "8501" not in compose  # Streamlit legacy fuera del compose
    assert "AUTO_SEMBRAR_DEMO" in compose


def test_backend_docker_incluye_ui_y_scripts() -> None:
    df = _leer("backend/Dockerfile")
    assert "frontend_moderno/" in df
    assert "scripts/" in df
    assert "entrada_docker.sh" in df


def test_entrypoint_siembra_y_arranca() -> None:
    sh = _leer("backend/entrada_docker.sh")
    assert "sembrar_demo.py" in sh
    assert "uvicorn backend.main:app" in sh


def test_scripts_inicio_abren_8000() -> None:
    assert "http://localhost:8000" in _leer("iniciar_legalrag.bat")
    assert "http://localhost:8000" in _leer("iniciar_legalrag.sh")
    assert "8501" not in _leer("iniciar_legalrag.bat")
    assert "8501" not in _leer("iniciar_legalrag.sh")
