#!/bin/bash
# ============================================================
# iniciar_saas.sh
# Arranca Backend FastAPI (:8000) y Frontend Streamlit (:8501)
# en una sola terminal, con limpieza automatica al salir.
#
# Uso:
#   chmod +x iniciar_saas.sh
#   ./iniciar_saas.sh
#
# Ctrl+C: mata ambos procesos limpiamente y termina el script.
# ============================================================

set -e

echo "Iniciando LegalRAG SaaS B2B..."

# Activar entorno virtual si existe (Unix/Linux/Mac).
# En Windows este archivo no se ejecuta: ver README.md para el .bat equivalente.
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# ------------------------------------------------------------
# Funcion de limpieza: se ejecuta al recibir INT/TERM o al
# terminar el script (trap EXIT). Asi nunca queda un proceso
# zombi si el usuario hace Ctrl+C durante el `sleep 3` o
# durante el `streamlit run`.
# ------------------------------------------------------------
cleanup() {
    echo ""
    echo "Apagando sistema LegalRAG SaaS..."
    if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null || true
        # Espera breve a que uvicorn cierre bien; si no, SIGKILL.
        sleep 1
        kill -0 "$BACKEND_PID" 2>/dev/null && kill -9 "$BACKEND_PID" 2>/dev/null || true
    fi
    exit 0
}
# El orden importa: primero EXIT (cubre salida normal y Ctrl+C),
# luego INT y TERM (cubren las senales especificas antes de que
# el script termine). Asi cleanup corre SIEMPRE.
trap cleanup EXIT
trap cleanup INT
trap cleanup TERM

# ------------------------------------------------------------
# 1) Backend FastAPI.
# ------------------------------------------------------------
echo "[1/2] Levantando Cerebro FastAPI (Puerto 8000)..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "      Backend PID=$BACKEND_PID"

# Esperar a que ChromaDB y SQLite se inicialicen antes de levantar Streamlit.
# Asi, cuando el usuario abre la UI, el backend ya responde.
sleep 3

# ------------------------------------------------------------
# 2) Frontend Streamlit.
# ------------------------------------------------------------
echo "[2/2] Levantando Interfaz Streamlit (Puerto 8501)..."
echo "      Abre http://localhost:8501 en tu navegador."
echo "      Ctrl+C para detener ambos servicios."
echo ""
streamlit run frontend/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0

# Cuando streamlit termina (Ctrl+C o salida normal), el trap EXIT
# ejecuta cleanup() que mata el backend.
