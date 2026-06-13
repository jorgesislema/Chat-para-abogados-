# LegalRAG_SaaS

> Plataforma SaaS multi-tenant para firmas de abogados.
> Busqueda semantica + RAG sobre documentos legales con embeddings 100%
> locales y razonamiento externalizado via OpenRouter.

## Que es esto

LegalRAG_SaaS combina:

- **Backend FastAPI** (puerto 8000): autenticacion, RBAC, ingesta de
  PDFs/DOCX, motor vectorial ChromaDB y orquestacion del LLM.
- **Frontend Streamlit** (puerto 8501): UI para que los abogados
  suban expedientes y hagan preguntas juridicas.
- **Embeddings locales** con `sentence-transformers/all-MiniLM-L6-v2`
  (sin llamadas externas para vectorizar).
- **LLM externo** via OpenRouter (solo recibe texto previamente
  ofuscado por la capa de Gobernanza PII).

## 🐳 Despliegue con Docker (Recomendado)

La forma más fiable de ejecutar LegalRAG_SaaS en cualquier máquina (local o
futuro VPS) es mediante Docker Compose. Los dos servicios se construyen
desde sus respectivos Dockerfiles, comparten una red privada de Docker y
persisten los datos de SQLite y ChromaDB en `backend/db/` mediante un
bind-mount, de modo que los expedientes no se pierden al reiniciar los
contenedores.

### Arquitectura del `docker-compose.yml`

| Servicio    | Imagen base           | Puerto host | Volumen de persistencia          | Red interna     |
|-------------|-----------------------|-------------|----------------------------------|-----------------|
| `backend`   | `python:3.11-slim`    | `8000`      | `./backend/db:/app/backend/db`   | `legalrag-net`  |
| `frontend`  | `python:3.11-slim`    | `8501`      | (no requiere)                    | `legalrag-net`  |

El frontend se conecta al backend usando el **nombre del servicio**
(`http://backend:8000`) gracias a la red interna de Docker Compose.
**Nunca uses `localhost`** dentro del contenedor.

### Pre-requisitos

- Docker Engine >= 24 y Docker Compose v2.
- Archivo `.env` en la raíz (copia de `.env.ejemplo`) con al menos:
  - `OPENROUTER_API_KEY`
  - `SECRET_KEY_JWT`

### Construir y arrancar

```bash
# 1) Construir las imagenes (solo la primera vez, o cuando cambien los Dockerfiles)
docker-compose build

# 2) Levantar backend + frontend en segundo plano
docker-compose up -d

# 3) Ver logs en tiempo real
docker-compose logs -f
```

Al cabo de unos 20-30 segundos (descarga de `sentence-transformers`
incluida), la suite queda operativa:

| URL                                   | Esperado                                        |
|---------------------------------------|-------------------------------------------------|
| `http://localhost:8000/salud`         | `{"estado":"ok","servicio":"LegalRAG_SaaS"}`    |
| `http://localhost:8501`               | UI de login de Streamlit                        |

### Comandos utiles

```bash
docker-compose ps              # Estado de los contenedores
docker-compose logs backend    # Solo logs del backend
docker-compose restart         # Reiniciar ambos servicios
docker-compose down            # Detener y eliminar contenedores (los datos en backend/db/ PERSISTEN)
docker-compose down -v         # Detener y eliminar tambien el volumen (BORRA expedientes)
docker-compose exec backend bash   # Abrir shell dentro del backend
```

### Persistencia y Gobernanza

- **SQLite** (`backend/db/legalrag.db`) y **ChromaDB**
  (`backend/db/chroma_data/`) viven dentro de `./backend/db/`, que está
  bind-mounteado a `/app/backend/db` en el contenedor. Si borras los
  contenedores con `docker-compose down` (sin `-v`), los expedientes
  siguen intactos en tu disco local.
- Las claves secretas se inyectan via `env_file: .env` y nunca se
  bakean dentro de la imagen. **No commitees `.env`**: ya está en
  `.gitignore`.

### Estructura nueva del proyecto

```
.
|-- backend/
|   |-- Dockerfile           # Imagen del backend
|   `-- ... (api, core, db, servicios, main.py)
|-- frontend/
|   |-- Dockerfile           # Imagen del frontend
|   |-- requerimientos.txt   # Dependencias minimas (streamlit + httpx)
|   `-- app.py
|-- docker-compose.yml       # Orquestacion backend + frontend
|-- .env                     # Variables secretas (NO commitear)
|-- .env.ejemplo             # Plantilla de variables
`-- requerimientos.txt       # Dependencias del backend
```

## Como ejecutar el proyecto (Modo Produccion Local)

### Linux / macOS (recomendado)

Un solo comando levanta backend + frontend con limpieza automatica
de procesos al salir (Ctrl+C).

```bash
# 1) Dar permisos de ejecucion al script (solo la primera vez)
chmod +x iniciar_saas.sh

