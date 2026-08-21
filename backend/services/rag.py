"""
services/rag.py
-----------------
Retrieval-Augmented Generation, wrapped as RAGService.

Pipeline: query -> ChromaDB L2 distance (top-K, approved-only)
-> Cross-Encoder reranker (top-N, threshold-filtered) -> Groq LLM
(async .ainvoke, never blocks the event loop)

RETRIEVAL SCOPE — CHANGED (per thesis spec): the index now holds only
paper ABSTRACTS, not full-document page chunks. Two consequences:
  1. Retrieval metric is ChromaDB's default L2 distance, not cosine
     (see services/vectorstore.py).
  2. Citations are now per-STUDY ("[Title]"), not per-PAGE ("(p. X)") —
     there's no page granularity left to cite, since full-document text
     is no longer embedded. See CITATION_RULE below.

CACHING: results are cached in the AIAnalysis table, keyed by
research_id — survives server restarts. Cache is cleared by
clear_cache_for() whenever the underlying paper's content changes
(delete, resubmission).

chat() is NOT cached — every conversation turn is unique by nature.
"""
import json
import logging

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_groq import ChatGroq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import GROQ_API_KEY, LLM_MODEL, RETRIEVAL_TOP_K
from models import AIAnalysis, Research, StatusEnum
from services.vectorstore import vectorstore_service
from services.reranker import reranker_service

logger = logging.getLogger(__name__)

CITATION_RULE = """
CITATION RULE: You may synthesize, paraphrase, and connect ideas across the
context in your own words — you are not limited to copying sentences. However,
every factual claim must be traceable to a specific study shown in the context
above. Cite the study inline immediately after the claim using its title in
square brackets, e.g. [Optimizing Rice Yield Using IoT Sensors]. If one idea
draws from multiple studies, cite all of them together, e.g. [Study A; Study
B]. Do NOT cite a study that is not shown in the context above. Do NOT cite a
study for your own connective or transitional sentences. Remember that you are
working from ABSTRACTS ONLY — do not claim knowledge of a study's exact
dataset, detailed methodology, hyperparameters, implementation details, or
complete experimental results unless the abstract itself states them. If the
proposal's question needs that level of detail, say the available abstracts
don't contain enough information rather than guessing.
"""

SIMILAR_PROMPT = PromptTemplate.from_template("""
You are a research similarity analyst for ISAT-U.
STRICT RULE: Use ONLY the context below. Do NOT reference any study outside this context.
If nothing is relevant, say: "No similar studies found in the ISAT-U repository."
""" + CITATION_RULE + """
Context:
{context}

Proposal: {question}

List the top 3 most similar studies. For each:
- Title, Authors, Year, College
- Why it is similar (2-3 sentences, based on the abstract)
- Similarity: HIGH / MODERATE / LOW
""")

SUMMARY_PROMPT = PromptTemplate.from_template("""
You are a research advisor at ISAT-U.
STRICT RULE: Use ONLY the context below. Do NOT use outside knowledge.
If nothing is relevant, say: "Not enough repository data to generate a summary."
""" + CITATION_RULE + """
Context:
{context}

Proposal: {question}

Write a 3-4 sentence summary covering:
1. How the proposal relates to existing ISAT-U research
2. What is already well-covered in the repository
3. What makes this proposal potentially unique
""")

GAPS_PROMPT = PromptTemplate.from_template("""
You are a research gap analyst for ISAT-U.
STRICT RULE: Identify gaps based ONLY on the context below.
If nothing is relevant, say: "Not enough repository data to identify gaps."
""" + CITATION_RULE + """
Context:
{context}

Proposal: {question}

Identify 3-5 research gaps. For each:
- Gap: One clear sentence
- Recommendation: How the proposal addresses it
- Urgency: HIGH / MEDIUM / LOW
""")

CHAT_PROMPT = PromptTemplate.from_template("""
You are a research assistant for ISAT-U students and faculty.
STRICT RULE: Answer ONLY from the context below.
If the answer is not there, say:
"That information is not in the ISAT-U repository. I can only answer from uploaded documents."
""" + CITATION_RULE + """
Context:
{context}

Conversation so far:
{history}

Current question: {question}

Give a helpful, concise, academically appropriate answer.
Cite studies from context when relevant.
""")


