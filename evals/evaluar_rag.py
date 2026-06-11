"""
Evaluador del RAG juridico de LegalRAG_SaaS.

Lee el dataset dorado, consulta al LLM por cada caso (respetando la
Regla de Oro: primero ofusca, despues envia al modelo) y registra los
resultados en `evals/metricas.md`.

Modos de ejecucion:
  - real   : requiere OPENROUTER_API_KEY. Cada caso consume tokens.
  - dry-run: NO consume tokens. Simula la respuesta usando la respuesta
             ideal del dataset. Util para validar el pipeline sin red.

Uso:
    python evals/evaluar_rag.py
    python evals/evaluar_rag.py --dry-run
    python evals/evaluar_rag.py --modelo meta-llama/llama-3-8b-instruct
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Protocol

# Permite ejecutar el script directamente (`python evals/evaluar_rag.py`)
# anadiendo la raiz del proyecto a sys.path.
_RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
if str(_RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(_RAIZ_PROYECTO))

from backend.servicios.ofuscacion import contiene_pii, ofuscar_texto  # noqa: E402
from backend.servicios.rag_motor import GeneradorRespuestaOpenRouter  # noqa: E402


# ============================================================
# Tipos y dataclasses de resultado.
# ============================================================

@dataclass(frozen=True)
class ResultadoCaso:
    """Resultado de evaluar un caso individual del dataset dorado."""

    id_caso: str
    categoria: str
    pregunta_cruda: str
    pregunta_ofuscada: str
    contexto_esperado: str
    respuesta_generada: str
    respuesta_ideal: str
    similitud_texto: float
    fidelidad_contexto: float
    contiene_pii_en_prompt: bool
    alerta: str


@dataclass
class ResumenEvaluacion:
    """Acumulado de una ejecucion completa de la evaluacion."""

    timestamp: str
    modelo: str
    modo: str  # "real" o "dry-run"
    total_casos: int
    similitud_media: float
    fidelidad_media: float
    casos_con_alerta: int
    casos: list[ResultadoCaso] = field(default_factory=list)


# ============================================================
# Protocolo para inyectar el generador (facilita tests con mock).
# ============================================================

class GeneradorLLM(Protocol):
    """Contrato minimo que debe cumplir cualquier generador."""

    def generar(self, prompt: str) -> str:  # pragma: no cover - interfaz
        ...


# ============================================================
# Funciones puras de metrica (todas testeables sin red).
# ============================================================

def calcular_similitud_texto(generada: str, ideal: str) -> float:
    """
    Devuelve un ratio entre 0 y 1 basado en `difflib.SequenceMatcher`.

    Es una primera aproximacion: en produccion se sustituira por una
    metrica semantica (embeddings o un juez LLM). Por ahora mide
    superposicion de caracteres, suficiente para detectar regresiones
    graves entre modelos.
    """
    if not generada or not ideal:
        return 0.0
    return SequenceMatcher(None, generada, ideal).ratio()


def calcular_fidelidad_contexto(generada: str, contexto: str) -> float:
    """
    Mide cuantos tokens unicos de la respuesta generada aparecen en
    el contexto esperado (cobertura tipo ROUGE-N simplificado).

    Una fidelidad baja sugiere alucinacion: la respuesta aporta
    informacion que NO estaba en el contexto recuperado.
    """
    if not generada or not contexto:
        return 0.0
    tokens_generados = _tokenizar(generada)
    tokens_contexto = _tokenizar(contexto)
    if not tokens_generados:
        return 0.0
    interseccion = tokens_generados & tokens_contexto
    return len(interseccion) / len(tokens_generados)


def _tokenizar(texto: str) -> set[str]:
    """Tokeniza en minusculas, descartando signos y stopwords triviales."""
    stopwords = {
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
        "al", "a", "y", "o", "u", "en", "por", "para", "con", "sin", "que",
        "se", "es", "no", "si", "lo", "le", "su", "sus", "mi", "tu", "te",
        "me", "ha", "han", "he", "ha", "como", "mas", "pero", "ya", "este",
        "esta", "estos", "estas", "ese", "esa", "esos", "esas", "ser", "estar",
    }
    palabras = re.findall(r"[a-z0-9]+", texto.lower())
    return {p for p in palabras if p not in stopwords and len(p) > 2}


def validar_prompt_ofuscado(
    pregunta_ofuscada: str,
    tokens_obligatorios: list[str],
) -> tuple[bool, str]:
    """
    Verifica que el prompt que ira al LLM cumple la Regla de Oro.

    Retorna (cumple, mensaje_alerta). Si falla, el mensaje describe
    que token falta o que PII cruda fue detectada.
    """
    if contiene_pii(pregunta_ofuscada):
        return False, "El prompt ofuscado AUN contiene PII cruda"
    for token in tokens_obligatorios:
        if token not in pregunta_ofuscada:
            return False, f"Falta el token obligatorio {token} en el prompt"
    return True, ""


# ============================================================
# Carga de dataset y evaluacion por caso.
# ============================================================

def cargar_dataset(ruta: Path) -> dict[str, Any]:
    """Lee el JSON del dataset dorado y valida que tenga la clave 'casos'."""
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el dataset en {ruta}")
    with ruta.open("r", encoding="utf-8") as fichero:
        datos = json.load(fichero)
    if "casos" not in datos or not isinstance(datos["casos"], list):
        raise ValueError("El dataset debe contener una lista bajo la clave 'casos'")
    return datos


def evaluar_caso(
    caso: dict[str, Any],
    generador: GeneradorLLM,
    construir_prompt: Callable[[str, str], str],
    dry_run: bool,
) -> ResultadoCaso:
    """
    Evalua un caso individual: ofusca, genera, mide similitud y fidelidad.
    """
    pregunta_cruda: str = caso["pregunta_cruda"]
    contexto: str = caso.get("contexto_esperado", "")
    ideal: str = caso["respuesta_ideal"]
    tokens_obligatorios: list[str] = caso.get("criterios", {}).get(
        "tokens_obligatorios_en_prompt", []
    )

    pregunta_ofuscada = ofuscar_texto(pregunta_cruda)
    prompt_final = construir_prompt(pregunta_ofuscada, contexto)

    if dry_run:
        # En modo dry-run usamos la respuesta ideal como "generada"
        # para poder validar el pipeline end-to-end sin gastar tokens.
        respuesta_generada = ideal
    else:
        respuesta_generada = generador.generar(prompt_final)

    pii_ok, mensaje_pii = validar_prompt_ofuscado(
        pregunta_ofuscada, tokens_obligatorios
    )

    alerta = ""
    if not pii_ok:
        alerta = mensaje_pii
    elif caso.get("criterios", {}).get("no_inventar_articulo") and _inventa_articulo(
        respuesta_generada, contexto
    ):
        alerta = "Posible alucinacion: menciona articulos no presentes en el contexto"

    return ResultadoCaso(
        id_caso=caso["id"],
        categoria=caso.get("categoria", "sin_categoria"),
        pregunta_cruda=pregunta_cruda,
        pregunta_ofuscada=pregunta_ofuscada,
        contexto_esperado=contexto,
        respuesta_generada=respuesta_generada,
        respuesta_ideal=ideal,
        similitud_texto=calcular_similitud_texto(respuesta_generada, ideal),
        fidelidad_contexto=calcular_fidelidad_contexto(respuesta_generada, contexto),
        contiene_pii_en_prompt=contiene_pii(pregunta_ofuscada),
        alerta=alerta,
    )


def _inventa_articulo(generada: str, contexto: str) -> bool:
    """
    Heuristica: si la respuesta menciona 'articulo XX' y ese numero NO
    aparece en el contexto, lo marcamos como posible alucinacion.
    """
    articulos_generados = set(re.findall(r"art[íi]culo\s+(\d+)", generada.lower()))
    articulos_contexto = set(re.findall(r"art[íi]culo\s+(\d+)", contexto.lower()))
    return bool(articulos_generados - articulos_contexto)


def construir_prompt_simple(pregunta_ofuscada: str, contexto: str) -> str:
    """Plantilla minima compatible con el resto del sistema."""
    partes = ["CONTEXTO RELEVANTE:", contexto or "(sin contexto)", ""]
    partes.append("PREGUNTA DEL ABOGADO (ya ofuscada):")
    partes.append(pregunta_ofuscada)
    return "\n".join(partes)


# ============================================================
# Reporte en Markdown.
# ============================================================

def renderizar_reporte(resumen: ResumenEvaluacion) -> str:
    """Serializa el resumen a Markdown con tabla por caso."""
    lineas: list[str] = []
    lineas.append(f"## Ejecucion: {resumen.timestamp}")
    lineas.append("")
    lineas.append(f"- **Modelo evaluado**: `{resumen.modelo}`")
    lineas.append(f"- **Modo**: `{resumen.modo}`")
    lineas.append(f"- **Casos evaluados**: {resumen.total_casos}")
    lineas.append(
        f"- **Similitud de texto media (SequenceMatcher)**: "
        f"{resumen.similitud_media:.3f}"
    )
    lineas.append(
        f"- **Fidelidad al contexto media**: "
        f"{resumen.fidelidad_media:.3f}"
    )
    lineas.append(f"- **Casos con alerta**: {resumen.casos_con_alerta}")
    lineas.append("")
    lineas.append("### Detalle por caso")
    lineas.append("")
    lineas.append(
        "| ID | Categoria | Sim. texto | Fidelidad | PII cruda | Alerta |"
    )
    lineas.append(
        "|----|-----------|------------|-----------|-----------|--------|"
    )
    for caso in resumen.casos:
        alerta_corta = caso.alerta.replace("|", "/") if caso.alerta else "OK"
        pii_presente = "SI" if caso.contiene_pii_en_prompt else "no"
        lineas.append(
            f"| {caso.id_caso} | {caso.categoria} | "
            f"{caso.similitud_texto:.3f} | {caso.fidelidad_contexto:.3f} | "
            f"{pii_presente} | {alerta_corta} |"
        )
    lineas.append("")
    lineas.append("---")
    lineas.append("")
    return "\n".join(lineas)


def anexar_a_metricas(contenido_md: str, ruta: Path) -> None:
    """Anade (append) una seccion al historial de metricas."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as fichero:
        fichero.write(contenido_md)


