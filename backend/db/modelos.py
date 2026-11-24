"""
Modelos ORM de SQLAlchemy para multi-tenancy, RBAC y trazabilidad.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

# Roles validos en el sistema (RBAC, Fase 2).
#   admin:   UNICO que crea/edita/borra usuarios, sube/borra libros y
#            documentos, y decide QUE puede ver cada usuario o grupo.
#   abogado: solo consulta y chatea, y solo sobre lo permitido.
#   cliente: solo consulta, y solo sobre sus propios casos (sin acceso
#            a la base global de leyes salvo permiso explicito).
ROL_ADMIN: str = "admin"
ROL_ABOGADO: str = "abogado"
ROL_CLIENTE: str = "cliente"
ROLES_VALIDOS: tuple[str, ...] = (ROL_ADMIN, ROL_ABOGADO, ROL_CLIENTE)


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
    # Grupo opcional (ej. "laboral-quito") para permisos por grupo:
    # un Permiso con `grupo` aplica a todos los usuarios del grupo.
    grupo: Mapped[str | None] = mapped_column(String(120), nullable=True, default=None)
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
    """
    Registro LEGACY de cada pregunta/respuesta (sin conversacion asociada).

    DEPRECADO en Fase F: el historial vive en `conversaciones` +
    `mensajes_chat`. Se conserva el modelo para no romper BDs existentes
    (ningun endpoint lo escribe; la auditoria usa `RegistroAuditoria`).
    No crear filas nuevas.
    """

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


class Permiso(Base):
    """
    Permiso documento/libro/caso <-> usuario o grupo (Fase 2).

    Solo el admin crea/edita/borra estas filas. El filtro se aplica
    DENTRO de la busqueda vectorial (`backend/servicios/permisos.py`
    + `MotorVectorial.buscar_en_colecciones`): un usuario NUNCA recibe
    texto de algo que no puede ver.

    - `usuario_id` o `grupo` (al menos uno; si ambos, aplica si coincide
      cualquiera): a quien se concede/deniega.
    - `recurso_tipo`: "coleccion" (ej. `casos_user_7`, `leyes_globales`)
      o "documento" (un `hash_archivo` concreto de `documentos_ingestados`).
    - `recurso_id`: nombre de la coleccion o hash del documento.
    - `puede_ver`: True concede, False deniega explicitamente (el deny
      gana sobre el grant, salvo la base propia/global del abogado).
    """

    __tablename__ = "permisos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True, index=True, default=None
    )
    grupo: Mapped[str | None] = mapped_column(String(120), nullable=True, default=None)
    recurso_tipo: Mapped[str] = mapped_column(String(32), nullable=False)
    recurso_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    puede_ver: Mapped[bool] = mapped_column(nullable=False, default=True)
    creado_por: Mapped[int | None] = mapped_column(nullable=True, default=None)
    fecha_alta: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )


class Caso(Base):
    """
    Caso/juicio del bufete con datos estructurados (Fase D).

    Los filtros exactos (materia, estado, numero de proceso) se hacen
    sobre estas columnas; la busqueda semantica va sobre ChromaDB.
    Todos los datos son ficticios en demo.
    """

    __tablename__ = "casos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    numero_proceso: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    materia: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    juzgado: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    estado: Mapped[str] = mapped_column(String(60), nullable=False, default="abierto")
    cliente_nombre: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    abogado_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuarios.id"), nullable=True, default=None
    )
    fecha_ingreso: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    fecha_audiencia: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # Username del cliente dueno del caso (para visibilidad del rol cliente).
    cliente_username: Mapped[str] = mapped_column(String(120), nullable=False, default="")


class Conversacion(Base):
    """Hilo de chat de un usuario (Fase E/F)."""

    __tablename__ = "conversaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id"), nullable=False, index=True
    )
    titulo: Mapped[str] = mapped_column(String(200), nullable=False, default="Sin titulo")
    materia: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    caso_id: Mapped[int | None] = mapped_column(
        ForeignKey("casos.id"), nullable=True, default=None
    )
    etiquetas: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    archivada: Mapped[bool] = mapped_column(nullable=False, default=False)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class MensajeChat(Base):
    """Un mensaje dentro de una conversacion (rol user|assistant)."""

    __tablename__ = "mensajes_chat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversacion_id: Mapped[int] = mapped_column(
        ForeignKey("conversaciones.id"), nullable=False, index=True
    )
    rol: Mapped[str] = mapped_column(String(20), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    fuentes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    creada_en: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False
    )


__all__ = [
    "Caso",
    "Conversacion",
    "DocumentoIngestado",
    "Expediente",
    "Mensaje",
    "MensajeChat",
    "Permiso",
    "RegistroAuditoria",
    "ROL_ABOGADO",
    "ROL_ADMIN",
    "ROL_CLIENTE",
    "ROLES_VALIDOS",
    "Tenant",
    "Usuario",
]
