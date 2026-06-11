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

Tablas minimas para soportar multi-tenancy y trazabilidad:

- **tenants**: `id`, `nombre`, `fecha_alta`.
- **usuarios**: `id`, `tenant_id`, `email`, `hash_contrasena`, `rol`.
- **expedientes**: `id`, `tenant_id`, `titulo`, `ruta_archivo`, `fecha_alta`.
- **mensajes**: `id`, `tenant_id`, `usuario_id`, `pregunta_ofuscada`, `respuesta`, `timestamp`.

Toda consulta de negocio debe filtrar por `tenant_id` extraido del JWT.

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

4. **CORS esta abierto (`allow_origins=["*"]`) solo en desarrollo.** En
   produccion, `backend/main.py` debe restringir a los origenes del
   frontend desplegado.

5. **El frontend es configurable** mediante la variable de entorno
   `URL_API_BACKEND` (default `http://localhost:8000`). Esto permite
   apuntar a un backend remoto sin tocar codigo.

### Endpoints clave consumidos por el frontend

| Metodo | Ruta | Proposito |
|---|---|---|
| GET  | `/salud` | Healthcheck publico del backend |
| GET  | `/api/chat/salud` | Healthcheck autenticado: valida el JWT |
| POST | `/api/chat` | Endpoint principal: recibe pregunta, ofusca, responde (placeholder) |
| POST | `/auth/login` | Login username/password (devuelve JWT) |
| POST | `/auth/registro` | Registro de tenant (devuelve JWT) |
| POST | `/admin/expedientes` | Subida de PDFs (multipart) |
| POST | `/chat/preguntar` | Endpoint RAG completo (incluye OpenRouter) |

El frontend Streamlit usa `/api/chat` y `/api/chat/salud` para el flujo
basico (conexion + chat). El endpoint `/chat/preguntar` se reserva para
clientes que quieran invocar la cadena RAG completa sin pasar por la
UI.

### Comandos de arranque

```powershell
# Terminal 1: backend
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:aplicacion --reload --port 8000

# Terminal 2: frontend
.\.venv\Scripts\Activate.ps1
streamlit run frontend/app.py
# Streamlit abrira automaticamente http://localhost:8501
```

Para apuntar el frontend a otro backend (por ejemplo, un staging):

```powershell
$env:URL_API_BACKEND = "https://staging-legalrag.example.com"
streamlit run frontend/app.py
```
