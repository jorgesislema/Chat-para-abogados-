"""
Esquemas Pydantic compartidos por los routers del backend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ============================================================
# Auth.
# ============================================================

class RegistroTenantIn(BaseModel):
    """Payload del endpoint legacy /auth/registro (crea tenant + admin)."""

    nombre_tenant: str = Field(min_length=2, max_length=120)
    username: str = Field(min_length=3, max_length=120)
    contrasena: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    """Payload del endpoint legacy /auth/login."""

    username: str = Field(min_length=3, max_length=120)
    contrasena: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expiracion_minutos: int


# ============================================================
# Admin (RBAC).
# ============================================================

class CrearUsuarioIn(BaseModel):
    """Body de POST /api/admin/usuarios. Solo accesible para admins."""

    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    rol: str = Field(pattern="^(admin|abogado|cliente)$")
    tenant_id: int = Field(ge=1)
    grupo: str | None = Field(default=None, max_length=120)


class CrearUsuarioOut(BaseModel):
    usuario_id: int
    username: str
    rol: str
    tenant_id: int


class CargarDocumentoOut(BaseModel):
    """Respuesta de POST /api/admin/documentos."""

    titulo: str
    nivel: str
    tenant_id: int
    chunks_indexados: int
    coleccion_destino: str
    # Huella digital SHA-256 del contenido binario del PDF.
    # Se persiste en la tabla `documentos_ingestados` para deduplicacion.
    # La UI puede mostrarla al admin como confirmacion de que el archivo
    # fue identificado univocamente.
    hash_archivo: str


# ============================================================
# Permisos (Fase 2: matriz usuario/grupo x documento).
# ============================================================

class PermisoIn(BaseModel):
    """Body de POST /api/admin/permisos. Solo accesible para admins."""

    usuario_id: int | None = Field(default=None, ge=1)
    grupo: str | None = Field(default=None, max_length=120)
    recurso_tipo: str = Field(pattern="^(coleccion|documento)$")
    recurso_id: str = Field(min_length=1, max_length=255)
    puede_ver: bool = True


class PermisoOut(BaseModel):
    """Una fila de la tabla permisos."""

    permiso_id: int
    tenant_id: int
    usuario_id: int | None
    grupo: str | None
    recurso_tipo: str
    recurso_id: str
    puede_ver: bool


# ============================================================
# Chat.
# ============================================================

class PreguntaIn(BaseModel):
    pregunta: str = Field(min_length=3, max_length=2000)


class RespuestaChatOut(BaseModel):
    pregunta_ofuscada: str
    respuesta: str
    # Fase 2: True si se denego por falta de permiso (sin llamar al LLM).
    acceso_denegado: bool = False


class SubirExpedienteOut(BaseModel):
    expediente_id: int
    titulo: str
    chunks_indexados: int
