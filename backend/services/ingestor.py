"""
services/ingestor.py
---------------------
PDF ingestion pipeline, wrapped as a class per the OOP requirement.
Same two-phase preview/confirm logic as before — duplicate detection,
resubmission with archived versions, discrepancy checking against the
student's own form entries.

INGESTION SCOPE — CHANGED (per thesis spec): only the paper's ABSTRACT is
chunked and embedded into ChromaDB now. The full PDF is still opened with
PyMuPDF and its text is still used for (a) AI metadata extraction — title,
authors, year, college, abstract, keywords — and (b) the discrepancy
checker that compares the PDF's own metadata against what the student
typed. None of that full-document text is embedded or retrieved anymore;
it's read once at ingest time and then discarded from the RAG index. The
PDF itself remains on disk and is still served to the PDF viewer — that's
just no longer connected to retrieval.

Adapted for FastAPI: works with UploadFile instead of Flask's file
object, writes to Research/ResearchVersion via SQLAlchemy async sessions,
and wraps every blocking call (PyMuPDF, the Groq metadata call, ChromaDB
writes) in asyncio.to_thread so nothing blocks the event loop.
"""
import json
import logging
import re
import shutil
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone

import aiofiles
import fitz  # PyMuPDF
from fastapi import UploadFile
from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    GROQ_API_KEY, LLM_MODEL, ABSTRACT_CHUNK_SIZE, ABSTRACT_CHUNK_OVERLAP,
    ALLOWED_EXTENSIONS, STAGING_DIR, PENDING_DIR, VERSIONS_DIR,
    DISCREPANCY_THRESHOLD, PREVIEW_MAX_AGE_SECONDS,
)
from models import Research, ResearchVersion, StatusEnum
from services.vectorstore import vectorstore_service

logger = logging.getLogger(__name__)


