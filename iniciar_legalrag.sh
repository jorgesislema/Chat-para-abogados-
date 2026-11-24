#!/bin/bash
echo "==================================================="
echo "  Iniciando Servidor Local de LegalRAG"
echo "==================================================="

echo "[1/3] Levantando infraestructura segura..."
docker-compose up -d --build

echo "[2/3] Esperando a que los motores arranquen (10 segundos)..."
sleep 10

echo "[3/3] Abriendo la plataforma en su navegador..."
# Detectar el SO para usar el comando de abrir navegador correcto
if which xdg-open > /dev/null; then
  xdg-open http://localhost:8501
elif which open > /dev/null; then
  open http://localhost:8501
else
  echo "Por favor, abra su navegador y entre a http://localhost:8501"
fi

echo "¡Sistema en linea!"
