# 01 - Arquitectura Detallada

## 1. Capas del Backend

El backend sigue una separacion por capas para mantener la logica aislada y testeable.

```
backend/
|-- main.py              # Punto de entrada FastAPI (crea la app, monta routers, eventos de inicio/fin)
|-- core/
|   |-- configuracion.py # Carga de variables de entorno (Settings)
|   |-- seguridad.py     # Hash de contrasenas, firma y validacion de JWT
|-- api/
|   |-- auth.py          # Endpoints: registro, login, refresh
|   |-- admin.py         # Endpoints de administracion del tenant (subir PDFs, listar expedientes)
|   |-- chat.py          # Endpoint de preguntas (orquesta la cadena RAG)
|-- db/
|   |-- base.py          # Declarative Base de SQLAlchemy
|   |-- modelos.py       # Modelos: Tenant, Usuario, Expediente, Mensaje
|   |-- sesion.py        # Engine y SessionLocal
|-- servicios/
    |-- ofuscacion.py    # Filtro PII (regex + diccionario configurable)
    |-- gestion_pdf.py   # Lectura y chunking de documentos legales
    |-- rag_motor.py     # Orquestacion: embed -> retrieve -> prompt -> OpenRouter
```

## 2. Capa de Datos Relacional (SQLite)

### 2.1 Esquema

Ruta por defecto: `backend/db/legalrag.db` (variable de entorno `CADENA_BD`).

| Tabla | Columnas | Notas |
|---|---|---|
| **tenants** | `id`, `nombre` (UNIQUE), `fecha_alta` | Una fila por firma de abogados. |
| **usuarios** | `id`, `tenant_id` (FK), `username` (UNIQUE), `password_hash`, `rol`, `fecha_alta` | `password_hash` SIEMPRE bcrypt. NUNCA texto plano. |
| **expedientes** | `id`, `tenant_id` (FK), `titulo`, `ruta_archivo`, `fecha_alta` | Metadata de PDFs/DOCX ingestados. |
| **mensajes** | `id`, `tenant_id` (FK), `usuario_id` (FK), `pregunta_ofuscada`, `respuesta`, `timestamp` | Auditoria del chat. |

Toda consulta de negocio debe filtrar por `tenant_id` extraido del JWT.

### 2.2 RBAC (Role-Based Access Control)

Dos roles validos (constantes en `backend/db/modelos.py`):

| Rol | Permisos |
|---|---|
| `admin` | Crear usuarios, cargar PDFs, consultar el RAG. |
| `abogado` | Consultar el RAG (su coleccion privada + global). NO puede crear usuarios ni subir docs. |

Endpoints sensibles (crear usuarios, subir PDFs) usan la dependencia
`verificar_admin` que devuelve **403 Forbidden** si el claim `rol` del
JWT no es `admin`. Esto se valida en backend, no se fia del frontend.

### 2.3 Seguridad de contrasenas

- Algoritmo: bcrypt directo (sin passlib, por compatibilidad con la
  version actual de Python).
- Salt: aleatorio por hash (dos hashes de la misma contrasena son distintos).
- Comparacion: `bcrypt.checkpw()` con manejo de excepciones (`ValueError`
  -> `False`, no panic).
- Test de regresion: `test_contrasena_no_se_almacena_en_texto_plano`
  verifica que tras un registro la BD contiene `$2...` (prefijo bcrypt)
  y NO la contrasena original.

### 2.4 JWT

- Libreria: `python-jose` (jose.jwt).
- Claims minimos: `sub` (usuario_id), `tenant_id`, `username`, `rol`,
  `iat`, `exp`.
- Expiracion por defecto: 60 minutos (`EXPIRACION_TOKEN_MINUTOS`).
- Algoritmo: HS256 con `SECRET_KEY_JWT`.
- Flujo OAuth2 Password: `POST /api/auth/token` recibe
  `application/x-www-form-urlencoded` con `username` y `password`.

## 3. Capa Vectorial (ChromaDB Local)

