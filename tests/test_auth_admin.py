"""
Tests TDD del sistema de autenticacion y administracion (RBAC).

Cubre:
  - Hashing de contrasenas con bcrypt (texto plano NUNCA en BD).
  - Verificacion correcta de contrasenas hasheadas.
  - Login via /api/auth/token (OAuth2PasswordRequestForm).
  - Creacion de usuarios en /api/admin/usuarios (solo admin).
  - RBAC: un usuario con rol 'abogado' recibe 403 al intentar
    crear usuarios.
  - Carga de PDFs en /api/admin/documentos.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
import pytest
import fitz  # PyMuPDF
from chromadb.config import Settings
from fastapi.testclient import TestClient

from backend.api.dependencias import (
    _reset_singletons_for_tests,
    obtener_motor_vectorial,
)
from backend.core.seguridad import (
    hashear_password,
    verificar_admin_desde_claims,
    verificar_password,
)
from backend.db.base import Base
from backend.db.conexion import engine
from backend.db.modelos import ROL_ABOGADO, ROL_ADMIN
from backend.db.vectorial import MotorVectorial
from backend.main import aplicacion


# ============================================================
# Fixtures.
# ============================================================

@pytest.fixture()
def cliente(embedding_function_compartido) -> TestClient:
    """Cliente FastAPI con BD reseteada y motor vectorial en EphemeralClient."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _reset_singletons_for_tests()

    cliente_efimero = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
    cliente_efimero.reset()
    motor_test = MotorVectorial(
        cliente=cliente_efimero,
        embedding_function=embedding_function_compartido,
    )
    aplicacion.dependency_overrides[obtener_motor_vectorial] = lambda: motor_test

    try:
        yield TestClient(aplicacion)
    finally:
        aplicacion.dependency_overrides.clear()
        _reset_singletons_for_tests()


def _bootstrap_admin(cliente: TestClient) -> str:
    """Crea tenant + admin via /auth/registro y devuelve el JWT."""
    r = cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete RBAC",
            "username": "admin_jefe",
            "contrasena": "contrasenaSegura123",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


# ============================================================
# Hashing de contrasenas (unit tests, sin HTTP).
# ============================================================

def test_hash_no_es_igual_a_texto_plano() -> None:
    """El hash NO puede ser identico a la contrasena original."""
    contrasena = "MiClaveSecreta_2026"
    hash_generado = hashear_password(contrasena)
    assert hash_generado != contrasena
    # bcrypt genera strings de ~60 caracteres
    assert len(hash_generado) >= 50


def test_hash_es_deterministico_pero_con_salt() -> None:
    """Dos hashes de la misma contrasena deben ser DIFERENTES (salt aleatorio)."""
    contrasena = "MiClaveSecreta_2026"
    h1 = hashear_password(contrasena)
    h2 = hashear_password(contrasena)
    assert h1 != h2, "bcrypt debe generar salts diferentes cada vez"


def test_verificar_password_acepta_la_contrasena_correcta() -> None:
    contrasena = "MiClaveSecreta_2026"
    hash_guardado = hashear_password(contrasena)
    assert verificar_password(contrasena, hash_guardado) is True


def test_verificar_password_rechaza_contrasena_incorrecta() -> None:
    contrasena = "MiClaveSecreta_2026"
    hash_guardado = hashear_password(contrasena)
    assert verificar_password("otra_cosa", hash_guardado) is False
    assert verificar_password("", hash_guardado) is False


def test_verificar_password_devuelve_false_con_hash_invalido() -> None:
    """Si el hash almacenado esta corrupto, NO debe explotar: debe devolver False."""
    assert verificar_password("cualquiera", "hash-no-valido") is False


# ============================================================
# verificar_admin_desde_claims (helper puro).
# ============================================================

def test_verificar_admin_true_si_rol_es_admin() -> None:
    assert verificar_admin_desde_claims({"rol": ROL_ADMIN}) is True


def test_verificar_admin_false_si_rol_es_abogado() -> None:
    assert verificar_admin_desde_claims({"rol": ROL_ABOGADO}) is False


def test_verificar_admin_false_si_no_hay_rol() -> None:
    assert verificar_admin_desde_claims({}) is False


# ============================================================
# Login via /api/auth/token (OAuth2 password flow).
# ============================================================

