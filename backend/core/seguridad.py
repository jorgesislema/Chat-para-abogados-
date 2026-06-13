# pyright: reportMissingTypeStubs=false

"""
Utilidades de seguridad: hashing de contrasenas y firma/validacion de tokens JWT.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
import bcrypt

from backend.core.configuracion import obtener_configuracion


class _ContextoHash:
    def hash(self, contrasena_plana: str) -> str:
        return bcrypt.hashpw(
            contrasena_plana.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

    def verify(self, contrasena_plana: str, hash_almacenado: str) -> bool:
        try:
            return bcrypt.checkpw(
                contrasena_plana.encode("utf-8"),
                hash_almacenado.encode("utf-8"),
            )
        except ValueError:
            return False


# Contexto de hashing: bcrypt es estandar para contrasenas de usuario.
_contexto_hash = _ContextoHash()


def hashear_contrasena(contrasena_plana: str) -> str:
    """Devuelve el hash bcrypt de una contrasena en texto plano."""
    return _contexto_hash.hash(contrasena_plana)


def verificar_contrasena(contrasena_plana: str, hash_almacenado: str) -> bool:
    """Compara una contrasena plana contra su hash."""
    return _contexto_hash.verify(contrasena_plana, hash_almacenado)


def crear_token_jwt(payload: dict[str, Any]) -> str:
    """Firma un JWT con el payload recibido y la expiracion configurada."""
    configuracion = obtener_configuracion()
    ahora = datetime.now(timezone.utc)
    payload_copia = dict(payload)
    payload_copia["iat"] = ahora
    payload_copia["exp"] = ahora + timedelta(
        minutes=configuracion.expiracion_token_minutos
    )
    return jwt.encode(
        payload_copia,
        configuracion.secret_key_jwt,
        algorithm=configuracion.algoritmo_jwt,
    )


def decodificar_token_jwt(token: str) -> dict[str, Any]:
    """Decodifica y valida un JWT. Lanza JWTError si es invalido o expiro."""
    configuracion = obtener_configuracion()
    return jwt.decode(
        token,
        configuracion.secret_key_jwt,
        algorithms=[configuracion.algoritmo_jwt],
    )


__all__ = [
    "JWTError",
    "crear_token_jwt",
    "decodificar_token_jwt",
    "hashear_contrasena",
    "hashear_password",
    "verificar_admin_desde_claims",
    "verificar_contrasena",
    "verificar_password",
]


# ============================================================
# Aliases en espanol -> en ingles (consistencia con el spec).
# ============================================================

hashear_password = hashear_contrasena
verificar_password = verificar_contrasena


def verificar_admin_desde_claims(claims: dict) -> bool:
    """
    Devuelve True si el payload del JWT pertenece a un usuario con
    rol 'admin'. Pensado para usarlo en dependencias de FastAPI.
    """
    return claims.get("rol") == "admin"
