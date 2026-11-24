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
from backend.api.esquemas import (
    CargarDocumentoOut,
    CrearUsuarioIn,
    CrearUsuarioOut,
    PermisoIn,
    PermisoOut,
)
from backend.core.seguridad import hashear_contrasena
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import (
    ROLES_VALIDOS,
    DocumentoIngestado,
    Permiso,
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
    segmentar_por_articulos,
    segmentar_texto,
)
from backend.servicios.telemetria import registrar_evento_admin
from backend.servicios.permisos import RECURSOS_VALIDOS

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
            grupo=payload.grupo,
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

    # Fase G2: si el texto trae encabezados de articulos, se indexa por
    # articulo (unidad completa + partes numeradas); si no, chunking legacy.
    unidades_articulo = segmentar_por_articulos(texto, ley=titulo)
    usar_articulos = len(unidades_articulo) > 0

    metadata = {
        "titulo": titulo,
        "archivo_original": file.filename,
        "categoria": categoria,
        "destino_tipo": destino_tipo,
        "tenant_id": tenant_destino,
        "subido_por": admin["usuario_id"],
        "hash_archivo": hash_calculado,
    }
    if usar_articulos:
        textos = [u["texto"] for u in unidades_articulo]
        metas = []
        for u in unidades_articulo:
            m = dict(metadata)
            m.update(
                {
                    "ley": titulo,
                    "articulo": u["articulo"],
                    "parte": u["parte"],
                    "total_partes": u["total_partes"],
                    "id_articulo": u["id_articulo"],
                }
            )
            metas.append(m)
        n_chunks = motor.ingestar_chunks_con_metadata(
            textos, nombre_coleccion, metadatas=metas
        )
    else:
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


# ============================================================
# Permisos documento/libro/caso <-> usuario o grupo (Fase 2).
# Solo el admin puede verlos y cambiarlos.
# ============================================================

@router.get(
    "/api/admin/permisos",
    response_model=list[PermisoOut],
)
def listar_permisos(
    admin: UsuarioActual = Depends(verificar_admin),
) -> list[PermisoOut]:
    """Lista las reglas de permiso de la firma del admin."""
    bd: Session = obtener_sesion_bd()
    try:
        filas = (
            bd.query(Permiso)
            .filter(Permiso.tenant_id == admin["tenant_id"])
            .order_by(Permiso.id)
            .all()
        )
    finally:
        bd.close()
    return [
        PermisoOut(
            permiso_id=f.id,
            tenant_id=f.tenant_id,
            usuario_id=f.usuario_id,
            grupo=f.grupo,
            recurso_tipo=f.recurso_tipo,
            recurso_id=f.recurso_id,
            puede_ver=bool(f.puede_ver),
        )
        for f in filas
    ]


@router.post(
    "/api/admin/permisos",
    response_model=PermisoOut,
    status_code=status.HTTP_201_CREATED,
)
def crear_permiso(
    payload: PermisoIn,
    admin: UsuarioActual = Depends(verificar_admin),
) -> PermisoOut:
    """
    Concede o deniega un recurso a un usuario o grupo.

    Al menos uno de `usuario_id` / `grupo` es obligatorio. Si el
    `usuario_id` apunta a un usuario de otra firma, 404.
    """
    if payload.usuario_id is None and not (payload.grupo or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica usuario_id o grupo (al menos uno).",
        )
    if payload.recurso_tipo not in RECURSOS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"recurso_tipo invalido. Valores: {list(RECURSOS_VALIDOS)}",
        )

    bd: Session = obtener_sesion_bd()
    try:
        if payload.usuario_id is not None:
            usuario = (
                bd.query(Usuario)
                .filter(
                    Usuario.id == payload.usuario_id,
                    Usuario.tenant_id == admin["tenant_id"],
                )
                .first()
            )
            if usuario is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Usuario no encontrado en tu firma.",
                )
        permiso = Permiso(
            tenant_id=admin["tenant_id"],
            usuario_id=payload.usuario_id,
            grupo=(payload.grupo or "").strip() or None,
            recurso_tipo=payload.recurso_tipo,
            recurso_id=payload.recurso_id.strip(),
            puede_ver=payload.puede_ver,
            creado_por=admin["usuario_id"],
        )
        bd.add(permiso)
        bd.commit()
        bd.refresh(permiso)
    except HTTPException:
        bd.rollback()
        raise
    except IntegrityError as exc:
        bd.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Regla de permiso duplicada.",
        ) from exc
    finally:
        bd.close()

    try:
        registrar_evento_admin(
            tenant_id=str(admin["tenant_id"]),
            accion="crear_permiso" if permiso.puede_ver else "denegar_permiso",
            detalle=(
                f"usuario_id={permiso.usuario_id} grupo={permiso.grupo!r} "
                f"{permiso.recurso_tipo}={permiso.recurso_id!r}"
            ),
            actor_usuario_id=admin["usuario_id"],
        )
    except Exception:  # noqa: BLE001 - la auditoria nunca rompe el 201
        LOGGER.warning("No se pudo auditar crear_permiso %r", payload.recurso_id)

    return PermisoOut(
        permiso_id=permiso.id,
        tenant_id=permiso.tenant_id,
        usuario_id=permiso.usuario_id,
        grupo=permiso.grupo,
        recurso_tipo=permiso.recurso_tipo,
        recurso_id=permiso.recurso_id,
        puede_ver=bool(permiso.puede_ver),
    )


