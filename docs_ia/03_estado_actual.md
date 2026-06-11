# 03 - Estado Actual del Proyecto

> Snapshot vivo de que modulos estan construidos, que funciona y que
> queda pendiente. Se actualiza al cerrar cada iteracion de desarrollo.

## Orquestador RAG (NUEVO)

> **Orquestador RAG implementado y conectado al endpoint de FastAPI. Busqueda vectorial y llamadas a OpenRouter integradas mediante asincronismo.**

### Archivos creados o modificados

| Archivo | Estado | Proposito |
|---|---|---|
| `backend/servicios/rag_motor.py` | Extendido | Nueva clase `OrquestadorRAG` (async) + clases legacy mantenidas |
| `backend/api/dependencias.py` | Extendido | `obtener_motor_vectorial` (singleton) + `obtener_orquestador_rag` (DI) |
| `backend/api/rutas_chat.py` | Reescrito | `POST /api/chat` ejecuta la cadena RAG real (antes era placeholder) |
| `backend/api/chat.py` | Reescrito | `POST /chat/preguntar` ahora usa `OrquestadorRAG` (async) |
| `tests/test_rag_motor.py` | Nuevo | 16 tests del orquestador con `AsyncMock` de AsyncOpenAI |
| `tests/test_api.py` | Actualizado | Mocks de `AsyncOpenAI` + fixture `cliente_con_motor_aislado` |
| `conftest.py` | Actualizado | Fixture `_variables_de_entorno_para_tests` con `OPENROUTER_API_KEY` fake |
| `pytest.ini` | Actualizado | `asyncio_mode = auto` para tests async |
| `requerimientos.txt` | Actualizado | `pytest-asyncio>=0.23,<2.0` |

### Decisiones de diseno

1. **Asincronismo de extremo a extremo**: `OrquestadorRAG.consultar` es
   `async def` y usa `AsyncOpenAI` (`await chat.completions.create(...)`).
   El endpoint `POST /api/chat` tambien es async, asi no se bloquea el
   event loop de FastAPI mientras el LLM piensa.

2. **Inyeccion de dependencias**:
   - `MotorVectorial` se construye UNA sola vez por proceso y se cachea
     en `_motor_vectorial_cache` (`obtener_motor_vectorial`). Esto evita
     los errores "database is locked" de ChromaDB y ahorra RAM.
   - `OrquestadorRAG` se construye por peticion (es barato) pero recibe
     el motor cacheado. El cliente `AsyncOpenAI` se construye dentro del
     orquestador salvo que se inyecte uno mockeado (para tests).

3. **Gobernanza reforzada**:
   - Paso A: `ofuscar_texto(pregunta_cruda)` se ejecuta ANTES de
     cualquier busqueda vectorial o llamada al LLM.
   - La version ofuscada es la que se vectoriza (asi la similitud coseno
     no se rompe con PII) y la que va al `messages[user]` del LLM.
   - Test clave `test_llm_recibe_pregunta_ofuscada_en_mensaje_user`
     verifica que el contenido del mensaje `user` enviado a OpenRouter
     no contiene `Juan Pérez` ni el DNI original.

4. **Contrato del retorno**:
   ```python
   {
       "pregunta_original": str,
       "pregunta_ofuscada": str,   # para auditoria de Gobernanza
       "respuesta": str,
       "modelo": str,
       "fuentes": [{"texto", "metadata", "puntuacion", "coleccion_origen"}, ...],
   }
   ```
   El frontend puede mostrar la `pregunta_ofuscada` en un expander para
   que el abogado verifique que la PII fue enmascarada antes de salir.

### Tests

- 16 nuevos tests en `tests/test_rag_motor.py` (todos async):
  - Cadena basica (4): dict estructurado, ofuscacion, llamada async, modelo correcto.
  - Gobernanza (2): pregunta ofuscada al LLM, contexto en system prompt.
  - Busqueda vectorial (3): tenant, global, exclusion de global.
  - Aislamiento (1): un tenant no ve datos de otro.
  - DI (3): motor inyectado, cliente LLM inyectado, tenant_id str/int.
  - Errores (1): tenant_id invalido lanza ValueError.
  - Fuentes (2): estructura correcta, vacias si no hay contexto.
- 4 tests actualizados en `tests/test_api.py` para mockear `AsyncOpenAI`.
- **Total acumulado del proyecto**: 94 tests pasando (14 ofuscacion +
  10 api + 16 evaluador + 19 ingesta + 18 vectorial + 16 rag_motor + 1 nuevo).
  (El desglose real: ofuscacion 14, api 14, evaluador 16, ingesta 19, vectorial 18, rag_motor 16 = 97. La cuenta actual: ver `pytest tests/`.)

