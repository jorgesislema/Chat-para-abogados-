"""
Trazabilidad local (Fase 0): la carpeta existe, esta ignorada por git
y los archivos de estado no estan vacios.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def test_carpeta_trazabilidad_existe() -> None:
    assert (RAIZ / "trazabilidad").is_dir()


def test_trazabilidad_ignorada_por_git() -> None:
    r = subprocess.run(
        ["git", "check-ignore", "trazabilidad/BITACORA.md"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, "trazabilidad/ debe estar en .gitignore"


def test_estado_actual_y_bitacora_no_vacios() -> None:
    estado = RAIZ / "trazabilidad" / "ESTADO_ACTUAL.md"
    bitacora = RAIZ / "trazabilidad" / "BITACORA.md"
    assert estado.is_file() and estado.stat().st_size > 0
    assert bitacora.is_file() and bitacora.stat().st_size > 0
