"""Pruebas del recopilador de logs."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import registro_logs as rl


def _app() -> FastAPI:
    rl.configurar_logs()
    app = FastAPI()
    app.add_middleware(rl.MiddlewareRegistro)

    @app.get("/ok")
    def ok() -> dict:
        return {"ok": True}

    @app.get("/falla")
    def falla() -> dict:
        raise RuntimeError("boom secreto")

    return app


def test_registra_llamada_ok_con_id() -> None:
    rl._recientes.clear()
    r = TestClient(_app()).get("/ok?clave=abc", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    assert r.headers["X-Id-Llamada"]
    ev = rl.leer_eventos(limite=5)[0]
    assert ev["ruta"] == "/ok" and ev["estado"] == 200 and ev["ms"] >= 0
    # Privacidad: no se guardan valores de query ni el token.
    texto = str(ev)
    assert "abc" not in texto and "Bearer" not in texto


def test_registra_error_500_con_traza_y_responde_json() -> None:
    rl._recientes.clear()
    r = TestClient(_app(), raise_server_exceptions=False).get("/falla")
    assert r.status_code == 500
    assert r.json()["id_llamada"] == r.headers["X-Id-Llamada"]
    ev = rl.leer_eventos(solo_errores=True)[0]
    assert ev["nivel"] == "ERROR" and ev["error"] == "RuntimeError" and "traza" in ev


def test_filtro_solo_errores() -> None:
    rl._recientes.clear()
    c = TestClient(_app(), raise_server_exceptions=False)
    c.get("/ok")
    c.get("/falla")
    assert all(e["nivel"] != "INFO" for e in rl.leer_eventos(solo_errores=True))
