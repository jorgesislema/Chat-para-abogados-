"""
Conversaciones, casos, metricas y streaming (Fases D/E/F).

  - CRUD de conversaciones con busqueda, filtros y paginacion.
  - Mensajes por conversacion (auto-titulo y auto-materia).
  - Casos con datos estructurados (filtros exactos).
  - Metricas del panel admin leidas de la auditoria real.
  - Exportacion de conversacion a PDF (PyMuPDF).
  - POST /api/chat/stream: respuesta en vivo (SSE).

Visibilidad: admin ve todo su tenant; abogado/cliente solo lo propio
(conversaciones) y sus casos (cliente: `cliente_username` propio).
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.chat import PreguntaSimpleIn, _contexto_permisos
from backend.api.dependencias import (
    UsuarioActual,
    obtener_motor_vectorial,
    obtener_orquestador_rag,
    obtener_usuario_actual,
    verificar_admin,
)
from backend.api.esquemas import (
    CasoCrearIn,
    CasoOut,
    ConversacionCrearIn,
    ConversacionEditarIn,
    ConversacionOut,
    MensajeChatIn,
    MensajeChatOut,
)
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import (
    Caso,
    Conversacion,
    DocumentoIngestado,
    MensajeChat,
    RegistroAuditoria,
    Usuario,
)
from backend.db.vectorial import MotorVectorial
from backend.servicios.rag_motor import OrquestadorRAG
from backend.servicios.telemetria import (
    formatear_fuentes_para_auditoria,
    guardar_auditoria,
    registrar_evento_admin,
)

router = APIRouter(tags=["conversaciones"])


# ============================================================
# Utilidades.
# ============================================================

MATERIAS_CLAVE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("laboral", ("despido", "liquidaci", "trabajo", "sueldo", "decimo", "vacacion", "contrato colectivo")),
    ("civil", ("contrato", "deuda", "factura", "arriendo", "propiedad", "herencia", "prescripci", "civil", "sentencia", "apelar", "apelaci", "demanda civil")),
    ("penal", ("delito", "fiscal", "denuncia", "prision", "audiencia de formulaci")),
    ("familia", ("divorcio", "custodia", "alimentos", "pension alimenticia", "tenencia", "visitas")),
    ("transito", ("transito", "tránsito", "multa", "licencia", "accidente de transito", "choque")),
    ("constitucional", ("accion extraordinaria", "tutela", "amparo", "habeas", "corte constitucional")),
    ("movilidad humana", ("visa", "refugio", "asilo", "migraci", "deportaci")),
    ("servicio publico", ("servidor publico", "sumario administrativo", "destituci", "LOSEP")),
)


def detectar_materia(texto: str) -> str:
    """Clasifica una pregunta en una materia por palabras clave."""
    bajo = (texto or "").lower()
    for materia, claves in MATERIAS_CLAVE:
        if any(c in bajo for c in claves):
            return materia
    return "general"


def generar_titulo(texto: str, max_palabras: int = 8) -> str:
    """Titulo automatico con las primeras palabras de la pregunta."""
    palabras = (texto or "").split()
    titulo = " ".join(palabras[:max_palabras]).strip()
    if len(palabras) > max_palabras:
        titulo += "…"
    return titulo[:200] or "Sin titulo"


def _iso(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _conversacion_visible(
    conv: Conversacion, usuario_actual: UsuarioActual
) -> bool:
    if usuario_actual.get("rol") == "admin":
        return conv.tenant_id == usuario_actual["tenant_id"]
    return conv.usuario_id == usuario_actual["usuario_id"]


def _a_out(conv: Conversacion, total_mensajes: int = 0) -> ConversacionOut:
    return ConversacionOut(
        id=conv.id,
        titulo=conv.titulo,
        materia=conv.materia or None,
        caso_id=conv.caso_id,
        etiquetas=conv.etiquetas or "",
        archivada=bool(conv.archivada),
        creada_en=_iso(conv.creada_en),
        actualizada_en=_iso(conv.actualizada_en),
        mensajes=total_mensajes,
    )


# ============================================================
# Conversaciones.
# ============================================================

@router.get("/api/conversaciones")
def listar_conversaciones(
    q: str | None = Query(default=None, max_length=200),
    materia: str | None = Query(default=None, max_length=80),
    caso_id: int | None = Query(default=None),
    archivada: bool | None = Query(default=None),
    desde: str | None = Query(default=None, max_length=20),
    hasta: str | None = Query(default=None, max_length=20),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> dict:
    """Lista paginada con buscador y filtros (titulo, materia, caso, fecha)."""
    bd: Session = obtener_sesion_bd()
    try:
        consulta = bd.query(Conversacion).filter(
            Conversacion.tenant_id == usuario_actual["tenant_id"]
        )
        if usuario_actual.get("rol") != "admin":
            consulta = consulta.filter(
                Conversacion.usuario_id == usuario_actual["usuario_id"]
            )
        if materia:
            consulta = consulta.filter(Conversacion.materia == materia)
        if caso_id is not None:
            consulta = consulta.filter(Conversacion.caso_id == caso_id)
        if archivada is not None:
            consulta = consulta.filter(Conversacion.archivada == archivada)
        if desde:
            consulta = consulta.filter(
                func.substr(Conversacion.creada_en, 1, 10) >= desde
            )
        if hasta:
            consulta = consulta.filter(
                func.substr(Conversacion.creada_en, 1, 10) <= hasta
            )
        if q:
            consulta = consulta.filter(Conversacion.titulo.ilike(f"%{q}%"))

        total = consulta.count()
        filas = (
            consulta.order_by(Conversacion.actualizada_en.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        ids = [c.id for c in filas]
        conteos: dict[int, int] = {}
        if ids:
            for cid, n in (
                bd.query(MensajeChat.conversacion_id, func.count(MensajeChat.id))
                .filter(MensajeChat.conversacion_id.in_(ids))
                .group_by(MensajeChat.conversacion_id)
            ):
                conteos[int(cid)] = int(n)
        items = [_a_out(c, conteos.get(c.id, 0)) for c in filas]
    finally:
        bd.close()
    import math

    return {
        "items": [i.model_dump() for i in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


@router.post(
    "/api/conversaciones",
    response_model=ConversacionOut,
    status_code=status.HTTP_201_CREATED,
)
def crear_conversacion(
    payload: ConversacionCrearIn,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> ConversacionOut:
    bd: Session = obtener_sesion_bd()
    try:
        conv = Conversacion(
            tenant_id=usuario_actual["tenant_id"],
            usuario_id=usuario_actual["usuario_id"],
            titulo=(payload.titulo or "").strip() or "Sin titulo",
            materia=(payload.materia or "").strip(),
            caso_id=payload.caso_id,
            etiquetas=(payload.etiquetas or "").strip(),
        )
        bd.add(conv)
        bd.commit()
        bd.refresh(conv)
    finally:
        bd.close()
    return _a_out(conv, 0)


def _obtener_conversacion_o_404(
    bd: Session, conv_id: int, usuario_actual: UsuarioActual
) -> Conversacion:
    conv = (
        bd.query(Conversacion)
        .filter(
            Conversacion.id == conv_id,
            Conversacion.tenant_id == usuario_actual["tenant_id"],
        )
        .first()
    )
    if conv is None or not _conversacion_visible(conv, usuario_actual):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversacion no encontrada.",
        )
    return conv


@router.get("/api/conversaciones/{conv_id}")
def obtener_conversacion(
    conv_id: int,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> dict:
    bd: Session = obtener_sesion_bd()
    try:
        conv = _obtener_conversacion_o_404(bd, conv_id, usuario_actual)
        mensajes = (
            bd.query(MensajeChat)
            .filter(MensajeChat.conversacion_id == conv.id)
            .order_by(MensajeChat.id)
            .all()
        )
        salida = _a_out(conv, len(mensajes))
        datos = salida.model_dump()
        datos["mensajes_lista"] = [
            MensajeChatOut(
                id=m.id,
                rol=m.rol,
                contenido=m.contenido,
                fuentes=m.fuentes or "",
                creada_en=_iso(m.creada_en),
            ).model_dump()
            for m in mensajes
        ]
        if conv.caso_id:
            caso = bd.query(Caso).filter(Caso.id == conv.caso_id).first()
            datos["caso"] = _a_caso(caso).model_dump() if caso else None
        else:
            datos["caso"] = None
    finally:
        bd.close()
    return datos


@router.patch("/api/conversaciones/{conv_id}", response_model=ConversacionOut)
def editar_conversacion(
    conv_id: int,
    payload: ConversacionEditarIn,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> ConversacionOut:
    bd: Session = obtener_sesion_bd()
    try:
        conv = _obtener_conversacion_o_404(bd, conv_id, usuario_actual)
        if payload.titulo is not None:
            conv.titulo = payload.titulo.strip()[:200] or conv.titulo
        if payload.materia is not None:
            conv.materia = payload.materia.strip()[:80]
        if payload.caso_id is not None:
            conv.caso_id = payload.caso_id
        if payload.etiquetas is not None:
            conv.etiquetas = payload.etiquetas.strip()[:500]
        if payload.archivada is not None:
            conv.archivada = payload.archivada
        bd.commit()
        bd.refresh(conv)
        total = (
            bd.query(func.count(MensajeChat.id))
            .filter(MensajeChat.conversacion_id == conv.id)
            .scalar()
            or 0
        )
    finally:
        bd.close()
    return _a_out(conv, int(total))


@router.delete("/api/conversaciones/{conv_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)  # 204 sin cuerpo (FastAPI 0.115 y 0.136)
def borrar_conversacion(
    conv_id: int,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> None:
    bd: Session = obtener_sesion_bd()
    try:
        conv = _obtener_conversacion_o_404(bd, conv_id, usuario_actual)
        bd.query(MensajeChat).filter(
            MensajeChat.conversacion_id == conv.id
        ).delete()
        bd.delete(conv)
        bd.commit()
    finally:
        bd.close()


@router.post(
    "/api/conversaciones/{conv_id}/mensajes",
    response_model=MensajeChatOut,
    status_code=status.HTTP_201_CREATED,
)
def agregar_mensaje(
    conv_id: int,
    payload: MensajeChatIn,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> MensajeChatOut:
    bd: Session = obtener_sesion_bd()
    try:
        conv = _obtener_conversacion_o_404(bd, conv_id, usuario_actual)
        msg = MensajeChat(
            conversacion_id=conv.id,
            rol=payload.rol,
            contenido=payload.contenido,
            fuentes=payload.fuentes or "",
        )
        bd.add(msg)
        # Auto-titulo y auto-materia con el primer mensaje del usuario.
        if payload.rol == "user":
            n_previos = (
                bd.query(func.count(MensajeChat.id))
                .filter(
                    MensajeChat.conversacion_id == conv.id,
                    MensajeChat.rol == "user",
                )
                .scalar()
                or 0
            )
            if n_previos == 0:
                if conv.titulo in ("", "Sin titulo"):
                    conv.titulo = generar_titulo(payload.contenido)
                if not conv.materia:
                    conv.materia = detectar_materia(payload.contenido)
        bd.commit()
        bd.refresh(msg)
    finally:
        bd.close()
    return MensajeChatOut(
        id=msg.id,
        rol=msg.rol,
        contenido=msg.contenido,
        fuentes=msg.fuentes or "",
        creada_en=_iso(msg.creada_en),
    )


@router.get("/api/conversaciones/{conv_id}/exportar.pdf")
def exportar_conversacion_pdf(
    conv_id: int,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> Response:
    """Exporta la conversacion a PDF (generado en servidor con PyMuPDF)."""
    bd: Session = obtener_sesion_bd()
    try:
        conv = _obtener_conversacion_o_404(bd, conv_id, usuario_actual)
        mensajes = (
            bd.query(MensajeChat)
            .filter(MensajeChat.conversacion_id == conv.id)
            .order_by(MensajeChat.id)
            .all()
        )
        titulo = conv.titulo
        lineas = [(m.rol, m.contenido) for m in mensajes]
    finally:
        bd.close()

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Exportacion PDF no disponible (falta PyMuPDF).",
        ) from exc

    doc = fitz.open()
    pagina = doc.new_page(width=595, height=842)
    y = 60.0
    ancho_util = 515.0

    def _nueva_pagina() -> None:
        nonlocal pagina, y
        pagina = doc.new_page(width=595, height=842)
        y = 60.0

    def _escribir(texto: str, tamano: int = 11, negrita: bool = False) -> None:
        nonlocal y
        fuente = "helv"
        for parrafo in texto.split("\n"):
            palabras = parrafo.split(" ") or [""]
            linea_actual = ""
            for palabra in palabras:
                prueba = (linea_actual + " " + palabra).strip()
                if fitz.get_text_length(prueba, fontname=fuente, fontsize=tamano) > ancho_util:
                    if y > 780:
                        _nueva_pagina()
                    pagina.insert_text((40, y), linea_actual, fontname=fuente, fontsize=tamano)
                    y += tamano + 4
                    linea_actual = palabra
                else:
                    linea_actual = prueba
            if linea_actual:
                if y > 780:
                    _nueva_pagina()
                pagina.insert_text((40, y), linea_actual, fontname=fuente, fontsize=tamano)
                y += tamano + 4
            y += 2

    _escribir("Bufete Andrade & Asociados - Conversacion", tamano=14, negrita=True)
    _escribir(titulo, tamano=12)
    _escribir("", tamano=11)
    for rol, contenido in lineas:
        quien = "Abogado" if rol == "user" else "Asistente IA"
        _escribir(f"{quien}:", tamano=11, negrita=True)
        _escribir(contenido, tamano=11)
        _escribir("", tamano=11)
    _escribir("Asistente con IA. No reemplaza el criterio de un abogado.", tamano=9)

    pdf_bytes = doc.tobytes()
    doc.close()
    nombre = "".join(c if c.isalnum() else "_" for c in titulo[:40]) or "conversacion"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.pdf"'},
    )


# ============================================================
# Casos.
# ============================================================

def _a_caso(c: Caso) -> CasoOut:
    return CasoOut(
        id=c.id,
        numero_proceso=c.numero_proceso,
        materia=c.materia,
        juzgado=c.juzgado or "",
        estado=c.estado or "",
        cliente_nombre=c.cliente_nombre or "",
        cliente_username=getattr(c, "cliente_username", "") or "",
        abogado_id=c.abogado_id,
        fecha_ingreso=c.fecha_ingreso or "",
        fecha_audiencia=c.fecha_audiencia or "",
    )


def _casos_visibles(bd: Session, usuario_actual: UsuarioActual):
    consulta = bd.query(Caso).filter(Caso.tenant_id == usuario_actual["tenant_id"])
    if usuario_actual.get("rol") == "cliente":
        consulta = consulta.filter(
            Caso.cliente_username == usuario_actual.get("username", "")
        )
    return consulta


@router.get("/api/casos")
def listar_casos(
    materia: str | None = Query(default=None, max_length=80),
    estado: str | None = Query(default=None, max_length=60),
    q: str | None = Query(default=None, max_length=200),
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> dict:
    """Filtros exactos: materia, estado, numero de proceso o cliente."""
    bd: Session = obtener_sesion_bd()
    try:
        consulta = _casos_visibles(bd, usuario_actual)
        if materia:
            consulta = consulta.filter(Caso.materia == materia)
        if estado:
            consulta = consulta.filter(Caso.estado == estado)
        if q:
            consulta = consulta.filter(
                (Caso.numero_proceso.ilike(f"%{q}%"))
                | (Caso.cliente_nombre.ilike(f"%{q}%"))
            )
        filas = consulta.order_by(Caso.id).all()
    finally:
        bd.close()
    return {"items": [_a_caso(c).model_dump() for c in filas], "total": len(filas)}


@router.post("/api/casos", response_model=CasoOut, status_code=status.HTTP_201_CREATED)
def crear_caso(
    payload: CasoCrearIn,
    admin: UsuarioActual = Depends(verificar_admin),
) -> CasoOut:
    bd: Session = obtener_sesion_bd()
    try:
        caso = Caso(
            tenant_id=admin["tenant_id"],
            numero_proceso=payload.numero_proceso.strip(),
            materia=payload.materia.strip(),
            juzgado=(payload.juzgado or "").strip(),
            estado=(payload.estado or "abierto").strip(),
            cliente_nombre=(payload.cliente_nombre or "").strip(),
            cliente_username=(payload.cliente_username or "").strip(),
            abogado_id=payload.abogado_id,
            fecha_ingreso=(payload.fecha_ingreso or "").strip(),
            fecha_audiencia=(payload.fecha_audiencia or "").strip(),
        )
        bd.add(caso)
        bd.commit()
        bd.refresh(caso)
    finally:
        bd.close()
    try:
        registrar_evento_admin(
            tenant_id=str(admin["tenant_id"]),
            accion="crear_caso",
            detalle=f"numero={caso.numero_proceso!r} materia={caso.materia}",
            actor_usuario_id=admin["usuario_id"],
        )
    except Exception:  # noqa: BLE001
        pass
    return _a_caso(caso)


@router.get("/api/casos/{caso_id}", response_model=CasoOut)
def obtener_caso(
    caso_id: int,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> CasoOut:
    bd: Session = obtener_sesion_bd()
    try:
        caso = (
            _casos_visibles(bd, usuario_actual)
            .filter(Caso.id == caso_id)
            .first()
        )
    finally:
        bd.close()
    if caso is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Caso no encontrado."
        )
    return _a_caso(caso)


# ============================================================
# Metricas y auditoria (panel admin).
# ============================================================

@router.get("/api/admin/metricas")
def metricas_admin(
    admin: UsuarioActual = Depends(verificar_admin),
) -> dict:
    """Consultas por dia, temas, documentos citados y tokens (30 dias)."""
    bd: Session = obtener_sesion_bd()
    try:
        tenant = str(admin["tenant_id"])
        base = bd.query(RegistroAuditoria).filter(
            RegistroAuditoria.tenant_id == tenant
        )
        total_consultas = base.count()
        tokens = base.with_entities(
            func.coalesce(func.sum(RegistroAuditoria.tokens_prompt), 0),
            func.coalesce(func.sum(RegistroAuditoria.tokens_completion), 0),
        ).first()
        hace_30 = datetime.now(timezone.utc) - timedelta(days=30)
        por_dia = (
            bd.query(
                func.substr(RegistroAuditoria.timestamp, 1, 10).label("dia"),
                func.count(RegistroAuditoria.id),
            )
            .filter(
                RegistroAuditoria.tenant_id == tenant,
                RegistroAuditoria.timestamp >= hace_30,
            )
            .group_by("dia")
            .order_by("dia")
            .all()
        )
        por_materia = (
            bd.query(Conversacion.materia, func.count(Conversacion.id))
            .filter(Conversacion.tenant_id == admin["tenant_id"])
            .group_by(Conversacion.materia)
            .order_by(func.count(Conversacion.id).desc())
            .limit(10)
            .all()
        )
        # Documentos mas citados: cuenta hashes en fuentes de mensajes.
        conteo_hashes: dict[str, int] = {}
        for (fuentes,) in (
            bd.query(MensajeChat.fuentes)
            .join(Conversacion, MensajeChat.conversacion_id == Conversacion.id)
            .filter(Conversacion.tenant_id == admin["tenant_id"])
            .all()
        ):
            try:
                datos = json.loads(fuentes or "[]")
            except (TypeError, ValueError):
                continue
            if isinstance(datos, list):
                for f in datos:
                    h = (f.get("hash") or "").strip() if isinstance(f, dict) else ""
                    if h:
                        conteo_hashes[h] = conteo_hashes.get(h, 0) + 1
        top_hashes = sorted(conteo_hashes.items(), key=lambda kv: kv[1], reverse=True)[:10]
        titulos = {}
        if top_hashes:
            for doc in (
                bd.query(DocumentoIngestado)
                .filter(
                    DocumentoIngestado.hash_archivo.in_([h for h, _ in top_hashes])
                )
                .all()
            ):
                titulos[doc.hash_archivo] = doc.nombre_archivo
        total_conversaciones = (
            bd.query(func.count(Conversacion.id))
            .filter(Conversacion.tenant_id == admin["tenant_id"])
            .scalar()
            or 0
        )
    finally:
        bd.close()
    return {
        "consultas_totales": total_consultas,
        "tokens_prompt": int(tokens[0] or 0),
        "tokens_completion": int(tokens[1] or 0),
        "consultas_por_dia": [
            {"dia": d, "consultas": int(n)} for d, n in por_dia
        ],
        "temas_mas_preguntados": [
            {"materia": m or "general", "conversaciones": int(n)}
            for m, n in por_materia
        ],
        "documentos_mas_citados": [
            {"hash": h[:12], "titulo": titulos.get(h, h[:12]), "citas": n}
            for h, n in top_hashes
        ],
        "conversaciones_totales": int(total_conversaciones),
    }


@router.get("/api/admin/auditoria")
def auditoria_admin(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: UsuarioActual = Depends(verificar_admin),
) -> dict:
    """Lista paginada de la auditoria de la firma (consultas + acciones)."""
    bd: Session = obtener_sesion_bd()
    try:
        consulta = (
            bd.query(RegistroAuditoria)
            .filter(RegistroAuditoria.tenant_id == str(admin["tenant_id"]))
            .order_by(RegistroAuditoria.id.desc())
        )
        total = consulta.count()
        filas = consulta.offset((page - 1) * page_size).limit(page_size).all()
        items = [
            {
                "id": r.id,
                "pregunta": r.pregunta_ofuscada,
                "respuesta": (r.respuesta_llm or "")[:500],
                "fuentes": r.fuentes_usadas or "",
                "tokens_prompt": r.tokens_prompt,
                "tokens_completion": r.tokens_completion,
                "timestamp": _iso(r.timestamp),
            }
            for r in filas
        ]
    finally:
        bd.close()
    import math

    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, math.ceil(total / page_size)),
    }


# ============================================================
# Streaming (SSE) para la respuesta en vivo.
# ============================================================

@router.post("/api/chat/stream")
async def chat_stream(
    payload: PreguntaSimpleIn,
    background_tasks: BackgroundTasks,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
):
    """SSE: eventos token/hecho/denegado/error con las fuentes al cierre."""
    rol, uid, grupo, reglas, existentes = _contexto_permisos(usuario_actual, motor)
    tenant_firma = str(usuario_actual["tenant_id"])
    gen = orquestador.consultar_stream(
        pregunta_cruda=payload.pregunta,
        tenant_id=str(usuario_actual["usuario_id"]),
        categoria=payload.categoria,
        rol=rol,
        usuario_id=uid,
        grupo=grupo,
        reglas_permiso=reglas,
        colecciones_existentes=existentes,
    )

    acumulada: list[str] = []
    pregunta_ofuscada = ""
    fuentes_finales: list = []

    async def _flujo():
        nonlocal pregunta_ofuscada, fuentes_finales
        try:
            async for evento in gen:
                tipo = evento.get("tipo")
                if tipo == "token":
                    acumulada.append(evento.get("texto", ""))
                elif tipo in ("hecho", "denegado", "error"):
                    pregunta_ofuscada = evento.get("pregunta_ofuscada", "")
                    fuentes_finales = evento.get("fuentes", [])
                    if tipo in ("denegado", "error"):
                        acumulada.append(evento.get("respuesta", ""))
                yield f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"
        finally:
            # La auditoria se programa aunque el cliente corte el stream.
            background_tasks.add_task(
                guardar_auditoria,
                tenant_id=tenant_firma,
                pregunta_ofuscada=pregunta_ofuscada,
                respuesta_llm="".join(acumulada),
                fuentes_usadas=formatear_fuentes_para_auditoria(fuentes_finales),
                tokens_prompt=0,
                tokens_completion=0,
            )

    return StreamingResponse(_flujo(), media_type="text/event-stream")