class Ingestor:
    def __init__(self):
        self._llm = ChatGroq(model=LLM_MODEL, api_key=GROQ_API_KEY, temperature=0.1)
        # Abstracts are short — avoid unnecessarily small chunks (per
        # spec). A typical 150-300 word abstract is ~900-1800 characters,
        # so with a chunk size in that range most abstracts end up as a
        # single chunk; longer ones split with enough overlap to keep
        # context intact across the boundary. Tune ABSTRACT_CHUNK_SIZE/
        # ABSTRACT_CHUNK_OVERLAP in config.py against real abstract
        # lengths in your repository.
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=ABSTRACT_CHUNK_SIZE, chunk_overlap=ABSTRACT_CHUNK_OVERLAP,
        )

    # ── Sync helpers (called via asyncio.to_thread) ────────────────────

    def _allowed(self, filename: str) -> bool:
        return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

    def _normalize_year(self, raw: str):
        match = re.search(r'\b(20\d{2})\b', str(raw))
        return int(match.group(1)) if match else None

    def _extract_pages(self, path: str) -> list[dict]:
        """
        Extracts per-page text from the full PDF. Used ONLY for metadata
        extraction and discrepancy checking below — no longer used to
        build embedded chunks (see module docstring).
        """
        try:
            doc = fitz.open(path)
            pages = []
            for i, page in enumerate(doc, start=1):
                text = page.get_text()
                if text.strip():
                    pages.append({"page": i, "text": text})
            logger.info(f"Extracted {len(pages)} pages from {Path(path).name}")
            return pages
        except Exception as e:
            logger.error(f"Page extraction failed: {e}")
            return []

    def _extract_metadata(self, pages: list[dict], fallback_name: str) -> dict:
        combined_head = "\n".join(p["text"] for p in pages)[:4000]
        prompt = f"""
Extract metadata from this ISAT-U academic thesis document.
Return ONLY valid JSON with these exact keys. Use "Unknown" if not found.

{{
  "title":    "Full research title",
  "authors":  "All authors comma-separated",
  "year":     "4-digit year e.g. 2024",
  "college":  "Full college name e.g. College of Industrial Technology",
  "abstract": "The COMPLETE abstract text, verbatim from the document — not a summary, not truncated",
  "keywords": "Keywords comma-separated"
}}

Document (first 4000 characters):
{combined_head}
"""
        try:
            resp = self._llm.invoke(prompt).content.strip()
            if "```" in resp:
                resp = resp.split("```")[1]
                if resp.startswith("json"):
                    resp = resp[4:]
            meta = json.loads(resp.strip())
        except Exception as e:
            logger.warning(f"Metadata extraction failed, using defaults: {e}")
            meta = {
                "title": fallback_name, "authors": "Unknown", "year": "Unknown",
                "college": "Unknown", "abstract": combined_head[:500], "keywords": "Unknown",
            }
        meta["year_normalized"] = self._normalize_year(meta.get("year", "Unknown"))
        return meta

    def _similarity(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    def _compare_metadata(self, form_data: dict, ai_meta: dict) -> list[dict]:
        warnings = []
        checks = [
            ("title", form_data.get("title", ""), ai_meta.get("title", ""), "Title"),
            ("department", form_data.get("department", ""), ai_meta.get("college", ""), "Department"),
            ("members", form_data.get("members", ""), ai_meta.get("authors", ""), "Authors"),
        ]
        for field_key, user_val, ai_val, label in checks:
            if not user_val:
                continue
            score = self._similarity(user_val, ai_val)
            if score < DISCREPANCY_THRESHOLD:
                warnings.append({"field": field_key, "label": label, "user_value": user_val, "ai_value": ai_val})
        return warnings

    def _cleanup_stale_previews(self):
        import time
        now = time.time()
        try:
            for f in STAGING_DIR.glob("*.json"):
                if now - f.stat().st_mtime > PREVIEW_MAX_AGE_SECONDS:
                    preview_id = f.stem
                    f.unlink(missing_ok=True)
                    (STAGING_DIR / f"{preview_id}.pdf").unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Preview cleanup failed (non-fatal): {e}")

    def _build_abstract_documents(self, meta: dict, stem: str, paper_id) -> list[Document]:
        """
        Chunks and tags ONLY the abstract for embedding — this is the
        entire ingestion-to-embedding surface now (per spec: PDF ->
        Abstract Extraction -> Text Chunking -> Metadata -> Embedding ->
        ChromaDB). Metadata carries the FULL abstract text on every chunk
        (not just that chunk's slice) so downstream RAG code can show the
        complete abstract in context even if it was split into >1 chunk.
        """
        abstract_text = (meta.get("abstract") or "").strip()
        if not abstract_text or abstract_text == "Unknown":
            abstract_text = "No abstract available for this paper."

        chunks = self._splitter.split_text(abstract_text) or [abstract_text]

        documents = []
        for idx, chunk in enumerate(chunks, start=1):
            documents.append(Document(
                page_content=chunk,
                metadata={
                    "paper_id": paper_id,
                    "title": meta.get("title", stem),
                    "authors": meta.get("authors", "Unknown"),
                    "year": str(meta.get("year_normalized") or "Unknown"),
                    "college": meta.get("college", "Unknown"),
                    "keywords": meta.get("keywords", "Unknown"),
                    "abstract": abstract_text,
                    "source": stem,
                    "chunk_id": idx,
                }
            ))
        return documents

    # ── PREVIEW ──────────────────────────────────────────────────────

    async def preview_upload(self, file: UploadFile, form_data: dict, db: AsyncSession) -> dict:
        import asyncio
        await asyncio.to_thread(self._cleanup_stale_previews)

        if not self._allowed(file.filename):
            raise ValueError("Only PDF files are accepted.")

        filename = file.filename.replace("/", "_").replace("\\", "_")
        stem = Path(filename).stem

        result = await db.execute(select(Research).where(Research.source_stem == stem))
        existing = result.scalar_one_or_none()
        is_resubmission = False

        if existing:
            if existing.status in (StatusEnum.pending, StatusEnum.approved):
                raise ValueError(
                    f"A paper named '{filename}' has already been submitted "
                    f"(status: {existing.status.value}). Please rename the file if this is different."
                )
            is_resubmission = True

        preview_id = str(uuid.uuid4())
        staged_path = STAGING_DIR / f"{preview_id}.pdf"

        content = await file.read()
        async with aiofiles.open(staged_path, "wb") as out:
            await out.write(content)

        try:
            test_doc = await asyncio.to_thread(fitz.open, str(staged_path))
            test_doc.close()
        except Exception:
            staged_path.unlink(missing_ok=True)
            raise ValueError("The uploaded file is not a valid PDF.")

        pages = await asyncio.to_thread(self._extract_pages, str(staged_path))
        if not pages:
            staged_path.unlink(missing_ok=True)
            raise ValueError("No text found in this PDF. It may be scanned/image-only.")

        ai_meta = await asyncio.to_thread(self._extract_metadata, pages, stem)
        warnings = self._compare_metadata(form_data, ai_meta)

        sidecar = {
            "preview_id": preview_id,
            "original_filename": filename,
            "source_stem": stem,
            "ai_meta": ai_meta,
            "form_data": form_data,
            "is_resubmission": is_resubmission,
            "existing_research_id": existing.id if existing else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (STAGING_DIR / f"{preview_id}.json").write_text(json.dumps(sidecar))

        return {
            "preview_id": preview_id, "warnings": warnings, "has_warnings": len(warnings) > 0,
            "resubmission": is_resubmission, "ai_metadata": ai_meta, "source": stem, "filename": filename,
        }

    # ── CONFIRM ──────────────────────────────────────────────────────

    async def confirm_upload(
        self, preview_id: str, final_values: dict, student_id: int, db: AsyncSession
    ) -> dict:
        import asyncio

        sidecar_path = STAGING_DIR / f"{preview_id}.json"
        staged_path = STAGING_DIR / f"{preview_id}.pdf"

        if not sidecar_path.exists() or not staged_path.exists():
            raise ValueError("This submission preview has expired or was already used.")

        sidecar = json.loads(sidecar_path.read_text())
        stem = sidecar["source_stem"]
        filename = sidecar["original_filename"]
        ai_meta = sidecar["ai_meta"]
        is_resubmission = sidecar["is_resubmission"]

        result = await db.execute(select(Research).where(Research.source_stem == stem))
        existing = result.scalar_one_or_none()

        if existing and existing.status in (StatusEnum.pending, StatusEnum.approved) and not is_resubmission:
            sidecar_path.unlink(missing_ok=True)
            staged_path.unlink(missing_ok=True)
            raise ValueError(f"A paper named '{filename}' was submitted by someone else in the meantime.")

        pending_path = PENDING_DIR / filename

        if is_resubmission and existing:
            if pending_path.exists():
                count_result = await db.execute(
                    select(ResearchVersion).where(ResearchVersion.research_id == existing.id)
                )
                version_num = len(count_result.scalars().all()) + 1
                archive_path = VERSIONS_DIR / f"{stem}_v{version_num}.pdf"
                shutil.copy(str(pending_path), str(archive_path))
                db.add(ResearchVersion(
                    research_id=existing.id, version_number=version_num, filepath=str(archive_path),
                ))
            await vectorstore_service.delete_by_source(stem)
            from services.rag import rag_service
            await rag_service.clear_cache_for(existing.id, db)

        shutil.move(str(staged_path), str(pending_path))

        # Full text re-extracted at confirm time in case the PDF changed
        # between preview and confirm — still used only for metadata
        # sanity (the "no text found" guard), not for embedding.
        pages = await asyncio.to_thread(self._extract_pages, str(pending_path))
        if not pages:
            raise ValueError("No text found in this PDF at confirm time.")

        meta = dict(ai_meta)
        if final_values.get("title"):
            meta["title"] = final_values["title"]
        if final_values.get("department"):
            meta["college"] = final_values["department"]
        if final_values.get("members"):
            meta["authors"] = final_values["members"]
        if final_values.get("abstract"):
            meta["abstract"] = final_values["abstract"]
        if final_values.get("year"):
            meta["year_normalized"] = self._normalize_year(final_values["year"])

        # Resolve paper_id BEFORE embedding, so every chunk's metadata
        # carries the real numeric Research.id rather than just the
        # filename stem. For a brand-new paper this means creating (and
        # flushing) the Research row first, then embedding, then doing
        # the rest of the field updates/commit below.
        if is_resubmission and existing:
            paper_id = existing.id
        else:
            new_research = Research(
                title=meta.get("title", stem), authors=meta.get("authors", "Unknown"),
                department=meta.get("college", "Unknown"), year=meta.get("year_normalized"),
                abstract=meta.get("abstract", ""), source_stem=stem, filepath=str(pending_path),
                student_id=student_id, status=StatusEnum.pending,
            )
            db.add(new_research)
            await db.flush()  # assigns new_research.id without committing yet
            paper_id = new_research.id
            existing = new_research  # so the field-update block below is uniform

        documents = self._build_abstract_documents(meta, stem, paper_id)
        await vectorstore_service.add_documents(documents)

        if is_resubmission:
            existing.title = meta.get("title", stem)
            existing.authors = meta.get("authors", "Unknown")
            existing.department = meta.get("college", "Unknown")
            existing.year = meta.get("year_normalized")
            existing.abstract = meta.get("abstract", "")
            existing.status = StatusEnum.pending
            existing.feedback_note = None
        # else: new_research already has correct fields from construction above

        await db.commit()
        sidecar_path.unlink(missing_ok=True)

        action_word = "resubmitted" if is_resubmission else "submitted"
        return {
            "success": True,
            "message": f"'{meta['title']}' {action_word} — abstract indexed as {len(documents)} chunk(s).",
            "metadata": meta, "chunks": len(documents), "source": stem, "resubmission": is_resubmission,
        }


# Module-level singleton
ingestor = Ingestor()