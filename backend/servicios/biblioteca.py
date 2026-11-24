"""
Servicio de Biblioteca: texto completo de leyes y sentencias + indice de articulos.

Funciones puras (indexar, reconstruir, buscar) y una funcion de acceso a BD
que guarda el texto la primera vez que se necesita.
"""

from __future__ import annotations

import json
import re
from typing import Any

# "Art. 6.-", "Art 54", "Artículo 195.1.-", "ARTICULO 12". Solo al inicio de linea.
_ENCABEZADO = re.compile(
    r"(?im)^[ \t]*((?:art(?:[ií]culo)?)\.?[ \t]*(\d{1,4}(?:\.\d{1,2})?))[ \t]*\.?[ \t]*-?"
)
_CONSULTA_ARTICULO = re.compile(
    r"^\s*(?:art(?:[ií]culo)?\.?\s*)?(\d{1,4}(?:\.\d{1,2})?)\s*\.?\s*$", re.IGNORECASE
)

# Los documentos viejos se cortaron con chunk_size=1000 y overlap=200.
_SOLAPE_LEGADO = 200


def indexar_articulos(texto: str) -> list[dict[str, Any]]:
    """Posicion de cada encabezado de articulo en el texto."""
    salida: list[dict[str, Any]] = []
    for m in _ENCABEZADO.finditer(texto or ""):
        inicio = m.start(1)
        salida.append(
            {"etiqueta": re.sub(r"\s+", " ", m.group(1)).strip(), "numero": m.group(2), "pos": inicio}
        )
    return salida


def consulta_a_numero(consulta: str) -> str | None:
    """'art 6', 'Artículo 6', '6' -> '6'. Cualquier otra cosa -> None."""
    m = _CONSULTA_ARTICULO.match(consulta or "")
    return m.group(1) if m else None


def reconstruir_texto(fragmentos: list[tuple[str, dict[str, Any]]]) -> str:
    """
    Une los pedazos guardados en el vector store en un solo texto.

    - Pedazos por articulo (nuevos): se ordenan por (articulo, parte) y se unen
      con linea en blanco.
    - Pedazos por caracteres (viejos): se ordenan por `indice` y se quita el
      solape de 200 caracteres entre pedazos vecinos.
    """
    if not fragmentos:
        return ""
    por_articulo = any("articulo" in (md or {}) for _, md in fragmentos)
    if por_articulo:
        ordenados = sorted(
            fragmentos,
            key=lambda f: (int((f[1] or {}).get("articulo", 0) or 0), int((f[1] or {}).get("parte", 1) or 1)),
        )
        return "\n\n".join(t.strip() for t, _ in ordenados if t and t.strip())
    ordenados = sorted(fragmentos, key=lambda f: int((f[1] or {}).get("indice", 0) or 0))
    partes: list[str] = []
    for i, (t, _) in enumerate(ordenados):
        partes.append(t if i == 0 else t[_SOLAPE_LEGADO:])
    return "".join(partes)


def buscar_en_texto(texto: str, consulta: str, limite: int = 3, ancho: int = 90) -> list[dict[str, Any]]:
    """Coincidencias literales (sin mayusculas ni tildes exactas) con un trozo alrededor."""
    q = (consulta or "").strip()
    if len(q) < 2:
        return []
    bajo, qb = texto.lower(), q.lower()
    salida: list[dict[str, Any]] = []
    desde = 0
    while len(salida) < limite:
        i = bajo.find(qb, desde)
        if i < 0:
            break
        a, b = max(0, i - ancho), min(len(texto), i + len(q) + ancho)
        salida.append({"pos": i, "fragmento": re.sub(r"\s+", " ", texto[a:b]).strip()})
        desde = i + len(q)
    return salida


def guardar_texto(bd, hash_archivo: str, titulo: str, texto: str, categoria: str = "") -> None:
    """Inserta o actualiza el texto completo (y la ficha) de un documento."""
    from backend.db.modelos import FichaDocumento, TextoDocumento

    fila = bd.query(TextoDocumento).filter(TextoDocumento.hash_archivo == hash_archivo).first()
    articulos = json.dumps(indexar_articulos(texto), ensure_ascii=False)
    if fila is None:
        bd.add(TextoDocumento(hash_archivo=hash_archivo, titulo=titulo, texto=texto, articulos_json=articulos))
    else:
        fila.texto, fila.articulos_json = texto, articulos
        if titulo and not fila.titulo:
            fila.titulo = titulo
    if bd.query(FichaDocumento).filter(FichaDocumento.hash_archivo == hash_archivo).first() is None:
        f = extraer_ficha(texto, categoria)
        bd.add(FichaDocumento(hash_archivo=hash_archivo, **f))
    bd.commit()


