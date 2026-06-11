"""
Modulo CRITICO de Gobernanza: Ofuscacion de PII (Informacion Personal Identificable).

Este modulo se ejecuta SIEMPRE antes de enviar cualquier texto a un proveedor
externo de LLM (OpenRouter). Su contrato es:

    >>> ofuscar_texto("El cliente Juan Pérez pagó")
    'El cliente [NOMBRE] pagó'

Si una nueva forma de PII es detectada en produccion, se anade su patron
a este archivo y su test correspondiente en tests/test_ofuscacion.py.
"""

from __future__ import annotations

import re

# ------------------------------------------------------------
# Diccionario de nombres propios comunes (es-ES / es-MX / es-AR).
# Usado como ancla para detectar pares "Nombre + Apellido(s)".
# En produccion se complementara con un modelo NER local.
# ------------------------------------------------------------
NOMBRES_PROPIOS_CONOCIDOS: set[str] = {
    "adrian", "adrián", "agustin", "agustín", "alberto", "alejandra", "alejandro",
    "alfonso", "alfredo", "alicia", "amalia", "amparo", "ana", "andrea", "andres",
    "andrés", "ángela", "angela", "antonio", "antonia", "aquilino", "arantza",
    "ariadna", "arturo", "ascension", "ascensión", "aurora", "baldomero",
    "bartolome", "bartolomé", "beatriz", "belen", "belén", "benjamin", "benjamín",
    "bernardo", "blanca", "borja", "camilo", "carla", "carlos", "carmelo",
    "carmen", "carolina", "catalina", "cecilia", "cesar", "césar", "clara",
    "claudia", "clemente", "concepcion", "concepción", "conrado", "constantino",
    "cristina", "cristobal", "cristóbal", "dario", "darío", "david", "diego",
    "dolores", "domingo", "eduardo", "elena", "elias", "elías", "elisa",
    "emilia", "emilio", "enrique", "esperanza", "esteban", "ester", "esther",
    "eugenia", "eusebio", "eva", "evaristo", "federico", "felipe", "felix",
    "félix", "fernando", "fidel", "francisco", "francisca", "gabriel", "gema",
    "gerardo", "gertrudis", "gloria", "gonzalo", "gregorio", "guadalupe",
    "guillermo", "gustavo", "helen", "hilda", "hugo", "ignacio", "iker",
    "inmaculada", "irene", "isaac", "isabel", "isidro", "ivan", "iván",
    "jacinto", "jacobo", "jaime", "javier", "jennifer", "jesus", "jesús",
    "joaquin", "joaquín", "jonathan", "jordi", "jorge", "jose", "josé",
    "josefa", "josefina", "juan", "juana", "julia", "julian", "julián",
    "laura", "leandro", "leon", "leonor", "lidia", "lorena", "lorenzo",
    "lucía", "lucia", "luis", "luisa", "magdalena", "manuel", "manuela",
    "mar", "marcela", "marcos", "margarita", "maria", "maría", "mariano",
    "marina", "marta", "martin", "martín", "mateo", "matias", "matías",
    "matilde", "mauricio", "maximiliano", "melania", "mercedes", "miguel",
    "milagros", "miriam", "mirian", "mohamed", "mónica", "monica", "nadia",
    "narciso", "natalia", "nicolas", "nicolás", "noelia", "noemi", "noemí",
    "olga", "oscar", "óscar", "pablo", "paola", "pascual", "patricia",
    "paula", "pedro", "pilar", "rafael", "ramiro", "ramon", "ramón",
    "raquel", "raul", "raúl", "reinaldo", "remedios", "ricardo", "rita",
    "roberto", "rocio", "rocío", "rodrigo", "rogelio", "roman", "román",
    "rosa", "rosalia", "rosalía", "rosario", "salvador", "salvadora",
    "samuel", "sandra", "santiago", "sara", "saul", "saúl", "sebastian",
    "sebastián", "serafin", "serafín", "sergio", "silvia", "sofia", "sofía",
    "soledad", "sonia", "sonía", "susana", "tania", "teodoro", "teresa",
    "tomas", "tomás", "ursula", "úrsula", "valentin", "valentín", "valeria",
    "valerio", "vanesa", "veronica", "verónica", "vicente", "victor", "víctor",
    "victoria", "vidal", "xavier", "yolanda", "zaida",
}

# ------------------------------------------------------------
# Patrones regex: ordenados de mas especifico a mas generico.
# Cada patron se reemplaza por un token estable y reversible
# solo dentro del propio backend (mapa en sesion si se requiere).
# ------------------------------------------------------------

# IBAN espanol: ES + 2 digitos + 20 digitos (5 grupos de 4).
_PATRON_IBAN = re.compile(r"\bES\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b")

# Tarjetas de credito (13-19 digitos, con separadores opcionales).
_PATRON_TARJETA = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# Correo electronico.
_PATRON_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# DNI / NIF espanol: 8 digitos + letra.
_PATRON_DNI = re.compile(r"\b\d{8}[A-Z]\b")

# Telefono espanol: 9 cifras (3+3+3) empezando por 6/7/9, con o sin +34.
_PATRON_TELEFONO = re.compile(
    r"(?:\+34[\s.-]?)?[679]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b"
)

# Fecha en formato dd/mm/aaaa o dd-mm-aaaa.
_PATRON_FECHA = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")

# Nombre propio + 1 a 3 apellidos (palabras capitalizadas).
# Se ordena por longitud descendente para que "José" no se empate con "Jose".
_NOMBRES_ORDENADOS = sorted(NOMBRES_PROPIOS_CONOCIDOS, key=len, reverse=True)
_PATRON_NOMBRE_PROPIO = re.compile(
    r"\b(?:" + "|".join(_NOMBRES_ORDENADOS) + r")"
    r"(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3}",
    re.IGNORECASE,
)


def _token_nombre(_match: re.Match) -> str:
    """Reemplaza un match de nombre propio por la ficha anonima."""
    return "[NOMBRE]"


def ofuscar_texto(texto: str) -> str:
    """
    Devuelve una copia del texto con toda la PII detectable enmascarada.

    Esta funcion es pura: no tiene efectos secundarios, no realiza I/O
    y es segura de invocar en bucles de alta frecuencia.
    """
    if not texto:
        return texto

    resultado = texto
    # Orden importante: patrones mas especificos primero para evitar
    # que un patron general (tarjeta) "robe" contenido de uno mas
    # concreto (IBAN).
    resultado = _PATRON_IBAN.sub("[IBAN]", resultado)
    resultado = _PATRON_TARJETA.sub("[TARJETA]", resultado)
    resultado = _PATRON_EMAIL.sub("[EMAIL]", resultado)
    resultado = _PATRON_TELEFONO.sub("[TELEFONO]", resultado)
    resultado = _PATRON_DNI.sub("[DNI]", resultado)
    resultado = _PATRON_FECHA.sub("[FECHA]", resultado)
    resultado = _PATRON_NOMBRE_PROPIO.sub(_token_nombre, resultado)
    return resultado


def contiene_pii(texto: str) -> bool:
    """
    Indica si el texto contiene PII sin enmascarar.

    Util como pre-check en los endpoints que iran a OpenRouter: si devuelve
    True, el caller deberia rechazar la peticion o aplicar ofuscar_texto.
    """
    if not texto:
        return False
    return ofuscar_texto(texto) != texto


__all__ = [
    "ofuscar_texto",
    "contiene_pii",
    "NOMBRES_PROPIOS_CONOCIDOS",
]
