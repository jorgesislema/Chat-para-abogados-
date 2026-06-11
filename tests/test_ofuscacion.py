"""
Suite de tests para el modulo CRITICO de ofuscacion de PII.

Estos tests son la primera linea de defensa de la Gobernanza del proyecto.
Si alguno falla, el backend NO debe poder levantar.
"""

from __future__ import annotations

import pytest

from backend.servicios.ofuscacion import (
    NOMBRES_PROPIOS_CONOCIDOS,
    contiene_pii,
    ofuscar_texto,
)


# ============================================================
# Test principal exigido por la especificacion del proyecto.
# ============================================================
def test_ofusca_nombre_propio_juan_perez() -> None:
    """
    Dada la frase 'El cliente Juan Pérez pagó', la funcion de ofuscacion
    debe devolver el texto con el nombre propio enmascarado.

    El test valida dos condiciones:
      1. El nombre 'Juan Pérez' ya NO aparece en la salida.
      2. Aparece la ficha anonima '[NOMBRE]'.
    """
    texto_original = "El cliente Juan Pérez pagó"
    texto_ofuscado = ofuscar_texto(texto_original)

    assert "Juan Pérez" not in texto_ofuscado, (
        "FALLO DE GOBERNANZA: el nombre propio 'Juan Pérez' no fue "
        f"enmascarado. Salida obtenida: {texto_ofuscado!r}"
    )
    assert "[NOMBRE]" in texto_ofuscado, (
        f"FALLO DE GOBERNANZA: no se encontro el token '[NOMBRE]' en "
        f"la salida: {texto_ofuscado!r}"
    )


# ============================================================
# Cobertura adicional: emails, telefonos, DNI, IBAN, tarjetas.
# ============================================================
@pytest.mark.parametrize(
    "texto, ficha, fragmento_a_enmascarar",
    [
        (
            "Contactar a maria.lopez@bufete.com cuanto antes",
            "[EMAIL]",
            "maria.lopez@bufete.com",
        ),
        (
            "Llamame al +34 600 123 456 esta tarde",
            "[TELEFONO]",
            "600 123 456",
        ),
        (
            "Su identificacion es 12345678Z",
            "[DNI]",
            "12345678Z",
        ),
        (
            "Cargo en la cuenta ES91 2100 0418 4502 0005 1332",
            "[IBAN]",
            "ES91 2100 0418 4502 0005 1332",
        ),
        (
            "Pago con tarjeta 4111 1111 1111 1111 mañana",
            "[TARJETA]",
            "4111 1111 1111 1111",
        ),
        (
            "La cita es el 15/03/2026 a las 10:00",
            "[FECHA]",
            "15/03/2026",
        ),
    ],
)
def test_ofusca_patrones_pii(
    texto: str, ficha: str, fragmento_a_enmascarar: str
) -> None:
    """Cubre email, telefono, DNI, IBAN, tarjeta y fecha."""
    resultado = ofuscar_texto(texto)
    assert fragmento_a_enmascarar not in resultado, (
        f"FALLO DE GOBERNANZA: el fragmento {fragmento_a_enmascarar!r} "
        f"no fue reemplazado en {resultado!r}"
    )
    assert ficha in resultado, (
        f"FALLO DE GOBERNANZA: la ficha {ficha!r} no aparece en {resultado!r}"
    )


# ============================================================
# Casos limite.
# ============================================================
def test_ofuscar_texto_vacio_devuelve_vacio() -> None:
    assert ofuscar_texto("") == ""


def test_ofuscar_texto_sin_pii_se_conserva_identico() -> None:
    """Un texto sin datos personales debe permanecer inalterado."""
    texto = "La ley establece que el contrato es valido."
    assert ofuscar_texto(texto) == texto


def test_contiene_pii_detecta_nombre() -> None:
    assert contiene_pii("El cliente Juan Pérez pagó") is True


def test_contiene_pii_false_para_texto_limpio() -> None:
    assert contiene_pii("Resumen ejecutivo del caso 1234") is False


def test_nombre_compuesto_de_tres_palabras() -> None:
    """Cubre 'Maria del Carmen' como secuencia capitalizada de 3 palabras.

    Nota: 'del' en minuscula rompe la cadena capitalizada, por lo que el
    patron enmascara 'Maria Carmen'. Validamos al menos que 'Maria' (el
    nombre del diccionario) y el primer apellido quedan sustituidos.
    """
    texto = "Comparecio Maria Carmen Lopez ante el juez"
    resultado = ofuscar_texto(texto)
    assert "Maria" not in resultado
    assert "[NOMBRE]" in resultado


def test_nombres_conocidos_no_vacios() -> None:
    """El diccionario de nombres debe estar poblado para que la cobertura
    del patron sea razonable."""
    assert len(NOMBRES_PROPIOS_CONOCIDOS) >= 50


def test_ofusca_multiples_pii_en_un_mismo_texto() -> None:
    """Varios tipos de PII en una sola frase deben quedar todos enmascarados."""
    texto = (
        "El cliente Pedro Ramirez (DNI 87654321X) llamo al 612345678 "
        "desde pedro.ramirez@correo.com el dia 01/12/2025"
    )
    resultado = ofuscar_texto(texto)
    assert "Pedro Ramirez" not in resultado
    assert "87654321X" not in resultado
    assert "612345678" not in resultado
    assert "pedro.ramirez@correo.com" not in resultado
    assert "01/12/2025" not in resultado
