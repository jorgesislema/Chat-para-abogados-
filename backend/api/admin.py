"""
Endpoints administrativos: carga de expedientes (PDFs) y consulta de estado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from backend.api.dependencias import UsuarioActual, obtener_usuario_actual
from backend.api.esquemas import SubirExpedienteOut
from backend.servicios.gestion_pdf import extraer_texto_pdf, segmentar_texto

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/expedientes",
    response_model=SubirExpedienteOut,
    status_code=status.HTTP_201_CREATED,
)
def subir_expediente(
    archivo: UploadFile,
    titulo: str,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> SubirExpedienteOut:
    """
    Recibe un PDF, lo fragmenta y devuelve el numero de chunks a indexar.

    La indexacion en ChromaDB se delega a una tarea async posterior
    (fuera del alcance de este endpoint para mantenerlo sincrono
    y simple de testear).
    """
    if not archivo.filename or not archivo.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se admiten archivos PDF",
        )

    contenido = archivo.file.read()
    if not contenido:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo esta vacio",
        )

    ruta_temporal = f"./tmp_{usuario_actual['tenant_id']}_{archivo.filename}"
    with open(ruta_temporal, "wb") as destino:
        destino.write(contenido)

    try:
        texto = extraer_texto_pdf(ruta_temporal)
    except Exception as exc:  # noqa: BLE001 - controlamos en el endpoint
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No se pudo extraer texto del PDF: {exc}",
        ) from exc
    finally:
        try:
            import os

            os.remove(ruta_temporal)
        except OSError:
            pass

    chunks = segmentar_texto(texto)
    return SubirExpedienteOut(
        expediente_id=0,  # se asignara al persistir en BD
        titulo=titulo,
        chunks_indexados=len(chunks),
    )
