"""
Tests TDD del motor vectorial.

Cubre:
  - Ingestion de chunks y persistencia de metadata.
  - Busqueda semantica (el chunk correcto aparece en el Top-K).
  - Aislamiento multi-tenant: colecciones de tenants distintos
    NO se contaminan entre si.
  - Inclusion/exclusion de la coleccion global de leyes publicas.
  - Validacion de parametros y errores de entrada.

Los tests usan `chromadb.EphemeralClient` (con `allow_reset=True`)
para NUNCA tocar el disco de la persistencia real y arrancar limpio
en cada test.
"""

from __future__ import annotations

import pytest
import chromadb
from chromadb.config import Settings

from backend.db.vectorial import (
    CATEGORIAS_VALIDAS,
    CATEGORIA_TODAS,
    COLECCION_GLOBAL,
    ErrorMotorVectorial,
    FragmentoRecuperado,
    MotorVectorial,
    nombre_coleccion_para_tenant,
)


# ============================================================
# Fixtures.
# ============================================================

@pytest.fixture()
def cliente_efimero() -> chromadb.api.ClientAPI:
    """
    Cliente ChromaDB en memoria con reset habilitado.

    Importante: en chromadb 1.x, `EphemeralClient()` comparte un store
    in-memory a nivel de proceso. Si dos tests usan el mismo nombre de
    coleccion se contaminarian. `reset()` al inicio de cada test
    garantiza aislamiento real.
    """
    cliente = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
    cliente.reset()
    return cliente


@pytest.fixture()
def motor(cliente_efimero, embedding_function_compartido) -> MotorVectorial:
    """MotorVectorial nuevo por test, con EF compartido para velocidad."""
    return MotorVectorial(
        cliente=cliente_efimero,
        embedding_function=embedding_function_compartido,
    )


# ============================================================
# Helpers para construir colecciones.
# ============================================================

def _chunks_juridicos() -> list[str]:
    """Chunks tematicamente distintos para validar ranking semantico."""
    return [
        "El Codigo Penal en su articulo 138 tipifica el delito de homicidio "
        "y establece las penas correspondientes.",
        "El procedimiento de divorcio se regula en el Codigo Civil, articulos "
        "81 a 107, y puede ser de mutuo acuerdo o contencioso.",
        "La hipoteca puede contener clausulas suelo declaradas nulas por "
        "falta de transparencia conforme a la jurisprudencia del Tribunal "
        "Supremo.",
    ]


# ============================================================
# Ingestion basica.
# ============================================================

def test_ingestar_chunks_devuelve_cantidad_insertada(motor: MotorVectorial) -> None:
    chunks = _chunks_juridicos()
    n = motor.ingestar_chunks(chunks, "casos_user_1")
    assert n == 3


def test_ingestar_lista_vacia_devuelve_cero(motor: MotorVectorial) -> None:
    assert motor.ingestar_chunks([], "casos_user_1") == 0


def test_ingestar_sin_nombre_coleccion_lanza_error(motor: MotorVectorial) -> None:
    with pytest.raises(ErrorMotorVectorial):
        motor.ingestar_chunks(["algo"], "")


def test_coleccion_se_crea_automaticamente_al_ingestar(motor: MotorVectorial) -> None:
    assert motor.coleccion_existe("casos_user_99") is False
    motor.ingestar_chunks(["contenido de prueba"], "casos_user_99")
    assert motor.coleccion_existe("casos_user_99") is True


def test_metadata_se_persiste_en_cada_chunk(motor: MotorVectorial) -> None:
    chunks = _chunks_juridicos()
    motor.ingestar_chunks(
        chunks,
        "casos_user_1",
        metadata={"archivo": "expediente_42.pdf", "tier": 3},
    )
    resultados = motor.buscar_contexto(
        "informacion penal", "casos_user_1", incluir_global=False, top_k=3
    )
    assert len(resultados) == 3
    for r in resultados:
        assert r.metadata.get("archivo") == "expediente_42.pdf"
        assert r.metadata.get("tier") == 3
        assert r.metadata.get("coleccion") == "casos_user_1"
        assert "indice" in r.metadata


# ============================================================
# Busqueda semantica.
# ============================================================

