"""
Endpoints de chat (unificados).

  - POST /api/chat        : cadena RAG completa para el frontend
                            (ofusca + ChromaDB + OpenRouter + auditoria).
  - GET  /api/chat/salud  : healthcheck autenticado (devuelve claims).
  - POST /chat/preguntar   : cadena RAG con contrato compacto
                            (sin `pregunta_original` ni `fuentes`).

Historial: antes existian `chat.py` (POST /chat/preguntar) y
`rutas_chat.py` (POST /api/chat). Se unificaron aqui (Fase 1A).
Los paths y contratos HTTP no cambian.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from backend.api.dependencias import (
    UsuarioActual,
    obtener_motor_vectorial,
    obtener_orquestador_rag,
    obtener_usuario_actual,
)
from backend.api.esquemas import PreguntaIn, RespuestaChatOut
from backend.db.conexion import obtener_sesion_bd
from backend.db.modelos import Permiso, Usuario
from backend.db.vectorial import MotorVectorial
from backend.servicios.permisos import PermisoRegla, filas_a_reglas
from backend.servicios.rag_motor import OrquestadorRAG
from backend.servicios.telemetria import (
    formatear_fuentes_para_auditoria,
    guardar_auditoria,
)

router = APIRouter(tags=["chat"])


def _contexto_permisos(
    usuario_actual: UsuarioActual,
    motor: MotorVectorial,
) -> tuple[str, int | None, str | None, list[PermisoRegla], list[str]]:
    """
    Resuelve (rol, usuario_id, grupo, reglas, colecciones_existentes)
    para el flujo permiso-aware del orquestador.

    Lee el grupo del usuario y las reglas de su tenant desde SQLite.
    Si la BD falla, devuelve alcance vacio (denegar por defecto).
    """
    rol = usuario_actual.get("rol", "abogado")
    usuario_id = usuario_actual["usuario_id"]
    grupo: str | None = None
    reglas: list[PermisoRegla] = []
    try:
        bd = obtener_sesion_bd()
        try:
            usuario = (
                bd.query(Usuario).filter(Usuario.id == usuario_id).first()
            )
            if usuario is not None:
                grupo = usuario.grupo
            filas = (
                bd.query(Permiso)
                .filter(Permiso.tenant_id == usuario_actual["tenant_id"])
                .all()
            )
            reglas = filas_a_reglas(filas)
        finally:
            bd.close()
    except Exception:  # noqa: BLE001 - sin BD no hay permisos: denegar por defecto
        reglas = []
    try:
        existentes = [c.name for c in motor._cliente.list_collections()]
    except Exception:  # noqa: BLE001 - fallback a la base minima
        existentes = []
    return rol, usuario_id, grupo, reglas, existentes


# ============================================================
# Esquemas Pydantic del endpoint /api/chat.
# ============================================================

class PreguntaSimpleIn(BaseModel):
    """Payload minimo que envia el frontend Streamlit."""

    pregunta: str = Field(min_length=3, max_length=2000)
    # Filtro opcional por categoria documental. Si es None o vacio,
    # el RAG busca en todas las categorias del tenant + la global.
    categoria: str | None = Field(default=None, max_length=64)


class FuenteOut(BaseModel):
    """Un chunk usado como contexto por el LLM."""

    id: str = ""
    texto: str
    metadata: dict
    puntuacion: float
    coleccion_origen: str


class RespuestaSimpleOut(BaseModel):
    """Respuesta final con la version ofuscada y las fuentes."""

    pregunta_original: str
    pregunta_ofuscada: str
    respuesta: str
    modelo: str
    tenant_id: int
    usuario_id: int
    fuentes: list[FuenteOut] = Field(default_factory=list)
    # Metricas de telemetria (Fase 6).
    tokens_prompt: int = 0
    tokens_completion: int = 0
    # Filtro de categoria aplicado (None = "Todas").
    categoria_filtrada: str | None = None
    # Fase 2: True si se denego por falta de permiso (sin llamar al LLM).
    acceso_denegado: bool = False


# ============================================================
# Endpoints.
# ============================================================

@router.post("/api/chat", response_model=RespuestaSimpleOut)
async def chat_frontend(
    payload: PreguntaSimpleIn,
    background_tasks: BackgroundTasks,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> RespuestaSimpleOut:
    """
    Ejecuta la cadena RAG completa para una pregunta del abogado
    y encola la auditoria asincrona en BackgroundTasks.

    El `tenant_id` (claims `tenant_id` del JWT, ID de la firma) se usa
    tanto para acotar la busqueda en ChromaDB como para etiquetar la
    auditoria. NOTA: para ChromaDB el orquestador recibe el
    `usuario_id` (claims `sub`, ID del abogado especifico), porque
    cada abogado tiene su coleccion privada `casos_user_<id>`.

    Fase 2: la busqueda respeta la matriz de permisos (solo el admin
    cambia permisos). Sin permiso, la respuesta lo indica sin filtrar
    texto ajeno (`acceso_denegado=True`, sin llamada al LLM).
    """
    abogado_id = str(usuario_actual["usuario_id"])
    tenant_firma = str(usuario_actual["tenant_id"])
    rol, uid, grupo, reglas, existentes = _contexto_permisos(usuario_actual, motor)

    resultado = await orquestador.consultar(
        pregunta_cruda=payload.pregunta,
        tenant_id=abogado_id,
        categoria=payload.categoria,
        rol=rol,
        usuario_id=uid,
        grupo=grupo,
        reglas_permiso=reglas,
        colecciones_existentes=existentes,
    )

    background_tasks.add_task(
        guardar_auditoria,
        tenant_id=tenant_firma,
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta_llm=resultado["respuesta"],
        fuentes_usadas=formatear_fuentes_para_auditoria(resultado["fuentes"]),
        tokens_prompt=resultado.get("tokens_prompt", 0),
        tokens_completion=resultado.get("tokens_completion", 0),
    )

    return RespuestaSimpleOut(
        pregunta_original=resultado["pregunta_original"],
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta=resultado["respuesta"],
        modelo=resultado["modelo"],
        tenant_id=usuario_actual["tenant_id"],
        usuario_id=usuario_actual["usuario_id"],
        fuentes=[FuenteOut(**f) for f in resultado["fuentes"]],
        tokens_prompt=resultado.get("tokens_prompt", 0),
        tokens_completion=resultado.get("tokens_completion", 0),
        categoria_filtrada=resultado.get("categoria_filtrada"),
        acceso_denegado=resultado.get("acceso_denegado", False),
    )


@router.get("/api/chat/salud", tags=["meta"])
def salud_chat_frontend(
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
) -> dict:
    """
    Healthcheck autenticado del modulo de chat.

    Devuelve los claims del usuario (incluido `rol`) para que el
    frontend pueda renderizar la UI segun RBAC sin necesidad de
    decodificar el JWT en cliente.
    """
    return {
        "estado": "ok",
        "tenant_id": usuario_actual["tenant_id"],
        "usuario_id": usuario_actual["usuario_id"],
        "rol": usuario_actual.get("rol", "abogado"),
        "email": usuario_actual.get("email", ""),
    }


@router.post("/chat/preguntar", response_model=RespuestaChatOut)
async def preguntar(
    payload: PreguntaIn,
    background_tasks: BackgroundTasks,
    usuario_actual: UsuarioActual = Depends(obtener_usuario_actual),
    orquestador: OrquestadorRAG = Depends(obtener_orquestador_rag),
    motor: MotorVectorial = Depends(obtener_motor_vectorial),
) -> RespuestaChatOut:
    """
    Punto de entrada del chat juridico (cadena RAG completa + auditoria).

    Mismo orquestador que `/api/chat`: ofusca, busca contexto en el
    motor vectorial, llama al LLM async y registra la auditoria en
    background. Contrato compacto para clientes que ya tienen
    frontend propio. Respeta permisos (Fase 2).
    """
    abogado_id = str(usuario_actual["usuario_id"])
    tenant_firma = str(usuario_actual["tenant_id"])
    rol, uid, grupo, reglas, existentes = _contexto_permisos(usuario_actual, motor)

    resultado = await orquestador.consultar(
        pregunta_cruda=payload.pregunta,
        tenant_id=abogado_id,
        rol=rol,
        usuario_id=uid,
        grupo=grupo,
        reglas_permiso=reglas,
        colecciones_existentes=existentes,
    )

    background_tasks.add_task(
        guardar_auditoria,
        tenant_id=tenant_firma,
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta_llm=resultado["respuesta"],
        fuentes_usadas=formatear_fuentes_para_auditoria(resultado["fuentes"]),
        tokens_prompt=resultado.get("tokens_prompt", 0),
        tokens_completion=resultado.get("tokens_completion", 0),
    )

    return RespuestaChatOut(
        pregunta_ofuscada=resultado["pregunta_ofuscada"],
        respuesta=resultado["respuesta"],
        acceso_denegado=resultado.get("acceso_denegado", False),
    )