### Pendiente

- Anadir `GET /admin/expedientes` para listar lo ingerido por tenant.
- Persistir los `Mensaje` (pregunta_ofuscada + respuesta + fuentes) en
  SQLite para tener historial y poder re-evaluar.
- Implementar re-ranking con un modelo local mas potente antes del LLM.

---

## Modulo de Motor Vectorial

> **Motor Vectorial implementado con ChromaDB y sentence-transformers (`all-MiniLM-L6-v2`). Aislamiento multi-tenant validado mediante colecciones separadas por ID de usuario.**

### Archivos creados o modificados

| Archivo | Estado | Proposito |
|---|---|---|
| `backend/db/vectorial.py` | Nuevo | Clase `MotorVectorial` con ChromaDB PersistentClient + SentenceTransformer embeddings |
| `tests/test_vectorial.py` | Nuevo | 18 tests: ingestion, busqueda semantica, aislamiento multi-tenant, coleccion global |
| `conftest.py` | Actualizado | Fixture `embedding_function_compartido` (scope=session) para no recargar el modelo en cada test |
| `requerimientos.txt` | Actualizado | `sentence-transformers>=2.2,<4.0`, `chromadb>=0.5,<2.0` |
| `.gitignore` | Actualizado | Bloquea `backend/db/chroma_data/` (vectores NUNCA al repo) |

### Decisiones de diseno

1. **100% local**: embeddings y persistencia ChromaDB corren en proceso.
   Cero llamadas a APIs externas para vectorizar.

2. **Modelo pequeno (`all-MiniLM-L6-v2`, 80 MB, 384 dim)**: equilibrio entre
   calidad semantica y RAM. Corre en CPU sin GPU.

3. **Aislamiento multi-tenant por coleccion** (NO por metadata):
   - Tier 1 (Leyes Publicas) -> `leyes_globales` (unica, accesible para todos).
   - Tier 3 (Casos Privados) -> `casos_user_<id>` (una por tenant).
   - El backend NUNCA consulta dos colecciones de tenants distintos en la
     misma query, garantizando que un tenant no puede ver datos de otro.

4. **Distancia coseno** (`hnsw:space: cosine`) en la configuracion de la
   coleccion: mas adecuada que L2 para embeddings normalizados de
   sentence-transformers.

5. **Testabilidad sin disco**: el constructor acepta un `cliente`
   ChromaDB pre-construido. Los tests usan `chromadb.EphemeralClient`
   con `allow_reset=True` para no ensuciar `backend/db/chroma_data/`
   con basura de tests.

6. **Inyeccion del embedding function**: el fixture `embedding_function_compartido`
   carga el modelo UNA sola vez por corrida de pytest y lo reutiliza en
   todos los tests. Sin esto, cada test paga ~3 segundos de carga del
   modelo de pytorch.

### Tests

- 18 nuevos tests en `tests/test_vectorial.py` cubriendo:
  - Ingestion con metadata (archivo, tier, indice, coleccion).
  - Busqueda semantica: el chunk correcto aparece en Top-K.
  - Ranking por relevancia descendente.
  - Aislamiento estricto entre `casos_user_1` y `casos_user_2`.
  - Inclusion y exclusion de la coleccion global.
  - Validacion de parametros y errores.
- **Total acumulado del proyecto**: 77 tests pasando (14 ofuscacion +
  10 api + 16 evaluador + 19 ingesta + 18 vectorial).

### Pendiente

- Cablear `MotorVectorial` en `backend/api/admin.py` para que el endpoint
  `/admin/expedientes` no solo trocee el PDF sino que tambiEN lo guarde
  en la coleccion del tenant (`casos_user_<id>`).
- Reemplazar el `MotorRAG` placeholder de `backend/api/rutas_chat.py` con
  el flujo completo: `buscar_contexto()` -> OpenRouter -> respuesta.
- Anadir campo `tier` automatico en metadata segun la carpeta de origen
  (`datos_prueba/leyes_publicas/` vs `datos_prueba/casos_clientes/`).

---

## Modulo de Ingesta Documental

> El modulo de ingesta documental ha sido creado para soportar **PDF
> nativos** y **Word** usando **PyMuPDF** y **python-docx**. Excel se
> excluyo por **integridad estructural** (requiere procesamiento tabular
> que no encaja con el chunking por caracteres del RAG). Carpeta de
> pruebas **protegida contra fugas de datos en Git** mediante
> `.gitignore` estricto.

### Archivos creados o modificados