- Directorio persistente: `./chromadb` (ruta configurable por `RUTA_CHROMADB`).
- Coleccion por tenant: `expedientes_<tenant_id>`, creada lazily en el primer ingest.
- Metadatos por chunk: `expediente_id`, `pagina`, `posicion`.
- Distancia: coseno (default de ChromaDB).

## 4. Cadena RAG (rag_motor.py)

```
pregunta_cruda
   -> ofuscar_texto(pregunta_cruda)           # servicios/ofuscacion.py
   -> embedding(pregunta_ofuscada)            # sentence-transformers (local)
   -> chromadb.query(embedding, filtro_tenant) # vector store local
   -> construir_prompt(contexto, pregunta)    # prompt template
   -> openai_client.chat.completions.create   # OpenRouter
   -> respuesta
```

## 5. Frontend (Streamlit)

- `frontend/app.py` contiene la UI principal.
- Maneja estado de sesion con `st.session_state`.
- Llama a la API FastAPI con `httpx` o `requests`.
- Nunca expone la API key de OpenRouter (vive solo en el backend).

## 6. Despliegue Local (desarrollo)

```powershell
# 1. Crear y activar entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Instalar dependencias
pip install -r requerimientos.txt

# 3. Copiar variables de entorno
Copy-Item .env.ejemplo .env
# Editar .env con valores reales

# 4. Levantar backend (terminal 1)
uvicorn backend.main:app --reload --port 8000

# 5. Levantar frontend (terminal 2)
streamlit run frontend/app.py

# 6. Ejecutar tests
pytest -v
```

## 7. Decisiones Tecnicas Clave (ADR resumidos)

- **FastAPI** sobre Flask: tipado fuerte con Pydantic y OpenAPI automatico.
- **SQLite** sobre Postgres: arranque sin dependencias externas; migrable luego.
- **ChromaDB** sobre FAISS: persistencia nativa y filtros por metadatos (tenant).
- **sentence-transformers** local: el modelo `all-MiniLM-L6-v2` mide ~80 MB y corre en CPU.
- **openai SDK** contra OpenRouter: misma interfaz, sin acoplar a un proveedor.
- **Streamlit** sobre React: prototipado rapido para SaaS interno de baja escala inicial.

## 8. Arquitectura de Puertos

La plataforma corre como **dos procesos independientes** en la misma
maquina de desarrollo, comunicandose exclusivamente por HTTP. Esta
separacion es deliberada: el frontend es reemplazable (Streamlit hoy,
podria ser React manana) y el backend es la unica superficie que toca
datos sensibles y proveedores externos.

| Servicio | Proceso | Puerto por defecto | URL local | Configuracion |
|---|---|---|---|---|
| Backend FastAPI | `uvicorn backend.main:aplicacion` | **8000** | `http://localhost:8000` | Variable `URL_API_BACKEND` en el frontend |
| Frontend Streamlit | `streamlit run frontend/app.py` | **8501** | `http://localhost:8501` | Variable `URL_API_BACKEND` apunta al backend |

### Diagrama de puertos

```
   Navegador del abogado
            |
            v  (HTTPS en produccion, HTTP en dev)
   +-----------------------+
   |  Streamlit            |   puerto 8501
   |  frontend/app.py      |   (no toca BD ni LLM)
   +----------+------------+
              |
              |  HTTP REST (JSON)
              |  Authorization: Bearer <jwt>
              v
   +-----------------------+
   |  FastAPI              |   puerto 8000
   |  backend/main.py      |   - Valida JWT
   |  /api/chat            |   - Ofusca PII (Regla de Oro)
   |  /auth/login          |   - Lee ChromaDB local
   |  /admin/expedientes   |   - Escribe a SQLite
   +----+---------+--------+
        |         |
        v         v
   ChromaDB    OpenRouter
   (local)     (HTTPS externo, solo prompt ofuscado)
```

### Reglas de la arquitectura de puertos

1. **El frontend NUNCA se conecta a SQLite, ChromaDB ni OpenRouter.**
   Toda la logica pasa por el backend. Esto mantiene un unico punto
   donde se aplica la Gobernanza PII.

2. **El backend NUNCA hace redirect ni render de HTML.** Es un servicio
   JSON puro. El frontend consume el JSON.