@router.delete(
    "/api/admin/permisos/{permiso_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # 204 sin cuerpo (compatible FastAPI 0.115 y 0.136)
)
def borrar_permiso(
    permiso_id: int,
    admin: UsuarioActual = Depends(verificar_admin),
) -> None:
    """Borra una regla de permiso de la firma del admin."""
    bd: Session = obtener_sesion_bd()
    try:
        permiso = (
            bd.query(Permiso)
            .filter(
                Permiso.id == permiso_id,
                Permiso.tenant_id == admin["tenant_id"],
            )
            .first()
        )
        if permiso is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permiso no encontrado.",
            )
        detalle = (
            f"usuario_id={permiso.usuario_id} grupo={permiso.grupo!r} "
            f"{permiso.recurso_tipo}={permiso.recurso_id!r}"
        )
        bd.delete(permiso)
        bd.commit()
    finally:
        bd.close()

    try:
        registrar_evento_admin(
            tenant_id=str(admin["tenant_id"]),
            accion="borrar_permiso",
            detalle=detalle,
            actor_usuario_id=admin["usuario_id"],
        )
    except Exception:  # noqa: BLE001 - la auditoria nunca rompe el 204
        LOGGER.warning("No se pudo auditar borrar_permiso %s", permiso_id)


@router.get("/api/admin/permisos/matriz")
def matriz_permisos(
    admin: UsuarioActual = Depends(verificar_admin),
) -> dict:
    """
    Matriz usuario x recurso para el panel del admin: usuarios de la
    firma con su grupo y todas las reglas de la firma.
    """
    bd: Session = obtener_sesion_bd()
    try:
        usuarios = (
            bd.query(Usuario)
            .filter(Usuario.tenant_id == admin["tenant_id"])
            .order_by(Usuario.id)
            .all()
        )
        permisos = (
            bd.query(Permiso)
            .filter(Permiso.tenant_id == admin["tenant_id"])
            .order_by(Permiso.id)
            .all()
        )
    finally:
        bd.close()
    return {
        "usuarios": [
            {
                "usuario_id": u.id,
                "username": u.username,
                "rol": u.rol,
                "grupo": u.grupo,
            }
            for u in usuarios
        ],
        "permisos": [
            {
                "permiso_id": p.id,
                "usuario_id": p.usuario_id,
                "grupo": p.grupo,
                "recurso_tipo": p.recurso_tipo,
                "recurso_id": p.recurso_id,
                "puede_ver": bool(p.puede_ver),
            }
            for p in permisos
        ],
    }


# ============================================================
# Libros y documentos: lista, colecciones y borrado (Fase D/F).
# ============================================================

@router.get("/api/admin/colecciones")
def listar_colecciones(
    _admin: UsuarioActual = Depends(verificar_admin),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> dict:
    """Colecciones existentes en ChromaDB (para la matriz de permisos)."""
    try:
        nombres = sorted(c.name for c in motor._cliente.list_collections())
    except Exception:  # noqa: BLE001
        nombres = []
    return {"colecciones": nombres}


@router.get("/api/admin/documentos")
def listar_documentos(
    _admin: UsuarioActual = Depends(verificar_admin),
) -> dict:
    """Documentos ingestados (registro anti-duplicados)."""
    bd: Session = obtener_sesion_bd()
    try:
        filas = (
            bd.query(DocumentoIngestado)
            .order_by(DocumentoIngestado.id.desc())
            .limit(200)
            .all()
        )
    finally:
        bd.close()
    return {
        "items": [
            {
                "id": d.id,
                "hash": d.hash_archivo,
                "nombre": d.nombre_archivo,
                "categoria": d.categoria,
                "destino": d.tenant_id_destino,
                "fecha": d.fecha_ingesta.isoformat() if d.fecha_ingesta else "",
            }
            for d in filas
        ]
    }


@router.delete(
    "/api/admin/documentos/{doc_id}", status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # 204 sin cuerpo (compatible FastAPI 0.115 y 0.136)
)
def borrar_documento(
    doc_id: int,
    admin: UsuarioActual = Depends(verificar_admin),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> None:
    """Borra el registro y todos sus chunks en ChromaDB (por hash)."""
    bd: Session = obtener_sesion_bd()
    try:
        doc = (
            bd.query(DocumentoIngestado).filter(DocumentoIngestado.id == doc_id).first()
        )
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Documento no encontrado.",
            )
        hash_borrar = doc.hash_archivo
        nombre = doc.nombre_archivo
        bd.delete(doc)
        bd.commit()
    finally:
        bd.close()

    # Borra los vectores con ese hash en todas las colecciones.
    try:
        for c in motor._cliente.list_collections():
            col = motor._cliente.get_collection(
                name=c.name, embedding_function=motor._ef
            )
            try:
                col.delete(where={"hash_archivo": hash_borrar})
            except Exception:  # noqa: BLE001 - coleccion sin ese campo
                continue
    except Exception:  # noqa: BLE001 - Chroma no disponible
        pass

    try:
        registrar_evento_admin(
            tenant_id=str(admin["tenant_id"]),
            accion="borrar_documento",
            detalle=f"nombre={nombre!r} hash={hash_borrar[:12]}",
            actor_usuario_id=admin["usuario_id"],
        )
    except Exception:  # noqa: BLE001
        LOGGER.warning("No se pudo auditar borrar_documento %s", doc_id)
