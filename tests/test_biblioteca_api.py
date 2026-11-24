"""La Biblioteca respeta permisos: nadie lee texto de un documento no permitido."""

from __future__ import annotations

import inspect

from backend.api import biblioteca
from backend.db.modelos import DocumentoIngestado
from backend.servicios.permisos import AlcancePermitido


def _doc(destino: str, h: str = "a" * 64) -> DocumentoIngestado:
    return DocumentoIngestado(hash_archivo=h, nombre_archivo="x.pdf", categoria="Leyes", tenant_id_destino=destino)


def test_coleccion_global_y_privada() -> None:
    assert biblioteca._coleccion_de(_doc("0")) == "leyes_globales"
    assert biblioteca._coleccion_de(_doc("7")) == "casos_user_7"


def test_permitido_por_coleccion_o_por_hash() -> None:
    d = _doc("7")
    assert not biblioteca._permitido(d, AlcancePermitido(colecciones=frozenset({"leyes_globales"})))
    assert biblioteca._permitido(d, AlcancePermitido(colecciones=frozenset({"casos_user_7"})))
    assert biblioteca._permitido(d, AlcancePermitido(hashes_documentos=frozenset({d.hash_archivo})))


def test_renombrar_exige_admin() -> None:
    ruta = next(r for r in biblioteca.router.routes if getattr(r, "path", "") == "/api/admin/biblioteca/{hash_archivo}")
    nombres = [d.call.__name__ for d in ruta.dependant.dependencies]
    assert "verificar_admin" in nombres
