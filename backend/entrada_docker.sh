#!/bin/bash
# Entrypoint del backend en Docker: siembra demo si se pide y arranca uvicorn.
set -e

if [ "${AUTO_SEMBRAR_DEMO:-0}" = "1" ]; then
  echo "[entrada] AUTO_SEMBRAR_DEMO=1: verificando datos demo..."
  python /app/scripts/sembrar_demo.py || echo "[entrada] aviso: la siembra fallo, se arranca igual"
fi

echo "[entrada] Arrancando API + interfaz web en :8000 ..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
