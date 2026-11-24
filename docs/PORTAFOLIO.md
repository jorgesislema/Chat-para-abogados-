# LegalRAG — Biblioteca jurídica con asistente de IA para bufetes

Plataforma web para estudios jurídicos. Reúne leyes, sentencias y expedientes del bufete en una biblioteca con buscador. Incluye un asistente de IA que responde citando la fuente. Está pensada para un bufete con varios abogados y clientes, donde cada uno ve solo lo que le corresponde.

> Proyecto de portafolio. Datos de demostración ficticios (bufete, abogados, clientes y casos). Las leyes son textos públicos de Ecuador.

---

## Problemas que resuelve

| Problema en un bufete | Cómo lo resuelve |
|---|---|
| Buscar un artículo en un código de 150 páginas en PDF toma tiempo | Visor con buscador: escribir "art 188" salta a esa línea y la resalta. |
| Las sentencias están sueltas y es difícil encontrarlas | Biblioteca con filtros por tribunal, año y materia, que se llenan solos al subir el PDF. |
| Un chat de IA común inventa artículos | El asistente responde con los documentos del bufete y muestra las fuentes que usó. |
| Datos de clientes que no deben salir del despacho | Los datos personales se ocultan antes de enviar la pregunta a la IA externa. La búsqueda corre en el propio servidor. |
| Un abogado no debe ver casos de otro, ni un cliente casos ajenos | Permisos por rol y por documento. El filtro va dentro de la búsqueda, así que no hay texto ajeno que filtrar en pantalla. |
| No se sabe quién consultó qué | Auditoría de consultas y de cambios del administrador, con métricas de uso. |
| Errores difíciles de rastrear | Registro de cada llamada con número de referencia. El usuario ve la referencia y el admin la busca. |

---

## Funciones principales

**Biblioteca jurídica**
- Lista de leyes, jurisprudencia y expedientes, con buscador por nombre.
- Búsqueda de una frase dentro de todos los textos, con un trozo de contexto en cada resultado.
- Filtros de jurisprudencia por tribunal, año y materia.

**Visor de documentos**
- Texto completo con índice de artículos. El Código del Trabajo tiene 650 artículos detectados.
- Buscador por número de artículo o por palabra, con botones de siguiente y anterior ("1 de 12").
- Botón "Copiar artículo" y botón "Explicar con el asistente".

**Asistente de IA (RAG)**
- RAG significa responder con documentos propios.
- Respuesta en vivo, palabra por palabra.
- Texto con formato y panel de fuentes citadas.
- Entiende preguntas de seguimiento dentro de la misma conversación.
- Aviso fijo: "No reemplaza el criterio de un abogado".

**Conversaciones**
- Historial agrupado por fecha (hoy, ayer, esta semana).
- Se pueden buscar, filtrar por materia, renombrar, archivar, borrar y exportar a PDF.
- La demo trae 200 conversaciones repartidas en 19 usuarios y 25 casos.

**Administración (solo admin)**
- Usuarios y roles: admin, abogado y cliente.
- Matriz de permisos usuario × colección, con interruptores.
- Subida de PDF con barra de progreso. Rechaza duplicados por huella SHA-256 y rechaza PDF escaneados sin texto.
- Ficha editable de cada documento: tribunal, año, materia y número de caso.
- Auditoría, métricas (consultas por día, temas más preguntados, documentos más citados) y registro de errores.

**Interfaz**
- Diseño propio en azul marino y dorado, con modo claro y oscuro.
- Se adapta a celular, tablet y escritorio. Probado a 390 px de ancho.
- Modo burbuja flotante: el chat se minimiza a un botón redondo que se puede arrastrar, con contador de mensajes no leídos.

---

## Tecnologías usadas

| Capa | Tecnología | Para qué |
|---|---|---|
| Servidor | Python 3.11, FastAPI, Uvicorn | API REST y respuesta en vivo (SSE) |
| Base de datos | SQLite + SQLAlchemy 2 | Usuarios, permisos, conversaciones, casos, auditoría y texto completo de los documentos |
| Búsqueda por significado | ChromaDB + sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`) | Búsqueda semántica en español, corre en el propio servidor |
| Lectura de documentos | PyMuPDF, python-docx | Sacar el texto de PDF y DOCX |
| IA generativa | API compatible con OpenAI (SenseNova; también OpenRouter, DeepSeek u otros) | Redactar la respuesta con las fuentes encontradas |
| Resiliencia | tenacity | Reintentos ante cortes o límites de la IA externa |
| Seguridad | JWT (python-jose), bcrypt (passlib) | Inicio de sesión, roles y bloqueo tras 5 intentos fallidos |
| Interfaz | HTML, CSS y JavaScript sin frameworks | Interfaz liviana sin pasos de compilación |
| Despliegue | Docker y Docker Compose | Arranque con un comando, con datos guardados fuera del contenedor |
| Calidad | pytest (228 pruebas), Playwright | Pruebas del servidor y capturas reales en escritorio y celular |

---

## Decisiones técnicas destacadas

- **Corte por artículo:** las leyes se guardan artículo por artículo, así que el asistente recibe artículos completos. Las sentencias se cortan por tamaño para no perder antecedentes ni considerandos.
- **Permisos dentro de la búsqueda:** el filtro de permisos se aplica en la consulta a ChromaDB. Un usuario sin permiso nunca recibe el texto, ni siquiera en la respuesta del servidor.
- **Privacidad:** la búsqueda y la conversión a vectores corren en el propio servidor. A la IA externa solo va la pregunta, con los datos personales ocultos, y los trozos de texto necesarios.
- **Proveedor de IA intercambiable:** basta cambiar el `.env`. El sistema deduce el proveedor por la clave presente y evita mezclar una clave con el servicio equivocado.
- **Recuperación:** un script rehace la base de búsqueda desde el texto guardado en SQLite si el índice se daña.
- **Trazabilidad:** cada llamada se registra con un ID. El registro nunca guarda contraseñas, tokens ni el texto de las preguntas.

---

## Cómo se ejecuta

```bash
# 1. Copiar .env.ejemplo a .env y completar SECRET_KEY_JWT y la clave de IA
# 2. Arrancar (puerto 8081 en este ejemplo)
PUERTO_HOST=8081 docker compose up -d --build
# 3. Abrir http://localhost:8081  →  usuario demo: m.vela / demo-2026
```

En Windows PowerShell el paso 2 es: `$env:PUERTO_HOST='8081'; docker compose up -d --build`.

---

## Capturas

Las capturas están en `docs/capturas/`:
- `01_biblioteca.png`: biblioteca en escritorio.
- `02_visor_articulo.png`: búsqueda de "art 188".
- `05_jurisprudencia_filtros.png`: filtros por tribunal y año.
- `07_movil_biblioteca.png` y `09_movil_visor.png`: vista en celular.
- `13_subida_progreso.png`: barra de progreso al subir un PDF.

---

## Límites conocidos

- Los PDF escaneados (imagen sin texto) se rechazan, porque no hay OCR (lectura de texto en imágenes).
- La IA externa puede tardar o fallar. En ese caso el sistema muestra un aviso y no inventa una respuesta.
- No hay medición formal de acierto del asistente con el conjunto de preguntas de evaluación.
