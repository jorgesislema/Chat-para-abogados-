# 02 - Gobernanza y Privacidad (PII)

## 1. Regla de Oro

> **Ningun dato personal identificable (PII) puede salir del backend hacia OpenRouter sin haber sido ofuscado localmente.**

Esta regla no es negociable. Cualquier nuevo endpoint que termine llamando a un LLM externo **debe** pasar la carga util por `ofuscar_texto()` antes del envio.

## 2. Que se considera PII

Para un despacho juridico, al menos:

- Nombres completos de personas fisicas (clientes, testigos, contrapartes).
- Correos electronicos.
- Telefonos y DNIs / pasaportes / identificadores fiscales.
- Direcciones postales exactas.
- Numeros de cuenta bancaria o tarjeta.
- Fechas de nacimiento cuando aparecen junto a nombre.

## 3. Estrategia de Ofuscacion

El modulo `backend/servicios/ofuscacion.py` implementa una primera version con:

1. **Patrones regex** para: email, telefono (variantes ES/MX/AR), DNI, IBAN, tarjeta, IP, fecha-dd/mm/aaaa.
2. **Diccionario de nombres** configurable por tenant para nombres propios que el regex no captura.
3. **Tokens de reemplazo** estables: `[NOMBRE]`, `[EMAIL]`, `[TELEFONO]`, `[DNI]`, `[IBAN]`, `[TARJETA]`, `[FECHA]`, `[DIRECCION]`.

El objetivo es **minimizar** la fuga, no garantizar anonimato criptografico. Es una capa mas dentro de la defensa en profundidad.

## 4. Test de Cumplimiento (TDD)

El test `tests/test_ofuscacion.py` **falla** la build si la ofuscacion deja pasar nombres propios tipicos. Ejemplos cubiertos:

- `"El cliente Juan Pérez pagó"` debe enmascarar a `Juan Pérez`.
- `"Contactar a maria.lopez@bufete.com"` debe enmascarar el correo.
- `"Telefono 600123456"` debe enmascarar el telefono.
- `"DNI 12345678Z"` debe enmascarar el identificador.

## 5. Auditoria y Logs

- Cada llamada al LLM registra en `mensajes` (SQLite) el **texto ofuscado** enviado, no el original.
- Los logs de aplicacion **nunca** deben imprimir el cuerpo crudo de la consulta del usuario.

## 6. Responsabilidades

| Rol | Responsabilidad |
|---|---|
| Desarrollador backend | Llamar `ofuscar_texto()` en todo punto de salida hacia red externa. |
| DevOps | Mantener `.env` fuera del repo, rotar `SECRET_KEY_JWT` periodicamente. |
| Admin del tenant | Configurar diccionario de nombres sensibles de su firma. |
| Tester | Mantener y ampliar `test_ofuscacion.py` con casos nuevos. |

## 7. Roadmap de Mejoras

- Sustituir el listado estatico de nombres por un modelo NER local (spaCy `es_core_news_lg`).
- Anonimizar referencias cruzadas: si `Juan Pérez` se reemplaza por `[CLIENTE_1]`, todas las menciones siguientes al mismo individuo deben usar el mismo token.
- Anadir capa de cifrado en reposo para los embeddings persistidos.
- Implementar rate limiting por tenant en los endpoints `/chat/*`.