def test_busqueda_recupera_chunk_penal_para_pregunta_sobre_delitos(
    motor: MotorVectorial,
) -> None:
    """
    El usuario pregunta sobre el Codigo Penal. El chunk correcto
    debe encabezar el Top-K (similitud semantica, no coincidencia literal).
    """
    motor.ingestar_chunks(_chunks_juridicos(), "casos_user_1")

    resultados = motor.buscar_contexto(
        "que dice el codigo penal sobre los delitos?",
        "casos_user_1",
        incluir_global=False,
        top_k=1,
    )
    assert len(resultados) == 1
    assert "Codigo Penal" in resultados[0].texto
    assert resultados[0].puntuacion > 0.0


def test_busqueda_recupera_chunk_divorcio_para_pregunta_sobre_matrimonio(
    motor: MotorVectorial,
) -> None:
    """
    Nota sobre el modelo: `all-MiniLM-L6-v2` es multilingue pero pequeno.
    Para queries parafraseadas en espanol, usamos keywords directos
    (mas fiables que sinonimos) y verificamos el top-1.
    """
    motor.ingestar_chunks(_chunks_juridicos(), "casos_user_1")
    resultados = motor.buscar_contexto(
        "divorcio matrimonial codigo civil",
        "casos_user_1",
        incluir_global=False,
        top_k=1,
    )
    assert len(resultados) == 1
    assert "divorcio" in resultados[0].texto or "Codigo Civil" in resultados[0].texto


def test_busqueda_ordenada_por_relevancia_descendente(
    motor: MotorVectorial,
) -> None:
    motor.ingestar_chunks(_chunks_juridicos(), "casos_user_1")
    resultados = motor.buscar_contexto(
        "delito penal homicidio",
        "casos_user_1",
        incluir_global=False,
        top_k=3,
    )
    assert len(resultados) == 3
    for i in range(len(resultados) - 1):
        assert resultados[i].puntuacion >= resultados[i + 1].puntuacion


def test_busqueda_con_pregunta_vacia_devuelve_lista_vacia(
    motor: MotorVectorial,
) -> None:
    motor.ingestar_chunks(_chunks_juridicos(), "casos_user_1")
    assert motor.buscar_contexto("", "casos_user_1") == []


def test_busqueda_top_k_cero_lanza_value_error(motor: MotorVectorial) -> None:
    with pytest.raises(ValueError):
        motor.buscar_contexto("hola", "casos_user_1", top_k=0)


def test_busqueda_en_coleccion_vacia_devuelve_lista_vacia(
    motor: MotorVectorial,
) -> None:
    """La coleccion no existe -> no hay resultados, no hay error."""
    resultados = motor.buscar_contexto(
        "cualquier cosa", "casos_user_inexistente", incluir_global=False
    )
    assert resultados == []


# ============================================================
# Aislamiento multi-tenant (TDD - test critico).
# ============================================================

def test_aislamiento_tenant1_no_ve_datos_de_tenant2(
    motor: MotorVectorial,
) -> None:
    """
    Aislamiento multi-tenant a nivel de coleccion: en un unico cliente
    ChromaDB (que es como funciona en produccion: un PersistentClient
    compartido, colecciones separadas), buscar en `casos_user_1` NO
    debe devolver chunks de `casos_user_2`.
    """
    motor.ingestar_chunks(
        ["Documento privado del cliente 1: tramitacion de divorcio express."],
        "casos_user_1",
        metadata={"tier": 3, "propietario": "cliente_1"},
    )
    motor.ingestar_chunks(
        ["Documento privado del cliente 2: reclamacion por despido improcedente."],
        "casos_user_2",
        metadata={"tier": 3, "propietario": "cliente_2"},
    )

    # Buscar en Tenant 1: solo ve su coleccion.
    resultados_1 = motor.buscar_contexto(
        "informacion de un cliente",
        "casos_user_1",
        incluir_global=False,
    )
    assert len(resultados_1) >= 1
    for r in resultados_1:
        assert "cliente 1" in r.texto
        assert "cliente 2" not in r.texto
        assert r.metadata.get("propietario") == "cliente_1"
        assert r.coleccion_origen == "casos_user_1"

    # Buscar en Tenant 2: solo ve su coleccion.
    resultados_2 = motor.buscar_contexto(
        "informacion de un cliente",
        "casos_user_2",
        incluir_global=False,
    )
    assert len(resultados_2) >= 1
    for r in resultados_2:
        assert "cliente 2" in r.texto
        assert "cliente 1" not in r.texto
        assert r.metadata.get("propietario") == "cliente_2"
        assert r.coleccion_origen == "casos_user_2"