def test_api_auth_token_con_credenciales_validas(cliente: TestClient) -> None:
    _bootstrap_admin(cliente)
    respuesta = cliente.post(
        "/api/auth/token",
        data={"username": "admin_jefe", "password": "contrasenaSegura123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert "access_token" in cuerpo
    assert cuerpo["token_type"] == "bearer"


def test_api_auth_token_con_password_incorrecta(cliente: TestClient) -> None:
    _bootstrap_admin(cliente)
    respuesta = cliente.post(
        "/api/auth/token",
        data={"username": "admin_jefe", "password": "OTRA_CONTRASENA"},
    )
    assert respuesta.status_code == 401
    assert "Credenciales" in respuesta.json()["detail"]


def test_api_auth_token_con_usuario_inexistente(cliente: TestClient) -> None:
    respuesta = cliente.post(
        "/api/auth/token",
        data={"username": "fantasma", "password": "lo_que_sea"},
    )
    assert respuesta.status_code == 401


# ============================================================
# Creacion de usuarios (POST /api/admin/usuarios) con RBAC.
# ============================================================

def test_admin_puede_crear_usuario_abogado(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)

    # Necesitamos saber el tenant_id del admin. Lo leemos directamente
    # de la BD para evitar hardcodear valores.
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import Tenant

    bd = obtener_sesion_bd()
    try:
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()

    respuesta = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_admin}"},
        json={
            "username": "abogado_nuevo",
            "password": "contrasenaSegura123",
            "rol": "abogado",
            "tenant_id": tenant_id,
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["username"] == "abogado_nuevo"
    assert cuerpo["rol"] == "abogado"
    assert "password" not in cuerpo  # la contraseña NUNCA debe devolverse


def test_usuario_abogado_NO_puede_crear_usuarios_devuelve_403(
    cliente: TestClient,
) -> None:
    """
    RBAC: el caso estrella. Un usuario con rol 'abogado' intenta
    crear un usuario y el sistema debe devolver 403 Forbidden.
    """
    token_admin = _bootstrap_admin(cliente)
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import Tenant

    bd = obtener_sesion_bd()
    try:
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()

    # El admin crea un abogado.
    r_crear = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_admin}"},
        json={
            "username": "abogado_rbac",
            "password": "contrasenaSegura123",
            "rol": "abogado",
            "tenant_id": tenant_id,
        },
    )
    assert r_crear.status_code == 201

    # El abogado hace login.
    r_login = cliente.post(
        "/api/auth/token",
        data={"username": "abogado_rbac", "password": "contrasenaSegura123"},
    )
    assert r_login.status_code == 200
    token_abogado = r_login.json()["access_token"]

    # El abogado intenta crear OTRO usuario: 403.
    r_intento = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_abogado}"},
        json={
            "username": "otro_usuario",
            "password": "contrasenaSegura123",
            "rol": "abogado",
            "tenant_id": tenant_id,
        },
    )
    assert r_intento.status_code == 403
    assert "administrador" in r_intento.json()["detail"].lower()


def test_usuarios_endpoint_sin_token_devuelve_401(cliente: TestClient) -> None:
    respuesta = cliente.post(
        "/api/admin/usuarios",
        json={
            "username": "x",
            "password": "contrasenaSegura123",
            "rol": "abogado",
            "tenant_id": 1,
        },
    )
    assert respuesta.status_code == 401


def test_crear_usuario_con_username_duplicado_devuelve_409(
    cliente: TestClient,
) -> None:
    token_admin = _bootstrap_admin(cliente)
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import Tenant

    bd = obtener_sesion_bd()
    try:
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()

    payload = {
        "username": "duplicado",
        "password": "contrasenaSegura123",
        "rol": "abogado",
        "tenant_id": tenant_id,
    }
    r1 = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_admin}"},
        json=payload,
    )
    assert r1.status_code == 201
    r2 = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_admin}"},
        json=payload,
    )
    assert r2.status_code == 409


def test_crear_usuario_con_rol_invalido_devuelve_422(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)
    respuesta = cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_admin}"},
        json={
            "username": "alguien",
            "password": "contrasenaSegura123",
            "rol": "superusuario",  # no esta en ROLES_VALIDOS
            "tenant_id": 1,
        },
    )
    assert respuesta.status_code == 422


# ============================================================
# Carga de PDFs (POST /api/admin/documentos).
# ============================================================

def _crear_pdf_dummy_en_memoria() -> bytes:
    """Genera un PDF de una pagina con texto conocido y lo devuelve como bytes."""
    buffer = io.BytesIO()
    doc = fitz.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_text((72, 72), "Contrato de prueba entre las partes para test de ingesta.")
    doc.save(buffer)
    doc.close()
    return buffer.getvalue()