class RAGService:
    def __init__(self):
        self._llm = ChatGroq(model=LLM_MODEL, api_key=GROQ_API_KEY, temperature=0.3)
        self._parser = StrOutputParser()

    # ── Cache (DB-backed via AIAnalysis) ────────────────────────────

    async def _get_cached(self, research_id: int, field: str, db: AsyncSession):
        result = await db.execute(select(AIAnalysis).where(AIAnalysis.research_id == research_id))
        analysis = result.scalar_one_or_none()
        if analysis and getattr(analysis, field):
            sources = json.loads(analysis.sources) if (field == "similar_studies" and analysis.sources) else []
            logger.info(f"Cache hit: {field} for research_id={research_id}")
            return {"result": getattr(analysis, field), "sources": sources}
        return None

    async def _save_cache(self, research_id: int, field: str, data: dict, db: AsyncSession, save_sources: bool = False):
        result = await db.execute(select(AIAnalysis).where(AIAnalysis.research_id == research_id))
        analysis = result.scalar_one_or_none()
        if not analysis:
            analysis = AIAnalysis(research_id=research_id)
            db.add(analysis)
        setattr(analysis, field, data["result"])
        if save_sources:
            analysis.sources = json.dumps(data["sources"])
        await db.commit()
        logger.info(f"Cached {field} for research_id={research_id}")

    async def clear_cache_for(self, research_id: int, db: AsyncSession):
        """Called on delete or resubmission — the paper's content changed,
        so any cached analysis describing the old content is now stale."""
        result = await db.execute(select(AIAnalysis).where(AIAnalysis.research_id == research_id))
        analysis = result.scalar_one_or_none()
        if analysis:
            await db.delete(analysis)
            await db.commit()
            logger.info(f"Cleared analysis cache for research_id={research_id}")

    # ── Retrieval ────────────────────────────────────────────────────

    async def _get_approved_sources(self, db: AsyncSession) -> set:
        result = await db.execute(select(Research.source_stem).where(Research.status == StatusEnum.approved))
        return {row[0] for row in result.all()}

    async def _retrieve_and_rerank(self, query: str, db: AsyncSession) -> list:
        """
        L2 Top-K Candidates -> Cross-Encoder -> Reranked Top-N.
        Relevance filtering happens inside reranker_service now — L2
        distance alone isn't a meaningful cutoff (see vectorstore.py).
        """
        approved = await self._get_approved_sources(db)
        if not approved:
            return []
        docs = await vectorstore_service.similarity_search(query, RETRIEVAL_TOP_K, approved)
        if not docs:
            return []
        return await reranker_service.rerank(query, docs)

    def _format_context(self, docs: list) -> str:
        """
        Builds the LLM context from retrieved abstract chunks. Dedupes by
        paper (not by chunk) and always shows the FULL abstract text from
        metadata — even if that paper matched via more than one chunk —
        so the LLM never sees a truncated abstract.
        """
        if not docs:
            return "No relevant research found in the repository."
        parts, seen = [], set()
        for doc in docs:
            title = doc.metadata.get("title", "Untitled")
            paper_id = doc.metadata.get("paper_id")
            key = paper_id if paper_id is not None else title
            if key in seen:
                continue
            seen.add(key)
            parts.append(
                f"[{title} | {doc.metadata.get('authors','?')} | "
                f"{doc.metadata.get('year','?')} | {doc.metadata.get('college','?')}]\n"
                f"{doc.metadata.get('abstract', doc.page_content)}"
            )
        return "\n\n---\n\n".join(parts)

    def _get_sources(self, docs: list) -> list:
        seen = {}
        for doc in docs:
            title = doc.metadata.get("title", "Untitled")
            paper_id = doc.metadata.get("paper_id")
            key = paper_id if paper_id is not None else title
            if key not in seen:
                seen[key] = {
                    "paper_id": paper_id,
                    "title": title,
                    "authors": doc.metadata.get("authors", "Unknown"),
                    "year": doc.metadata.get("year", "Unknown"),
                    "college": doc.metadata.get("college", "Unknown"),
                }
        return list(seen.values())

    async def _run(self, prompt: PromptTemplate, query: str, db: AsyncSession, history: str = "") -> dict:
        docs = await self._retrieve_and_rerank(query, db)
        context = self._format_context(docs)
        invoke_input = {"context": context, "question": query}
        if history:
            invoke_input["history"] = history
        chain = prompt | self._llm | self._parser
        result = await chain.ainvoke(invoke_input)   # async — never blocks the event loop
        return {"result": result, "sources": self._get_sources(docs)}

    # ── Public methods ──────────────────────────────────────────────

    async def get_similar_studies(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "similar_studies", db)
        if cached:
            return cached
        data = await self._run(SIMILAR_PROMPT, proposal, db)
        await self._save_cache(research_id, "similar_studies", data, db, save_sources=True)
        return data

    async def get_summary(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "summary", db)
        if cached:
            return cached
        data = await self._run(SUMMARY_PROMPT, proposal, db)
        await self._save_cache(research_id, "summary", data, db)
        return data

    async def get_research_gaps(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "research_gaps", db)
        if cached:
            return cached
        data = await self._run(GAPS_PROMPT, proposal, db)
        await self._save_cache(research_id, "research_gaps", data, db)
        return data

    async def chat(self, question: str, history: list, db: AsyncSession) -> dict:
        history_text = ""
        if history:
            lines = []
            for turn in history:
                role = "Student" if turn.get("role") == "user" else "Assistant"
                content = turn.get("content", "").strip()
                if content:
                    lines.append(f"{role}: {content}")
            history_text = "\n".join(lines)
        return await self._run(CHAT_PROMPT, question, db, history=history_text or "No prior conversation.")


rag_service = RAGService()