| Archivo | Estado | Proposito |
|---|---|---|
| `backend/servicios/gestion_pdf.py` | Reescrito | 3 funciones puras: `extraer_texto_pdf`, `extraer_texto_docx`, `segmentar_texto` |
| `tests/test_ingesta.py` | Nuevo | 19 tests: TDD del chunking, roundtrip PDF, roundtrip DOCX, errores de entrada |
| `datos_prueba/leyes_publicas/` | Nuevo (Tier 1) | Leyes publicas (BOE, codigos, doctrina). Sin PII. |
| `datos_prueba/casos_clientes/` | Nuevo (Tier 3) | Expedientes de clientes. Contiene PII. |
| `.gitignore` | Actualizado | Bloquea `datos_prueba/`, `*.pdf`, `*.docx`, `*.doc`, `*.xls`, `*.xlsx`, `*.odt`, `*.rtf` |
| `requerimientos.txt` | Actualizado | Anade `pymupdf>=1.24,<2.0` y `python-docx==1.1.2` |
| `backend/api/admin.py` | Actualizado | `fragmentar_texto` -> `segmentar_texto` (rename) |

### Decisiones de diseno

1. **Sin OCR**. Se asume PDF seleccionable. Si un PDF viene escaneado,
   `extraer_texto_pdf` lanza `ErrorIngestaDocumento` con el mensaje
   "ejecutar OCR externamente antes de ingestar". Esto mantiene la RAM
   baja, alineado con la arquitectura hibrida del proyecto.

2. **PyMuPDF sobre pypdf**: ~10x mas rapido y mejor manejo de tablas
   y notas al pie, tipicas en documentos legales.

3. **Chunking por caracteres con overlap fijo** (1000 / 200 por defecto).
   Suficiente para la mayoria de textos juridicos. La mejora a chunking
   por fronteras de oracion o parrafo queda en el roadmap.

4. **Data Tiering**: las dos carpetas en `datos_prueba/` reflejan los
   niveles de sensibilidad. En el futuro, `backend/api/admin.py`
   etiquetara los metadatos de ChromaDB con el tier correspondiente
   para aplicar politicas de acceso diferenciadas.

### Tests

- 19 nuevos tests en `tests/test_ingesta.py`.
- Cobertura: lista de strings, overlap entre chunks, validacion de
  parametros, roundtrip PDF y DOCX, error en PDF sin texto, error en
  extension incorrecta, tablas en DOCX.
- **Total acumulado del proyecto**: 59 tests pasando (14 ofuscacion +
  10 api + 16 evaluador + 19 ingesta).

### Pendiente

- Endpoint `GET /admin/expedientes` para listar lo ingerido.
- Deteccion automatica de PDFs escaneados (sin texto) en lugar de
  exigir OCR manual.
- Cablear el `MotorVectorial` directamente con `gestion_pdf.segmentar_texto`
  en el endpoint `/admin/expedientes` para automatizar la ingestion
  (hoy el endpoint solo cuenta chunks, no los persiste).

---

## Modulo de Gobernanza PII (existente)

- `backend/servicios/ofuscacion.py` ofusca nombres, emails, telefonos,
  DNI, IBAN, tarjetas y fechas antes de cualquier llamada externa.
- Regla de Oro verificada por test
  `test_ofuscacion.py::test_ofusca_nombre_propio_juan_perez` y por
  el test E2E `test_api.py::test_chat_aplica_ofuscacion_antes_del_llm`.

## Modulo de Evals (existente)

- `evals/dataset_dorado.json` con 5 casos juridicos.
- `evals/evaluar_rag.py` ejecutable manualmente con `--dry-run` o
  con tokens reales. Log historico append-only en `evals/metricas.md`.

## Frontend SaaS (existente)

- `frontend/app.py` con sidebar de Llave de Acceso (JWT) y chat con
  historial en `st.session_state`.
- Conexion HTTP unicamente al backend (puerto 8000). NUNCA toca
  SQLite, ChromaDB ni OpenRouter.

## Endpoints expuestos (resumen)

| Metodo | Ruta | Modulo |
|---|---|---|
| GET  | `/salud` | `backend/main.py` |
| POST | `/auth/registro` | `backend/api/auth.py` |
| POST | `/auth/login` | `backend/api/auth.py` |
| POST | `/admin/expedientes` | `backend/api/admin.py` (multipart) |
| POST | `/chat/preguntar` | `backend/api/chat.py` (RAG completo) |
| POST | `/api/chat` | `backend/api/rutas_chat.py` (placeholder) |
| GET  | `/api/chat/salud` | `backend/api/rutas_chat.py` (valida JWT) |