# ============================================================
# Orquestacion y CLI.
# ============================================================

def ejecutar_evaluacion(
    ruta_dataset: Path,
    ruta_metricas: Path,
    modelo: str,
    dry_run: bool,
) -> ResumenEvaluacion:
    """Punto de entrada reutilizable (importable desde tests)."""
    dataset = cargar_dataset(ruta_dataset)
    generador: GeneradorLLM
    if dry_run:
        generador = _GeneradorDryRun()
    else:
        generador = GeneradorRespuestaOpenRouter(modelo=modelo)

    resultados: list[ResultadoCaso] = []
    for caso in dataset["casos"]:
        resultados.append(
            evaluar_caso(caso, generador, construir_prompt_simple, dry_run)
        )

    similitudes = [c.similitud_texto for c in resultados]
    fidelidades = [c.fidelidad_contexto for c in resultados]
    resumen = ResumenEvaluacion(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        modelo=modelo,
        modo="dry-run" if dry_run else "real",
        total_casos=len(resultados),
        similitud_media=statistics.fmean(similitudes) if similitudes else 0.0,
        fidelidad_media=statistics.fmean(fidelidades) if fidelidades else 0.0,
        casos_con_alerta=sum(1 for c in resultados if c.alerta),
        casos=resultados,
    )
    anexar_a_metricas(renderizar_reporte(resumen), ruta_metricas)
    return resumen


