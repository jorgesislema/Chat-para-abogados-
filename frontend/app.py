"""
UI principal de Streamlit para LegalRAG_SaaS.

Esta capa es EXCLUSIVAMENTE una interfaz visual web:
  - NO se conecta a SQLite ni a ChromaDB directamente.
  - NO invoca a OpenRouter directamente.
  - Toda la logica de negocio vive en el backend FastAPI.

Flujo de la aplicacion:
  1) Si NO hay sesion valida, se muestra la pantalla de Login
     (Usuario + Contrasena contra /api/auth/token) y un expander
     colapsable de auto-registro de firma.
  2) Si la sesion es valida, se muestra el escritorio:
     - Sidebar:
         * Info de sesion + boton Cerrar Sesion.
         * Panel admin (SOLO si rol='admin'): Subir Documento con
           selector de Categoria y radio de Destino (Base Global vs
           Expediente Privado de un abogado), y Crear Abogado.
     - Area central: Chat con selector de "Contexto" (Todas / Leyes /
       Jurisprudencia / Expediente) que envia la categoria al backend.

RBAC en la UI:
  Si `st.session_state.rol != "admin"`, el codigo NI SIQUIERA renderiza
  los formularios de carga ni de creacion de usuarios. La proteccion
  fuerte sigue siendo el backend (`verificar_admin`); esta es solo
  UX para no mostrar controles que daran 403.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

# ============================================================
# Configuracion (sin valores hardcodeados).
# ============================================================

URL_API_BACKEND: str = os.getenv("URL_API_BACKEND", "http://localhost:8000")
ENDPOINT_LOGIN: str = f"{URL_API_BACKEND}/api/auth/token"
ENDPOINT_REGISTRO: str = f"{URL_API_BACKEND}/auth/registro"
ENDPOINT_CHAT: str = f"{URL_API_BACKEND}/api/chat"
ENDPOINT_SALUD_CHAT: str = f"{URL_API_BACKEND}/api/chat/salud"
ENDPOINT_DOCUMENTOS: str = f"{URL_API_BACKEND}/api/admin/documentos"
ENDPOINT_ADMIN_USUARIOS: str = f"{URL_API_BACKEND}/api/admin/usuarios"

TIMEOUT_LOGIN: float = 10.0
TIMEOUT_SALUD: float = 5.0
TIMEOUT_CHAT: float = 60.0
TIMEOUT_UPLOAD: float = 90.0

# Roles reconocidos (deben matchear backend/api/esquemas.py).
ROL_ADMIN: str = "admin"
ROL_ABOGADO: str = "abogado"

# Categorias documentales (deben matchear backend/db/vectorial.py).
CATEGORIAS: tuple[str, ...] = ("Leyes", "Jurisprudencia", "Expediente")
CATEGORIA_TODAS: str = "Todas"


# ============================================================
# Gestion del estado de sesion.
# ============================================================

def _inicializar_estado() -> None:
    """Crea las claves del session_state la primera vez que se ejecuta."""
    defaults: dict[str, Any] = {
        "autenticado": False,
        "token": "",
        "rol": ROL_ABOGADO,           # por defecto el mas restrictivo
        "info_usuario": {},
        "mensajes": [],
        "categoria_chat": CATEGORIA_TODAS,
    }
    for clave, valor in defaults.items():
        if clave not in st.session_state:
            st.session_state[clave] = valor


def _cerrar_sesion() -> None:
    """Limpia el session_state para volver a la pantalla de login."""
    for clave in (
        "autenticado",
        "token",
        "rol",
        "info_usuario",
        "mensajes",
        "categoria_chat",
    ):
        st.session_state.pop(clave, None)
    _inicializar_estado()


def _es_admin() -> bool:
    """True si el usuario autenticado tiene rol admin."""
    return st.session_state.get("rol") == ROL_ADMIN


def _headers_auth() -> dict[str, str]:
    """Header `Authorization: Bearer <token>` para todas las llamadas autenticadas."""
    return {"Authorization": f"Bearer {st.session_state.token}"}


# ============================================================
# Validacion de la sesion contra el backend.
# ============================================================

def _validar_sesion_existente() -> None:
    """
    Si hay un token guardado de una sesion anterior, lo confirma contra
    `/api/chat/salud`. Si el backend lo rechaza (401) o el servidor
    no responde, limpia la sesion para forzar un nuevo login.

    Ademas, actualiza `rol` en session_state para que la UI refleje
    cambios de rol sin necesidad de re-login manual.
    """
    if not st.session_state.autenticado or not st.session_state.token:
        return
    try:
        respuesta = httpx.get(
            ENDPOINT_SALUD_CHAT,
            headers=_headers_auth(),
            timeout=TIMEOUT_SALUD,
        )
    except httpx.HTTPError:
        return

    if respuesta.status_code == 200:
        datos = respuesta.json()
        st.session_state.info_usuario = datos
        st.session_state.rol = datos.get("rol", ROL_ABOGADO)
    elif respuesta.status_code == 401:
        _cerrar_sesion()


# ============================================================
# Pantalla de login.
# ============================================================

def mostrar_pantalla_login() -> None:
    """Formulario centrado de Usuario + Contrasena contra /api/auth/token."""
    st.markdown(
        "<h1 style='text-align: center;'>⚖️ Bufete Inteligente - LegalRAG</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='text-align: center; color: #9CA3AF;'>"
        "Plataforma multi-tenant de consulta juridica con busqueda "
        "semantica y razonamiento aumentado."
        "</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    _, col_centro, _ = st.columns([1, 2, 1])
    with col_centro:
        with st.form("formulario_login", clear_on_submit=False):
            usuario = st.text_input(
                "Usuario",
                placeholder="ej: juan.perez",
                autocomplete="username",
            )
            contrasena = st.text_input(
                "Contrasena",
                type="password",
                placeholder="Ingresa tu contrasena",
                autocomplete="current-password",
            )
            submitted = st.form_submit_button(
                "Iniciar Sesion",
                use_container_width=True,
                type="primary",
            )

        if submitted:
            _intentar_login(usuario, contrasena)

        st.caption(
            "Si no tienes cuenta, contacta al administrador de tu firma "
            "para que te registre."
        )

        st.divider()
        with st.expander("Primera vez aqui? Registrar mi firma", expanded=False):
            _renderizar_formulario_registro()


def _intentar_login(usuario: str, contrasena: str) -> None:
    """POST form-urlencoded a /api/auth/token, guarda token + rol."""
    usuario = (usuario or "").strip()
    if not usuario or not contrasena:
        st.error("Completa usuario y contrasena para continuar.")
        return

    with st.spinner("Validando credenciales..."):
        try:
            respuesta = httpx.post(
                ENDPOINT_LOGIN,
                data={"username": usuario, "password": contrasena},
                timeout=TIMEOUT_LOGIN,
            )
        except httpx.HTTPError as exc:
            st.error(
                f"No se pudo contactar al servidor. Detalle: {exc}"
            )
            return

    if respuesta.status_code == 200:
        datos = respuesta.json()
        token = datos.get("access_token", "")
        if not token:
            st.error("El servidor respondio 200 OK pero sin token.")
            return
        st.session_state.token = token
        _cargar_info_usuario()
        st.session_state.autenticado = True
        st.success("Sesion iniciada. Cargando tu escritorio...")
        st.rerun()
    elif respuesta.status_code == 401:
        st.error("Credenciales invalidas")
    elif respuesta.status_code == 422:
        st.error("Credenciales con formato invalido (minimo 3 caracteres).")
    else:
        st.error(
            f"Error inesperado del servidor ({respuesta.status_code}). "
            "Intentalo de nuevo en unos minutos."
        )


def _cargar_info_usuario() -> None:
    """
    Llama a /api/chat/salud y guarda la respuesta en session_state.
    Tambien fija `rol` (clave para el RBAC en la UI).
    """
    try:
        respuesta = httpx.get(
            ENDPOINT_SALUD_CHAT,
            headers=_headers_auth(),
            timeout=TIMEOUT_SALUD,
        )
        if respuesta.status_code == 200:
            datos = respuesta.json()
            st.session_state.info_usuario = datos
            st.session_state.rol = datos.get("rol", ROL_ABOGADO)
    except httpx.HTTPError:
        st.session_state.info_usuario = {}


# ============================================================
# Registro de nueva firma (auto-onboarding para tenants nuevos).
# ============================================================

def _renderizar_formulario_registro() -> None:
    st.caption(
        "Crea una firma nueva. Tu primer usuario sera el administrador "
        "de la firma y podra dar de alta al resto del equipo."
    )
    with st.form("formulario_registro", clear_on_submit=False):
        nombre_tenant = st.text_input(
            "Nombre de la firma",
            placeholder="ej: Bufete Perez & Asociados",
        )
        col1, col2 = st.columns(2)
        with col1:
            username = st.text_input(
                "Usuario administrador",
                placeholder="ej: admin",
                autocomplete="username",
            )
        with col2:
            contrasena = st.text_input(
                "Contrasena",
                type="password",
                placeholder="minimo 8 caracteres",
                autocomplete="new-password",
            )
        confirmar = st.text_input(
            "Confirmar contrasena",
            type="password",
            placeholder="repite la contrasena",
            autocomplete="new-password",
        )
        submitted = st.form_submit_button(
            "Crear firma y empezar",
            use_container_width=True,
            type="primary",
        )

    if submitted:
        _intentar_registro(nombre_tenant, username, contrasena, confirmar)


def _intentar_registro(
    nombre_tenant: str,
    username: str,
    contrasena: str,
    confirmar: str,
) -> None:
    nombre_tenant = (nombre_tenant or "").strip()
    username = (username or "").strip()
    if not nombre_tenant or not username or not contrasena:
        st.error("Completa todos los campos para crear la firma.")
        return
    if len(nombre_tenant) < 2:
        st.error("El nombre de la firma debe tener al menos 2 caracteres.")
        return
    if len(username) < 3:
        st.error("El usuario debe tener al menos 3 caracteres.")
        return
    if len(contrasena) < 8:
        st.error("La contrasena debe tener al menos 8 caracteres.")
        return
    if contrasena != confirmar:
        st.error("Las contrasenas no coinciden.")
        return

    with st.spinner("Creando tu firma..."):
        try:
            respuesta = httpx.post(
                ENDPOINT_REGISTRO,
                json={
                    "nombre_tenant": nombre_tenant,
                    "username": username,
                    "contrasena": contrasena,
                },
                timeout=TIMEOUT_LOGIN,
            )
        except httpx.HTTPError as exc:
            st.error(f"No se pudo contactar al servidor. Detalle: {exc}")
            return

    if respuesta.status_code == 201:
        datos = respuesta.json()
        token = datos.get("access_token", "")
        if not token:
            st.success("Firma creada. Inicia sesion con tus credenciales.")
            return
        st.session_state.token = token
        _cargar_info_usuario()
        st.session_state.autenticado = True
        st.success("Firma creada. Bienvenido!")
        st.rerun()
    elif respuesta.status_code == 409:
        st.error(
            "Ya existe una firma o un usuario con esos datos. "
            "Prueba con otros nombres o inicia sesion si ya tienes cuenta."
        )
    elif respuesta.status_code == 422:
        st.error(_formatear_detalle_422(respuesta.json().get("detail", respuesta.text)))
    else:
        st.error(
            f"Error inesperado del servidor ({respuesta.status_code})."
        )


def _formatear_detalle_422(detalle: Any) -> str:
    """Convierte la lista de errores de Pydantic v2 en un mensaje legible."""
    if isinstance(detalle, list) and detalle:
        partes: list[str] = []
        for err in detalle:
            loc = err.get("loc", [])
            campos = [str(x) for x in loc if x not in ("body", "query", "path")]
            campo = ".".join(campos) if campos else "dato"
            msg = err.get("msg", "valor invalido")
            partes.append(f"{campo}: {msg}")
        return "Datos invalidos. " + " | ".join(partes)
    return f"Datos invalidos. {detalle}"


# ============================================================
# Sidebar: sesion + Cerrar Sesion + (admin only) carga + usuarios.
# ============================================================

def _panel_lateral() -> None:
    """Sidebar con sesion, logout y (solo admin) carga + gestion usuarios."""
    with st.sidebar:
        st.markdown("### ⚖️ Bufete Inteligente")
        info = st.session_state.info_usuario or {}
        st.caption(
            f"**{info.get('email') or info.get('username') or 'Sesion activa'}**"
            f"  ·  Rol: `{st.session_state.rol}`"
            f"  ·  Tenant: {info.get('tenant_id', '?')}"
        )
        st.divider()

        if st.button(
            "Cerrar Sesion", use_container_width=True, type="secondary"
        ):
            _cerrar_sesion()
            st.rerun()

        # Las funciones de administracion SOLO aparecen si el rol es admin.
        # Esta es la proteccion UX: el backend igual valida con
        # `verificar_admin`, pero aqui ni siquiera dibujamos los controles.
        if _es_admin():
            st.divider()
            _renderizar_subida_documento()
            st.divider()
            _renderizar_gestion_usuarios()
        else:
            st.divider()
            st.caption(
                "Tu cuenta no tiene permisos de administracion. "
                "Contacta al admin de tu firma para subir documentos o "
                "crear nuevos usuarios."
            )


# --- Subida de documento (SOLO admin) ---

def _renderizar_subida_documento() -> None:
    """
    Formulario de carga de PDF con:
      - Selector de Categoria (Leyes / Jurisprudencia / Expediente).
      - Radio de Destino (Base Global visible para todos los abogados,
        o Expediente Privado de un abogado especifico).
    """
    st.subheader("Subir Documento")
    st.caption(
        "Como administrador puedes indexar PDFs en la base comun "
        "o asignarlos al expediente privado de un abogado."
    )

    with st.form("formulario_subida_documento", clear_on_submit=True):
        titulo = st.text_input(
            "Titulo del documento",
            placeholder="Demanda 2026 - Cliente X",
        )
        categoria = st.selectbox(
            "Categoria",
            options=list(CATEGORIAS),
            help=(
                "Clasificacion documental: el asistente usara este campo "
                "para filtrar el contexto cuando el abogado lo elija."
            ),
        )
        destino = st.radio(
            "Destino",
            options=["Base Global (todos los abogados)", "Expediente Privado"],
            help=(
                "Global: visible para todos los abogados al consultar. "
                "Privado: solo el abogado destinatario lo ve en su chat."
            ),
        )
        destino_tenant_id: str = "global"
        if destino == "Expediente Privado":
            abogado_id = st.number_input(
                "Tenant ID del Abogado destinatario",
                min_value=1,
                step=1,
                value=1,
                help=(
                    "ID del abogado (no de la firma) al que se asigna "
                    "el documento. Lo encuentras en su JWT (claim `sub`)."
                ),
            )
            destino_tenant_id = str(int(abogado_id))

        archivo = st.file_uploader(
            "Archivo PDF", type=["pdf"], accept_multiple_files=False
        )
        submitted = st.form_submit_button(
            "Subir e indexar", use_container_width=True
        )

    if submitted:
        if not titulo or not titulo.strip():
            st.error("Indica un titulo para el documento.")
            return
        if archivo is None:
            st.error("Adjunta un archivo PDF.")
            return
        _subir_documento(
            archivo=archivo,
            titulo=titulo.strip(),
            categoria=categoria,
            destino_tenant_id=destino_tenant_id,
        )


def _subir_documento(
    archivo: Any,
    titulo: str,
    categoria: str,
    destino_tenant_id: str,
) -> None:
    """POST multipart al endpoint admin con categoria y destino."""
    with st.spinner("Subiendo e indexando PDF..."):
        try:
            respuesta = httpx.post(
                ENDPOINT_DOCUMENTOS,
                files={
                    "file": (
                        archivo.name,
                        archivo.read(),
                        "application/pdf",
                    ),
                },
                data={
                    "categoria": categoria,
                    "destino_tenant_id": destino_tenant_id,
                    "titulo": titulo,
                },
                headers=_headers_auth(),
                timeout=TIMEOUT_UPLOAD,
            )
        except httpx.HTTPError as exc:
            st.error(f"No se pudo conectar al backend: {exc}")
            return

    if respuesta.status_code == 201:
        cuerpo = respuesta.json()
        st.success(
            f"Documento indexado: {cuerpo['chunks_indexados']} chunks en "
            f"`{cuerpo['coleccion_destino']}`."
        )
    elif respuesta.status_code == 401:
        st.error("Tu sesion expiro. Cierra sesion e ingresa de nuevo.")
        _cerrar_sesion()
        st.rerun()
    elif respuesta.status_code == 403:
        st.error(
            "Tu cuenta no tiene permisos para cargar documentos. "
            "Esta funcion es exclusiva para administradores."
        )
    elif respuesta.status_code == 400:
        st.error(
            _formatear_detalle_422(respuesta.json().get("detail", respuesta.text))
        )
    elif respuesta.status_code == 422:
        detalle = respuesta.json().get("detail", respuesta.text)
        st.error(f"No se pudo extraer texto del PDF: {detalle}")
    else:
        st.error(f"Error del backend ({respuesta.status_code}): {respuesta.text}")


# --- Gestion de usuarios (SOLO admin) ---

def _renderizar_gestion_usuarios() -> None:
    """Formulario para crear un abogado o admin en la firma actual."""
    st.subheader("Crear Abogado")
    st.caption("Da de alta a un nuevo miembro en tu firma.")

    info = st.session_state.info_usuario or {}
    tenant_por_defecto = int(info.get("tenant_id") or 1)

    with st.form("formulario_crear_usuario", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            username = st.text_input(
                "Usuario", placeholder="ej: abogado_nuevo"
            )
            rol = st.selectbox(
                "Rol",
                options=[ROL_ABOGADO, ROL_ADMIN],
                help="`abogado`: solo consulta. `admin`: gestiona usuarios y documentos.",
            )
        with col2:
            password = st.text_input("Contrasena", type="password")
            tenant_id = st.number_input(
                "Tenant ID (firma)",
                min_value=1,
                value=tenant_por_defecto,
                step=1,
            )
        submitted = st.form_submit_button(
            "Crear usuario", use_container_width=True
        )

    if submitted:
        if not username or not username.strip() or not password:
            st.error("Usuario y contrasena son obligatorios.")
            return
        if len(password) < 8:
            st.error("La contrasena debe tener al menos 8 caracteres.")
            return
        _crear_usuario(username.strip(), password, rol, int(tenant_id))


def _crear_usuario(
    username: str, password: str, rol: str, tenant_id: int
) -> None:
    with st.spinner("Creando usuario..."):
        try:
            respuesta = httpx.post(
                ENDPOINT_ADMIN_USUARIOS,
                json={
                    "username": username,
                    "password": password,
                    "rol": rol,
                    "tenant_id": tenant_id,
                },
                headers=_headers_auth(),
                timeout=TIMEOUT_LOGIN,
            )
        except httpx.HTTPError as exc:
            st.error(f"No se pudo conectar al backend: {exc}")
            return

    if respuesta.status_code == 201:
        cuerpo = respuesta.json()
        st.success(
            f"Usuario `{cuerpo['username']}` creado (rol={cuerpo['rol']}, "
            f"tenant_id={cuerpo['tenant_id']})."
        )
    elif respuesta.status_code == 401:
        st.error("Tu sesion expiro. Cierra sesion e ingresa de nuevo.")
        _cerrar_sesion()
        st.rerun()
    elif respuesta.status_code == 403:
        st.error(
            "Tu cuenta no tiene permisos para crear usuarios. "
            "Esta funcion es exclusiva para administradores."
        )
    elif respuesta.status_code == 409:
        st.error(f"Ya existe un usuario con username `{username}`.")
    elif respuesta.status_code == 422:
        st.error(_formatear_detalle_422(respuesta.json().get("detail", respuesta.text)))
    else:
        st.error(f"Error del backend ({respuesta.status_code}): {respuesta.text}")


# ============================================================
# Area principal: chat con el asistente (visible para todos los roles).
# ============================================================

def _area_chat() -> None:
    """Area central: titulo, selector de Contexto, historial e input."""
    st.title("⚖️ Bufete Inteligente - LegalRAG")
    st.caption(
        "Consulta tu base documental con lenguaje natural. "
        "El sistema protege los datos personales antes de cualquier "
        "consulta externa."
    )

    # Selector de Contexto: persiste en session_state para que el chat
    # siempre lo mande al backend.
    contexto = st.radio(
        "Contexto",
        options=[CATEGORIA_TODAS, *CATEGORIAS],
        horizontal=True,
        help=(
            "Filtra los chunks que el RAG enviara al asistente. "
            "`Todas` no aplica filtro y combina Leyes, Jurisprudencia y "
            "Expedientes."
        ),
    )
    st.session_state.categoria_chat = contexto

    if not st.session_state.mensajes:
        with st.expander("Como empezar", expanded=True):
            st.markdown(
                "- **Carga PDFs** desde el panel lateral "
                "(solo administradores).\n"
                "- **Pregunta en lenguaje natural** sobre jurisprudencia, "
                "articulos, plazos, etc.\n"
                "- **Filtra el contexto** con el selector de arriba para "
                "limitar la busqueda a una categoria.\n"
                "- Cada respuesta cita los **fragmentos relevantes** "
                "usados para sustentarla."
            )

    _renderizar_historial()
    _renderizar_input_chat()


def _renderizar_historial() -> None:
    for mensaje in st.session_state.mensajes:
        with st.chat_message(mensaje["role"]):
            st.markdown(mensaje["content"])
            if mensaje["role"] == "assistant" and mensaje.get("pregunta_ofuscada"):
                with st.expander("Ver pregunta enviada al asistente (anonimizada)"):
                    st.code(mensaje["pregunta_ofuscada"], language="text")


def _renderizar_input_chat() -> None:
    prompt = st.chat_input("Escribe tu consulta juridica...")
    if prompt is None:
        return
    texto = prompt.strip()
    if not texto:
        st.warning("La pregunta no puede estar vacia.")
        return
    _enviar_pregunta(texto)


def _enviar_pregunta(pregunta: str) -> None:
    # 1) Pintar el mensaje del usuario.
    st.session_state.mensajes.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    # 2) Construir payload: la categoria del Contexto se envia solo si
    #    no es "Todas" (asi el backend no se molesta filtrando cuando
    #    el usuario quiere ver todo).
    categoria_enviar: str | None = st.session_state.categoria_chat
    if categoria_enviar == CATEGORIA_TODAS:
        categoria_enviar = None
    payload: dict[str, Any] = {"pregunta": pregunta}
    if categoria_enviar is not None:
        payload["categoria"] = categoria_enviar

    # 3) Llamar al backend.
    with st.chat_message("assistant"):
        with st.spinner("Consultando al asistente..."):
            try:
                respuesta = httpx.post(
                    ENDPOINT_CHAT,
                    json=payload,
                    headers=_headers_auth(),
                    timeout=TIMEOUT_CHAT,
                )
            except httpx.HTTPError as exc:
                msg = f"No se pudo conectar al backend: {exc}"
                st.error(msg)
                st.session_state.mensajes.append(
                    {"role": "assistant", "content": msg}
                )
                return

        if respuesta.status_code == 401:
            msg = "Tu sesion expiro. Cierra sesion e ingresa de nuevo."
            st.error(msg)
            st.session_state.mensajes.append(
                {"role": "assistant", "content": msg}
            )
            _cerrar_sesion()
            st.rerun()
            return

        if respuesta.status_code >= 400:
            msg = f"Error del backend ({respuesta.status_code}): {respuesta.text}"
            st.error(msg)
            st.session_state.mensajes.append(
                {"role": "assistant", "content": msg}
            )
            return

        datos = respuesta.json()
        texto_respuesta = datos.get("respuesta", "(sin respuesta)")
        pregunta_ofuscada = datos.get("pregunta_ofuscada", "")
        categoria_usada = datos.get("categoria_filtrada")

        # Si el backend aplico un filtro, lo mostramos para confirmar al usuario.
        sufijo = ""
        if categoria_usada:
            sufijo = f" _(filtrado por `{categoria_usada}`)_"
        st.markdown(texto_respuesta + sufijo)
        if pregunta_ofuscada:
            with st.expander("Ver pregunta enviada al asistente (anonimizada)"):
                st.code(pregunta_ofuscada, language="text")

        st.session_state.mensajes.append(
            {
                "role": "assistant",
                "content": texto_respuesta + sufijo,
                "pregunta_ofuscada": pregunta_ofuscada,
            }
        )


# ============================================================
# Entry point.
# ============================================================

def main() -> None:
    st.set_page_config(
        page_title="Bufete Inteligente - LegalRAG",
        page_icon="⚖️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inicializar_estado()
    _validar_sesion_existente()

    if not st.session_state.autenticado:
        mostrar_pantalla_login()
        return

    _panel_lateral()
    _area_chat()


if __name__ == "__main__":
    main()
