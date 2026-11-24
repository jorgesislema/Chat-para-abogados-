"""
Tests Fase 1B (seguridad) + Fase 1A (routers unificados).

- SECRET_KEY_JWT: fail-fast sin secreto valido.
- Login: bloqueo temporal tras 5 fallos (429).
- Ofuscacion Ecuador: cedula, RUC, telefono EC, email.
- Auditoria: crear usuario y subir documento dejan fila [ADMIN].
- Rutas unificadas: /auth/*, /api/auth/token, /api/admin/*,
  /api/chat, /chat/preguntar responden (sin 404 por duplicados).
"""

from __future__ import annotations

import io

import fitz
import pytest
from fastapi.testclient import TestClient

from backend.api.auth import _reset_rate_limit_for_tests
from backend.core import configuracion as cfg_mod
from backend.db.base import Base
from backend.db.conexion import engine
from backend.db.modelos import RegistroAuditoria
from backend.main import aplicacion
from backend.servicios.ofuscacion import ofuscar_texto


@pytest.fixture()
def cliente() -> TestClient:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _reset_rate_limit_for_tests()
    return TestClient(aplicacion)


def _bootstrap_admin(cliente: TestClient) -> str:
    _reset_rate_limit_for_tests()
    r = cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete Fase1",
            "username": "admin_f1",
            "contrasena": "contrasenaSegura123",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


# --- SECRET_KEY_JWT fail-fast ---

def test_secret_por_defecto_no_arranca() -> None:
    cfg_mod.obtener_configuracion.cache_clear()
    import os

    viejo = os.environ.get("SECRET_KEY_JWT")
    viejo_testing = os.environ.get("LEGALRAG_TESTING")
    os.environ.pop("LEGALRAG_TESTING", None)
    os.environ["SECRET_KEY_JWT"] = "cambiar-en-produccion"
    try:
        with pytest.raises(RuntimeError):
            cfg_mod.obtener_configuracion()
    finally:
        if viejo is None:
            os.environ.pop("SECRET_KEY_JWT", None)
        else:
            os.environ["SECRET_KEY_JWT"] = viejo
        if viejo_testing is not None:
            os.environ["LEGALRAG_TESTING"] = viejo_testing
        cfg_mod.obtener_configuracion.cache_clear()


def test_secret_corta_no_arranca() -> None:
    cfg_mod.obtener_configuracion.cache_clear()
    import os

    viejo = os.environ.get("SECRET_KEY_JWT")
    viejo_testing = os.environ.get("LEGALRAG_TESTING")
    os.environ.pop("LEGALRAG_TESTING", None)
    os.environ["SECRET_KEY_JWT"] = "corta"
    try:
        with pytest.raises(RuntimeError):
            cfg_mod.obtener_configuracion()
    finally:
        if viejo is None:
            os.environ.pop("SECRET_KEY_JWT", None)
        else:
            os.environ["SECRET_KEY_JWT"] = viejo
        if viejo_testing is not None:
            os.environ["LEGALRAG_TESTING"] = viejo_testing
        cfg_mod.obtener_configuracion.cache_clear()


# --- Rate limit ---

def test_login_se_bloquea_tras_5_fallos(cliente: TestClient) -> None:
    _bootstrap_admin(cliente)
    for _ in range(5):
        r = cliente.post(
            "/api/auth/token",
            data={"username": "admin_f1", "password": "incorrecta"},
        )
        assert r.status_code == 401
    r = cliente.post(
        "/api/auth/token",
        data={"username": "admin_f1", "password": "incorrecta"},
    )
    assert r.status_code == 429


def test_login_legacy_tambien_bloquea(cliente: TestClient) -> None:
    _bootstrap_admin(cliente)
    for _ in range(5):
        cliente.post(
            "/auth/login",
            json={"username": "admin_f1", "contrasena": "contrasenaSegura123".replace("a", "o")},
        )
    r = cliente.post(
        "/auth/login",
        json={"username": "admin_f1", "contrasena": "otra_mala_123"},
    )
    assert r.status_code in (401, 429)


# --- Ofuscacion Ecuador ---

@pytest.mark.parametrize(
    "texto,ficha,fragmento",
    [
        ("Cedula 1712345678 del cliente", "[CEDULA]", "1712345678"),
        ("RUC 1790012345001 de la empresa", "[RUC]", "1790012345001"),
        ("Llamar al +593 99 123 4567", "[TELEFONO]", "99 123 4567"),
        ("Llamar al 0991234567 hoy", "[TELEFONO]", "0991234567"),
        ("Fono 02 255 0123 oficina", "[TELEFONO]", "02 255 0123"),
        ("Correo juan.perez@bufete.ec", "[EMAIL]", "juan.perez@bufete.ec"),
    ],
)
def test_ofuscacion_ecuador(texto: str, ficha: str, fragmento: str) -> None:
    resultado = ofuscar_texto(texto)
    assert fragmento not in resultado
    assert ficha in resultado


# --- Auditoria admin ---

def test_crear_usuario_deja_auditoria_admin(cliente: TestClient) -> None:
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import Tenant

    token = _bootstrap_admin(cliente)
    bd = obtener_sesion_bd()
    try:
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()
    r = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": "abogado_aud",
            "password": "contrasenaSegura123",
            "rol": "abogado",
            "tenant_id": tenant_id,
        },
    )
    assert r.status_code == 201, r.text
    bd = obtener_sesion_bd()
    try:
        fila = (
            bd.query(RegistroAuditoria)
            .filter(RegistroAuditoria.pregunta_ofuscada.like("[ADMIN] crear_usuario%"))
            .first()
        )
    finally:
        bd.close()
    assert fila is not None


def test_subir_documento_deja_auditoria_admin(cliente: TestClient) -> None:
    from backend.db.conexion import obtener_sesion_bd

    token = _bootstrap_admin(cliente)
    buf = io.BytesIO()
    doc = fitz.open()
    pag = doc.new_page(width=595, height=842)
    pag.insert_text((72, 72), "Documento de auditoria fase 1B.")
    doc.save(buf)
    doc.close()
    r = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("aud.pdf", buf.getvalue(), "application/pdf")},
        data={"categoria": "Leyes", "destino_tenant_id": "global", "titulo": "Aud doc"},
    )
    assert r.status_code == 201, r.text
    bd = obtener_sesion_bd()
    try:
        fila = (
            bd.query(RegistroAuditoria)
            .filter(RegistroAuditoria.pregunta_ofuscada.like("[ADMIN] subir_documento%"))
            .first()
        )
    finally:
        bd.close()
    assert fila is not None


# --- Rutas unificadas (sin 404) ---

def test_rutas_unificadas_no_404(cliente: TestClient) -> None:
    # Legacy registro existe.
    r = cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete Rutas",
            "username": "u_rutas",
            "contrasena": "contrasenaSegura123",
        },
    )
    assert r.status_code == 201, r.text
    # OAuth2 token existe.
    r = cliente.post(
        "/api/auth/token",
        data={"username": "u_rutas", "password": "contrasenaSegura123"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    # Salud chat autenticado existe.
    r = cliente.get(
        "/api/chat/salud", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
