@echo off
title Iniciando Bufete Inteligente LegalRAG...
echo ===================================================
echo   Iniciando Servidor Local de LegalRAG
echo   Por favor, espere mientras se carga la IA...
echo ===================================================

echo [1/3] Levantando infraestructura segura...
docker compose up -d --build

echo [2/3] Esperando a que los motores arranquen (60 segundos la primera vez:
echo        se descargan los modelos locales de IA)...
timeout /t 60 /nobreak > NUL

echo [3/3] Abriendo la plataforma en su navegador...
start http://localhost:8000

echo ¡Sistema en linea! Puede cerrar esta ventana negra.
exit
