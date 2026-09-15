"""
services/reranker.py
---------------------
Cross-encoder re-ranker using FastEmbed's ONNX-based TextCrossEncoder.
Replaces the previous sentence-transformers + torch stack to cut memory
usage from ~568M params (BAAI/bge-reranker-v2-m3) down to ~22M params
(Xenova/ms-marco-MiniLM-L-6-v2, 80 MB ONNX).

Async wrapper — inference runs in a worker thread via asyncio.to_thread
so it never blocks FastAPI's event loop.
"""
import asyncio
import logging

from fastembed.rerank.cross_encoder import TextCrossEncoder
from config import RERANKER_MODEL, RERANK_TOP_K

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(self):
        logger.info(f"Loading re-ranker: {RERANKER_MODEL}")
        self._model = TextCrossEncoder(model_name=RERANKER_MODEL)
        logger.info("Re-ranker ready.")

    async def rerank(self, query: str, documents: list) -> list:
        if not documents:
            return []

        def _score():
            doc_texts = [doc.page_content for doc in documents]
            scores = list(self._model.rerank(query, doc_texts))
            ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
            top = [doc for doc, _ in ranked[:RERANK_TOP_K]]
            logger.info(f"Re-ranked {len(documents)} → kept top {len(top)}")
            return top

        return await asyncio.to_thread(_score)


reranker_service = RerankerService()
