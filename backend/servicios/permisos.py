"""
Resolucion de permisos documento/libro/caso <-> usuario o grupo (Fase 2).

Solo el admin crea/edita/borra permisos (endpoints en `backend/api/admin.py`).
Este modulo es LOGICA PURA: recibe filas de permiso como dicts y decide
que colecciones y que documentos (hashes) puede ver un usuario.

Reglas:
  - admin: ve TODAS las colecciones existentes.
  - abogado: base = {global `leyes_globales`, propia `casos_user_<id>`}
    + colecciones concedidas - colecciones denegadas. La base propia y
    la global NUNCA se quitan (un deny sobre ellas se ignora).
  - cliente: SIN base (ni global ni propia). Solo lo concedido
    explicitamente ("solo sus propios casos").
  - Permiso `documento` (hash_archivo): concede chunks con ese hash
    aunque vivan en una coleccion no concedida. El deny de coleccion
    gana sobre el grant de coleccion, pero un grant de documento
    concreto sigue permitiendo esos chunks (el admin concedio el
    documento exacto a proposito).
  - El deny gana sobre el grant al mismo nivel.

El filtro se aplica DENTRO de la busqueda vectorial: el orquestador
solo consulta las colecciones permitidas y, para hashes concedidos
en colecciones ajenas, usa `where={"hash_archivo": {"$in": [...]}}`
(filtro nativo de ChromaDB, no post-procesado).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.db.vectorial import (
    COLECCION_GLOBAL,
    nombre_coleccion_para_tenant,
)

# Respuesta determinista cuando hay contenido pero sin acceso.
# NO pasa por el LLM: asi es testeable y nunca filtra texto.
MENSAJE_SIN_ACCESO: str = (
    "No tienes acceso a ese documento o caso. Si necesitas consultarlo, "
    "pide al administrador que te conceda permiso."
)

RECURSO_COLECCION = "coleccion"
RECURSO_DOCUMENTO = "documento"
RECURSOS_VALIDOS: tuple[str, ...] = (RECURSO_COLECCION, RECURSO_DOCUMENTO)


@dataclass(frozen=True)
class PermisoRegla:
    """Vista liviana de una fila de la tabla permisos."""

    tenant_id: int
    usuario_id: int | None = None
    grupo: str | None = None
    recurso_tipo: str = RECURSO_COLECCION
    recurso_id: str = ""
    puede_ver: bool = True


@dataclass(frozen=True)
class AlcancePermitido:
    """Resultado de resolver los permisos de un usuario."""

    colecciones: frozenset[str] = field(default_factory=frozenset)
    hashes_documentos: frozenset[str] = field(default_factory=frozenset)


def _aplica_a(regla: PermisoRegla, usuario_id: int, grupo: str | None) -> bool:
    """Una regla aplica si coincide el usuario o su grupo."""
    if regla.usuario_id is not None and regla.usuario_id == usuario_id:
        return True
    if regla.grupo and grupo and regla.grupo == grupo:
        return True
    return False


def resolver_alcance(
    rol: str,
    usuario_id: int,
    grupo: str | None,
    reglas: list[PermisoRegla],
    colecciones_existentes: list[str],
) -> AlcancePermitido:
    """
    Calcula que colecciones y que documentos (hashes) puede ver el usuario.

    - admin: todas las existentes (las reglas se ignoran).
    - abogado: base propia+global + grants - denies (la base no se quita).
    - cliente: solo grants explicitos.
    """
    reglas_aplicables = [r for r in reglas if _aplica_a(r, usuario_id, grupo)]

    if rol == "admin":
        return AlcancePermitido(
            colecciones=frozenset(colecciones_existentes),
            hashes_documentos=frozenset(),
        )

    if rol == "abogado":
        base = {COLECCION_GLOBAL, nombre_coleccion_para_tenant(usuario_id)}
    else:  # cliente (y cualquier rol futuro restringido): sin base
        base = set()

    concedidas = {
        r.recurso_id
        for r in reglas_aplicables
        if r.recurso_tipo == RECURSO_COLECCION and r.puede_ver
    }
    denegadas = {
        r.recurso_id
        for r in reglas_aplicables
        if r.recurso_tipo == RECURSO_COLECCION and not r.puede_ver
    }
    hashes = frozenset(
        r.recurso_id
        for r in reglas_aplicables
        if r.recurso_tipo == RECURSO_DOCUMENTO and r.puede_ver
    )

    # Deny gana sobre grant, pero la base del abogado es intocable.
    colecciones = (base | concedidas) - (denegadas - base)
    # Solo colecciones que existen de verdad.
    existentes = set(colecciones_existentes)
    return AlcancePermitido(
        colecciones=frozenset(c for c in colecciones if c in existentes),
        hashes_documentos=hashes,
    )


def filas_a_reglas(filas: list) -> list[PermisoRegla]:
    """Convierte filas ORM `Permiso` a `PermisoRegla` (desacopla de SQLAlchemy)."""
    return [
        PermisoRegla(
            tenant_id=int(f.tenant_id),
            usuario_id=f.usuario_id,
            grupo=f.grupo,
            recurso_tipo=str(f.recurso_tipo),
            recurso_id=str(f.recurso_id),
            puede_ver=bool(f.puede_ver),
        )
        for f in filas
    ]


__all__ = [
    "AlcancePermitido",
    "MENSAJE_SIN_ACCESO",
    "PermisoRegla",
    "RECURSO_COLECCION",
    "RECURSO_DOCUMENTO",
    "RECURSOS_VALIDOS",
    "filas_a_reglas",
    "resolver_alcance",
]
