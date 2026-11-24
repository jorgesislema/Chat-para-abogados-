"""
Aislamiento de las pruebas: NUNCA tocan los datos reales.

Antes de importar `backend`, se apunta la base SQLite y la base vectorial a
una carpeta temporal. Sin esto, las pruebas dejaban documentos falsos
("aud.pdf", "Ley de demostracion") dentro de la biblioteca real.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="legalrag_tests_"))
os.environ["CADENA_BD"] = f"sqlite:///{(_TMP / 'prueba.db').as_posix()}"
os.environ["RUTA_CHROMADB"] = str(_TMP / "chroma")
os.environ["RUTA_LOGS"] = str(_TMP / "logs")
os.environ.setdefault("LEGALRAG_TESTING", "1")

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