# 2) Arrancar todo
./iniciar_saas.sh
```

El script:

- Activa `.venv` si existe.
- Levanta el backend en `http://localhost:8000`.
- Espera 3 segundos a que ChromaDB y SQLite se inicialicen.
- Levanta el frontend en `http://localhost:8501`.
- Captura Ctrl+C y mata AMBOS procesos (backend + frontend)
  limpiamente.

Para detener el sistema: `Ctrl+C` en la terminal donde corre el script.

### Windows (PowerShell)

El script `.sh` no se ejecuta directamente en Windows. Tienes dos
opciones equivalentes:

**Opcion A: dos terminales separadas** (la mas simple).

```powershell
# Terminal 1: backend
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Terminal 2: frontend
.\.venv\Scripts\Activate.ps1
streamlit run frontend/app.py --server.port 8501
```

**Opcion B: archivo `.bat` equivalente** (un solo doble-clic).

Crea `iniciar_saas.bat` en la raiz:

```bat
@echo off
echo Iniciando LegalRAG SaaS B2B...

REM Activar entorno virtual
call .venv\Scripts\activate.bat

REM Lanzar backend en segundo plano
start "Backend FastAPI" cmd /k "uvicorn backend.main:app --host 0.0.0.0 --port 8000"

REM Esperar inicializacion de ChromaDB y SQLite
timeout /t 3 /nobreak > NUL

REM Lanzar frontend
streamlit run frontend/app.py --server.port 8501
```

Nota: el frontend hereda la ventana actual (al cerrarla se cierra
Streamlit, pero el backend queda en su propia ventana). Para matar
ambos: cerrar las dos ventanas.

## Verificacion de la instalacion

Tras arrancar, abre dos URLs para confirmar que todo responde:

| URL | Esperado |
|---|---|
| `http://localhost:8000/salud` | `{"estado":"ok","servicio":"LegalRAG_SaaS"}` |
| `http://localhost:8501` | UI de login con sidebar de Streamlit |

## Suite de tests

```bash
.\.venv\Scripts\Activate.ps1
pytest -v
```

Estado actual: **117 tests pasando** (ofuscacion, auth, RBAC, ingesta,
vector store, motor RAG, evaluador de calidad).

## Estructura del proyecto

```
.
|-- backend/                # FastAPI + SQLAlchemy + servicios
|   |-- api/                # Routers REST
|   |-- core/               # Configuracion, JWT, hashing
|   |-- db/                 # Modelos + engine SQLAlchemy
|   |-- servicios/          # Ofuscacion, gestion_pdf, rag_motor, vectorial
|   `-- main.py             # Entry point FastAPI
|-- frontend/
|   |-- app.py              # UI principal
|   `-- paginas/
|       `-- admin.py        # UI de administracion (RBAC)
|-- tests/                  # Suite pytest (TDD)
|-- evals/                  # Evaluaciones de calidad del LLM
|-- docs_ia/                # Memoria del proyecto
|-- datos_prueba/           # [GITIGNORED] PDFs/DOCX de prueba
|-- iniciar_saas.sh         # Script de arranque (Unix)
|-- conftest.py             # Fixtures compartidos
|-- pytest.ini              # Configuracion de pytest
|-- requerimientos.txt      # Dependencias
`-- .env.ejemplo            # Plantilla de variables de entorno
```

## Configuracion (.env)

Copia `.env.ejemplo` a `.env` y rellena al menos:

```env
OPENROUTER_API_KEY=sk-or-v1-...   # https://openrouter.ai/keys
SECRET_KEY_JWT=<64+ caracteres aleatorios>
```

El resto de variables tienen defaults sensatos. Ver
`docs_ia/01_arquitectura.md` para el detalle completo.

## Documentacion interna

- [Arquitectura detallada](./docs_ia/01_arquitectura.md)
- [Gobernanza y PII](./docs_ia/02_gobernanza.md)
- [Estado actual del proyecto](./docs_ia/03_estado_actual.md)
- [Testing vs Evals](./docs_ia/04_testing_y_evals.md)
- [Mapa de la arquitectura](./mapa.md)
