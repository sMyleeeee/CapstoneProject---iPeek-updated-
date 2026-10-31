"""
services/vectorstore.py
------------------------
ChromaDB access wrapped as a class, per the project's OOP requirement.
Embeddings now come from FastEmbed instead of HuggingFace — same
BAAI/bge-m3 model, ONNX runtime backend.

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


def build_source_filter(approved_sources, exclude_source: str | None = None) -> dict:
    """Chroma metadata filter for retrieval.

    Without exclude_source this is the historical
    {"source": {"$in": [...]}} filter. With it, an extra $ne clause keeps
    the current paper's own chunks out of its similarity results — every
    other analysis (summary, IMRaD, gaps, refine) passes None and still
    sees the paper's own text.
    """
    sources_filter = {"source": {"$in": list(approved_sources)}}
    if not exclude_source:
        return sources_filter
    return {"$and": [sources_filter, {"source": {"$ne": exclude_source}}]}


class VectorStoreService:
    def __init__(self):
        logger.info(f"Loading FastEmbed embedding model: {EMBEDDING_MODEL}")
        self._embeddings = FastEmbedEmbeddings(model_name=EMBEDDING_MODEL)
        self._store = Chroma(
            collection_name=CHROMA_COLLECTION,
            embedding_function=self._embeddings,
            persist_directory=CHROMA_DIR,
            collection_metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"ChromaDB ready — collection: {CHROMA_COLLECTION}")

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

    async def count_chunks(self) -> int:
        """Strict counterpart of get_chunk_count(): raises when the vector
        store is unreachable, so callers can report "unknown" instead of a
        false 0. get_chunk_count keeps its 0 fallback for reindex_chroma's
        end-of-run summary."""
        def _count():
            return self._store._collection.count()
        return await asyncio.to_thread(_count)

    async def similarity_search(
        self, query: str, k: int, approved_sources: set, score_threshold: float,
        exclude_source: str | None = None,
    ) -> list:
        def _search():
            if not approved_sources:
                return []
            docs_and_scores = self._store.similarity_search_with_relevance_scores(
                query, k=k,
                filter=build_source_filter(approved_sources, exclude_source),
            )
            return [doc for doc, score in docs_and_scores if score >= score_threshold]
        return await asyncio.to_thread(_search)


# Module-level singleton — created once at import, reused across requests
vectorstore_service = VectorStoreService()