class _GeneradorDryRun:
    """Generador que NO consume tokens: usa la respuesta ideal del caller."""

    def generar(self, prompt: str) -> str:  # pragma: no cover - trivial
        return (
            "[dry-run] Respuesta simulada. El modo dry-run omite la llamada "
            "al LLM y registra solo el prompt construido."
        )


def _parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta la evaluacion del RAG sobre el dataset dorado."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).parent / "dataset_dorado.json",
        help="Ruta al JSON del dataset dorado.",
    )
    parser.add_argument(
        "--metricas",
        type=Path,
        default=Path(__file__).parent / "metricas.md",
        help="Ruta al log de metricas.",
    )
    parser.add_argument(
        "--modelo",
        type=str,
        default="meta-llama/llama-3-8b-instruct",
        help="Identificador del modelo a evaluar (sobrescribe el del dataset).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No consume tokens: simula la respuesta para validar el pipeline.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parsear_argumentos()
    resumen = ejecutar_evaluacion(
        ruta_dataset=args.dataset,
        ruta_metricas=args.metricas,
        modelo=args.modelo,
        dry_run=args.dry_run,
    )
    print(
        f"[evaluar_rag] {resumen.total_casos} casos | "
        f"similitud={resumen.similitud_media:.3f} | "
        f"fidelidad={resumen.fidelidad_media:.3f} | "
        f"alertas={resumen.casos_con_alerta} | "
        f"modo={resumen.modo}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
