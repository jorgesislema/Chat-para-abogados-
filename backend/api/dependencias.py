# pyright: reportMissingTypeStubs=false

"""
Dependencias compartidas por los routers:
  - `obtener_usuario_actual`: extrae los claims del JWT.
  - `obtener_motor_vectorial`: devuelve el singleton del MotorVectorial
    persistido en disco (un unico cliente ChromaDB por proceso).
  - `obtener_orquestador_rag`: construye el OrquestadorRAG con el
    MotorVectorial inyectado (Dependency Injection). El cliente
    AsyncOpenAI se construye internamente; para tests, se puede
    sobreescribir via `app.dependency_overrides`.

Los singletons se exponen tambien como `_reset_singletons_for_tests()`
para que los tests puedan invalidar el cache entre ejecuciones.
"""

from __future__ import annotations

from typing import TypedDict

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError

from backend.core.seguridad import decodificar_token_jwt
from backend.db.vectorial import MotorVectorial
from backend.servicios.rag_motor import OrquestadorRAG

_esquema_oauth = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=True)


class UsuarioActual(TypedDict):
    usuario_id: int
    tenant_id: int
    rol: str
    email: str


def obtener_usuario_actual(token: str = Depends(_esquema_oauth)) -> UsuarioActual:
    """Decodifica el JWT y devuelve los claims basicos del usuario."""
    try:
        payload = decodificar_token_jwt(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    usuario_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not usuario_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token con claims incompletos",
        )
    return {
        "usuario_id": int(usuario_id),
        "tenant_id": int(tenant_id),
        "rol": payload.get("rol", "abogado"),
        "email": payload.get("email", ""),
    }


def verificar_admin(
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> UsuarioActual:
    """
    Dependencia RBAC: garantiza que el usuario autenticado tiene
    rol 'admin'. Devuelve 403 Forbidden en caso contrario.

    Uso:
        @router.post(..., dependencies=[Depends(verificar_admin)])
        def mi_endpoint(): ...
    """
    if usuario_actual.get("rol") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere rol de administrador para esta operacion",
        )
    return usuario_actual


# ============================================================
# Singletons lazy para no duplicar clientes ChromaDB en el proceso.
# ============================================================

_motor_vectorial_cache: MotorVectorial | None = None


def obtener_motor_vectorial() -> MotorVectorial:
    """
    Devuelve el MotorVectorial compartido del proceso.

    La primera llamada crea un `PersistentClient` en la ruta por defecto
    (`backend/db/chroma_data/`). Las llamadas siguientes reusan la
    instancia cacheada.

    Importante: ChromaDB mantiene bloqueos sobre su directorio; tener
    un solo cliente por proceso evita errores de "database is locked".
    """
    global _motor_vectorial_cache
    if _motor_vectorial_cache is None:
        _motor_vectorial_cache = MotorVectorial()
    return _motor_vectorial_cache


def obtener_orquestador_rag(
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> OrquestadorRAG:
    """
    Construye un `OrquestadorRAG` con el motor vectorial compartido.

    Cada llamada crea un orquestador NUEVO (es barato, no mantiene
    estado). Lo costoso (cliente ChromaDB) se comparte; lo barato
    (cliente AsyncOpenAI) se reconstruye para no acoplar peticiones.
    """
    return OrquestadorRAG(motor_vectorial=motor)


def _reset_singletons_for_tests() -> None:
    """
    Invalida el cache de singletons. Pensado SOLO para uso en tests
    que necesitan un MotorVectorial fresco (por ejemplo, contra un
    `EphemeralClient`).
    """
    global _motor_vectorial_cache
    _motor_vectorial_cache = None
