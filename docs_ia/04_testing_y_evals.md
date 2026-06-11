# 04 - Testing vs Evaluaciones: la division determinista / estocastica

> Documento de gobernanza tecnica. Define por que `tests/` y `evals/`
> son carpetas distintas con reglas de ejecucion distintas.

## 1. La regla fundamental

```
tests/  = logica DETERMINISTA  -> pytest en cada commit (rapido, gratis)
evals/  = calidad ESTOCASTICA  -> ejecucion manual con tokens (lento, caro)
```

Mezclar ambas carpetas rompe uno de los dos bucles de feedback: o bien
hacemos que el CI sea lento y caro, o bien perdemos observabilidad sobre
la calidad real del LLM. La separacion es deliberada.

## 2. Tabla comparativa

| Aspecto | `tests/` | `evals/` |
|---|---|---|
| Runner | `pytest` (automatizado) | `python evals/evaluar_rag.py` (manual) |
| Frecuencia | En cada commit / PR | Al cambiar modelo o chunking |
| Coste | CPU y RAM locales | Tokens de OpenRouter ($$) |
| Latencia tipica | < 1 minuto | Minutos, depende del tamano del dataset |
| Red | NO requiere | SI requiere (LLM externo) |
| Determinismo | SI (mismo input -> mismo output) | NO (mismo input -> respuestas que varian) |
| Que mide | Logica de negocio: ofuscacion, auth, contratos de API | Fidelidad, precision, alucinaciones, cobertura |
| Fallo tipico | Asercion que no se cumple | Metrica por debajo del umbral definido en `evals/metricas.md` |
| Output | Reporte de pytest (pass/fail) | Nueva seccion en `evals/metricas.md` (append-only) |

## 3. Que va en `tests/`

Codigo **determinista y rapido** que queremos protegido por TDD:

- `ofuscacion.py`: ofuscar "Juan Perez" -> "[NOMBRE]" (mismo input, mismo output, gratis).
- Endpoints de `auth.py`, `admin.py`, `chat.py`: mocks del LLM para validar contratos.
- `gestion_pdf.py` chunking: dado un texto fijo, produce chunks fijos.
- `seguridad.py` JWT: hashes, expiracion, claims.
- `rag_motor.prompt builder`: dado contexto + pregunta, arma el string correcto.

Si un test necesita conectarse a OpenRouter, **no es un test**: es una
evaluacion y va en `evals/`.

## 4. Que va en `evals/`

Calidad **estocastica y costosa** que solo revisamos cuando cambia algo
de fondo en el sistema:

- `dataset_dorado.json`: preguntas y respuestas ideales, editadas a mano.
- `evaluar_rag.py`: itera sobre el dataset y produce metricas.
- `metricas.md`: registro historico append-only.

### Cuando ejecutar la evaluacion

1. **Cambio de modelo base**: pasar de `llama-3-8b-instruct` a `claude-3-sonnet`
   altera todas las respuestas; hay que re-evaluar antes de mergear.
2. **Cambio en el chunking** (`backend/servicios/gestion_pdf.py`): el contexto
   que llega al LLM es distinto y puede cambiar la calidad.
3. **Cambio en el prompt del sistema** (`backend/servicios/rag_motor.py`):
   pequenas variaciones de prompt tienen efectos no obvios.
4. **Anadir o quitar categorias juridicas** del dataset dorado.

### Cuando NO ejecutar la evaluacion

- En cada commit: consume tokens sin necesidad.
- En CI: el pipeline queda bloqueado por latencia de red y costos variables.
- Sin haber cambiado nada del modelo, prompt o chunking: las metricas seran
  ruido estocastico del sampling del LLM.

## 5. Modos de ejecucion del evaluador

```bash
# Con tokens reales (gasta API key):
python evals/evaluar_rag.py

# Sin gastar tokens, valida el pipeline end-to-end:
python evals/evaluar_rag.py --dry-run

# Evaluando un modelo alternativo:
python evals/evaluar_rag.py --modelo anthropic/claude-3-sonnet
```

El modo `dry-run` es seguro de ejecutar en cualquier maquina: NO realiza
llamadas a OpenRouter. Util para verificar que el dataset es valido y
que el reporte se genera correctamente.

## 6. Umbrales de aceptacion (definidos en `evals/metricas.md`)

| Metrica | Minimo aceptable | Deseable |
|---|---|---|
| Similitud media | 0.55 | 0.75 |
| Fidelidad media | 0.60 | 0.80 |
| Casos con alerta | 0 | 0 |
| PII cruda en prompts | 0 (bloqueante) | 0 |

Si una corrida queda por debajo del minimo aceptable, **no se hace merge**
del cambio que la provoco hasta entender la regresion.

## 7. Anti-patrones a evitar

- Mover tests de `tests/` a `evals/` "porque tambien prueban algo del LLM".
  Si la prueba es determinista, va en `tests/` con un mock del LLM.
- Mover la evaluacion a `tests/` "para que se ejecute en CI". Rompe el CI.
- Borrar entradas antiguas de `metricas.md`. La historia es el valor del archivo.
- Usar el dataset dorado como "tests" y ejecutarlo en cada PR: ademas de
  caro, el resultado varia por la temperatura del LLM y genera falsos fallos.
