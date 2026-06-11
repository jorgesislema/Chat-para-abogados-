"""
UI principal de Streamlit para LegalRAG_SaaS.

Esta capa es EXCLUSIVAMENTE una interfaz visual web:
  - NO se conecta a SQLite.
  - NO invoca a OpenRouter directamente.
  - Toda la logica vive en el backend FastAPI.

Toda comunicacion con el backend se hace via peticiones HTTP RESTful
(httpx) usando el header `Authorization: Bearer <jwt>`.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

# URL del backend. Por defecto apunta al puerto de desarrollo estandar de FastAPI.
# Se puede sobreescribir con la variable de entorno URL_API_BACKEND.
URL_API_BACKEND: str = os.getenv("URL_API_BACKEND", "http://localhost:8000")
ENDPOINT_CHAT: str = f"{URL_API_BACKEND}/api/chat"
ENDPOINT_SALUD: str = f"{URL_API_BACKEND}/salud"
ENDPOINT_SALUD_CHAT: str = f"{URL_API_BACKEND}/api/chat/salud"
TIMEOUT_SALUD: float = 5.0
TIMEOUT_CHAT: float = 60.0


# ============================================================
# Inicializacion del estado de sesion.
# ============================================================

def _inicializar_estado() -> None:
    """Crea las claves del session_state la primera vez que se ejecuta."""
    estado_inicial: dict[str, Any] = {
        "autenticado": False,
        "token": "",
        "info_usuario": {},
        "mensajes": [],
    }
    for clave, valor in estado_inicial.items():
        if clave not in st.session_state:
            st.session_state[clave] = valor


def _cerrar_sesion() -> None:
    """Limpia el session_state para volver a la pantalla de bienvenida."""
    for clave in ("autenticado", "token", "info_usuario", "mensajes"):
        st.session_state.pop(clave, None)


# ============================================================
# Panel lateral (sidebar): autenticacion y configuracion.
# ============================================================

def _panel_lateral() -> None:
    with st.sidebar:
        st.header("Configuracion")
        st.caption(f"Backend: `{URL_API_BACKEND}`")
        st.divider()

        if not st.session_state.autenticado:
            _formulario_llave_acceso()
        else:
            _panel_sesion_activa()


def _formulario_llave_acceso() -> None:
    """Sidebar con el formulario para pegar la Llave de Acceso (JWT)."""
    st.subheader("Iniciar sesion")
    st.caption(
        "Pega tu **Llave de Acceso (JWT)** emitida por el backend. "
        "Se enviara en cada peticion como `Authorization: Bearer <token>`."
    )

    with st.form("formulario_llave_acceso", clear_on_submit=False):
        token = st.text_input(
            "Llave de Acceso (JWT)",
            type="password",
            placeholder="eyJhbGciOiJIUzI1NiIs...",
        )
        submitted = st.form_submit_button("Conectar al backend", use_container_width=True)

    if submitted:
        if not token or not token.strip():
            st.error("La llave de acceso no puede estar vacia.")
            return
        _intentar_conectar(token.strip())


def _intentar_conectar(token: str) -> None:
    """Llama al endpoint protegido /api/chat/salud para validar el token."""
    with st.spinner("Validando llave de acceso contra el backend..."):
        try:
            respuesta = httpx.get(
                ENDPOINT_SALUD_CHAT,
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT_SALUD,
            )
        except httpx.HTTPError as exc:
            st.error(
                f"No se pudo contactar al backend en `{URL_API_BACKEND}`. "
                f"Asegurate de que FastAPI este corriendo. Detalle: {exc}"
            )
            return

    if respuesta.status_code == 200:
        datos = respuesta.json()
        st.session_state.autenticado = True
        st.session_state.token = token
        st.session_state.info_usuario = datos
        st.success("Llave de acceso valida. Sesion iniciada.")
        st.rerun()
    elif respuesta.status_code == 401:
        st.error("Llave de acceso invalida o expirada. Verifica el JWT.")
    else:
        st.error(
            f"Error inesperado del backend ({respuesta.status_code}): "
            f"{respuesta.text}"
        )


def _panel_sesion_activa() -> None:
    """Sidebar ya autenticado: muestra info y boton de logout."""
    st.subheader("Sesion activa")
    info = st.session_state.info_usuario
    st.write(f"**Tenant ID:** `{info.get('tenant_id', '?')}`")
    st.write(f"**Usuario ID:** `{info.get('usuario_id', '?')}`")
    token_corto = st.session_state.token[:18] + "..." if len(st.session_state.token) > 18 else st.session_state.token
    st.caption(f"Token: `{token_corto}`")

    st.divider()
    if st.button("Cerrar sesion", use_container_width=True):
        _cerrar_sesion()
        st.rerun()

    if st.button("Limpiar historial del chat", use_container_width=True):
        st.session_state.mensajes = []
        st.rerun()


# ============================================================
# Area central: bienvenida o chat.
# ============================================================

def _area_principal() -> None:
    if not st.session_state.autenticado:
        _pantalla_bienvenida()
    else:
        _area_chat()


def _pantalla_bienvenida() -> None:
    st.title("LegalRAG_SaaS")
    st.markdown(
        "Asistente juridico multi-tenant con **RAG local** (embeddings en este "
        "servidor) y **LLM externo** (OpenRouter).\n\n"
        "Para comenzar, ingresa tu **Llave de Acceso (JWT)** en el panel "
        "lateral izquierdo. El frontend NO almacena credenciales: cada "
        "peticion se firma en el momento con el token que pegues."
    )

    with st.expander("Como obtener una llave de acceso", expanded=False):
        st.markdown(
            "1. Solicita a tu administrador el JWT de tu tenant.\n"
            "2. Tambien puedes registrar uno nuevo (modo dev):\n"
            "```\n"
            "curl -X POST http://localhost:8000/auth/registro \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"nombre_tenant\":\"Demo\",\"email\":\"yo@bufete.com\","
            "\"contrasena\":\"micontrasena123\"}'\n"
            "```\n"
            "3. Copia el campo `access_token` de la respuesta y pegalo en el panel."
        )


def _area_chat() -> None:
    st.title("Asistente Juridico")
    st.caption(
        f"Conectado a `{URL_API_BACKEND}`. "
        "El backend aplica la Gobernanza PII antes de cualquier llamada externa."
    )

    _renderizar_historial()
    _renderizar_input_chat()


def _renderizar_historial() -> None:
    """Muestra todos los mensajes acumulados en `st.session_state.mensajes`."""
    for mensaje in st.session_state.mensajes:
        with st.chat_message(mensaje["role"]):
            st.markdown(mensaje["content"])
            # Si el mensaje del asistente trae la pregunta ofuscada, la
            # mostramos en un expander para reforzar la confianza del
            # usuario en la Gobernanza PII.
            if mensaje["role"] == "assistant" and mensaje.get("pregunta_ofuscada"):
                with st.expander("Ver pregunta ofuscada enviada al LLM"):
                    st.code(mensaje["pregunta_ofuscada"], language="text")


def _renderizar_input_chat() -> None:
    """Caja de entrada que envia la pregunta al backend."""
    prompt = st.chat_input("Escribe tu consulta juridica...")
    if prompt is None:
        return
    texto = prompt.strip()
    if not texto:
        st.warning("La pregunta no puede estar vacia.")
        return
    _enviar_pregunta(texto)


def _enviar_pregunta(pregunta: str) -> None:
    """Llama al backend y persiste el intercambio en el session_state."""
    # 1. Pintar inmediatamente el mensaje del usuario.
    st.session_state.mensajes.append({"role": "user", "content": pregunta})
    with st.chat_message("user"):
        st.markdown(pregunta)

    # 2. Llamar al backend y pintar la respuesta.
    with st.chat_message("assistant"):
        with st.spinner("Consultando al backend..."):
            try:
                respuesta = httpx.post(
                    ENDPOINT_CHAT,
                    json={"pregunta": pregunta},
                    headers={
                        "Authorization": f"Bearer {st.session_state.token}",
                    },
                    timeout=TIMEOUT_CHAT,
                )
            except httpx.HTTPError as exc:
                mensaje_error = (
                    f"No se pudo conectar al backend en `{URL_API_BACKEND}`. "
                    f"Detalle: {exc}"
                )
                st.error(mensaje_error)
                st.session_state.mensajes.append(
                    {"role": "assistant", "content": mensaje_error}
                )
                return

        if respuesta.status_code == 401:
            mensaje_error = (
                "Tu llave de acceso expiro o ya no es valida. "
                "Cierra sesion e ingresa un nuevo JWT."
            )
            st.error(mensaje_error)
            st.session_state.mensajes.append(
                {"role": "assistant", "content": mensaje_error}
            )
            return

        if respuesta.status_code >= 400:
            detalle = respuesta.text
            mensaje_error = (
                f"Error del backend ({respuesta.status_code}): {detalle}"
            )
            st.error(mensaje_error)
            st.session_state.mensajes.append(
                {"role": "assistant", "content": mensaje_error}
            )
            return

        datos = respuesta.json()
        texto_respuesta = datos.get("respuesta", "(sin respuesta)")
        pregunta_ofuscada = datos.get("pregunta_ofuscada", "")

        st.markdown(texto_respuesta)
        if pregunta_ofuscada:
            with st.expander("Ver pregunta ofuscada enviada al LLM"):
                st.code(pregunta_ofuscada, language="text")

        st.session_state.mensajes.append(
            {
                "role": "assistant",
                "content": texto_respuesta,
                "pregunta_ofuscada": pregunta_ofuscada,
            }
        )


# ============================================================
# Entry point.
# ============================================================

def main() -> None:
    st.set_page_config(
        page_title="LegalRAG_SaaS",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inicializar_estado()
    _panel_lateral()
    _area_principal()


if __name__ == "__main__":
    main()
