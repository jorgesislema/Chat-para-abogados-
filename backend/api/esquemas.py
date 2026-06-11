"""
Esquemas Pydantic compartidos por los routers del backend.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegistroTenantIn(BaseModel):
    nombre_tenant: str = Field(min_length=2, max_length=120)
    email: EmailStr
    contrasena: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    contrasena: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expiracion_minutos: int


class PreguntaIn(BaseModel):
    pregunta: str = Field(min_length=3, max_length=2000)


class RespuestaChatOut(BaseModel):
    pregunta_ofuscada: str
    respuesta: str


class SubirExpedienteOut(BaseModel):
    expediente_id: int
    titulo: str
    chunks_indexados: int
