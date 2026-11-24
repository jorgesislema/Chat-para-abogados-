"""
Rutas de administracion (unificadas, protegidas por RBAC: rol 'admin').

  - POST /api/admin/usuarios:   crea un nuevo usuario en un tenant.
  - POST /api/admin/documentos: sube un PDF y lo ingesta en ChromaDB
                                 con destino (global o expediente
                                 privado) y categoria (Leyes,
                                 Jurisprudencia, Expediente).

Historial: antes existian `admin.py` (POST /admin/expedientes, solo
contaba chunks sin persistir) y `rutas_admin.py` (implementacion
completa con deduplicacion por hash). Se conserva la implementacion
completa (Fase 1A). El endpoint legacy `/admin/expedientes` se
elimina: no lo usa el frontend ni los tests, y no persistia nada.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.dependencias import (
    UsuarioActual,
    obtener_motor_vectorial,
    verificar_admin,
)
from backend.api.esquemas import CargarDocumentoOut, CrearUsuarioIn, CrearUsuarioOut
from backend.core.seguridad import hashear_contrasena
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import (
    ROLES_VALIDOS,
    DocumentoIngestado,
    Usuario,
)
from backend.db.vectorial import (
    CATEGORIAS_VALIDAS,
    COLECCION_GLOBAL,
    MotorVectorial,
    nombre_coleccion_para_tenant,
)
from backend.servicios.gestion_pdf import (
    ErrorIngestaDocumento,
    extraer_texto_pdf,
    segmentar_texto,
)
from backend.servicios.telemetria import registrar_evento_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ============================================================
# Gestion de usuarios.
# ============================================================

@router.post(
    "/api/admin/usuarios",
    response_model=CrearUsuarioOut,
    status_code=status.HTTP_201_CREATED,
)
def crear_usuario(
    payload: CrearUsuarioIn,
    _admin: UsuarioActual = Depends(verificar_admin),
) -> CrearUsuarioOut:
    """
    Crea un nuevo usuario en el sistema.

    Restringido a usuarios con rol 'admin'. El nuevo usuario queda
    asociado al `tenant_id` indicado en el payload (que debe existir).
    """
    if payload.rol not in ROLES_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Rol invalido. Valores permitidos: {list(ROLES_VALIDOS)}",
        )

    bd: Session = obtener_sesion_bd()
    try:
        nuevo = Usuario(
            tenant_id=payload.tenant_id,
            username=payload.username,
            password_hash=hashear_contrasena(payload.password),
            rol=payload.rol,
        )
        bd.add(nuevo)
        bd.commit()
        bd.refresh(nuevo)
    except IntegrityError as exc:
        bd.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El username '{payload.username}' ya existe",
        ) from exc
    finally:
        bd.close()

    try:
        registrar_evento_admin(
            tenant_id=str(nuevo.tenant_id),
            accion="crear_usuario",
            detalle=f"username={nuevo.username!r} rol={nuevo.rol} id={nuevo.id}",
            actor_usuario_id=_admin["usuario_id"],
        )
    except Exception:  # noqa: BLE001 - la auditoria nunca rompe el 201
        LOGGER.warning("No se pudo auditar crear_usuario %r", payload.username)

    return CrearUsuarioOut(
        usuario_id=nuevo.id,
        username=nuevo.username,
        rol=nuevo.rol,
        tenant_id=nuevo.tenant_id,
    )


# ============================================================
# Carga de documentos.
# ============================================================

@router.post(
    "/api/admin/documentos",
    response_model=CargarDocumentoOut,
    status_code=status.HTTP_201_CREATED,
)
async def cargar_documento(  # noqa: PLR0915 - flujo completo a proposito
    file: UploadFile = File(...),
    categoria: str = Form(...),
    destino_tenant_id: str = Form(...),
    titulo: str = Form(...),
    admin: UsuarioActual = Depends(verificar_admin),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> CargarDocumentoOut:
    """
    Recibe un PDF, lo extrae, lo segmenta y lo ingesta en ChromaDB,
    con deduplicacion por hash SHA-256.

    Pipeline completo:
      1. Validar archivo y parametros.
      2. Resolver destino (global / expediente privado).
      3. Leer el contenido en memoria y calcular SHA-256.
      4. Consultar `documentos_ingestados` por hash: si ya existe,
         abortar con HTTP 409 Conflict (Deduplicacion).
      5. Extraer texto del PDF (PyMuPDF) -> segmentar -> ingestar
         en ChromaDB con la metadata completa.
      6. Persistir el registro del hash en SQLite para que la
         proxima subida del mismo archivo sea rechazada.

    Parametros (multipart/form-data):
        file: archivo PDF.
        categoria: una de CATEGORIAS_VALIDAS ("Leyes", "Jurisprudencia",
            "Expediente"). Se guarda en la metadata de cada chunk
            para que el RAG pueda filtrar por categoria desde el chat.
        destino_tenant_id: "global" para indexar en la coleccion
            compartida `leyes_globales` (visible para todos los
            abogados). Cualquier otro valor se interpreta como el ID
            numerico de un abogado y se indexa en su coleccion privada
            `casos_user_<id>`.
        titulo: nombre del documento para metadata y logging.

    Returns:
        Cantidad de chunks indexados, la coleccion de destino y el
        hash SHA-256 del archivo.
    """
    # ---- 1) Validaciones de entrada ----
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se admiten archivos PDF",
        )
    if categoria not in CATEGORIAS_VALIDAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"categoria invalida: {categoria!r}. "
                f"Valores permitidos: {list(CATEGORIAS_VALIDAS)}"
            ),
        )

    # ---- 2) Resolucion del destino ----
    destino = (destino_tenant_id or "").strip()
    if destino == "global":
        nombre_coleccion = COLECCION_GLOBAL
        tenant_destino = 0
        destino_tipo = "global"
    else:
        if not destino.isdigit() or int(destino) <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "destino_tenant_id debe ser 'global' o el ID numerico "
                    "positivo de un abogado (ej. 4)."
                ),
            )
        abogado_id = int(destino)
        nombre_coleccion = nombre_coleccion_para_tenant(abogado_id)
        tenant_destino = abogado_id
        destino_tipo = f"abogado_{abogado_id}"

    # ---- 3) Lectura en memoria + hash SHA-256 ----
    contenido = await file.read()
    if not contenido:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo esta vacio",
        )
    hash_calculado = hashlib.sha256(contenido).hexdigest()

    # ---- 4) Deduplicacion: si el hash ya existe, abortar con 409 ----
    bd: Session = obtener_sesion_bd()
    try:
        existente = (
            bd.query(DocumentoIngestado)
            .filter(DocumentoIngestado.hash_archivo == hash_calculado)
            .first()
        )
        if existente is not None:
            LOGGER.info(
                "Deduplicacion: PDF con hash %s ya ingestado (id=%s, "
                "nombre=%r, fecha=%s). Rechazando con 409.",
                hash_calculado[:12],
                existente.id,
                existente.nombre_archivo,
                existente.fecha_ingesta.isoformat(),
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "El documento ya fue ingestado previamente "
                    f"(nombre={existente.nombre_archivo!r}, "
                    f"fecha={existente.fecha_ingesta.isoformat()})."
                ),
            )
    finally:
        bd.close()

    # ---- 5) Extraer texto -> segmentar -> ingestar en ChromaDB ----
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", delete=False, dir=tempfile.gettempdir()
    ) as tmp:
        tmp.write(contenido)
        ruta_temporal = tmp.name

    try:
        try:
            texto = extraer_texto_pdf(ruta_temporal)
        except ErrorIngestaDocumento as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No se pudo extraer texto del PDF: {exc}",
            ) from exc
    finally:
        try:
            Path(ruta_temporal).unlink(missing_ok=True)
        except OSError:
            pass

    chunks = segmentar_texto(texto)
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El PDF no produjo texto segmentable",
        )

    metadata = {
        "titulo": titulo,
        "archivo_original": file.filename,
        "categoria": categoria,
        "destino_tipo": destino_tipo,
        "tenant_id": tenant_destino,
        "subido_por": admin["usuario_id"],
        "hash_archivo": hash_calculado,
    }
    n_chunks = motor.ingestar_chunks(chunks, nombre_coleccion, metadata=metadata)

    # ---- 6) Persistir el hash en SQLite (orden: tras ingesta OK) ----
    bd = obtener_sesion_bd()
    try:
        bd.add(
            DocumentoIngestado(
                hash_archivo=hash_calculado,
                nombre_archivo=file.filename,
                categoria=categoria,
                tenant_id_destino=str(tenant_destino),
            )
        )
        bd.commit()
    except IntegrityError:
        bd.rollback()
        LOGGER.warning(
            "Deduplicacion: carrera detectada en hash %s. Abortando con 409.",
            hash_calculado[:12],
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El documento ya fue ingestado previamente.",
        ) from None
    finally:
        bd.close()

    LOGGER.info(
        "Ingesta OK: %d chunks en %s, hash=%s, categoria=%s, subido_por=%s",
        n_chunks,
        nombre_coleccion,
        hash_calculado[:12],
        categoria,
        admin["usuario_id"],
    )

    try:
        registrar_evento_admin(
            tenant_id=str(admin["tenant_id"]),
            accion="subir_documento",
            detalle=f"titulo={titulo!r} categoria={categoria} coleccion={nombre_coleccion} hash={hash_calculado[:12]}",
            actor_usuario_id=admin["usuario_id"],
        )
    except Exception:  # noqa: BLE001 - la auditoria nunca rompe el 201
        LOGGER.warning("No se pudo auditar subir_documento %r", titulo)

    return CargarDocumentoOut(
        titulo=titulo,
        nivel=destino_tipo,
        tenant_id=tenant_destino,
        chunks_indexados=n_chunks,
        coleccion_destino=nombre_coleccion,
        hash_archivo=hash_calculado,
    )
