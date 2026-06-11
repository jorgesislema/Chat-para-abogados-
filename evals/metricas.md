# Historial de Evaluaciones del RAG

> Registro **append-only** del rendimiento del RAG juridico.
> Cada ejecucion de `python evals/evaluar_rag.py` anade una seccion al final
> con timestamp, modelo evaluado y metricas agregadas.
>
> **NO borrar entradas anteriores**: este archivo es la memoria historica
> que justifica cambios de modelo o de algoritmo de chunking.

## Como interpretar este archivo

- **Similitud de texto media**: ratio medio (0 a 1) entre la respuesta
  generada y la respuesta ideal usando `difflib.SequenceMatcher`. Mide
  coincidencia literal; valores altos no garantizan respuestas correctas,
  pero valores bajos delatan regresiones graves.
- **Fidelidad al contexto media**: proporcion de tokens unicos de la
  respuesta generada que provienen del contexto recuperado. Una fidelidad
  baja indica posible alucinacion.
- **Casos con alerta**: numero de casos que dispararon alguna heuristica
  de riesgo (PII en prompt, articulo no presente en contexto, etc.).
- **Columna PII cruda**: `SI` significa que el prompt enviado al LLM
  todavia contenia datos personales. Es un fallo de Gobernanza bloqueante.

## Umbrales sugeridos (revisar en cada cambio de modelo)

| Metrica | Minimo aceptable | Deseable |
|---|---|---|
| Similitud media | 0.55 | 0.75 |
| Fidelidad media | 0.60 | 0.80 |
| Casos con alerta | 0 | 0 |
| PII cruda en prompts | 0 | 0 |

## Cuando ejecutar la evaluacion

1. Se cambia el modelo base (por ejemplo, de `llama-3-8b-instruct` a `claude-3-sonnet`).
2. Se modifica el algoritmo de chunking en `backend/servicios/gestion_pdf.py`.
3. Se actualiza el prompt del sistema en `backend/servicios/rag_motor.py`.
4. Se anaden o eliminan categorias juridicas del dataset dorado.

## Ejecuciones

<!-- A continuacion se iran anadiendo las secciones de cada corrida. -->
## Ejecucion: 2026-06-11 15:45:39 UTC

- **Modelo evaluado**: `meta-llama/llama-3-8b-instruct`
- **Modo**: `dry-run`
- **Casos evaluados**: 5
- **Similitud de texto media (SequenceMatcher)**: 1.000
- **Fidelidad al contexto media**: 0.439
- **Casos con alerta**: 0

### Detalle por caso

| ID | Categoria | Sim. texto | Fidelidad | PII cruda | Alerta |
|----|-----------|------------|-----------|-----------|--------|
| caso_001_despido_improcedente | derecho_laboral | 1.000 | 0.618 | no | OK |
| caso_002_clausula_suelo | derecho_bancario | 1.000 | 0.541 | no | OK |
| caso_003_custodia_compartida | derecho_de_familia | 1.000 | 0.581 | no | OK |
| caso_004_plazo_reclamacion | derecho_mercantil | 1.000 | 0.346 | no | OK |
| caso_005_gobernanza_pii | gobernanza | 1.000 | 0.111 | no | OK |

---
