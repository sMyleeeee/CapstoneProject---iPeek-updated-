"""
services/vectorstore.py
------------------------
ChromaDB access wrapped as a class, per the project's OOP requirement.
Embeddings come from FastEmbed (BAAI/bge-m3 model, ONNX runtime backend).

RETRIEVAL METRIC — CHANGED (per thesis spec): now uses ChromaDB's DEFAULT
L2 (squared Euclidean) distance metric for first-stage retrieval, instead
of the "cosine" hnsw:space override used previously. L2 is not trained —
it is a pure vector-space distance calculation done at query time: a
smaller distance means the query and document vectors sit closer together
in embedding space, i.e. greater candidate relevance. This module performs
first-stage candidate retrieval only; final relevance ordering is the
Cross-Encoder reranker's job (see services/reranker.py) — vectorization
itself does not perform retrieval, ChromaDB's L2 index does.

Because L2 distance is unbounded (unlike cosine similarity, which is
naturally 0-1), this module no longer applies a similarity-score
threshold. It simply returns the top-K nearest candidates by distance and
lets the Cross-Encoder decide what is actually relevant afterward.

Loaded once at import time (module-level singleton).

All public methods are async — they wrap the underlying synchronous
chromadb/fastembed calls in a thread via asyncio.to_thread(), so a slow
embedding/query never blocks FastAPI's event loop.
"""
import asyncio
import logging

from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_chroma import Chroma

from config import CHROMA_DIR, CHROMA_COLLECTION, EMBEDDING_MODEL

logger = logging.getLogger(__name__)


class VectorStoreService:
    def __init__(self):
        logger.info(f"Loading FastEmbed embedding model: {EMBEDDING_MODEL}")
        self._embeddings = FastEmbedEmbeddings(model_name=EMBEDDING_MODEL)
        # No collection_metadata override here anymore. Omitting
        # "hnsw:space" lets ChromaDB fall back to its default index
        # metric, which is L2 — this is what the spec calls for, and it
        # requires no training: it's computed directly from the raw
        # embedding vectors at index-build and query time.
        self._store = Chroma(
            collection_name=CHROMA_COLLECTION,
            embedding_function=self._embeddings,
            persist_directory=CHROMA_DIR,
        )
        logger.info(f"ChromaDB ready — collection: {CHROMA_COLLECTION} (L2 distance)")

    async def add_documents(self, documents: list) -> None:
        await asyncio.to_thread(self._store.add_documents, documents)

    async def delete_by_source(self, source: str) -> int:
        def _delete():
            results = self._store.get(where={"source": source})
            ids = results.get("ids", [])
            if ids:
                self._store.delete(ids=ids)
            return len(ids)
        count = await asyncio.to_thread(_delete)
        logger.info(f"Deleted {count} chunks for source '{source}'")
        return count

    async def get_chunk_count(self) -> int:
        def _count():
            try:
                return self._store._collection.count()
            except Exception as e:
                logger.error(f"Chunk count failed: {e}")
                return 0
        return await asyncio.to_thread(_count)

    async def similarity_search(self, query: str, k: int, approved_sources: set) -> list:
        """
        First-stage candidate retrieval: query -> FastEmbed -> ChromaDB L2.

        Returns the top-K nearest documents by L2 distance among approved
        sources, WITHOUT any similarity-score filtering (see module note
        above). Downstream code should treat this as "candidates" and
        rely on the Cross-Encoder reranker to decide what's relevant.
        """
        def _search():
            if not approved_sources:
                return []
            # similarity_search_with_score() returns raw L2 distance
            # (lower = closer) when the collection's index metric is L2.
            # This replaces the old similarity_search_with_relevance_
            # scores(), which normalized cosine similarity into a 0-1
            # score and only makes sense under a cosine index.
            docs_and_distances = self._store.similarity_search_with_score(
                query, k=k, filter={"source": {"$in": list(approved_sources)}}
            )
            docs_and_distances.sort(key=lambda pair: pair[1])  # ascending distance = most relevant first
            return [doc for doc, _distance in docs_and_distances]
        return await asyncio.to_thread(_search)


# Module-level singleton — created once at import, reused across requests
vectorstore_service = VectorStoreService()