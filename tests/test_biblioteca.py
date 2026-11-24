"""Pruebas de la Biblioteca: indice de articulos, reconstruccion y busqueda."""

from __future__ import annotations

from backend.servicios.biblioteca import (
    buscar_en_texto,
    consulta_a_numero,
    indexar_articulos,
    reconstruir_texto,
)
from backend.servicios.gestion_pdf import segmentar_texto

LEY = (
    "Art. 6.- Todas las ecuatorianas son ciudadanas.\n\n"
    "Art. 7.- Son ecuatorianas por nacimiento:\n1. Las nacidas en el Ecuador.\n\n"
    "Artículo 195.1.- Despido ineficaz.\nTexto largo.\n"
    "Art. 8.- Por naturalizacion.\n"
)


def test_indexa_articulos_con_posicion_exacta() -> None:
    idx = indexar_articulos(LEY)
    assert [a["numero"] for a in idx] == ["6", "7", "195.1", "8"]
    for a in idx:
        assert LEY[a["pos"]:].lower().startswith("art")


def test_consulta_a_numero() -> None:
    assert consulta_a_numero("art 6") == "6"
    assert consulta_a_numero("Artículo 195.1") == "195.1"
    assert consulta_a_numero("  54 ") == "54"
    assert consulta_a_numero("despido intempestivo") is None


def test_reconstruye_texto_legado_sin_perder_ni_duplicar() -> None:
    original = "".join(f"Frase numero {i}. " for i in range(400))
    trozos = segmentar_texto(original)  # 1000 / 200
    # Se mezclan para comprobar que se ordena por `indice`.
    fragmentos = [(t, {"indice": i}) for i, t in enumerate(trozos)][::-1]
    assert reconstruir_texto(fragmentos) == original


def test_reconstruye_por_articulo_en_orden() -> None:
    frag = [
        ("Art. 2.- B", {"articulo": 2, "parte": 1}),
        ("Art. 1.- A parte 2", {"articulo": 1, "parte": 2}),
        ("Art. 1.- A parte 1", {"articulo": 1, "parte": 1}),
    ]
    assert reconstruir_texto(frag).splitlines()[0] == "Art. 1.- A parte 1"


def test_busca_texto_con_contexto() -> None:
    r = buscar_en_texto(LEY, "DESPIDO", limite=2)
    assert len(r) == 1 and "Despido ineficaz" in r[0]["fragmento"]
    assert buscar_en_texto(LEY, "x") == []


SENTENCIA = (
    "Sentencia No. 2435-17-EP/23\n\nQuito, D.M., 15 de marzo de 2023\n\nCASO No. 2435-17-EP\n\n"
    "EL PLENO DE LA CORTE CONSTITUCIONAL DEL ECUADOR\n\n"
    "Tema: La Corte analiza si la sentencia de la Sala de lo Laboral vulneró el debido proceso "
    "del trabajador despedido.\n\n1. Antecedentes\n"
)


def test_extrae_ficha_de_sentencia() -> None:
    from backend.servicios.biblioteca import extraer_ficha

    f = extraer_ficha(SENTENCIA)
    assert f["tribunal"] == "Corte Constitucional"
    assert f["anio"] == 2023
    assert f["numero_proceso"] == "2435-17-EP"
    assert f["materia"] == "laboral"
    assert f["tema"].startswith("La Corte analiza")


def test_ficha_vacia_si_no_hay_datos() -> None:
    from backend.servicios.biblioteca import extraer_ficha

    f = extraer_ficha("Texto cualquiera sin datos.")
    assert f["tribunal"] == "" and f["anio"] is None and f["numero_proceso"] == ""
