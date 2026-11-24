"""
Reconstruye la base de busqueda (ChromaDB) desde el texto guardado en SQLite.

Uso (con el contenedor detenido o dentro de el):
    python scripts/reconstruir_indice.py

Sirve cuando ChromaDB se dana (por ejemplo "Failed to apply logs to the hnsw
segment writer"). Lee `documentos_ingestados` + `textos_documento` y vuelve a
indexar cada documento con el mismo corte que usa la subida normal.
Debe correr sobre una carpeta de ChromaDB nueva o vacia.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.conexion import obtener_sesion_bd  # noqa: E402
from backend.db.modelos import DocumentoIngestado, TextoDocumento  # noqa: E402
from backend.db.vectorial import (  # noqa: E402
    COLECCION_GLOBAL,
    MotorVectorial,
    nombre_coleccion_para_tenant,
)
from backend.servicios.gestion_pdf import segmentar_por_articulos, segmentar_texto  # noqa: E402


def main() -> int:
    motor = MotorVectorial()
    bd = obtener_sesion_bd()
    try:
        textos = {t.hash_archivo: t for t in bd.query(TextoDocumento).all()}
        docs = bd.query(DocumentoIngestado).all()
        sin_texto = []
        for d in docs:
            t = textos.get(d.hash_archivo)
            if t is None or not t.texto.strip():
                sin_texto.append(d.nombre_archivo)
                continue
            destino = (d.tenant_id_destino or "0").strip()
            if destino in ("0", "global", ""):
                coleccion, tenant, tipo = COLECCION_GLOBAL, 0, "global"
            else:
                coleccion, tenant, tipo = nombre_coleccion_para_tenant(int(destino)), int(destino), f"abogado_{destino}"
            base = {
                "titulo": t.titulo or d.nombre_archivo,
                "archivo_original": d.nombre_archivo,
                "categoria": d.categoria,
                "destino_tipo": tipo,
                "tenant_id": tenant,
                "hash_archivo": d.hash_archivo,
            }
            unidades = segmentar_por_articulos(t.texto, ley=base["titulo"]) if d.categoria == "Leyes" else []
            if unidades:
                metas = [
                    {**base, "ley": base["titulo"], "articulo": u["articulo"], "parte": u["parte"],
                     "total_partes": u["total_partes"], "id_articulo": u["id_articulo"]}
                    for u in unidades
                ]
                n = motor.ingestar_chunks_con_metadata([u["texto"] for u in unidades], coleccion, metadatas=metas)
            else:
                n = motor.ingestar_chunks(segmentar_texto(t.texto), coleccion, metadata=base)
            print(f"OK  {base['titulo'][:60]:60}  {n} partes  -> {coleccion}")
        for nombre in sin_texto:
            print(f"SIN TEXTO (volver a subir el PDF): {nombre}")
        return 0
    finally:
        bd.close()


if __name__ == "__main__":
    raise SystemExit(main())
