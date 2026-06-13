"""
Rutas oficiales de autenticacion (estandar OAuth2 Password Flow).

  - POST /api/auth/token: recibe credenciales via `application/x-www-form-urlencoded`
    y devuelve un JWT firmado.

Este endpoint sigue la convencion de FastAPI `OAuth2PasswordRequestForm`,
lo que lo hace compatible con clientes OAuth2 estandar (Swagger UI,
clientes HTTP automaticos, etc.).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from backend.api.esquemas import TokenOut
from backend.core.configuracion import obtener_configuracion
from backend.core.seguridad import crear_token_jwt, verificar_contrasena
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import Usuario

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenOut)
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
            headers={"WWW-Authenticate": "Bearer"},
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