# ---------------- Ficha: tribunal, anio, materia, numero de caso ----------------

_TRIBUNALES = [
    (r"corte\s+constitucional", "Corte Constitucional"),
    (r"corte\s+nacional\s+de\s+justicia", "Corte Nacional de Justicia"),
    (r"corte\s+provincial", "Corte Provincial"),
    (r"tribunal\s+contencioso\s+electoral", "Tribunal Contencioso Electoral"),
    (r"tribunal\s+de\s+lo\s+contencioso\s+administrativo|tribunal\s+contencioso\s+administrativo", "Tribunal Contencioso Administrativo"),
    (r"unidad\s+judicial", "Unidad Judicial"),
    (r"consejo\s+de\s+la\s+judicatura", "Consejo de la Judicatura"),
]
_MATERIAS_CLAVE = [
    ("laboral", ("laboral", "trabajador", "empleador", "despido", "remuneraci")),
    ("penal", ("penal", "delito", "pena privativa", "fiscal")),
    ("constitucional", ("garant", "acci\u00f3n extraordinaria", "acci\u00f3n de protecci\u00f3n", "constituci\u00f3n")),
    ("civil", ("civil", "contrato", "obligaci", "propiedad")),
    ("familia", ("familia", "divorcio", "alimentos", "tenencia")),
    ("tr\u00e1nsito", ("tr\u00e1nsito", "conductor", "veh\u00edculo")),
    ("tributario", ("tributari", "impuesto", "sri")),
    ("administrativo", ("administrativ", "servidor p\u00fablico", "servicio p\u00fablico")),
]
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_FECHA = re.compile(rf"\d{{1,2}}\s+de\s+(?:{_MESES})\s+de\s+((?:19|20)\d{{2}})", re.IGNORECASE)
_CASO = re.compile(r"(?:caso|proceso|juicio)\s*(?:n[o\u00ba\u00b0]\.?|n\u00famero)?\s*[:.]?\s*([0-9]{2,6}[-/][\w/.-]{2,20})", re.IGNORECASE)
_SENTENCIA = re.compile(r"sentencia\s*(?:n[o\u00ba\u00b0]\.?)\s*([0-9]{2,6}-[\w/.-]{2,20})", re.IGNORECASE)
_TEMA = re.compile(r"tema\s*:\s*(.{20,600}?)(?:\n\s*\n|\n\s*\d+\.\s)", re.IGNORECASE | re.DOTALL)


def extraer_ficha(texto: str, categoria: str = "") -> dict[str, Any]:
    """Saca tribunal, anio, materia, numero de caso y tema de las primeras paginas.

    Las leyes (categoria "Leyes") no tienen tribunal ni anio propios: la primera
    fecha del texto suele ser una reforma, no su fecha. Solo se saca la materia.
    """
    es_ley = categoria == "Leyes"
    inicio = (texto or "")[:9000]
    bajo = inicio.lower()
    tribunal = ""
    for patron, nombre in _TRIBUNALES:
        if not es_ley and re.search(patron, bajo):
            tribunal = nombre
            break
    anio = None
    m = None if es_ley else _FECHA.search(inicio)
    if m:
        anio = int(m.group(1))
    materia, mejor = "", 0
    for nombre, claves in _MATERIAS_CLAVE:
        n = sum(bajo.count(c) for c in claves)
        if n > mejor:
            materia, mejor = nombre, n
    caso = _CASO.search(inicio) or _SENTENCIA.search(inicio)
    tema = _TEMA.search(inicio)
    return {
        "tribunal": tribunal,
        "anio": anio,
        "materia": materia if mejor >= 2 else "",
        "numero_proceso": (caso.group(1).strip(" .") if caso else "")[:100],
        "tema": re.sub(r"\s+", " ", tema.group(1)).strip()[:600] if tema else "",
    }