def test_carga_pdf_dummy_devuelve_201_y_chunk_count(cliente: TestClient) -> None:
    """
    El admin sube un PDF dummy. El endpoint debe:
      - Responder 201.
      - Devolver el numero de chunks indexados (> 0).
      - Indicar la coleccion destino.
      - Devolver el hash SHA-256 del archivo.
    """
    token_admin = _bootstrap_admin(cliente)
    pdf_bytes = _crear_pdf_dummy_en_memoria()

    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Expediente",
            "destino_tenant_id": "1",
            "titulo": "Contrato de prueba",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["chunks_indexados"] >= 1
    assert cuerpo["nivel"] == "abogado_1"
    assert cuerpo["coleccion_destino"].startswith("casos_user_")
    # El hash es hex SHA-256 (64 chars).
    assert len(cuerpo["hash_archivo"]) == 64
    assert all(c in "0123456789abcdef" for c in cuerpo["hash_archivo"])


def test_carga_pdf_destino_global_va_a_leyes_globales(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)
    pdf_bytes = _crear_pdf_dummy_en_memoria()

    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("ley.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "global",
            "titulo": "Ley de prueba",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["coleccion_destino"] == "leyes_globales"
    assert respuesta.json()["nivel"] == "global"


def test_carga_pdf_sin_archivo_devuelve_422(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)
    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        data={"categoria": "Leyes", "destino_tenant_id": "global", "titulo": "Sin archivo"},
    )
    assert respuesta.status_code == 422


def test_carga_pdf_no_pdf_devuelve_400(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)
    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("notas.txt", b"texto plano no es un pdf", "text/plain")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "global",
            "titulo": "No es PDF",
        },
    )
    assert respuesta.status_code == 400


def test_carga_pdf_con_destino_invalido_devuelve_400(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)
    pdf_bytes = _crear_pdf_dummy_en_memoria()
    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "secret",
            "titulo": "Destino raro",
        },
    )
    assert respuesta.status_code == 400


def test_carga_pdf_con_categoria_invalida_devuelve_400(cliente: TestClient) -> None:
    token_admin = _bootstrap_admin(cliente)
    pdf_bytes = _crear_pdf_dummy_en_memoria()
    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "CategoriaInventada",
            "destino_tenant_id": "global",
            "titulo": "Categoria rara",
        },
    )
    assert respuesta.status_code == 400


def test_abogado_NO_puede_cargar_documentos_devuelve_403(
    cliente: TestClient,
) -> None:
    """El abogado no puede subir PDFs aunque tenga JWT valido."""
    token_admin = _bootstrap_admin(cliente)
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import Tenant

    bd = obtener_sesion_bd()
    try:
        tenant_id = bd.query(Tenant).first().id
    finally:
        bd.close()

    # Admin crea un abogado.
    cliente.post(
        "/api/admin/usuarios",
        headers={"Authorization": f"Bearer {token_admin}"},
        json={
            "username": "abogado_sin_permiso",
            "password": "contrasenaSegura123",
            "rol": "abogado",
            "tenant_id": tenant_id,
        },
    )
    # Login del abogado.
    r_login = cliente.post(
        "/api/auth/token",
        data={"username": "abogado_sin_permiso", "password": "contrasenaSegura123"},
    )
    token_abogado = r_login.json()["access_token"]
    pdf_bytes = _crear_pdf_dummy_en_memoria()

    r_intento = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_abogado}"},
        files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "global",
            "titulo": "Intento",
        },
    )
    assert r_intento.status_code == 403


# ============================================================
# Deduplicacion por hash SHA-256 (Anti-Data-Poisoning Fase 8).
# ============================================================
#
# Si un admin sube el mismo PDF dos veces (mismo nombre, mismo
# contenido o no), el endpoint debe:
#   - Devolver 201 + hash en la primera subida.
#   - Devolver 409 Conflict en la segunda (con mensaje claro).
#   - Persistir un unico registro en `documentos_ingestados`.
# Esto evita que ChromaDB acabe con vectores duplicados del mismo
# documento, lo que degrada la precision del RAG.


def test_hash_se_persiste_en_documentos_ingestados_despues_de_201(
    cliente: TestClient,
) -> None:
    """Tras 201, la BD tiene un registro DocumentoIngestado con el hash."""
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import DocumentoIngestado

    token_admin = _bootstrap_admin(cliente)
    pdf_bytes = _crear_pdf_dummy_en_memoria()
    import hashlib

    hash_esperado = hashlib.sha256(pdf_bytes).hexdigest()

    respuesta = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("demo.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "global",
            "titulo": "Demo deduplicacion",
        },
    )
    assert respuesta.status_code == 201, respuesta.text
    hash_devuelto = respuesta.json()["hash_archivo"]
    assert hash_devuelto == hash_esperado

    # Verificar que se guardo en BD.
    bd = obtener_sesion_bd()
    try:
        registro = (
            bd.query(DocumentoIngestado)
            .filter(DocumentoIngestado.hash_archivo == hash_esperado)
            .first()
        )
    finally:
        bd.close()
    assert registro is not None
    assert registro.nombre_archivo == "demo.pdf"
    assert registro.categoria == "Leyes"
    assert registro.tenant_id_destino == "0"  # "global" -> 0


