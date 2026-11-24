"""
Recopilador de logs: guarda un registro de CADA llamada y de cada error.

Salida: `logs/legalrag.jsonl` (una linea JSON por evento, con rotacion).
Cada llamada HTTP genera un evento con: id de llamada, metodo, ruta,
estado, milisegundos, usuario y, si falla, el tipo de error y su traza.
El navegador tambien reporta sus errores (POST /api/logs/cliente).

PRIVACIDAD: NUNCA se guardan cuerpos de peticion, contrasenas, tokens ni
el texto de las preguntas. Solo metadatos tecnicos.
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

RAIZ = Path(__file__).resolve().parents[2]
DIR_LOGS = Path(os.getenv("RUTA_LOGS", str(RAIZ / "logs")))
ARCHIVO_LOG = DIR_LOGS / "legalrag.jsonl"
MAX_BYTES = 5 * 1024 * 1024
COPIAS = 5
MAX_TRAZA = 4000

# Rutas que no se registran (ruido).
_IGNORAR = {"/salud", "/favicon.ico"}
_PREFIJOS_ESTATICOS = (".js", ".css", ".png", ".ico", ".svg", ".html")

LOGGER = logging.getLogger("legalrag.llamadas")
_listo = False
# Ultimos eventos en memoria para consultarlos rapido desde el panel.
_recientes: deque[dict[str, Any]] = deque(maxlen=500)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class _FormatoJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        evento = getattr(record, "evento", None)
        if evento is None:
            evento = {
                "ts": _ahora(),
                "nivel": record.levelname,
                "origen": record.name,
                "mensaje": record.getMessage(),
            }
            if record.exc_info:
                evento["traza"] = "".join(traceback.format_exception(*record.exc_info))[-MAX_TRAZA:]
        return json.dumps(evento, ensure_ascii=False, default=str)


class _ColectorMemoria(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _recientes.append(json.loads(self.format(record)))
        except Exception:  # noqa: BLE001 - un log jamas debe tumbar la app
            pass


def configurar_logs() -> None:
    """Instala los manejadores una sola vez (idempotente)."""
    global _listo
    if _listo:
        return
    try:
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        archivo = RotatingFileHandler(
            ARCHIVO_LOG, maxBytes=MAX_BYTES, backupCount=COPIAS, encoding="utf-8"
        )
        archivo.setFormatter(_FormatoJson())
        memoria = _ColectorMemoria()
        memoria.setFormatter(_FormatoJson())
        raiz = logging.getLogger()
        raiz.setLevel(logging.INFO)
        for h in (archivo, memoria):
            raiz.addHandler(h)
        # Silencia detalles ruidosos de librerias.
        for ruidoso in ("httpx", "httpcore", "chromadb", "sentence_transformers"):
            logging.getLogger(ruidoso).setLevel(logging.WARNING)
    except OSError:
        # Sin permisos de escritura: seguimos sin archivo, nunca caemos.
        pass
    _listo = True


def registrar_evento(nivel: str, evento: dict[str, Any]) -> None:
    """Registra un evento estructurado."""
    evento = {"ts": _ahora(), "nivel": nivel, **evento}
    LOGGER.log(
        logging.ERROR if nivel == "ERROR" else logging.WARNING if nivel == "WARNING" else logging.INFO,
        evento.get("mensaje", evento.get("tipo", "")),
        extra={"evento": evento},
    )


def _usuario_de(request: Request) -> dict[str, Any]:
    """Lee id y rol del token SIN fallar y sin guardar el token."""
    cabecera = request.headers.get("authorization", "")
    if not cabecera.lower().startswith("bearer "):
        return {}
    try:
        from backend.core.seguridad import decodificar_token_jwt

        p = decodificar_token_jwt(cabecera[7:])
        return {"usuario_id": p.get("sub"), "rol": p.get("rol")}
    except Exception:  # noqa: BLE001
        return {}


class MiddlewareRegistro(BaseHTTPMiddleware):
    """Registra cada llamada: estado, duracion y error."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        ruta = request.url.path
        if ruta in _IGNORAR or ruta.endswith(_PREFIJOS_ESTATICOS) or request.method == "OPTIONS":
            return await call_next(request)

        id_llamada = request.headers.get("x-id-llamada") or uuid.uuid4().hex[:12]
        inicio = time.perf_counter()
        base = {
            "tipo": "llamada",
            "id": id_llamada,
            "metodo": request.method,
            "ruta": ruta,
            "claves_query": sorted(request.query_params.keys()),
            "ip": request.client.host if request.client else "",
            **_usuario_de(request),
        }
        try:
            respuesta = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            ms = round((time.perf_counter() - inicio) * 1000)
            registrar_evento("ERROR", {
                **base, "estado": 500, "ms": ms,
                "error": type(exc).__name__, "mensaje": str(exc)[:500],
                "traza": traceback.format_exc()[-MAX_TRAZA:],
            })
            return JSONResponse(
                {"detail": "Error interno del servidor", "id_llamada": id_llamada},
                status_code=500,
                headers={"X-Id-Llamada": id_llamada},
            )
        ms = round((time.perf_counter() - inicio) * 1000)
        nivel = "ERROR" if respuesta.status_code >= 500 else "WARNING" if respuesta.status_code >= 400 else "INFO"
        registrar_evento(nivel, {**base, "estado": respuesta.status_code, "ms": ms})
        respuesta.headers["X-Id-Llamada"] = id_llamada
        return respuesta


def leer_eventos(
    limite: int = 100, solo_errores: bool = False, texto: str = ""
) -> list[dict[str, Any]]:
    """Devuelve los eventos recientes (mas nuevos primero)."""
    eventos = list(_recientes)
    if not eventos and ARCHIVO_LOG.exists():
        try:
            lineas = ARCHIVO_LOG.read_text(encoding="utf-8").splitlines()[-500:]
            eventos = [json.loads(x) for x in lineas if x.strip()]
        except (OSError, ValueError):
            eventos = []
    if solo_errores:
        eventos = [e for e in eventos if e.get("nivel") in ("ERROR", "WARNING")]
    if texto:
        t = texto.lower()
        eventos = [e for e in eventos if t in json.dumps(e, ensure_ascii=False).lower()]
    return list(reversed(eventos))[:limite]
