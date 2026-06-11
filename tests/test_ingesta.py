"""
Tests TDD del modulo de ingesta documental.

Cubre:
  - `segmentar_texto`: lista de strings, solapamiento, validacion, casos limite.
  - `extraer_texto_docx`: roundtrip con un DOCX generado en `tmp_path`.
  - `extraer_texto_pdf`: roundtrip con un PDF generado en `tmp_path`.
  - Errores de entrada (ruta vacia, archivo inexistente, formato incorrecto).
"""

from __future__ import annotations

import pytest

from backend.servicios.gestion_pdf import (
    ErrorIngestaDocumento,
    extraer_texto_docx,
    extraer_texto_pdf,
    segmentar_texto,
)


# ============================================================
# segmentar_texto: contrato basico.
# ============================================================

def test_segmentar_texto_devuelve_lista_de_strings() -> None:
    """Un texto largo produce una lista no vacia de strings."""
    texto_largo = "Lorem ipsum dolor sit amet. " * 500  # ~14.000 chars
    chunks = segmentar_texto(texto_largo)

    assert isinstance(chunks, list)
    assert len(chunks) > 1
    assert all(isinstance(chunk, str) for chunk in chunks)
    # El primer chunk arranca en el primer caracter y el ultimo
    # chunk termina en el ultimo caracter (no hay gap al inicio ni al final).
    assert chunks[0] == texto_largo[: len(chunks[0])]
    assert chunks[-1] == texto_largo[-len(chunks[-1]) :]


def test_segmentar_texto_con_defaults_chunk_size_1000_overlap_200() -> None:
    """Verifica los parametros por defecto exigidos por la especificacion."""
    texto = "x" * 3000
    chunks = segmentar_texto(texto)
    # Con chunk_size=1000 y overlap=200: paso=800.
    # Posiciones: [0, 800, 1600, 2400], fin en 3000.
    # Chunks en: 0-1000, 800-1800, 1600-2600, 2400-3000.
    assert len(chunks) == 4
    assert len(chunks[0]) == 1000
    assert len(chunks[1]) == 1000
    assert len(chunks[2]) == 1000
    assert len(chunks[3]) == 600  # ultimo chunk truncado: 3000 - 2400


def test_segmentar_texto_texto_vacio_devuelve_lista_vacia() -> None:
    assert segmentar_texto("") == []


# ============================================================
# segmentar_texto: solapamiento (TDD - test clave).
# ============================================================

def test_segmentar_texto_solapamiento_coincide_entre_chunks() -> None:
    """
    El final de un chunk debe coincidir EXACTAMENTE con el inicio
    del siguiente chunk en `overlap` caracteres.

    Esto es lo que evita que se pierda contexto legal al cortar:
    si el chunk N termina con "...articulo 54 establece" y el chunk
    N+1 empieza con "articulo 54 establece que...", los ultimos
    `overlap` caracteres del N son los primeros `overlap` del N+1.
    """
    texto = "abcdefghijklmnopqrstuvwxyz" * 50  # 1300 chars repetidos
    chunk_size = 100
    overlap = 30

    chunks = segmentar_texto(texto, chunk_size=chunk_size, overlap=overlap)
    assert len(chunks) >= 2

    for i in range(len(chunks) - 1):
        cola_chunk_actual = chunks[i][-overlap:]
        cabeza_chunk_siguiente = chunks[i + 1][:overlap]
        assert cola_chunk_actual == cabeza_chunk_siguiente, (
            f"Solapamiento incorrecto entre chunks {i} y {i + 1}: "
            f"final={cola_chunk_actual!r} vs inicio={cabeza_chunk_siguiente!r}"
        )


def test_segmentar_texto_posiciones_conocidas() -> None:
    """
    Test determinista: conocemos las posiciones exactas y podemos
    asertar el contenido de cada chunk.
    """
    texto = "0123456789" * 10  # 100 chars, patron repetido
    chunks = segmentar_texto(texto, chunk_size=20, overlap=5)

    # Paso = 15. Chunks esperados en posiciones: 0, 15, 30, 45, 60, 75, 90.
    assert len(chunks) == 7
    assert chunks[0] == texto[0:20]   # 0-19
    assert chunks[1] == texto[15:35]  # 15-34
    assert chunks[2] == texto[30:50]
    assert chunks[3] == texto[45:65]
    assert chunks[4] == texto[60:80]
    assert chunks[5] == texto[75:95]
    assert chunks[6] == texto[90:100]  # ultimo, mas corto


# ============================================================
# segmentar_texto: validacion de parametros.
# ============================================================

def test_segmentar_texto_chunk_size_cero_lanza_error() -> None:
    with pytest.raises(ValueError):
        segmentar_texto("algo", chunk_size=0, overlap=0)


def test_segmentar_texto_overlap_mayor_o_igual_a_chunk_size_lanza_error() -> None:
    with pytest.raises(ValueError):
        segmentar_texto("algo", chunk_size=10, overlap=10)
    with pytest.raises(ValueError):
        segmentar_texto("algo", chunk_size=10, overlap=20)


def test_segmentar_texto_overlap_negativo_lanza_error() -> None:
    with pytest.raises(ValueError):
        segmentar_texto("algo", chunk_size=10, overlap=-1)


