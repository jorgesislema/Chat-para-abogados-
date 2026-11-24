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
    """LEGACY: respuesta del eliminado POST /admin/expedientes. Sin uso."""

    expediente_id: int
    titulo: str
    chunks_indexados: int


# ============================================================
# Conversaciones y casos (Fase D/E/F).
# ============================================================

class ConversacionOut(BaseModel):
    """Una conversacion del historial."""

    id: int
    titulo: str
    materia: str | None = None
    caso_id: int | None = None
    etiquetas: str = ""
    archivada: bool = False
    creada_en: str = ""
    actualizada_en: str = ""
    mensajes: int = 0


class ConversacionCrearIn(BaseModel):
    titulo: str | None = Field(default=None, max_length=200)
    materia: str | None = Field(default=None, max_length=80)
    caso_id: int | None = None
    etiquetas: str | None = Field(default="", max_length=500)


class ConversacionEditarIn(BaseModel):
    titulo: str | None = Field(default=None, max_length=200)
    materia: str | None = Field(default=None, max_length=80)
    caso_id: int | None = None
    etiquetas: str | None = Field(default=None, max_length=500)
    archivada: bool | None = None


class MensajeChatIn(BaseModel):
    rol: str = Field(pattern="^(user|assistant)$")
    contenido: str = Field(min_length=1, max_length=20000)
    fuentes: str | None = Field(default="", max_length=5000)


class MensajeChatOut(BaseModel):
    id: int
    rol: str
    contenido: str
    fuentes: str = ""
    creada_en: str = ""


class CasoOut(BaseModel):
    id: int
    numero_proceso: str
    materia: str
    juzgado: str = ""
    estado: str = ""
    cliente_nombre: str = ""
    cliente_username: str = ""
    abogado_id: int | None = None
    fecha_ingreso: str = ""
    fecha_audiencia: str = ""


class CasoCrearIn(BaseModel):
    numero_proceso: str = Field(min_length=3, max_length=80)
    materia: str = Field(min_length=2, max_length=80)
    juzgado: str | None = Field(default="", max_length=200)
    estado: str | None = Field(default="abierto", max_length=60)
    cliente_nombre: str | None = Field(default="", max_length=200)
    cliente_username: str | None = Field(default="", max_length=120)
    abogado_id: int | None = None
    fecha_ingreso: str | None = Field(default="", max_length=20)
    fecha_audiencia: str | None = Field(default="", max_length=20)
