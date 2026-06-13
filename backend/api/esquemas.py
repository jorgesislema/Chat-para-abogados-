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
    rol: str = Field(pattern="^(admin|abogado)$")
    tenant_id: int = Field(ge=1)


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
# Chat.
# ============================================================

class PreguntaIn(BaseModel):
    pregunta: str = Field(min_length=3, max_length=2000)


class RespuestaChatOut(BaseModel):
    pregunta_ofuscada: str
    respuesta: str


class SubirExpedienteOut(BaseModel):
    expediente_id: int
    titulo: str
    chunks_indexados: int