3. **Toda peticion Frontend -> Backend lleva el header**
   `Authorization: Bearer <jwt>`. Sin token, el endpoint responde
   `401 Unauthorized`.

4. **CORS esta restringido** a los origenes exactos del frontend:
   `http://localhost:8501` y `http://127.0.0.1:8501` (ver
   `backend/main.py:42-51`). Wildcard `["*"]` esta prohibido en
   produccion porque rompe la proteccion CSRF del navegador.

5. **El frontend es configurable** mediante la variable de entorno
   `URL_API_BACKEND` (default `http://localhost:8000`). Esto permite
   apuntar a un backend remoto sin tocar codigo.

### Endpoints clave consumidos por el frontend

| Metodo | Ruta | Auth | Proposito |
|---|---|---|---|
| GET  | `/salud` | publico | Healthcheck del backend |
| POST | `/auth/registro` | publico | Crea tenant + primer admin (legacy/dev) |
| POST | `/auth/login` | publico | Login legacy (JSON) |
| POST | `/api/auth/token` | publico | **OAuth2 Password Grant** (form-urlencoded) |
| GET  | `/api/chat/salud` | JWT | Healthcheck autenticado |
| POST | `/api/chat` | JWT | Cadena RAG completa (ofusca + ChromaDB + OpenRouter + auditoria) |
| POST | `/chat/preguntar` | JWT | Cadena RAG completa (esquema compacto + auditoria) |
| POST | `/api/admin/usuarios` | JWT + `rol=admin` | Crea usuarios en un tenant |
| POST | `/api/admin/documentos` | JWT + `rol=admin` | Sube PDFs y los indexa |
| POST | `/admin/expedientes` | JWT | Legacy: cuenta chunks de un PDF |

El frontend Streamlit usa:
- `frontend/app.py`: `/api/chat` y `/api/chat/salud` para el flujo de chat.
- `frontend/paginas/admin.py`: `/api/admin/usuarios` y `/api/admin/documentos`
  para la consola de admin (visible solo para usuarios con `rol=admin`).

El endpoint `/api/auth/token` es el estandar OAuth2 y se usa desde la
interfaz de admin (y por clientes HTTP externos).

### Comandos de arranque

#### Linux / macOS (recomendado): un solo script

```bash
chmod +x iniciar_saas.sh
./iniciar_saas.sh
```

El script `iniciar_saas.sh`:

1. Activa `.venv` si existe.
2. Levanta el backend (`uvicorn`) en segundo plano.
3. Espera 3 segundos a que ChromaDB y SQLite se inicialicen.
4. Levanta el frontend (`streamlit run`) en primer plano.
5. Captura `Ctrl+C` y mata **ambos procesos** limpiamente
   (`trap EXIT/INT/TERM` + verificacion de PID vivo).

#### Windows: dos terminales o un `.bat`

PowerShell equivalente en dos terminales:

```powershell
# Terminal 1
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:aplicacion --port 8000

# Terminal 2
.\.venv\Scripts\Activate.ps1
streamlit run frontend/app.py --server.port 8501
```

O bien un `iniciar_saas.bat` que use `start` para abrir el backend
en una ventana aparte (ver `README.md` para el codigo).

Para apuntar el frontend a otro backend (por ejemplo, un staging):

```powershell
$env:URL_API_BACKEND = "https://staging-legalrag.example.com"
streamlit run frontend/app.py
```

## 9. Telemetria y Auditoria (Fase 6)

### 9.1 Objetivo

Cada vez que el orquestador responde una pregunta juridica, queremos
dejar constancia para tres fines:

1. **Cumplimiento legal**: rastro de quien pregunto que, cuando
   y sobre que documentos (requisito del RGPD / legislacion de
   trazabilidad juridica).
2. **Costes**: medicion del consumo de tokens de OpenRouter por
   tenant y por modelo, para poder facturar o limitar uso.
3. **Calidad**: set de pares `(pregunta_ofuscada, respuesta_llm,
   fuentes)` que alimenta el `evals/evaluar_rag.py` para detectar
   regresiones.

