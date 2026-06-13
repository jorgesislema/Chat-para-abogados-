"""
Modelos ORM de SQLAlchemy para multi-tenancy, RBAC y trazabilidad.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

# Roles validos en el sistema (RBAC).
ROL_ADMIN: str = "admin"
ROL_ABOGADO: str = "abogado"
ROLES_VALIDOS: tuple[str, ...] = (ROL_ADMIN, ROL_ABOGADO)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """Una firma de abogados. Aislamiento multi-tenant de datos."""

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    fecha_alta: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="tenant")


class Usuario(Base):
    """
    Usuario del sistema. Pertenece a un Tenant y tiene un rol RBAC.

    Atributos:
        id: identificador interno.
        tenant_id: tenant al que pertenece (aislamiento de datos).
        username: nombre de usuario unico, usado para login.
        password_hash: hash bcrypt. NUNCA en texto plano.
        rol: "admin" (puede crear usuarios y subir docs) o "abogado"
             (solo consulta).
        fecha_alta: timestamp de creacion.
    """

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False
    )
    username: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    rol: Mapped[str] = mapped_column(
        String(40), default=ROL_ABOGADO, nullable=False
    )
    fecha_alta: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="usuarios")


class Expediente(Base):
    """Metadato de un expediente (PDF/DOCX) ingestado en el sistema."""

    __tablename__ = "expedientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    ruta_archivo: Mapped[str] = mapped_column(String(500), nullable=False)
    fecha_alta: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )


class Mensaje(Base):
    """Registro de cada pregunta/respuesta del chat juridico."""

    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id"), nullable=False
    )
    pregunta_ofuscada: Mapped[str] = mapped_column(Text, nullable=False)
    respuesta: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )


class RegistroAuditoria(Base):
    """
    Auditoria asincrona de las llamadas al LLM (Fase 6: Telemetria).

    Una fila por pregunta respondida. Se escribe en background tras
    devolver la respuesta al usuario, para NO afectar la latencia.

    Decisiones de diseno:
      - `tenant_id` es String (no FK): el registro debe sobrevivir
        incluso si el tenant se elimina (requisito legal de trazabilidad).
      - `pregunta_ofuscada` NUNCA `pregunta_cruda`: la Gobernanza PII
        se aplica ANTES de tocar la BD de auditoria.
      - `fuentes_usadas` guarda los IDs de ChromaDB separados por
        comas, para poder reconstruir que documentos uso el LLM.
      - `tokens_prompt` / `tokens_completion` para medir consumo de
        OpenRouter por tenant y modelo.
    """

    __tablename__ = "registros_auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(120), nullable=False, index=True
    )
    pregunta_ofuscada: Mapped[str] = mapped_column(Text, nullable=False)
    respuesta_llm: Mapped[str] = mapped_column(Text, nullable=False)
    fuentes_usadas: Mapped[str] = mapped_column(
        String(1000), default="", nullable=False
    )
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class DocumentoIngestado(Base):
    """
    Registro de un PDF ingestado al vector store, indexado por el
    hash SHA-256 de su contenido binario.

    Sirve como mecanismo de **deduplicacion**: si un admin intenta
    subir el mismo PDF dos veces, el endpoint `POST /api/admin/documentos`
    consulta esta tabla, detecta el hash duplicado y rechaza con
    HTTP 409 Conflict. Esto evita:
      - Duplicar vectores en ChromaDB (degrada la precision del RAG).
      - Desperdiciar CPU/embeddings en re-procesamiento.
      - Contaminar la auditoria con ingestas identicas.

    El hash se calcula sobre el contenido binario crudo del archivo
    (`hashlib.sha256(contenido).hexdigest()`), no sobre el texto
    extraido: asi dos PDFs visualmente identicos pero con
    codificaciones internas distintas tambien se distinguen.

    Atributos:
        id: PK autoincremental.
        hash_archivo: SHA-256 hex (64 chars). UNIQUE + indexado
            para que la consulta de deduplicacion sea O(log n).
        nombre_archivo: nombre original del PDF (puede repetirse entre
            archivos distintos; el hash es lo que importa).
        categoria: una de las CATEGORIAS_VALIDAS del vector store.
        tenant_id_destino: a quien se asigno el documento. String para
            soportar tanto "0" (global) como IDs de abogados.
        fecha_ingesta: timestamp UTC del momento de la ingesta.
    """

    __tablename__ = "documentos_ingestados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash_archivo: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    nombre_archivo: Mapped[str] = mapped_column(String(500), nullable=False)
    categoria: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id_destino: Mapped[str] = mapped_column(String(64), nullable=False)
    fecha_ingesta: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )


__all__ = [
    "DocumentoIngestado",
    "Expediente",
    "Mensaje",
    "RegistroAuditoria",
    "ROL_ABOGADO",
    "ROL_ADMIN",
    "ROLES_VALIDOS",
    "Tenant",
    "Usuario",
]
