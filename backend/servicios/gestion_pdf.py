"""
Servicio de ingesta documental: extraccion de texto y chunking.

Soportamos dos formatos:
  - PDF nativo (texto seleccionable, sin OCR pesado).
  - Word (.docx) con extraccion a nivel de parrafo.

Decisiones de diseno:
  - Se prioriza bajo consumo de RAM: NO se carga el modelo de OCR.
    Si un PDF viene escaneado, se rechaza con un error claro.
  - El chunking es por caracteres con solapamiento (overlap) configurable,
    para no perder contexto legal entre cortes. En el futuro se mejorara
    a chunking por fronteras de oracion o parrafo.
  - Excel (.xlsx) queda EXCLUIDO por integridad estructural: requiere
    procesamiento tabular (cabeceras, columnas, normalizacion) que no
    encaja con el chunking por caracteres del RAG.

Las funciones son PURAS: leen el archivo, devuelven string o list[str],
no mantienen estado y no tocan disco fuera del parametro recibido.
"""

from __future__ import annotations

from pathlib import Path

# ============================================================
# Excepciones de dominio.
# ============================================================


class ErrorIngestaDocumento(ValueError):
    """Error de entrada al procesar un documento (ruta vacia, formato
    no soportado, archivo danado, etc.)."""


# ============================================================
# Extraccion de texto.
# ============================================================


def extraer_texto_pdf(ruta_archivo: str) -> str:
    """
    Lee un PDF nativo y devuelve el texto concatenado de todas las paginas.

    Usa PyMuPDF (`fitz`), que es ~10x mas rapido que pypdf y maneja mejor
    PDFs legales con tablas y notas al pie.

    Raises:
        ErrorIngestaDocumento: si la ruta esta vacia, el archivo no existe
            o el PDF no se puede abrir.
    """
    if not ruta_archivo or not ruta_archivo.strip():
        raise ErrorIngestaDocumento("La ruta del archivo es obligatoria")

    ruta = Path(ruta_archivo)
    if not ruta.exists():
        raise ErrorIngestaDocumento(f"No existe el archivo: {ruta_archivo}")
    if ruta.stat().st_size == 0:
        raise ErrorIngestaDocumento(f"El archivo esta vacio: {ruta_archivo}")

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF (fitz) no esta instalado. Agregalo a requerimientos.txt"
        ) from exc

    try:
        documento = fitz.open(ruta_archivo)
    except Exception as exc:  # noqa: BLE001 - fitz lanza multiples tipos
        raise ErrorIngestaDocumento(
            f"No se pudo abrir el PDF (puede estar danado o protegido): {exc}"
        ) from exc

    try:
        paginas: list[str] = []
        for pagina in documento:
            texto = pagina.get_text("text") or ""
            if texto:
                paginas.append(texto)
    finally:
        documento.close()

    if not paginas:
        # Si el PDF esta escaneado (sin texto seleccionable), advertimos
        # al caller: este modulo NO hace OCR por ahorro de RAM.
        raise ErrorIngestaDocumento(
            "El PDF no contiene texto seleccionable. Es un PDF escaneado: "
            "ejecutar OCR externamente antes de ingestar."
        )

    return "\n".join(paginas)


def extraer_texto_docx(ruta_archivo: str) -> str:
    """
    Lee un archivo Word (.docx) y devuelve sus parrafos concatenados.

    Implementacion: abre el documento con `python-docx`, itera sobre
    los bloques a nivel de parrafo (incluye listas y tablas simples)
    y los une con saltos de linea.

    Raises:
        ErrorIngestaDocumento: si la ruta esta vacia, el archivo no existe
            o el docx no se puede abrir.
    """
    if not ruta_archivo or not ruta_archivo.strip():
        raise ErrorIngestaDocumento("La ruta del archivo es obligatoria")

    ruta = Path(ruta_archivo)
    if not ruta.exists():
        raise ErrorIngestaDocumento(f"No existe el archivo: {ruta_archivo}")
    if ruta.stat().st_size == 0:
        raise ErrorIngestaDocumento(f"El archivo esta vacio: {ruta_archivo}")
    if ruta.suffix.lower() != ".docx":
        raise ErrorIngestaDocumento(
            f"Extension no soportada para DOCX: {ruta.suffix} "
            "(se esperaba .docx)"
        )

    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError(
            "python-docx no esta instalado. Agregalo a requerimientos.txt"
        ) from exc

    try:
        documento = Document(ruta_archivo)
    except Exception as exc:  # noqa: BLE001 - python-docx lanza PackagiError, etc.
        raise ErrorIngestaDocumento(
            f"No se pudo abrir el DOCX (puede estar danado): {exc}"
        ) from exc

    parrafos: list[str] = []
    for parrafo in documento.paragraphs:
        texto = (parrafo.text or "").strip()
        if texto:
            parrafos.append(texto)

    # Incluimos el texto de las tablas como lineas adicionales, preservando
    # el orden aproximado (tabla tras los parrafos anteriores).
    for tabla in documento.tables:
        for fila in tabla.rows:
            for celda in fila.cells:
                texto = (celda.text or "").strip()
                if texto:
                    parrafos.append(texto)

    if not parrafos:
        raise ErrorIngestaDocumento(
            "El DOCX no contiene texto extraible (puede ser solo imagenes)."
        )

    return "\n".join(parrafos)


# ============================================================
# Segmentacion (chunking) por caracteres con solapamiento.
# ============================================================


def segmentar_texto(
    texto: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> list[str]:
    """
    Divide un texto largo en chunks solapados de aproximadamente
    `chunk_size` caracteres.

    Cada chunk (salvo el ultimo) contiene `chunk_size` caracteres.
    El siguiente chunk arranca `chunk_size - overlap` posiciones mas
    adelante, lo que produce un solapamiento efectivo de `overlap`
    caracteres entre chunks consecutivos. Asi se preserva contexto
    legal entre cortes: si un chunk termina hablando del articulo 54
    y el siguiente empieza con "dicho articulo establece que...", el
    solapamiento garantiza que ese "dicho articulo" siga presente en
    el primer chunk.

    Parametros:
        texto: texto completo a segmentar.
        chunk_size: tamano maximo de cada chunk en caracteres.
        overlap: numero de caracteres compartidos entre chunks.

    Retorna:
        Lista ordenada de strings (uno por chunk).

    Raises:
        ValueError: si `chunk_size` no es positivo o `overlap` no esta
            en el rango [0, chunk_size).
    """
    if not texto:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size debe ser positivo")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap debe estar en [0, chunk_size)")

    chunks: list[str] = []
    paso = chunk_size - overlap
    posicion = 0
    longitud = len(texto)
    while posicion < longitud:
        fin = min(posicion + chunk_size, longitud)
        chunks.append(texto[posicion:fin])
        if fin == longitud:
            break
        posicion += paso
    return chunks


__all__ = [
    "ErrorIngestaDocumento",
    "extraer_texto_pdf",
    "extraer_texto_docx",
    "segmentar_texto",
]