def test_segunda_subida_del_mismo_pdf_devuelve_409(
    cliente: TestClient,
) -> None:
    """Subir el MISMO binario dos veces -> la segunda vez es 409 Conflict."""
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import DocumentoIngestado

    token_admin = _bootstrap_admin(cliente)
    pdf_bytes = _crear_pdf_dummy_en_memoria()

    # 1ra subida: OK.
    r1 = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("dup.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "global",
            "titulo": "Duplicado A",
        },
    )
    assert r1.status_code == 201, r1.text

    # 2da subida con MISMO contenido binario: 409.
    r2 = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("dup.pdf", pdf_bytes, "application/pdf")},
        data={
            "categoria": "Leyes",
            "destino_tenant_id": "global",
            "titulo": "Duplicado B",
        },
    )
    assert r2.status_code == 409, r2.text
    assert "ya fue ingestado" in r2.json()["detail"].lower()

    # Y en la BD sigue habiendo UN SOLO registro (no se duplico).
    bd = obtener_sesion_bd()
    try:
        total = bd.query(DocumentoIngestado).count()
    finally:
        bd.close()
    assert total == 1


def test_pdf_distinto_con_mismo_nombre_NO_es_duplicado(
    cliente: TestClient,
) -> None:
    """Dos PDFs con el mismo nombre de archivo pero contenido distinto
    NO colisionan en el hash: ambos se ingestan correctamente.

    El deduplicado opera sobre el contenido binario, no sobre el nombre
    del archivo. Esto evita falsos positivos cuando dos clientes suben
    accidentalmente archivos con el mismo nombre.
    """
    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import DocumentoIngestado

    token_admin = _bootstrap_admin(cliente)
    pdf1 = _crear_pdf_dummy_en_memoria()

    # Generamos un PDF con contenido DIFERENTE cambiando una palabra.
    buffer = io.BytesIO()
    doc = fitz.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_text((72, 72), "Contrato MODIFICADO entre las partes.")
    doc.save(buffer)
    doc.close()
    pdf2_distinto = buffer.getvalue()
    assert pdf1 != pdf2_distinto  # sanity: contenido binario distinto

    # Subimos ambos con el MISMO nombre de archivo "repetido.pdf".
    r1 = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("repetido.pdf", pdf1, "application/pdf")},
        data={"categoria": "Leyes", "destino_tenant_id": "global", "titulo": "v1"},
    )
    assert r1.status_code == 201, r1.text

    r2 = cliente.post(
        "/api/admin/documentos",
        headers={"Authorization": f"Bearer {token_admin}"},
        files={"file": ("repetido.pdf", pdf2_distinto, "application/pdf")},
        data={"categoria": "Leyes", "destino_tenant_id": "global", "titulo": "v2"},
    )
    assert r2.status_code == 201, r2.text

    # Ambos registros estan en la BD con hashes distintos.
    bd = obtener_sesion_bd()
    try:
        registros = bd.query(DocumentoIngestado).all()
    finally:
        bd.close()
    assert len(registros) == 2
    assert registros[0].hash_archivo != registros[1].hash_archivo
    # Ambos se llaman "repetido.pdf" (mismo nombre, distinto hash).
    assert all(r.nombre_archivo == "repetido.pdf" for r in registros)


# ============================================================
# Contrasenas NUNCA almacenadas en texto plano (seguridad).
# ============================================================

def test_contrasena_no_se_almacena_en_texto_plano(cliente: TestClient) -> None:
    """
    Tras el registro, la BD debe contener el HASH, no la contrasena.
    """
    contrasena_plana = "MiContrasenaSecreta_2026"
    cliente.post(
        "/auth/registro",
        json={
            "nombre_tenant": "Bufete Hash",
            "username": "usuario_hash",
            "contrasena": contrasena_plana,
        },
    )

    from backend.db.conexion import obtener_sesion_bd
    from backend.db.modelos import Usuario

    bd = obtener_sesion_bd()
    try:
        usuario = (
            bd.query(Usuario).filter(Usuario.username == "usuario_hash").first()
        )
    finally:
        bd.close()

    assert usuario is not None
    # La contrasena plana NO debe estar en la BD de ninguna forma.
    assert contrasena_plana not in usuario.password_hash
    # El hash empieza por $2 (prefijo bcrypt valido).
    assert usuario.password_hash.startswith("$2")
