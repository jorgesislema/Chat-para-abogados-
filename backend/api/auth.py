"""
Endpoints LEGACY de autenticacion y registro multi-tenant.

Pensados para el primer arranque del sistema: crean un tenant y su
primer usuario administrador. Para la operacion normal (admin que
crea usuarios, usuarios que hacen login) usar las rutas oficiales:

  - POST /api/auth/token     (rutas_auth.py)  -- login OAuth2
  - POST /api/admin/usuarios  (rutas_admin.py) -- crear usuarios
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
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
from backend.db.modelos import ROL_ADMIN, Tenant, Usuario

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/registro", response_model=TokenOut, status_code=status.HTTP_201_CREATED
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

    token = crear_token_jwt(
        {
            "sub": str(usuario.id),
            "tenant_id": tenant.id,
            "username": usuario.username,
            "rol": usuario.rol,
        }
    )
    return TokenOut(
        access_token=token,
        expiracion_minutos=obtener_configuracion().expiracion_token_minutos,
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn) -> TokenOut:
    """Valida credenciales (username + password) y emite un JWT."""
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
        )

    token = crear_token_jwt(
        {
            "sub": str(usuario.id),
            "tenant_id": usuario.tenant_id,
            "username": usuario.username,
            "rol": usuario.rol,
        }
    )
    return TokenOut(
        access_token=token,
        expiracion_minutos=obtener_configuracion().expiracion_token_minutos,
    )
