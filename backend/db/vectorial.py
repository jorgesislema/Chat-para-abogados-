"""
Motor vectorial 100% local para LegalRAG_SaaS.

Implementa la capa de embeddings + busqueda por similitud usando:
  - ChromaDB como vector store (persistido en disco por defecto).
  - sentence-transformers (modelo all-MiniLM-L6-v2, 384 dim) como
    funcion de embedding local. CERO llamadas externas.

Aislamiento multi-tenant:
  - Tier 1 (Leyes Publicas): coleccion global `leyes_globales`,
    accesible para todos los tenants autenticados.
  - Tier 3 (Casos Privados): una coleccion por tenant con el
    patron `casos_user_<id>`. El backend NUNCA mezcla colecciones
    entre tenants en una misma consulta.

Las funciones son deterministas y testeables: el constructor acepta
un cliente ChromaDB pre-construido (util para tests con EphemeralClient
que no toca disco).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.utils import embedding_functions

# Nombre canonico de la coleccion global (Tier 1).
COLECCION_GLOBAL: str = "leyes_globales"
PREFIJO_COLECCION_TENANT: str = "casos_user_"
# Multilingual optimizado para espanol (~470MB, 384 dim). Muy superior
# a `all-MiniLM-L6-v2` para textos largos en espanol juridico.
# ATENCION: cambiar de modelo invalida los vectores ya indexados en
# ChromaDB; tras un upgrade, hay que re-ingestar todos los documentos.
MODELO_EMBEDDINGS_POR_DEFECTO: str = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

# Categorias validas para clasificar los chunks ingeridos. Se guardan
# en la metadata de ChromaDB y el RAG puede filtrar por ellas desde
# el chat (selector "Contexto" del frontend).
CATEGORIAS_VALIDAS: tuple[str, ...] = ("Leyes", "Jurisprudencia", "Expediente")

# Marcador especial para que la UI del frontend represente "sin filtro"
# (busca en todas las categorias). El orquestador lo trata como None.
CATEGORIA_TODAS: str = "Todas"

# Ruta por defecto de la persistencia ChromaDB: backend/db/chroma_data/.
_RUTA_PERSISTENCIA_POR_DEFECTO: Path = (
    Path(__file__).resolve().parent / "chroma_data"
)


# ============================================================
# Tipos de resultado.
# ============================================================

@dataclass(frozen=True)
class FragmentoRecuperado:
    """
    Un chunk relevante devuelto por la busqueda vectorial.

    Atributos:
        id: identificador unico del documento en ChromaDB (util para
            trazabilidad de auditoria).
        texto: contenido textual del chunk.
        metadata: diccionario con metadatos arbitrarios del chunk
            (archivo origen, tier, indice, etc.).
        puntuacion: relevancia normalizada en [0, 1]. Mayor = mas
            relevante. Se calcula como `max(0, 1 - distancia_coseno)`.
        coleccion_origen: nombre de la coleccion de la que procede
            (util para depurar cuando se mezcla busqueda tenant + global).
    """

    id: str = ""
    texto: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    puntuacion: float = 0.0
    coleccion_origen: str = ""


# ============================================================
# Excepciones de dominio.
# ============================================================

class ErrorMotorVectorial(RuntimeError):
    """Error al operar con ChromaDB o sentence-transformers."""


def nombre_coleccion_para_tenant(usuario_id: int) -> str:
    """Devuelve el nombre canonico de la coleccion privada de un tenant."""
    return f"{PREFIJO_COLECCION_TENANT}{usuario_id}"


# ============================================================
# Clase principal.
# ============================================================

class MotorVectorial:
    """
    Fachada delgada sobre ChromaDB con embeddings locales.

    Tres modos de construccion (en orden de prioridad):
      1. `cliente=` pre-construido: util para tests con EphemeralClient.
      2. `ruta_persistencia=`: usa PersistentClient con esa ruta.
      3. Default: PersistentClient en `backend/db/chroma_data/`.

    Adicionalmente se puede inyectar un `embedding_function` ya
    construido para que tests con multiples instancias compartan
    el modelo en memoria y no re-carguen pesos.
    """

    def __init__(
        self,
        cliente: ClientAPI | None = None,
        ruta_persistencia: str | Path | None = None,
        modelo_embeddings: str = MODELO_EMBEDDINGS_POR_DEFECTO,
        embedding_function: Any = None,
    ) -> None:
        # --- Resolucion del cliente ChromaDB ---
        if cliente is not None:
            self._cliente = cliente
            self._es_efimero = True
        else:
            if ruta_persistencia is not None:
                ruta = Path(ruta_persistencia)
            elif os.getenv("RUTA_CHROMADB", "").strip():
                # Respeta .env / pruebas. Una ruta relativa se cuenta desde la raiz del proyecto.
                ruta = Path(os.environ["RUTA_CHROMADB"].strip())
                if not ruta.is_absolute():
                    ruta = Path(__file__).resolve().parents[2] / ruta
            else:
                ruta = _RUTA_PERSISTENCIA_POR_DEFECTO
            ruta.mkdir(parents=True, exist_ok=True)
            self._cliente = chromadb.PersistentClient(path=str(ruta))
            self._es_efimero = False

        # --- Resolucion de la funcion de embedding ---
        if embedding_function is not None:
            self._ef = embedding_function
        else:
            try:
                self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=modelo_embeddings
                )
            except Exception as exc:  # noqa: BLE001 - chromadb/sentence-transformers lanzan varios
                raise ErrorMotorVectorial(
                    f"No se pudo cargar el modelo de embeddings "
                    f"'{modelo_embeddings}': {exc}"
                ) from exc

        self._modelo_embeddings = modelo_embeddings

    # --------------------------------------------------------
    # Gestion de colecciones.
    # --------------------------------------------------------

    def obtener_o_crear_coleccion(self, nombre: str) -> Any:
        """
        Devuelve la coleccion, creandola si no existe. Idempotente.

        Configuramos la distancia coseno porque es la mas adecuada
        para embeddings normalizados de sentence-transformers.
        """
        return self._cliente.get_or_create_collection(
            name=nombre,
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

    def coleccion_existe(self, nombre: str) -> bool:
        """Indica si la coleccion existe en el cliente actual."""
        try:
            self._cliente.get_collection(
                name=nombre, embedding_function=self._ef
            )
            return True
        except Exception:  # noqa: BLE001 - chromadb NotFoundError + variantes
            return False

    # --------------------------------------------------------
    # Ingestion.
    # --------------------------------------------------------

    def ingestar_chunks(
        self,
        chunks: list[str],
        nombre_coleccion: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """
        Vectoriza y persiste una lista de chunks en la coleccion indicada.

        Cada chunk recibe como metadata los campos provistos en `metadata`
        mas un `indice` (posicion dentro del lote) y un `coleccion`
        con el nombre de la coleccion destino.

        Retorna el numero de chunks ingeridos.
        """
        if not chunks:
            return 0
        if not nombre_coleccion or not nombre_coleccion.strip():
            raise ErrorMotorVectorial("nombre_coleccion es obligatorio")

        coleccion = self.obtener_o_crear_coleccion(nombre_coleccion)

        # Generamos IDs unicos por seguridad incluso si el caller repite.
        ids = [f"{nombre_coleccion}_{uuid.uuid4().hex[:12]}" for _ in chunks]

        metadatas: list[dict[str, Any]] = []
        for i in range(len(chunks)):
            meta_base = dict(metadata or {})
            meta_base["indice"] = i
            meta_base["coleccion"] = nombre_coleccion
            metadatas.append(meta_base)

        # ChromaDB valida que ids, documents y metadatas tengan la misma
        # longitud; si llegan vacios, retornamos 0 arriba.
        coleccion.add(documents=chunks, ids=ids, metadatas=metadatas)
        return len(chunks)

    def ingestar_chunks_con_metadata(
        self,
        chunks: list[str],
        nombre_coleccion: str,
        metadatas: list[dict[str, Any]],
    ) -> int:
        """
        Ingesta con metadata por chunk (Fase G2: articulos y partes).

        Cada chunk lleva su propio dict (ej. articulo/parte/id_articulo);
        se anaden `indice` y `coleccion` como en `ingestar_chunks`.
        """
        if not chunks:
            return 0
        if not nombre_coleccion or not nombre_coleccion.strip():
            raise ErrorMotorVectorial("nombre_coleccion es obligatorio")
        if len(metadatas) != len(chunks):
            raise ErrorMotorVectorial("chunks y metadatas deben tener igual longitud")

        coleccion = self.obtener_o_crear_coleccion(nombre_coleccion)
        ids = [f"{nombre_coleccion}_{uuid.uuid4().hex[:12]}" for _ in chunks]
        finales: list[dict[str, Any]] = []
        for i, meta in enumerate(metadatas):
            meta_base = dict(meta or {})
            meta_base["indice"] = i
            meta_base["coleccion"] = nombre_coleccion
            finales.append(meta_base)
        coleccion.add(documents=chunks, ids=ids, metadatas=finales)
        return len(chunks)

    # --------------------------------------------------------
    # Busqueda.
    # --------------------------------------------------------

    def buscar_contexto(
        self,
        pregunta: str,
        nombre_coleccion: str,
        incluir_global: bool = True,
        top_k: int = 5,
        filtro_categoria: str | None = None,
    ) -> list[FragmentoRecuperado]:
        """
        Vectoriza `pregunta` y devuelve los Top-K chunks mas relevantes.

        Por defecto consulta AMBAS colecciones:
          - La del tenant (`nombre_coleccion`, ej. `casos_user_42`).
          - La global de leyes publicas (si `incluir_global=True`).

        Si se indica `filtro_categoria`, ChromaDB solo devuelve chunks
        cuya metadata `categoria` coincida exactamente (filtro `where`
        nativo del vector store, no post-procesado en Python).

        Categorias validas: `Leyes`, `Jurisprudencia`, `Expediente`.
        Pasar `None` (o `CATEGORIA_TODAS` que el orquestador normaliza
        a None) significa "sin filtro de categoria".

        Los resultados se fusionan, se ordenan por relevancia
        descendente y se truncan a `top_k`.

        Si la coleccion del tenant no existe (aun no se ha ingestado
        nada en ella), la busqueda sigue funcionando contra la global
        sin lanzar error.
        """
        if not pregunta or not pregunta.strip():
            return []
        if top_k <= 0:
            raise ValueError("top_k debe ser positivo")

        # Normalizamos el marcador "Todas" a None (sin filtro).
        filtro_efectivo: str | None = filtro_categoria
        if filtro_efectivo == CATEGORIA_TODAS or filtro_efectivo == "":
            filtro_efectivo = None

        # Validacion defensiva: si llega una categoria, debe estar en
        # el set valido. Cualquier otra cosa es bug del caller.
        if filtro_efectivo is not None and filtro_efectivo not in CATEGORIAS_VALIDAS:
            raise ValueError(
                f"filtro_categoria invalido: {filtro_efectivo!r}. "
                f"Valores validos: {CATEGORIAS_VALIDAS} o None."
            )

        resultados: list[FragmentoRecuperado] = []

        # 1) Busqueda en la coleccion del tenant.
        if self.coleccion_existe(nombre_coleccion):
            tenant = self._cliente.get_collection(
                name=nombre_coleccion, embedding_function=self._ef
            )
            resultados.extend(
                self._consultar(
                    tenant,
                    pregunta,
                    top_k,
                    nombre_coleccion,
                    filtro_categoria=filtro_efectivo,
                )
            )

        # 2) Busqueda opcional en la coleccion global.
        if incluir_global and nombre_coleccion != COLECCION_GLOBAL:
            if self.coleccion_existe(COLECCION_GLOBAL):
                global_col = self._cliente.get_collection(
                    name=COLECCION_GLOBAL, embedding_function=self._ef
                )
                resultados.extend(
                    self._consultar(
                        global_col,
                        pregunta,
                        top_k,
                        COLECCION_GLOBAL,
                        filtro_categoria=filtro_efectivo,
                    )
                )

        # 3) Ranking y truncado.
        resultados.sort(key=lambda f: f.puntuacion, reverse=True)
        return resultados[:top_k]

    # --------------------------------------------------------
    # Busqueda permiso-aware (Fase 2).
    # --------------------------------------------------------

    def buscar_en_colecciones(
        self,
        pregunta: str,
        colecciones: list[str],
        top_k: int = 5,
        filtro_categoria: str | None = None,
        where_por_coleccion: dict[str, dict[str, Any]] | None = None,
    ) -> list[FragmentoRecuperado]:
        """
        Busca SOLO en las colecciones indicadas (lista explicita ya
        filtrada por `backend/servicios/permisos.py`).

        El filtro de permisos se aplica DENTRO del vector store:
          - Solo se consultan las colecciones de la lista (nunca se
            abre una coleccion fuera de ella para traer texto).
          - `where_por_coleccion` permite un filtro `where` nativo de
            ChromaDB por coleccion (ej. `{"hash_archivo": {"$in": [...]}}`
            para conceder un documento concreto dentro de una coleccion
            ajena). Se combina con `filtro_categoria` mediante `$and`.
            El filtrado ocurre en el servidor: los chunks descartados
            por `where` nunca llegan al proceso Python.

        NOTA: la deteccion de "existe contenido restringido" va por
        `mejor_puntuacion_en`, que no devuelve texto.
        """
        if not pregunta or not pregunta.strip():
            return []
        if top_k <= 0:
            raise ValueError("top_k debe ser positivo")

        filtro_efectivo: str | None = filtro_categoria
        if filtro_efectivo == CATEGORIA_TODAS or filtro_efectivo == "":
            filtro_efectivo = None
        if filtro_efectivo is not None and filtro_efectivo not in CATEGORIAS_VALIDAS:
            raise ValueError(
                f"filtro_categoria invalido: {filtro_efectivo!r}. "
                f"Valores validos: {CATEGORIAS_VALIDAS} o None."
            )

        resultados: list[FragmentoRecuperado] = []
        for nombre in colecciones:
            if not self.coleccion_existe(nombre):
                continue
            coleccion = self._cliente.get_collection(
                name=nombre, embedding_function=self._ef
            )
            where_extra = (where_por_coleccion or {}).get(nombre)
            resultados.extend(
                self._consultar(
                    coleccion,
                    pregunta,
                    top_k,
                    nombre,
                    filtro_categoria=filtro_efectivo,
                    where_extra=where_extra,
                )
            )

        resultados.sort(key=lambda f: f.puntuacion, reverse=True)
        return resultados[:top_k]

    def mejor_puntuacion_en(        self,
        pregunta: str,
        colecciones: list[str],
        filtro_categoria: str | None = None,
    ) -> float:
        """
        Indica si hay contenido relevante en las colecciones indicadas
        SIN devolver texto (para decidir "sin acceso" sin filtrar).

        Retorna la mejor puntuacion en [0, 1] (0.0 si no hay nada).
        El caller compara contra un umbral y, si lo supera, responde
        con el mensaje canned de denegacion. El texto del documento
        prohibido NUNCA sale de ChromaDB hacia Python mas alla de la
        puntuacion.
        """
        if not pregunta or not pregunta.strip():
            return 0.0
        mejor = 0.0
        for nombre in colecciones:
            if not self.coleccion_existe(nombre):
                continue
            try:
                coleccion = self._cliente.get_collection(
                    name=nombre, embedding_function=self._ef
                )
                kwargs: dict[str, Any] = {"query_texts": [pregunta], "n_results": 1}
                if filtro_categoria not in (None, "", CATEGORIA_TODAS):
                    kwargs["where"] = {"categoria": filtro_categoria}
                respuesta = coleccion.query(**kwargs)
            except Exception:  # noqa: BLE001 - coleccion vacia o error: se ignora
                continue
            distancias = (respuesta.get("distances") or [[]])[0]
            if distancias:
                puntuacion = max(0.0, min(1.0, 1.0 - float(distancias[0])))
                mejor = max(mejor, puntuacion)
        return mejor

    def obtener_articulo(
        self, id_articulo: str, colecciones: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """
        Recupera el ARTICULO COMPLETO por `id_articulo` (Fase G2).

        Junta todas sus partes en orden (`parte` 1..N) aunque el
        vectorial solo haya matcheado una. Retorna (texto, metadata
        de la primera parte). Si no existe, ("", {}).
        """
        partes: list[tuple[int, str, dict[str, Any]]] = []
        for nombre in colecciones:
            if not self.coleccion_existe(nombre):
                continue
            try:
                coleccion = self._cliente.get_collection(
                    name=nombre, embedding_function=self._ef
                )
                resp = coleccion.get(where={"id_articulo": id_articulo})
            except Exception:  # noqa: BLE001 - se ignora y se sigue
                continue
            docs = resp.get("documents") or []
            metas = resp.get("metadatas") or []
            for doc, meta in zip(docs, metas):
                meta = dict(meta or {})
                try:
                    orden = int(meta.get("parte", 1))
                except (TypeError, ValueError):
                    orden = 1
                partes.append((orden, doc, meta))
        if not partes:
            return "", {}
        partes.sort(key=lambda t: t[0])
        texto = "\n\n".join(p[1] for p in partes).strip()
        return texto, partes[0][2]

    # --------------------------------------------------------
    # Utilidades internas.
    # --------------------------------------------------------

    def _consultar(
        self,
        coleccion: Any,
        pregunta: str,
        top_k: int,
        coleccion_origen: str,
        filtro_categoria: str | None = None,
        where_extra: dict[str, Any] | None = None,
    ) -> list[FragmentoRecuperado]:
        """Ejecuta `coleccion.query` y mapea el resultado a FragmentoRecuperado."""
        # ChromaDB acepta `where={"campo": "valor"}` para filtrar por metadata.
        # `where_extra` (ej. hash_archivo $in) se combina con la categoria
        # mediante `$and`. El filtrado ocurre en el servidor.
        clauses: list[dict[str, Any]] = []
        if filtro_categoria is not None:
            clauses.append({"categoria": filtro_categoria})
        if where_extra:
            clauses.append(where_extra)
        kwargs: dict[str, Any] = {"query_texts": [pregunta], "n_results": top_k}
        if len(clauses) == 1:
            kwargs["where"] = clauses[0]
        elif len(clauses) > 1:
            kwargs["where"] = {"$and": clauses}
        try:
            respuesta = coleccion.query(**kwargs)
        except Exception as exc:  # noqa: BLE001 - chromadb lanza varias
            raise ErrorMotorVectorial(
                f"Error consultando la coleccion {coleccion_origen}: {exc}"
            ) from exc

        documentos = (respuesta.get("documents") or [[]])[0]
        distancias = (respuesta.get("distances") or [[]])[0]
        metadatas = (respuesta.get("metadatas") or [[]])[0]
        ids = (respuesta.get("ids") or [[]])[0]

        resultados: list[FragmentoRecuperado] = []
        for i, texto in enumerate(documentos):
            distancia = float(distancias[i]) if i < len(distancias) else 1.0
            metadata = dict(metadatas[i]) if i < len(metadatas) else {}
            id_doc = str(ids[i]) if i < len(ids) else ""
            # Distancia coseno en [0, 2] -> relevancia en [0, 1].
            puntuacion = max(0.0, min(1.0, 1.0 - distancia))
            resultados.append(
                FragmentoRecuperado(
                    id=id_doc,
                    texto=texto,
                    metadata=metadata,
                    puntuacion=puntuacion,
                    coleccion_origen=coleccion_origen,
                )
            )
        return resultados


__all__ = [
    "CATEGORIA_TODAS",
    "CATEGORIAS_VALIDAS",
    "COLECCION_GLOBAL",
    "MODELO_EMBEDDINGS_POR_DEFECTO",
    "ErrorMotorVectorial",
    "FragmentoRecuperado",
    "MotorVectorial",
    "nombre_coleccion_para_tenant",
]