def test_busqueda_en_coleccion_inexistente_no_filtra_otras(
    motor: MotorVectorial,
) -> None:
    """
    Si el tenant aun no tiene coleccion, no debe 'ver' las de otros
    tenants como efecto colateral. Solo devolveria resultados de la
    global si incluir_global=True.
    """
    motor.ingestar_chunks(
        ["Secreto del cliente 99 en su coleccion privada."],
        "casos_user_99",
        metadata={"tier": 3},
    )
    resultados = motor.buscar_contexto(
        "secreto",
        "casos_user_1",  # coleccion inexistente para este cliente
        incluir_global=False,
    )
    assert resultados == []


# ============================================================
# Coleccion global de leyes publicas.
# ============================================================

def test_coleccion_global_se_incluye_por_defecto(motor: MotorVectorial) -> None:
    motor.ingestar_chunks(
        ["La Constitucion Espanola reconoce el derecho a la igualdad "
         "en su articulo 14."],
        COLECCION_GLOBAL,
        metadata={"tier": 1, "fuente": "BOE"},
    )
    motor.ingestar_chunks(
        ["Mi caso particular de herencia en Cordoba."],
        "casos_user_42",
        metadata={"tier": 3},
    )

    resultados = motor.buscar_contexto(
        "que dice la constitucion sobre igualdad?",
        "casos_user_42",
        incluir_global=True,
        top_k=5,
    )
    textos = " ".join(r.texto for r in resultados)
    assert "Constitucion" in textos
    # Y ademas debe figurar de que coleccion viene cada resultado.
    origenes = {r.coleccion_origen for r in resultados}
    assert COLECCION_GLOBAL in origenes


def test_coleccion_global_se_puede_excluir(motor: MotorVectorial) -> None:
    motor.ingestar_chunks(
        ["Ley general aplicable a todos los ciudadanos."],
        COLECCION_GLOBAL,
        metadata={"tier": 1},
    )
    motor.ingestar_chunks(
        ["Mi caso privado especifico."],
        "casos_user_42",
        metadata={"tier": 3},
    )

    resultados = motor.buscar_contexto(
        "informacion general",
        "casos_user_42",
        incluir_global=False,
        top_k=5,
    )
    for r in resultados:
        assert r.coleccion_origen == "casos_user_42"


def test_nombre_coleccion_para_tenant_es_deterministico() -> None:
    """La funcion utilitaria que nombra colecciones debe ser estable."""
    assert nombre_coleccion_para_tenant(42) == "casos_user_42"
    assert nombre_coleccion_para_tenant(1) == "casos_user_1"


# ============================================================
# Validacion de errores y forma del resultado.
# ============================================================

def test_fragmento_recuperado_es_dataclass_inmutable() -> None:
    """El dataclass debe ser frozen para que no se mute el resultado."""
    from dataclasses import FrozenInstanceError

    frag = FragmentoRecuperado(
        texto="hola",
        metadata={"k": "v"},
        puntuacion=0.9,
        coleccion_origen="x",
    )
    with pytest.raises(FrozenInstanceError):
        frag.texto = "mutado"  # type: ignore[misc]


def test_puntuacion_esta_normalizada_en_rango_unitario(
    motor: MotorVectorial,
) -> None:
    motor.ingestar_chunks(_chunks_juridicos(), "casos_user_1")
    resultados = motor.buscar_contexto(
        "pregunta generica", "casos_user_1", incluir_global=False, top_k=3
    )
    for r in resultados:
        assert 0.0 <= r.puntuacion <= 1.0


# ============================================================
# Filtro por categoria (Anti-Data-Poisoning / selector "Contexto").
# ============================================================
#
# La metadata `categoria` se guarda al ingestar y ChromaDB la filtra
# en el momento del `query` (no post-procesamos en Python), asi que
# la latencia no se ve afectada por categorias "ruidosas".


