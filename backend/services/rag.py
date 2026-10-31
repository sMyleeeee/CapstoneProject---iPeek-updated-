"""
services/rag.py
-----------------
Retrieval-Augmented Generation, wrapped as RAGService.

Pipeline: query -> ChromaDB cosine similarity (top 20, approved-only)
-> reranker (top 5) -> Groq LLM (async .ainvoke, never blocks event loop)

CACHING: unlike the original Flask system's in-memory dict, results are
cached in the AIAnalysis table, keyed by research_id — survives server
restarts. Cache is cleared by clear_cache_for() whenever the underlying
paper's content changes (delete, resubmission).
"""
import json
import logging

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_groq import ChatGroq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import GROQ_API_KEY, LLM_MODEL, RETRIEVAL_TOP_K, SCORE_THRESHOLD
from models import AIAnalysis, Research, StatusEnum
from services.vectorstore import vectorstore_service
from services.reranker import reranker_service

logger = logging.getLogger(__name__)

CITATION_RULE = """
CITATION RULE: You may synthesize, paraphrase, and connect ideas across the
context in your own words — you are not limited to copying sentences. However,
every factual claim must be traceable to a specific page shown in the context
above. Cite the page inline immediately after the claim, formatted as (p. X).
If one idea draws from multiple pages, cite all of them together,
e.g. (p. 12, p. 24). Do NOT invent page numbers that are not shown in the
context. Do NOT cite a page for your own connective or transitional sentences.
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
- Why it is similar (2-3 sentences, with page citations for specific claims)
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

IMRAD_PROMPT = PromptTemplate.from_template("""
You are a research advisor at ISAT-U writing an IMRaD-style reading guide.
STRICT RULE: Use ONLY the context below. Do NOT use outside knowledge.
If nothing is relevant, say: "Not enough repository data to generate an IMRaD summary."
""" + CITATION_RULE + """
Context:
{context}

Proposal: {question}

Structure your answer as EXACTLY four sections, each starting with its own
line in this exact format (bold header followed by a colon):

**Introduction:**
**Methods:**
**Results:**
**Discussion:**

