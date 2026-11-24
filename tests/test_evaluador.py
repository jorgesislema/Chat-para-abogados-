"""
Tests del evaluador del RAG.

Solo cubrimos las funciones PURAS y el modo dry-run (que no consume
tokens). El modo real, que llama a OpenRouter, se valida manualmente
siguiendo la regla documentada en docs_ia/04_testing_y_evals.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.evaluar_rag import (
    ResumenEvaluacion,
    _inventa_articulo,
    calcular_fidelidad_contexto,
    calcular_similitud_texto,
    construir_prompt_simple,
    ejecutar_evaluacion,
    renderizar_reporte,
    validar_prompt_ofuscado,
)


# ============================================================
# Funciones puras de metrica.
# ============================================================

def test_similitud_identica_es_uno() -> None:
    assert calcular_similitud_texto("hola mundo", "hola mundo") == 1.0


def test_similitud_textos_distintos_es_menor_a_uno() -> None:
    valor = calcular_similitud_texto("perro", "gato")
    assert 0.0 <= valor < 1.0


def test_similitud_strings_vacios_es_cero() -> None:
    assert calcular_similitud_texto("", "algo") == 0.0
    assert calcular_similitud_texto("algo", "") == 0.0
    assert calcular_similitud_texto("", "") == 0.0


def test_fidelidad_total_cuando_respuesta_esta_en_contexto() -> None:
    contexto = (
        "El articulo 54 del Estatuto de los Trabajadores regula el despido "
        "disciplinario en el ambito laboral"
    )
    respuesta = "articulo 54 Estatuto trabajadores despido disciplinario laboral"
    valor = calcular_fidelidad_contexto(respuesta, contexto)
    assert valor == 1.0


def test_fidelidad_baja_cuando_respuesta_inventa_contenido() -> None:
    contexto = "El articulo 54 regula el despido disciplinario en Espana"
    respuesta = "segun la ley de marte cualquier despido debe aprobarse en el senado"
    valor = calcular_fidelidad_contexto(respuesta, contexto)
    assert valor < 0.3


def test_fidelidad_strings_vacios_es_cero() -> None:
    assert calcular_fidelidad_contexto("", "algo") == 0.0
    assert calcular_fidelidad_contexto("algo", "") == 0.0


# ============================================================
# Heuristica de alucinacion (articulo no presente en contexto).
# ============================================================

def test_inventa_articulo_cuando_numero_no_aparece_en_contexto() -> None:
    contexto = "Articulo 54 del Estatuto de los Trabajadores"
    generada = "Conforme al articulo 89 del codigo civil..."
    assert _inventa_articulo(generada, contexto) is True


def test_no_inventa_articulo_cuando_numero_esta_en_contexto() -> None:
    contexto = "Articulo 54 del Estatuto de los Trabajadores"
    generada = "El articulo 54 exige requisitos claros"
    assert _inventa_articulo(generada, contexto) is False


def test_inventa_articulo_ignora_si_no_se_menciona_ninguno() -> None:
    contexto = "Cualquier cosa"
    generada = "No se cita ningun articulo en la respuesta"
    assert _inventa_articulo(generada, contexto) is False


# ============================================================
# Validador de ofuscacion del prompt.
# ============================================================

def test_validar_prompt_ok_si_no_tiene_pii_y_tiene_tokens() -> None:
    prompt = "Mi cliente [NOMBRE] con DNI [DNI] quiere impugnar"
    cumple, alerta = validar_prompt_ofuscado(prompt, ["[NOMBRE]", "[DNI]"])
    assert cumple is True
    assert alerta == ""


def test_validar_prompt_falla_si_queda_pii_cruda() -> None:
    prompt = "Mi cliente Juan Perez con DNI 12345678Z quiere impugnar"
    cumple, alerta = validar_prompt_ofuscado(prompt, ["[NOMBRE]"])
    assert cumple is False
    assert "PII" in alerta


def test_validar_prompt_falla_si_falta_token_obligatorio() -> None:
    prompt = "Mi cliente [NOMBRE] quiere impugnar"  # falta [DNI]
    cumple, alerta = validar_prompt_ofuscado(prompt, ["[NOMBRE]", "[DNI]"])
    assert cumple is False
    assert "[DNI]" in alerta


# ============================================================
# Pipeline end-to-end en modo dry-run (no consume tokens).
# ============================================================

def test_pipeline_dry_run_genera_reporte_y_lo_anexa(tmp_path: Path) -> None:
    ruta_dataset = Path(__file__).resolve().parents[1] / "evals" / "dataset_dorado.json"
    ruta_metricas = tmp_path / "metricas.md"

    resumen = ejecutar_evaluacion(
        ruta_dataset=ruta_dataset,
        ruta_metricas=ruta_metricas,
        modelo="modelo-de-test",
        dry_run=True,
    )

    # 5 casos base + 15 de cita literal G2 en el dataset actual.
    assert resumen.total_casos == 20
    assert resumen.modo == "dry-run"
    assert 0.0 <= resumen.similitud_media <= 1.0
    assert 0.0 <= resumen.fidelidad_media <= 1.0
    # El archivo de metricas debe existir y contener la seccion anadida.
    assert ruta_metricas.exists()
    contenido = ruta_metricas.read_text(encoding="utf-8")
    assert "Ejecucion:" in contenido
    assert "modelo-de-test" in contenido


def test_pipeline_dry_run_detecta_pii_en_caso_gobernanza(
    tmp_path: Path,
) -> None:
    """
    El caso_005 del dataset contiene PII cruda: tras la ofuscacion,
    el prompt que iria al LLM NO debe contenerla. Validamos que el
    evaluador lo detecta (con `contiene_pii_en_prompt = False`).
    """
    ruta_dataset = Path(__file__).resolve().parents[1] / "evals" / "dataset_dorado.json"
    ruta_metricas = tmp_path / "metricas.md"

    resumen = ejecutar_evaluacion(
        ruta_dataset=ruta_dataset,
        ruta_metricas=ruta_metricas,
        modelo="dry-run",
        dry_run=True,
    )

    caso_pii = next(c for c in resumen.casos if c.id_caso == "caso_005_gobernanza_pii")
    assert caso_pii.contiene_pii_en_prompt is False
    # Los tokens [NOMBRE], [DNI], etc. deben haber sido inyectados.
    for token in ("[NOMBRE]", "[DNI]", "[EMAIL]", "[FECHA]", "[TELEFONO]"):
        assert token in caso_pii.pregunta_ofuscada
    # Y no debe haber alerta de PII en el prompt.
    assert "PII" not in caso_pii.alerta


# ============================================================
# Reporte en Markdown.
# ============================================================

def test_renderizar_reporte_incluye_metricas_principales() -> None:
    resumen = ResumenEvaluacion(
        timestamp="2026-06-11 00:00:00 UTC",
        modelo="modelo-x",
        modo="dry-run",
        total_casos=2,
        similitud_media=0.5,
        fidelidad_media=0.7,
        casos_con_alerta=1,
    )
    md = renderizar_reporte(resumen)
    assert "modelo-x" in md
    assert "dry-run" in md
    assert "0.500" in md
    assert "0.700" in md
    assert "Casos con alerta" in md


def test_construir_prompt_incluye_contexto_y_pregunta() -> None:
    prompt = construir_prompt_simple("Consulta ya ofuscada", "Contexto legal X")
    assert "Contexto legal X" in prompt
    assert "Consulta ya ofuscada" in prompt
    assert "CONTEXTO RELEVANTE" in prompt
    assert "ya ofuscada" in prompt
