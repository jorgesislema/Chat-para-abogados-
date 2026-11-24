"""
Endpoints de autenticacion (unificados).

Contiene TODAS las rutas de auth en un solo modulo:
  - POST /auth/registro   (legacy/dev: crea tenant + primer admin)
  - POST /auth/login      (legacy: login JSON username + contrasena)
  - POST /api/auth/token  (oficial OAuth2 Password Flow, form-urlencoded)

Historial: antes existian `auth.py` (/auth/*) y `rutas_auth.py`
(/api/auth/*) montados a la vez en main.py. Se unificaron aqui
(Fase 1A) para eliminar la duplicacion. Las rutas y contratos
HTTP no cambian: solo cambia la organizacion interna.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.esquemas import LoginIn, RegistroTenantIn, TokenOut
from backend.core.configuracion import obtener_configuracion
from backend.core.seguridad import (
    crear_token_jwt,
    hashear_contrasena,
    verificar_contrasena,
)
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import ROL_ADMIN, Usuario, Tenant

# Sin prefix: cada ruta declara su path completo para preservar
# los contratos legacy (/auth/*) y oficiales (/api/auth/*).
router = APIRouter(tags=["auth"])

# ============================================================
# Rate limiting de login (Fase 1B): 5 fallos -> bloqueo 5 min.
# In-memory por proceso (suficiente para portafolio/demo; en
# produccion usar Redis). Clave: username en minusculas.
# ============================================================

MAX_INTENTOS_LOGIN = 5
VENTANA_INTENTOS_SEGUNDOS = 600  # 10 minutos
BLOQUEO_SEGUNDOS = 300  # 5 minutos

_intentos_fallidos: dict[str, list[float]] = {}
_bloqueados_hasta: dict[str, float] = {}


def _ahora_ts() -> float:
    import time

    return time.time()


def _login_bloqueado(username: str) -> bool:
    clave = (username or "").strip().lower()
    hasta = _bloqueados_hasta.get(clave)
    if hasta is None:
        return False
    if _ahora_ts() >= hasta:
        _bloqueados_hasta.pop(clave, None)
        return False
    return True


def _registrar_fallo_login(username: str) -> None:
    clave = (username or "").strip().lower()
    ahora = _ahora_ts()
    lista = _intentos_fallidos.setdefault(clave, [])
    lista.append(ahora)
    # Recorta a la ventana.
    lista[:] = [t for t in lista if ahora - t <= VENTANA_INTENTOS_SEGUNDOS]
    if len(lista) >= MAX_INTENTOS_LOGIN:
        _bloqueados_hasta[clave] = ahora + BLOQUEO_SEGUNDOS
        _intentos_fallidos.pop(clave, None)


def _limpiar_intentos_login(username: str) -> None:
    clave = (username or "").strip().lower()
    _intentos_fallidos.pop(clave, None)
    _bloqueados_hasta.pop(clave, None)


def _reset_rate_limit_for_tests() -> None:
    """Solo tests: vacia el estado del rate limiting."""
    _intentos_fallidos.clear()
    _bloqueados_hasta.clear()


def _respuesta_bloqueado() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Demasiados intentos fallidos. Intenta de nuevo en unos minutos.",
    )


def _emitir_token(usuario: Usuario, tenant_id: int) -> TokenOut:
    token = crear_token_jwt(
        {
            "sub": str(usuario.id),
            "tenant_id": tenant_id,
            "username": usuario.username,
            "rol": usuario.rol,
        }
    )
    return TokenOut(
        access_token=token,
        expiracion_minutos=obtener_configuracion().expiracion_token_minutos,
    )


@router.post(
    "/auth/registro", response_model=TokenOut, status_code=status.HTTP_201_CREATED
)
def registrar_tenant(payload: RegistroTenantIn) -> TokenOut:
    """Crea un tenant nuevo con su primer usuario administrador."""
    bd: Session = obtener_sesion_bd()
    try:
        tenant = Tenant(nombre=payload.nombre_tenant)
        bd.add(tenant)
        bd.flush()

        usuario = Usuario(
            tenant_id=tenant.id,
            username=payload.username,
            password_hash=hashear_contrasena(payload.contrasena),
            rol=ROL_ADMIN,
        )
        bd.add(usuario)
        bd.commit()
        bd.refresh(usuario)
        bd.refresh(tenant)
    except IntegrityError as exc:
        bd.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username o tenant ya registrados",
        ) from exc
    finally:
        bd.close()

    return _emitir_token(usuario, tenant.id)


@router.post("/auth/login", response_model=TokenOut)
def login(payload: LoginIn) -> TokenOut:
    """Valida credenciales (username + password) y emite un JWT."""
    if _login_bloqueado(payload.username):
        raise _respuesta_bloqueado()
    bd: Session = obtener_sesion_bd()
    try:
        usuario = (
            bd.query(Usuario)
            .filter(Usuario.username == payload.username)
            .first()
        )
    finally:
        bd.close()

    if not usuario or not verificar_contrasena(
        payload.contrasena, usuario.password_hash
    ):
        _registrar_fallo_login(payload.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
        )

    _limpiar_intentos_login(payload.username)
    return _emitir_token(usuario, usuario.tenant_id)


@router.post("/api/auth/token", response_model=TokenOut)
def login_para_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> TokenOut:
    """
    OAuth2 Password Grant.

    Body (form-urlencoded):
        - username: nombre de usuario.
        - password: contrasena en texto plano.

    Returns:
        access_token (JWT firmado), tipo y expiracion en minutos.
    """
    if _login_bloqueado(form_data.username):
        raise _respuesta_bloqueado()
    bd: Session = obtener_sesion_bd()
    try:
        usuario = (
            bd.query(Usuario)
            .filter(Usuario.username == form_data.username)
            .first()
        )
    finally:
        bd.close()

    if not usuario or not verificar_contrasena(
        form_data.password, usuario.password_hash
    ):
        _registrar_fallo_login(form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _limpiar_intentos_login(form_data.username)
    return _emitir_token(usuario, usuario.tenant_id)