### 9.2 Modelo de datos: `RegistroAuditoria`

Tabla `registros_auditoria` (`backend/db/modelos.py`):

| Columna | Tipo | Notas |
|---|---|---|
| `id` | Integer PK | Autoincrement. |
| `tenant_id` | String(120), **indexado** | El de la FIRMA (no del abogado). String (no FK) para que el registro sobreviva a borrados del tenant. |
| `pregunta_ofuscada` | Text | **Regla de Oro**: solo la pregunta YA OFUSCADA llega aqui. NUNCA la cruda. |
| `respuesta_llm` | Text | Texto generado por el LLM. |
| `fuentes_usadas` | String(1000) | IDs de ChromaDB separados por comas (`id1,id2,...`). |
| `tokens_prompt` | Integer | Tokens consumidos en el prompt. |
| `tokens_completion` | Integer | Tokens generados por el LLM. |
| `timestamp` | DateTime, `server_default=func.now()` | Inmutable, puesto por la BD. |

### 9.3 Asincronismo con `BackgroundTasks`

El guardado se hace via `BackgroundTasks` de FastAPI para que el
usuario NO pague la latencia de la BD:

```
HTTP POST /api/chat
   |
   v
[endpoint async]
   |
   +-- ofuscar pregunta
   +-- buscar contexto (ChromaDB)
   +-- await orquestador.consultar(...)    <-- bloquea hasta que el LLM responde
   |
   +-- background_tasks.add_task(guardar_auditoria, ...)
   |
   v
[respuesta 200 enviada al cliente]  <-- el cliente ya recibio respuesta
   |
   v
[background task corre en background]   <-- abre su propia sesion SQLite, escribe, cierra
```

**Reglas clave**:

- `guardar_auditoria` **abre su PROPIA sesion** de SQLAlchemy
  (`obtener_sesion_bd()`). La sesion de la request HTTP ya esta
  cerrada cuando corre el background task.
- `BackgroundTasks` ejecuta la tarea en el MISMO event loop,
  despues de enviar la respuesta. Si la tarea falla (ej. BD
  bloqueada), el usuario YA recibio su 200; la excepcion se loguea
  pero no afecta al cliente. Esto lo valida
  `test_endpoint_api_chat_no_bloquea_si_falla_la_auditoria`.

### 9.4 Extraccion de tokens en el orquestador

El orquestador (`backend/servicios/rag_motor.py`) lee
`respuesta.usage.prompt_tokens` y `respuesta.usage.completion_tokens`
de forma defensiva:

```python
tokens_prompt = 0
tokens_completion = 0
usage = getattr(respuesta_llm, "usage", None)
if usage is not None:
    tokens_prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_completion = int(getattr(usage, "completion_tokens", 0) or 0)
```

Si el proveedor no devuelve `usage` (modelo antiguo, mock, error), el
orquestador registra 0 y sigue. Esto se valida con
`test_consultar_sin_usage_devuelve_tokens_cero` y
`test_consultar_extrae_tokens_del_usage`.

### 9.5 Diagrama de la cadena RAG + Auditoria

```
Abogado (Streamlit)        FastAPI                OrquestadorRAG        ChromaDB       OpenRouter       SQLite (audit)
      |                       |                        |                    |               |                  |
      |--POST /api/chat JWT-->|                        |                    |               |                  |
      |                       |--consultar(...)-------->|                    |               |                  |
      |                       |                        |--ofuscar texto     |               |                  |
      |                       |                        |--buscar_contexto-->|               |                  |
      |                       |                        |<--fragmentos--------|               |                  |
      |                       |                        |--await LLM--------->|               |                  |
      |                       |                        |<--respuesta+usage--|               |                  |
      |                       |<--dict {resp, tokens, fuentes, ofuscada}                  |                  |
      |<--200 OK {respuesta, tokens_*, fuentes}---|                        |               |                  |
      |                       |                        |                    |               |                  |
      |                       |--[BACKGROUND]-->guardar_auditoria(...)     |               |                  |
      |                       |                        |                    |               |                  |
      |                       |                        |                    |               |                  |--INSERT row
```