def test_filtro_categoria_devuelve_solo_chunks_de_esa_categoria(
    motor: MotorVectorial,
) -> None:
    """Si filtro por 'Leyes', NO aparecen chunks de 'Expediente'."""
    motor.ingestar_chunks(
        ["Articulo 1 de la Constitucion: la igualdad es un derecho."],
        COLECCION_GLOBAL,
        metadata={"categoria": "Leyes"},
    )
    motor.ingestar_chunks(
        ["Demanda de despido del cliente Juan Perez en 2024."],
        "casos_user_1",
        metadata={"categoria": "Expediente"},
    )

    resultados = motor.buscar_contexto(
        "derechos del trabajador",
        "casos_user_1",
        incluir_global=True,
        filtro_categoria="Leyes",
    )
    assert len(resultados) >= 1
    for r in resultados:
        assert r.metadata.get("categoria") == "Leyes"
        assert "despido" not in r.texto.lower()


def test_filtro_categoria_none_no_filtra(motor: MotorVectorial) -> None:
    """`filtro_categoria=None` (default) devuelve chunks de TODAS las categorias."""
    motor.ingestar_chunks(
        ["Ley de contratos del estado."],
        COLECCION_GLOBAL,
        metadata={"categoria": "Leyes"},
    )
    motor.ingestar_chunks(
        ["Caso de jurisprudencia sobre incumplimiento contractual."],
        "casos_user_1",
        metadata={"categoria": "Jurisprudencia"},
    )
    motor.ingestar_chunks(
        ["Expediente interno del cliente 5."],
        "casos_user_1",
        metadata={"categoria": "Expediente"},
    )

    resultados = motor.buscar_contexto(
        "contrato",
        "casos_user_1",
        incluir_global=True,
    )
    categorias = {r.metadata.get("categoria") for r in resultados}
    # Sin filtro deben verse al menos 2 categorias distintas.
    assert len(categorias) >= 2


def test_filtro_categoria_vacia_se_trata_como_none(motor: MotorVectorial) -> None:
    """El frontend puede mandar '' o 'Todas'; ambos significan sin filtro."""
    motor.ingestar_chunks(
        ["Ley aplicable."],
        COLECCION_GLOBAL,
        metadata={"categoria": "Leyes"},
    )
    r_vacia = motor.buscar_contexto(
        "ley", "casos_user_1", incluir_global=True, filtro_categoria=""
    )
    r_todas = motor.buscar_contexto(
        "ley", "casos_user_1", incluir_global=True, filtro_categoria=CATEGORIA_TODAS
    )
    # Ambos deben traer el chunk de Leyes (no estan filtrando).
    assert any(x.metadata.get("categoria") == "Leyes" for x in r_vacia)
    assert any(x.metadata.get("categoria") == "Leyes" for x in r_todas)


def test_filtro_categoria_invalida_lanza_value_error(
    motor: MotorVectorial,
) -> None:
    """Pasar una categoria que no esta en CATEGORIAS_VALIDAS es bug del caller."""
    motor.ingestar_chunks(["algo"], "casos_user_1")
    with pytest.raises(ValueError) as exc:
        motor.buscar_contexto(
            "x", "casos_user_1", filtro_categoria="Categoria Inventada"
        )
    assert "filtro_categoria invalido" in str(exc.value)


def test_sin_chunks_de_la_categoria_solicitada_devuelve_vacio(
    motor: MotorVectorial,
) -> None:
    """Si la coleccion no tiene chunks de la categoria pedida, no falla: []."""
    motor.ingestar_chunks(
        ["Solo leyes publicas aqui."],
        COLECCION_GLOBAL,
        metadata={"categoria": "Leyes"},
    )
    resultados = motor.buscar_contexto(
        "cualquier cosa",
        "casos_user_1",
        incluir_global=True,
        filtro_categoria="Jurisprudencia",
    )
    assert resultados == []


def test_categorias_validas_contiene_las_tres_esperadas() -> None:
    """Documenta el set valido para que el frontend lo reuse."""
    assert CATEGORIAS_VALIDAS == ("Leyes", "Jurisprudencia", "Expediente")
    assert CATEGORIA_TODAS == "Todas"