def test_segmentar_texto_overlap_cero_produce_chunks_disjuntos() -> None:
    """Sin overlap, los chunks no comparten caracteres."""
    texto = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 chars
    chunks = segmentar_texto(texto, chunk_size=10, overlap=0)

    assert len(chunks) == 4
    assert chunks[0] == texto[0:10]
    assert chunks[1] == texto[10:20]
    assert chunks[2] == texto[20:30]
    assert chunks[3] == texto[30:36]


# ============================================================
# extraer_texto_pdf.
# ============================================================

def _crear_pdf_de_prueba(ruta, texto: str) -> None:
    """Helper: crea un PDF nativo de una pagina con el texto dado."""
    import fitz

    documento = fitz.open()
    pagina = documento.new_page(width=595, height=842)  # A4 aprox.
    pagina.insert_text((72, 72), texto)
    documento.save(ruta)
    documento.close()


def test_extraer_texto_pdf_roundtrip(tmp_path) -> None:
    ruta = tmp_path / "contrato.pdf"
    contenido = "Este es un contrato de prueba entre las partes."
    _crear_pdf_de_prueba(ruta, contenido)

    texto_extraido = extraer_texto_pdf(str(ruta))
    assert "contrato de prueba" in texto_extraido


def test_extraer_texto_pdf_ruta_vacia_lanza_error() -> None:
    with pytest.raises(ErrorIngestaDocumento):
        extraer_texto_pdf("")


def test_extraer_texto_pdf_archivo_inexistente_lanza_error(tmp_path) -> None:
    ruta_inexistente = tmp_path / "no_existe.pdf"
    with pytest.raises(ErrorIngestaDocumento):
        extraer_texto_pdf(str(ruta_inexistente))


def test_extraer_texto_pdf_archivo_vacio_lanza_error(tmp_path) -> None:
    ruta_vacia = tmp_path / "vacio.pdf"
    ruta_vacia.write_bytes(b"")
    with pytest.raises(ErrorIngestaDocumento):
        extraer_texto_pdf(str(ruta_vacia))


def test_extraer_texto_pdf_pdf_sin_texto_seleccionable_advierte(tmp_path) -> None:
    """
    PDFs escaneados no tienen texto seleccionable. Como esta version
    NO hace OCR (por ahorro de RAM), debe lanzar error claro.
    """
    import fitz

    ruta = tmp_path / "escaneado.pdf"
    # Creamos un PDF con una pagina en blanco (sin texto).
    documento = fitz.open()
    documento.new_page(width=595, height=842)
    documento.save(ruta)
    documento.close()

    with pytest.raises(ErrorIngestaDocumento) as exc_info:
        extraer_texto_pdf(str(ruta))
    assert "OCR" in str(exc_info.value) or "escaneado" in str(exc_info.value)


# ============================================================
# extraer_texto_docx.
# ============================================================

def _crear_docx_de_prueba(ruta, parrafos: list[str]) -> None:
    """Helper: crea un DOCX con los parrafos dados."""
    from docx import Document

    documento = Document()
    for parrafo in parrafos:
        documento.add_paragraph(parrafo)
    documento.save(ruta)


def test_extraer_texto_docx_roundtrip(tmp_path) -> None:
    ruta = tmp_path / "demanda.docx"
    parrafos = [
        "En la ciudad de Madrid, a 15 de marzo de 2026.",
        "El demandante Juan Perez interpone demanda de despido.",
    ]
    _crear_docx_de_prueba(ruta, parrafos)

    texto = extraer_texto_docx(str(ruta))
    assert "Madrid" in texto
    assert "demanda de despido" in texto
    # Los parrafos deben estar separados por saltos de linea.
    assert "\n" in texto


def test_extraer_texto_docx_ruta_vacia_lanza_error() -> None:
    with pytest.raises(ErrorIngestaDocumento):
        extraer_texto_docx("")


def test_extraer_texto_docx_archivo_inexistente_lanza_error(tmp_path) -> None:
    ruta = tmp_path / "no_existe.docx"
    with pytest.raises(ErrorIngestaDocumento):
        extraer_texto_docx(str(ruta))


def test_extraer_texto_docx_extension_incorrecta_lanza_error(tmp_path) -> None:
    """Si la extension no es .docx, rechazamos antes de parsear."""
    ruta = tmp_path / "no_es_docx.txt"
    ruta.write_text("hola", encoding="utf-8")
    with pytest.raises(ErrorIngestaDocumento):
        extraer_texto_docx(str(ruta))


def test_extraer_texto_docx_con_tabla(tmp_path) -> None:
    """Verifica que se extrae tambien el contenido de tablas."""
    from docx import Document

    ruta = tmp_path / "con_tabla.docx"
    doc = Document()
    doc.add_paragraph("Cabecera del documento")
    tabla = doc.add_table(rows=2, cols=2)
    tabla.cell(0, 0).text = "Concepto"
    tabla.cell(0, 1).text = "Importe"
    tabla.cell(1, 0).text = "Honorarios"
    tabla.cell(1, 1).text = "1500 EUR"
    doc.save(ruta)

    texto = extraer_texto_docx(str(ruta))
    assert "Cabecera del documento" in texto
    assert "Honorarios" in texto
    assert "1500 EUR" in texto
