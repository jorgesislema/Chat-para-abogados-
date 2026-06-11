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
MODELO_EMBEDDINGS_POR_DEFECTO: str = "sentence-transformers/all-MiniLM-L6-v2"

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
        texto: contenido textual del chunk.
        metadata: diccionario con metadatos arbitrarios del chunk
            (archivo origen, tier, indice, etc.).
        puntuacion: relevancia normalizada en [0, 1]. Mayor = mas
            relevante. Se calcula como `max(0, 1 - distancia_coseno)`.
        coleccion_origen: nombre de la coleccion de la que procede
            (util para depurar cuando se mezcla busqueda tenant + global).
    """

    texto: str
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
            ruta = Path(ruta_persistencia) if ruta_persistencia is not None else _RUTA_PERSISTENCIA_POR_DEFECTO
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

    # --------------------------------------------------------
    # Busqueda.
    # --------------------------------------------------------

    def buscar_contexto(
        self,
        pregunta: str,
        nombre_coleccion: str,
        incluir_global: bool = True,
        top_k: int = 5,
    ) -> list[FragmentoRecuperado]:
        """
        Vectoriza `pregunta` y devuelve los Top-K chunks mas relevantes.

        Por defecto consulta AMBAS colecciones:
          - La del tenant (`nombre_coleccion`, ej. `casos_user_42`).
          - La global de leyes publicas (si `incluir_global=True`).

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

        resultados: list[FragmentoRecuperado] = []

        # 1) Busqueda en la coleccion del tenant.
        if self.coleccion_existe(nombre_coleccion):
            tenant = self._cliente.get_collection(
                name=nombre_coleccion, embedding_function=self._ef
            )
            resultados.extend(
                self._consultar(tenant, pregunta, top_k, nombre_coleccion)
            )

        # 2) Busqueda opcional en la coleccion global.
        if incluir_global and nombre_coleccion != COLECCION_GLOBAL:
            if self.coleccion_existe(COLECCION_GLOBAL):
                global_col = self._cliente.get_collection(
                    name=COLECCION_GLOBAL, embedding_function=self._ef
                )
                resultados.extend(
                    self._consultar(
                        global_col, pregunta, top_k, COLECCION_GLOBAL
                    )
                )

        # 3) Ranking y truncado.
        resultados.sort(key=lambda f: f.puntuacion, reverse=True)
        return resultados[:top_k]

    # --------------------------------------------------------
    # Utilidades internas.
    # --------------------------------------------------------

    def _consultar(
        self,
        coleccion: Any,
        pregunta: str,
        top_k: int,
        coleccion_origen: str,
    ) -> list[FragmentoRecuperado]:
        """Ejecuta `coleccion.query` y mapea el resultado a FragmentoRecuperado."""
        try:
            respuesta = coleccion.query(query_texts=[pregunta], n_results=top_k)
        except Exception as exc:  # noqa: BLE001 - chromadb lanza varias
            raise ErrorMotorVectorial(
                f"Error consultando la coleccion {coleccion_origen}: {exc}"
            ) from exc

        documentos = (respuesta.get("documents") or [[]])[0]
        distancias = (respuesta.get("distances") or [[]])[0]
        metadatas = (respuesta.get("metadatas") or [[]])[0]

        resultados: list[FragmentoRecuperado] = []
        for i, texto in enumerate(documentos):
            distancia = float(distancias[i]) if i < len(distancias) else 1.0
            metadata = dict(metadatas[i]) if i < len(metadatas) else {}
            # Distancia coseno en [0, 2] -> relevancia en [0, 1].
            puntuacion = max(0.0, min(1.0, 1.0 - distancia))
            resultados.append(
                FragmentoRecuperado(
                    texto=texto,
                    metadata=metadata,
                    puntuacion=puntuacion,
                    coleccion_origen=coleccion_origen,
                )
            )
        return resultados


__all__ = [
    "COLECCION_GLOBAL",
    "MODELO_EMBEDDINGS_POR_DEFECTO",
    "ErrorMotorVectorial",
    "FragmentoRecuperado",
    "MotorVectorial",
    "nombre_coleccion_para_tenant",
]
