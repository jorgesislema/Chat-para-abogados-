"""
Endpoints de autenticacion y registro multi-tenant.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.esquemas import LoginIn, RegistroTenantIn, TokenOut
from backend.core.seguridad import (
    crear_token_jwt,
    hashear_contrasena,
    verificar_contrasena,
)
from backend.core.configuracion import obtener_configuracion
from backend.db.modelos import Tenant, Usuario
from backend.db.sesion import obtener_sesion_bd

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/registro", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def registrar_tenant(payload: RegistroTenantIn) -> TokenOut:
    """Crea un tenant nuevo con su primer usuario administrador."""
    bd: Session = obtener_sesion_bd()
    try:
        tenant = Tenant(nombre=payload.nombre_tenant)
        bd.add(tenant)
        bd.flush()

        usuario = Usuario(
            tenant_id=tenant.id,
            email=payload.email,
            hash_contrasena=hashear_contrasena(payload.contrasena),
            rol="admin",
        )
        bd.add(usuario)
        bd.commit()
        bd.refresh(usuario)
        bd.refresh(tenant)
    except IntegrityError as exc:
        bd.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email o tenant ya registrados",
        ) from exc
    finally:
        bd.close()

    token = crear_token_jwt(
        {
            "sub": str(usuario.id),
            "tenant_id": tenant.id,
            "email": usuario.email,
            "rol": usuario.rol,
        }
    )
    return TokenOut(
        access_token=token,
        expiracion_minutos=obtener_configuracion().expiracion_token_minutos,
    )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn) -> TokenOut:
    """Valida credenciales y emite un JWT."""
    bd: Session = obtener_sesion_bd()
    try:
        usuario = bd.query(Usuario).filter(Usuario.email == payload.email).first()
    finally:
        bd.close()

    if not usuario or not verificar_contrasena(payload.contrasena, usuario.hash_contrasena):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
        )

    token = crear_token_jwt(
        {
            "sub": str(usuario.id),
            "tenant_id": usuario.tenant_id,
            "email": usuario.email,
            "rol": usuario.rol,
        }
    )
    return TokenOut(
        access_token=token,
        expiracion_minutos=obtener_configuracion().expiracion_token_minutos,
    )
