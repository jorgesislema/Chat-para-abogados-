"""
Biblioteca: leyes y sentencias para leer en pantalla, con buscador de articulo.

  - GET   /api/biblioteca                 : lista de documentos permitidos.
  - GET   /api/biblioteca/buscar?q=       : busca un texto en toda la biblioteca.
  - GET   /api/biblioteca/{hash}          : texto completo + indice de articulos.
  - PATCH /api/admin/biblioteca/{hash}    : renombrar (solo admin).

Permisos: el mismo criterio del chat. Un usuario solo ve los documentos de las
colecciones que le permiten (o los documentos concedidos por hash).
El texto de un documento no permitido NUNCA se devuelve.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.api.chat import _contexto_permisos
from backend.api.dependencias import (
    UsuarioActual,
    obtener_motor_vectorial,
    obtener_usuario_actual,
    verificar_admin,
)
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import DocumentoIngestado, FichaDocumento, TextoDocumento
from backend.db.vectorial import COLECCION_GLOBAL, MotorVectorial, nombre_coleccion_para_tenant
from backend.servicios.biblioteca import (
    buscar_en_texto,
    guardar_texto,
    reconstruir_texto,
)
from backend.servicios.permisos import AlcancePermitido, resolver_alcance

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["biblioteca"])


def _coleccion_de(doc: DocumentoIngestado) -> str:
    destino = (doc.tenant_id_destino or "0").strip()
    if destino in ("0", "global", ""):
        return COLECCION_GLOBAL
    return nombre_coleccion_para_tenant(int(destino)) if destino.isdigit() else COLECCION_GLOBAL


def _alcance(usuario: UsuarioActual, motor: MotorVectorial) -> AlcancePermitido:
    rol, uid, grupo, reglas, existentes = _contexto_permisos(usuario, motor)
    return resolver_alcance(rol, int(uid or 0), grupo, reglas, existentes)


def _permitido(doc: DocumentoIngestado, alcance: AlcancePermitido) -> bool:
    return _coleccion_de(doc) in alcance.colecciones or doc.hash_archivo in alcance.hashes_documentos


def _reconstruir_desde_vectores(motor: MotorVectorial, doc: DocumentoIngestado) -> tuple[str, str]:
    """Devuelve (titulo, texto) leyendo los pedazos del vector store."""
    col = motor._cliente.get_collection(_coleccion_de(doc))  # noqa: SLF001
    res = col.get(where={"hash_archivo": doc.hash_archivo}, include=["documents", "metadatas"])
    pares = list(zip(res.get("documents") or [], res.get("metadatas") or []))
    titulo = ""
    for _, md in pares:
        titulo = (md or {}).get("titulo") or titulo
    return titulo or doc.nombre_archivo, reconstruir_texto(pares)


def _texto_de(motor: MotorVectorial, bd, doc: DocumentoIngestado) -> TextoDocumento | None:
    fila = bd.query(TextoDocumento).filter(TextoDocumento.hash_archivo == doc.hash_archivo).first()
    if fila is not None:
        return fila
    try:
        titulo, texto = _reconstruir_desde_vectores(motor, doc)
    except Exception:  # noqa: BLE001 - coleccion borrada o sin pedazos
        LOGGER.warning("No se pudo reconstruir el texto del documento %s", doc.hash_archivo[:12], exc_info=True)
        return None
    if not texto.strip():
        return None
    guardar_texto(bd, doc.hash_archivo, titulo, texto, doc.categoria)
    return bd.query(TextoDocumento).filter(TextoDocumento.hash_archivo == doc.hash_archivo).first()


def _sincronizar_registro(motor: MotorVectorial, bd) -> None:
    """
    Crea la fila de `documentos_ingestados` de todo documento que existe en el
    vector store pero no en el registro (pasa con ingestas viejas o borrados
    parciales). Asi la Biblioteca siempre muestra lo que el chat realmente usa.
    """
    conocidos = {h for (h,) in bd.query(DocumentoIngestado.hash_archivo).all()}
    nuevos: dict[str, dict[str, Any]] = {}
    try:
        colecciones = [c.name for c in motor._cliente.list_collections()]  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return
    for nombre in colecciones:
        try:
            md_lista = motor._cliente.get_collection(nombre).get(include=["metadatas"]).get("metadatas") or []  # noqa: SLF001
        except Exception:  # noqa: BLE001
            continue
        for md in md_lista:
            h = (md or {}).get("hash_archivo")
            if h and h not in conocidos and h not in nuevos:
                nuevos[h] = {**md, "_col": nombre}
    for h, md in nuevos.items():
        col = md["_col"]
        destino = "0" if col == COLECCION_GLOBAL else col.replace("casos_user_", "")
        bd.add(DocumentoIngestado(
            hash_archivo=h,
            nombre_archivo=str(md.get("archivo_original") or md.get("titulo") or "documento.pdf")[:500],
            categoria=str(md.get("categoria") or "Leyes")[:64],
            tenant_id_destino=destino,
        ))
    if nuevos:
        bd.commit()


@router.get("/api/biblioteca")
def listar_biblioteca(
    categoria: str = Query("", max_length=64),
    q: str = Query("", max_length=100),
    tribunal: str = Query("", max_length=200),
    anio: int | None = Query(None, ge=1800, le=2200),
    materia: str = Query("", max_length=64),
    usuario: UsuarioActual = Depends(obtener_usuario_actual),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> dict[str, Any]:
    alcance = _alcance(usuario, motor)
    bd = obtener_sesion_bd()
    try:
        _sincronizar_registro(motor, bd)
        docs = [d for d in bd.query(DocumentoIngestado).order_by(DocumentoIngestado.nombre_archivo).all() if _permitido(d, alcance)]
        _asegurar_fichas(motor, bd, docs)
        titulos = {t.hash_archivo: t.titulo for t in bd.query(TextoDocumento.hash_archivo, TextoDocumento.titulo).all()}
        fichas = {f.hash_archivo: f for f in bd.query(FichaDocumento).all()}
        items, facetas = [], {"tribunales": set(), "anios": set(), "materias": set()}
        for d in docs:
            if categoria and d.categoria != categoria:
                continue
            f = fichas.get(d.hash_archivo)
            # Las opciones de los filtros salen de los documentos de esta categoria.
            if f:
                if f.tribunal:
                    facetas["tribunales"].add(f.tribunal)
                if f.anio:
                    facetas["anios"].add(f.anio)
                if f.materia:
                    facetas["materias"].add(f.materia)
            titulo = titulos.get(d.hash_archivo) or d.nombre_archivo
            if q and q.lower() not in f"{titulo} {d.nombre_archivo}".lower():
                continue
            if tribunal and (not f or f.tribunal != tribunal):
                continue
            if anio and (not f or f.anio != anio):
                continue
            if materia and (not f or f.materia != materia):
                continue
            items.append({
                "hash": d.hash_archivo, "titulo": titulo, "archivo": d.nombre_archivo,
                "categoria": d.categoria, "ambito": "Compartido" if _coleccion_de(d) == COLECCION_GLOBAL else "Privado",
                "fecha": d.fecha_ingesta.isoformat() if d.fecha_ingesta else "",
                "tribunal": f.tribunal if f else "", "anio": f.anio if f else None,
                "materia": f.materia if f else "", "numero_proceso": f.numero_proceso if f else "",
            })
        return {
            "total": len(items), "items": items,
            "facetas": {"tribunales": sorted(facetas["tribunales"]), "anios": sorted(facetas["anios"], reverse=True), "materias": sorted(facetas["materias"])},
        }
    finally:
        bd.close()


def _asegurar_fichas(motor: MotorVectorial, bd, docs: list[DocumentoIngestado]) -> None:
    """Crea la ficha (tribunal, anio...) de los documentos que aun no la tienen."""
    con_ficha = {h for (h,) in bd.query(FichaDocumento.hash_archivo).all()}
    for d in docs:
        if d.hash_archivo in con_ficha:
            continue
        fila = _texto_de(motor, bd, d)  # al crear el texto tambien crea la ficha
        if fila is None:
            continue
        if bd.query(FichaDocumento).filter(FichaDocumento.hash_archivo == d.hash_archivo).first() is None:
            bd.add(FichaDocumento(hash_archivo=d.hash_archivo, **extraer_ficha(fila.texto, d.categoria)))
            bd.commit()


@router.get("/api/biblioteca/buscar")
def buscar_biblioteca(
    q: str = Query(..., min_length=2, max_length=100),
    usuario: UsuarioActual = Depends(obtener_usuario_actual),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> dict[str, Any]:
    """Busca una frase en el texto de los documentos permitidos (ya guardados)."""
    alcance = _alcance(usuario, motor)
    bd = obtener_sesion_bd()
    try:
        _sincronizar_registro(motor, bd)
        docs = {d.hash_archivo: d for d in bd.query(DocumentoIngestado).all() if _permitido(d, alcance)}
        for d in docs.values():
            _texto_de(motor, bd, d)  # construye el texto la primera vez
        resultados = []
        for t in bd.query(TextoDocumento).all():
            if t.hash_archivo not in docs:
                continue
            hits = buscar_en_texto(t.texto, q, limite=3)
            if hits:
                resultados.append({"hash": t.hash_archivo, "titulo": t.titulo, "categoria": docs[t.hash_archivo].categoria, "coincidencias": hits})
        return {"total": len(resultados), "items": resultados[:30]}
    finally:
        bd.close()


@router.get("/api/biblioteca/{hash_archivo}")
def abrir_documento(
    hash_archivo: str,
    usuario: UsuarioActual = Depends(obtener_usuario_actual),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> dict[str, Any]:
    alcance = _alcance(usuario, motor)
    bd = obtener_sesion_bd()
    try:
        doc = bd.query(DocumentoIngestado).filter(DocumentoIngestado.hash_archivo == hash_archivo).first()
        # Mismo mensaje si no existe o no esta permitido: no se revela que existe.
        if doc is None or not _permitido(doc, alcance):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
        fila = _texto_de(motor, bd, doc)
        if fila is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Este documento no tiene texto disponible para leer")
        ficha = bd.query(FichaDocumento).filter(FichaDocumento.hash_archivo == doc.hash_archivo).first()
        return {
            "hash": doc.hash_archivo, "titulo": fila.titulo or doc.nombre_archivo,
            "archivo": doc.nombre_archivo, "categoria": doc.categoria,
            "ficha": {
                "tribunal": ficha.tribunal if ficha else "", "anio": ficha.anio if ficha else None,
                "materia": ficha.materia if ficha else "", "numero_proceso": ficha.numero_proceso if ficha else "",
                "tema": ficha.tema if ficha else "",
            },
            "texto": fila.texto, "articulos": json.loads(fila.articulos_json or "[]"),
        }
    finally:
        bd.close()


class RenombrarIn(BaseModel):
    titulo: str = Field(min_length=2, max_length=200)


@router.patch("/api/admin/biblioteca/{hash_archivo}", dependencies=[Depends(verificar_admin)])
def renombrar_documento(
    hash_archivo: str,
    datos: RenombrarIn,
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> dict[str, str]:
    bd = obtener_sesion_bd()
    try:
        doc = bd.query(DocumentoIngestado).filter(DocumentoIngestado.hash_archivo == hash_archivo).first()
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
        fila = _texto_de(motor, bd, doc)
        if fila is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Sin texto para renombrar")
        fila.titulo = datos.titulo.strip()
        bd.commit()
        return {"hash": hash_archivo, "titulo": fila.titulo}
    finally:
        bd.close()


class FichaIn(BaseModel):
    tribunal: str = Field(default="", max_length=200)
    anio: int | None = Field(default=None, ge=1800, le=2200)
    materia: str = Field(default="", max_length=64)
    numero_proceso: str = Field(default="", max_length=100)


@router.patch("/api/admin/biblioteca/{hash_archivo}/ficha", dependencies=[Depends(verificar_admin)])
def editar_ficha(hash_archivo: str, datos: FichaIn) -> dict[str, Any]:
    """El admin corrige tribunal, anio, materia o numero de caso."""
    bd = obtener_sesion_bd()
    try:
        ficha = bd.query(FichaDocumento).filter(FichaDocumento.hash_archivo == hash_archivo).first()
        if ficha is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
        ficha.tribunal = datos.tribunal.strip()
        ficha.anio = datos.anio
        ficha.materia = datos.materia.strip().lower()
        ficha.numero_proceso = datos.numero_proceso.strip()
        bd.commit()
        return {"hash": hash_archivo, **datos.model_dump()}
    finally:
        bd.close()
