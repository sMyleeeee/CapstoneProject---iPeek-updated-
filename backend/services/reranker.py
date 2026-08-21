"""
services/reranker.py
---------------------
Cross-encoder re-ranker (BAAI/bge-reranker-v2-m3), wrapped as a class.
Pretrained, no fine-tuning performed as part of this thesis — per spec,
"the Cross-Encoder should preferably be a pretrained model unless
fine-tuning is explicitly required," and it isn't here.

Async wrapper around sentence-transformers' CrossEncoder — the model
itself is still synchronous/CPU-bound, so predict() runs in a thread via
asyncio.to_thread so it never blocks FastAPI's event loop.

RELEVANCE FILTERING — CHANGED: score-threshold filtering now lives here
instead of in vectorstore.py. L2 distance (first-stage retrieval) isn't a
meaningful relevance threshold on its own — the Cross-Encoder's
query-document relevance score is the actual "is this relevant" signal.
Documents scoring below RERANK_SCORE_THRESHOLD are dropped before being
returned, so a query with no genuinely relevant research in the
repository naturally ends up with an empty context — which is what
triggers each prompt's "not enough repository data" fallback in
services/rag.py, instead of the LLM being handed weak matches to
hallucinate around.

NOTE: RERANK_SCORE_THRESHOLD needs to be added to config.py. The
bge-reranker-v2-m3 model outputs raw (unbounded) logits, not
probabilities — 0.0 is a reasonable starting point (keeps only
above-average matches), but tune it against your evaluation set's
Recall@K / Precision@K rather than picking a value from theory alone.
"""
import asyncio
import logging

from sentence_transformers import CrossEncoder
from config import RERANKER_MODEL, RERANK_TOP_K, RERANK_SCORE_THRESHOLD

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(self):
        logger.info(f"Loading re-ranker: {RERANKER_MODEL}")
        self._model = CrossEncoder(RERANKER_MODEL, max_length=512)
        logger.info("Re-ranker ready.")

    async def rerank(self, query: str, documents: list) -> list:
        """
        L2 Top-K Candidates -> Cross-Encoder -> Reranked Top-N.

        Scores every (query, document) pair, drops anything below
        RERANK_SCORE_THRESHOLD, and returns at most RERANK_TOP_K
        documents ordered by descending relevance.
        """
        if not documents:
            return []

        def _score():
            pairs = [(query, doc.page_content) for doc in documents]
            scores = self._model.predict(pairs)
            ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
            relevant = [(doc, score) for doc, score in ranked if score >= RERANK_SCORE_THRESHOLD]
            top = [doc for doc, _score in relevant[:RERANK_TOP_K]]
            logger.info(
                f"Re-ranked {len(documents)} candidates -> "
                f"{len(relevant)} above threshold -> kept top {len(top)}"
            )
            return top

        return await asyncio.to_thread(_score)


reranker_service = RerankerService()