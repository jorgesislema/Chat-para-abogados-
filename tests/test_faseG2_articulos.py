"""
Tests Fase G2: articulos completos y literales.

- Corte por articulo (deteccion, partes numeradas, sin cortar frases).
- Articulo completo ordenado aunque se matchee una parte.
- "Dame el articulo N de la ley X" devuelve ESE articulo (15 casos).
- Continuacion automatica ante finish_reason == length.
- Verificacion anti-invento (sustituye por el original guardado).
- Ambiguedad de ley -> pide aclaracion sin LLM.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import chromadb
from chromadb.config import Settings
from fastapi.testclient import TestClient

from backend.api.dependencias import (
    _reset_singletons_for_tests,
    obtener_motor_vectorial,
)
from backend.db.base import Base
from backend.db.conexion import engine
from backend.db.vectorial import COLECCION_GLOBAL, MotorVectorial
from backend.main import aplicacion
from backend.servicios.gestion_pdf import segmentar_por_articulos
from backend.servicios.rag_motor import OrquestadorRAG

INCISOS = " ".join(
    f"Inciso {i}: el trabajador tiene derecho a la parte proporcional "
    f"correspondiente al periodo laborado, sin excepcion alguna. " * 4
    for i in range(1, 9)
)
ART_54 = (
    "Art. 54.- De la jornada maxima: La jornada ordinaria de trabajo "
    "sera de ocho horas diarias. " + INCISOS
)
TEXTO_LEY = (
    "Art. 1.- Ambito: esta ley ficticia regula la jornada.\n\n"
    + ART_54
    + "\n\nArt. 2.- Cierre: disposicion final ficticia de dos lineas.\n"
)


@pytest.fixture()
def motor(embedding_function_compartido) -> MotorVectorial:
    cliente = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
    cliente.reset()
    return MotorVectorial(
        cliente=cliente, embedding_function=embedding_function_compartido
    )


def _mock_llm(respuestas: list[tuple[str, str]]) -> MagicMock:
    """Respuestas (texto, finish_reason) en orden de llamada."""
    mocks = []
    for texto, finish in respuestas:
        r = MagicMock()
        r.choices = [MagicMock()]
        r.choices[0].message.content = texto
        r.choices[0].finish_reason = finish
        r.usage = None
        mocks.append(r)
    mock = MagicMock()
    mock.chat.completions.create = AsyncMock(side_effect=mocks)
    return mock


def _orquestador(motor: MotorVectorial, mock: MagicMock) -> OrquestadorRAG:
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "backend.servicios.rag_motor.AsyncOpenAI", return_value=mock
    ):
        return OrquestadorRAG(motor_vectorial=motor, cliente_llm=mock)


# ---------- corte por articulo ----------

def test_segmentar_detecta_articulos_y_numera_partes() -> None:
    assert len(ART_54) > 3000
    unidades = segmentar_por_articulos(TEXTO_LEY, ley="Codigo Demo")
    por_art: dict[int, list] = {}
    for u in unidades:
        por_art.setdefault(u["articulo"], []).append(u)
    assert set(por_art) == {1, 54, 2}
    partes_54 = sorted(por_art[54], key=lambda u: u["parte"])
    assert len(partes_54) >= 2
    assert partes_54[0]["total_partes"] == len(partes_54)
    assert all(p["id_articulo"] == "codigo-demo:art-54" for p in partes_54)
    # Nunca a media frase: cada parte (salvo resto) termina en signo.
    for p in partes_54:
        assert p["texto"][-1] in ".!?"


def test_sin_encabezados_retorna_vacio_para_fallback() -> None:
    assert segmentar_por_articulos("Texto corrido sin articulos. " * 50) == []


def test_obtener_articulo_completo_y_ordenado(motor: MotorVectorial) -> None:
    unidades = segmentar_por_articulos(TEXTO_LEY, ley="Codigo Demo")
    metas = [
        {"titulo": "Codigo Demo", "categoria": "Leyes", "hash_archivo": "h1",
         "ley": "Codigo Demo", "articulo": u["articulo"], "parte": u["parte"],
         "total_partes": u["total_partes"], "id_articulo": u["id_articulo"]}
        for u in unidades
    ]
    motor.ingestar_chunks_con_metadata(
        [u["texto"] for u in unidades], COLECCION_GLOBAL, metas
    )
    texto, meta = motor.obtener_articulo("codigo-demo:art-54", [COLECCION_GLOBAL])
    norm = lambda s: " ".join(s.split())
    assert norm(texto) == norm(ART_54)  # primera a ultima palabra, en orden
    # Orden real: los incisos aparecen en secuencia creciente.
    posiciones = [texto.index(f"Inciso {i}:") for i in range(1, 9)]
    assert posiciones == sorted(posiciones)
    assert meta["total_partes"] == len([u for u in unidades if u["articulo"] == 54])


# ---------- cita literal exacta ----------

@pytest.mark.asyncio()
async def test_cita_literal_sustituye_parafrasis(motor: MotorVectorial) -> None:
    original = (
        "Art. 7.- De las vacaciones: todo trabajador gozara de quince dias "
        "de descanso anual remunerado, mas un dia adicional por cada ano "
        "posterior al quinto, hasta treinta dias como maximo."
    )
    motor.ingestar_chunks(
        [original], COLECCION_GLOBAL,
        metadata={"titulo": "Codigo de Trabajo de demostracion",
                  "categoria": "Leyes", "hash_archivo": "h7",
                  "ley": "Codigo de Trabajo de demostracion",
                  "articulo": 7, "parte": 1, "total_partes": 1,
                  "id_articulo": "codigo-de-trabajo-de-demostraci:art-7"},
    )
    mock = _mock_llm([("En resumen, las vacaciones son de unos dias.", "stop")])
    orquestador = _orquestador(motor, mock)
    r = await orquestador.consultar(
        "Dame el articulo 7 del Codigo de Trabajo de demostracion",
        tenant_id=1,
    )
    assert r["cita_literal"] is True
    assert original in r["respuesta"]  # literal, no el resumen del mock
    assert "unos dias" not in r["respuesta"]
    # Temperatura 0 en citas.
    assert mock.chat.completions.create.call_args.kwargs["temperature"] == 0.0


@pytest.mark.asyncio()
@pytest.mark.parametrize("n", list(range(1, 16)))
async def test_exacta_15_articulos(motor: MotorVectorial, n: int) -> None:
    textos = [
        f"Art. {i}.- Marca MARCA-{i:02d}: contenido distintivo ficticio del articulo {i}."
        for i in range(1, 16)
    ]
    for t in textos:
        motor.ingestar_chunks(
            [t], COLECCION_GLOBAL,
            metadata={"titulo": "Ley Quince", "categoria": "Leyes",
                      "hash_archivo": f"hq{n}", "ley": "Ley Quince",
                      "articulo": int(t.split()[1].rstrip(".-")), "parte": 1,
                      "total_partes": 1,
                      "id_articulo": f"ley-quince:art-{t.split()[1].rstrip('.-')}"},
        )
    mock = _mock_llm([("resumen generico", "stop")])
    orquestador = _orquestador(motor, mock)
    r = await orquestador.consultar(f"Dame el articulo {n} de la Ley Quince", tenant_id=1)
    assert f"MARCA-{n:02d}" in r["respuesta"]
    for otro in range(1, 16):
        if otro != n:
            assert f"MARCA-{otro:02d}" not in r["respuesta"]


@pytest.mark.asyncio()
async def test_ambigua_pide_ley_sin_llm(motor: MotorVectorial) -> None:
    for ley, slug in [("Ley Alfa", "ley-alfa"), ("Ley Beta", "ley-beta")]:
        motor.ingestar_chunks(
            [f"Art. 9.- Contenido de {ley}."], COLECCION_GLOBAL,
            metadata={"titulo": ley, "categoria": "Leyes", "hash_archivo": f"h{slug}",
                      "ley": ley, "articulo": 9, "parte": 1, "total_partes": 1,
                      "id_articulo": f"{slug}:art-9"},
        )
    mock = _mock_llm([("no deberia usarse", "stop")])
    orquestador = _orquestador(motor, mock)
    r = await orquestador.consultar("Dame el articulo 9", tenant_id=1)
    assert "cual te refieres" in r["respuesta"]
    assert mock.chat.completions.create.await_count == 0


# ---------- continuacion y expansion ----------

@pytest.mark.asyncio()
async def test_continuacion_une_y_no_avisa(motor: MotorVectorial) -> None:
    mock = _mock_llm([("AAAA ", "length"), ("BBBB", "stop")])
    orquestador = _orquestador(motor, mock)
    r = await orquestador.consultar("¿Que dice la ley?", tenant_id=1)
    assert r["respuesta"] == "AAAA BBBB"
    assert r["respuesta_cortada"] is False
    assert mock.chat.completions.create.await_count == 2


@pytest.mark.asyncio()
async def test_triple_corte_avisa(motor: MotorVectorial) -> None:
    mock = _mock_llm([("a", "length"), ("b", "length"), ("c", "length")])
    orquestador = _orquestador(motor, mock)
    r = await orquestador.consultar("¿Que dice la ley?", tenant_id=1)
    assert r["respuesta"] == "abc (Nota: la respuesta alcanzo el limite de longitud; muestra el texto disponible.)"
    assert r["respuesta_cortada"] is True


@pytest.mark.asyncio()
async def test_expansion_entrega_3_partes_en_orden(motor: MotorVectorial) -> None:
    cuerpo = (
        "Art. 20.- Norma larga ficticia. "
        + " ".join(f"Parrafo {i} con contenido relevante sobre plazos." for i in range(150))
    )
    assert len(cuerpo) > 3000
    unidades = segmentar_por_articulos(cuerpo, ley="Ley Larga")
    assert len(unidades) == 3
    metas = [
        {"titulo": "Ley Larga", "categoria": "Leyes", "hash_archivo": "hl",
         "ley": "Ley Larga", "articulo": 20, "parte": u["parte"],
         "total_partes": u["total_partes"], "id_articulo": u["id_articulo"]}
        for u in unidades
    ]
    motor.ingestar_chunks_con_metadata(
        [u["texto"] for u in unidades], COLECCION_GLOBAL, metas
    )
    mock = _mock_llm([("ok", "stop")])
    orquestador = _orquestador(motor, mock)
    r = await orquestador.consultar(
        "¿Que dice la norma larga sobre plazos?", tenant_id=1
    )
    completas = [f for f in r["fuentes"] if f["metadata"].get("articulo_completo")]
    assert len(completas) >= 1
    norm = lambda s: " ".join(s.split())
    assert norm(completas[0]["texto"]) == norm(cuerpo)  # 3 partes en orden