Write 2-4 sentences per section covering, respectively: the background and
purpose of the proposal; how the study was carried out; the key findings or
expected results; and the interpretation, limitations, and implications.
Do not add any text before, between, or after the four section headers.
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

    # Fields worth naming the paper for when retrieval comes back empty.
    _PAPER_SCOPED_ANALYSES = {"summary", "imrad", "research_gaps"}

    async def _source_stem_for(self, research_id: int, db: AsyncSession) -> str:
        result = await db.execute(
            select(Research.source_stem).where(Research.id == research_id)
        )
        return result.scalar_one_or_none() or f"id={research_id}"

    async def _save_if_usable(
        self, research_id: int, field: str, data: dict, db: AsyncSession,
        save_sources: bool = False,
    ) -> dict:
        """Return the analysis to the caller, but persist it to ai_analysis
        ONLY when the run actually used retrieved documents AND produced
        non-empty text. A no-context or blank answer is deliberately left
        uncached so the next view retries instead of freezing a bad answer."""
        result_text = data.get("result") or ""
        if not data.get("had_docs"):
            if field in self._PAPER_SCOPED_ANALYSES:
                stem = await self._source_stem_for(research_id, db)
                logger.warning(
                    f"No retrieved context for '{field}' (paper '{stem}', "
                    f"research_id={research_id}) — result returned but not cached"
                )
        elif result_text.strip():
            await self._save_cache(research_id, field, data, db,
                                   save_sources=save_sources)
        else:
            logger.info(
                f"Blank '{field}' result for research_id={research_id} — not cached"
            )
        return {"result": data["result"], "sources": data["sources"]}

    # ── Retrieval ────────────────────────────────────────────────────

    async def _get_approved_sources(self, db: AsyncSession) -> set:
        result = await db.execute(select(Research.source_stem).where(Research.status == StatusEnum.approved))
        return {row[0] for row in result.all()}

    async def _retrieve_and_rerank(
        self, query: str, db: AsyncSession, exclude_source: str | None = None
    ) -> list:
        approved = await self._get_approved_sources(db)
        if exclude_source:
            approved.discard(exclude_source)
        if not approved:
            return []
        docs = await vectorstore_service.similarity_search(
            query, RETRIEVAL_TOP_K, approved, SCORE_THRESHOLD,
            exclude_source=exclude_source,
        )
        if not docs:
            return []
        return await reranker_service.rerank(query, docs)

    def _format_context(self, docs: list) -> str:
        if not docs:
            return "No relevant documents found in the repository."
        parts, seen = [], set()
        for doc in docs:
            title = doc.metadata.get("title", "Untitled")
            page = doc.metadata.get("page", "?")
            key = (title, page)
            if key in seen:
                continue
            seen.add(key)
            parts.append(
                f"[{title} | {doc.metadata.get('authors','?')} | "
                f"{doc.metadata.get('year','?')} | {doc.metadata.get('college','?')} | "
                f"Page {page}]\n{doc.page_content}"
            )
        return "\n\n---\n\n".join(parts)

    def _get_sources(self, docs: list, exclude_source: str | None = None) -> list:
        """One entry per paper. exclude_source keeps the analysed paper out
        of its own similarity source list."""
        seen = {}
        for doc in docs:
            source = doc.metadata.get("source")
            if exclude_source and source == exclude_source:
                continue
            title = doc.metadata.get("title", "Untitled")
            page = doc.metadata.get("page", None)
            key = source or title
            if key not in seen:
                seen[key] = {
                    "title": title, "authors": doc.metadata.get("authors", "Unknown"),
                    "year": doc.metadata.get("year", "Unknown"), "college": doc.metadata.get("college", "Unknown"),
                    "pages": [],
                }
            if page is not None and page not in seen[key]["pages"]:
                seen[key]["pages"].append(page)
        sources = list(seen.values())
        for s in sources:
            s["pages"].sort()
        return sources

    async def _run(
        self, prompt: PromptTemplate, query: str, db: AsyncSession,
        exclude_source: str | None = None,
    ) -> dict:
        docs = await self._retrieve_and_rerank(query, db, exclude_source=exclude_source)
        context = self._format_context(docs)
        chain = prompt | self._llm | self._parser
        result = await chain.ainvoke({"context": context, "question": query})   # async — never blocks the event loop
        return {"result": result, "sources": self._get_sources(docs, exclude_source=exclude_source),
                "had_docs": bool(docs)}

    # ── Public methods ──────────────────────────────────────────────

    async def get_similar_studies(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "similar_studies", db)
        if cached:
            return cached
        # Similarity is the ONLY analysis that must not see the paper's own
        # chunks — summary/IMRaD/gaps/refine keep using the paper's own text.
        stem = await self._source_stem_for(research_id, db)
        data = await self._run(SIMILAR_PROMPT, proposal, db, exclude_source=stem)
        return await self._save_if_usable(
            research_id, "similar_studies", data, db, save_sources=True
        )

    async def get_summary(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "summary", db)
        if cached:
            return cached
        data = await self._run(SUMMARY_PROMPT, proposal, db)
        return await self._save_if_usable(research_id, "summary", data, db)

    async def get_research_gaps(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "research_gaps", db)
        if cached:
            return cached
        data = await self._run(GAPS_PROMPT, proposal, db)
        return await self._save_if_usable(research_id, "research_gaps", data, db)

    async def get_imrad(self, proposal: str, research_id: int, db: AsyncSession) -> dict:
        cached = await self._get_cached(research_id, "imrad", db)
        if cached:
            return cached
        data = await self._run(IMRAD_PROMPT, proposal, db)
        return await self._save_if_usable(research_id, "imrad", data, db)

    async def refine(self, proposal: str, target: str, research_id: int, db: AsyncSession) -> dict:
        """Re-run a specific analysis with a user-refined query. Bypasses cache."""
        prompt_map = {"similarity": SIMILAR_PROMPT, "summary": SUMMARY_PROMPT, "gap": GAPS_PROMPT, "imrad": IMRAD_PROMPT}
        prompt = prompt_map.get(target)
        if not prompt:
            raise ValueError(f"Invalid refine target: {target}")
        return await self._run(prompt, proposal, db)


rag_service = RAGService()