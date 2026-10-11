"""
services/ingestor.py
---------------------
PDF ingestion pipeline, wrapped as a class per the OOP requirement.
Same two-phase preview/confirm logic as the original Flask system —
duplicate detection, resubmission with archived versions, discrepancy
checking against the student's own form entries.

Adapted for FastAPI: works with UploadFile instead of Flask's file
object, writes to Research/ResearchVersion via SQLAlchemy async
sessions instead of raw sqlite3, and wraps every blocking call
(PyMuPDF, the Groq metadata call, ChromaDB writes) in asyncio.to_thread
so nothing blocks the event loop.
"""
import json
import logging
import re
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
    GROQ_API_KEY, LLM_MODEL, CHUNK_SIZE, CHUNK_OVERLAP,
    ALLOWED_EXTENSIONS, STAGING_DIR,
    DISCREPANCY_THRESHOLD, PREVIEW_MAX_AGE_SECONDS,
)
from models import Research, ResearchVersion, StatusEnum
from services.vectorstore import vectorstore_service
from services.storage import storage_service

logger = logging.getLogger(__name__)


class Ingestor:
    def __init__(self):
        self._llm = ChatGroq(model=LLM_MODEL, api_key=GROQ_API_KEY, temperature=0.1)
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        )

    # ── Sync helpers (called via asyncio.to_thread) ────────────────────

    def _allowed(self, filename: str) -> bool:
        return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

    def _normalize_year(self, raw: str):
        match = re.search(r'\b(20\d{2})\b', str(raw))
        return int(match.group(1)) if match else None

    def _extract_pages(self, source) -> list[dict]:
        """source: local file path (str) or raw PDF bytes (staged/Storage)."""
        try:
            if isinstance(source, (bytes, bytearray)):
                doc = fitz.open(stream=source, filetype="pdf")
                label = "uploaded bytes"
            else:
                doc = fitz.open(source)
                label = Path(source).name
            pages = []
            for i, page in enumerate(doc, start=1):
                text = page.get_text()
                if text.strip():
                    pages.append({"page": i, "text": text})
            logger.info(f"Extracted {len(pages)} pages from {label}")
            return pages
        except Exception as e:
            logger.error(f"Page extraction failed: {e}")
            return []

    def _extract_metadata(self, pages: list[dict], fallback_name: str) -> dict:
        combined_head = "\n".join(p["text"] for p in pages)[:3000]
        prompt = f"""
Extract metadata from this ISAT-U academic thesis document.
Return ONLY valid JSON with these exact keys. Use "Unknown" if not found.

{{
  "title":    "Full research title",
  "authors":  "All authors comma-separated",
  "year":     "4-digit year e.g. 2024",
  "college":  "Full college name e.g. College of Industrial Technology",
  "abstract": "First 2 sentences of abstract",
  "keywords": "Keywords comma-separated"
}}

Document (first 3000 characters):
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
                "college": "Unknown", "abstract": combined_head[:200], "keywords": "Unknown",
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

    # ── Embedding helpers (shared with reindex_chroma.py) ───────────

    def build_documents(self, pages: list[dict], meta: dict, stem: str) -> list:
        """Chunk extracted pages into LangChain Documents with the exact
        metadata schema used for normal ingestion."""
        documents = []
        for p in pages:
            for chunk in self._splitter.split_text(p["text"]):
                documents.append(Document(
                    page_content=chunk,
                    metadata={
                        "title": meta.get("title", stem), "authors": meta.get("authors", "Unknown"),
                        "year": str(meta.get("year_normalized") or "Unknown"),
                        "college": meta.get("college", "Unknown"),
                        "keywords": meta.get("keywords", "Unknown"),
                        "abstract": meta.get("abstract", ""), "source": stem, "page": p["page"],
                    }
                ))
        return documents

    async def index_documents(self, stem: str, documents: list) -> bool:
        """Replace this paper's chunks in ChromaDB (delete-then-add keeps
        re-runs idempotent — no duplicate entries for the same source)."""
        try:
            await vectorstore_service.delete_by_source(stem)
            await vectorstore_service.add_documents(documents)
            return True
        except Exception:
            logger.exception(f"Vector indexing failed for '{stem}'")
            return False

    # ── CONFIRM ──────────────────────────────────────────────────────

    async def confirm_upload(
        self, preview_id: str, final_values: dict, public_pdf_consent: bool,
        student_id: int, db: AsyncSession
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

        # Committed PDFs live in the private Supabase Storage bucket:
        # pending/{stem}.pdf is the canonical copy (Research.filepath holds
        # this object key). Staged previews stay ephemeral on local disk.
        pending_key = storage_service.pending_key(stem)

        if is_resubmission and existing:
            count_result = await db.execute(
                select(ResearchVersion).where(ResearchVersion.research_id == existing.id)
            )
            version_num = len(count_result.scalars().all()) + 1
            # Archive the CURRENT pending copy before overwriting it.
            if await asyncio.to_thread(storage_service.exists, pending_key):
                version_key = storage_service.version_key(stem, version_num)
                await asyncio.to_thread(storage_service.copy, pending_key, version_key)
                db.add(ResearchVersion(
                    research_id=existing.id, version_number=version_num, filepath=version_key,
                ))
            from services.rag import rag_service
            await rag_service.clear_cache_for(existing.id, db)

        staged_bytes = staged_path.read_bytes()
        await asyncio.to_thread(storage_service.upload, pending_key, staged_bytes)
        staged_path.unlink(missing_ok=True)

        pages = await asyncio.to_thread(self._extract_pages, staged_bytes)
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

        documents = await asyncio.to_thread(self.build_documents, pages, meta, stem)

        indexed = await self.index_documents(stem, documents)

        if is_resubmission and existing:
            existing.title = meta.get("title", stem)
            existing.authors = meta.get("authors", "Unknown")
            existing.department = meta.get("college", "Unknown")
            existing.year = meta.get("year_normalized")
            existing.abstract = meta.get("abstract", "")
            existing.status = StatusEnum.pending
            existing.feedback_note = None
            existing.public_pdf_consent = bool(public_pdf_consent)
        else:
            db.add(Research(
                title=meta.get("title", stem), authors=meta.get("authors", "Unknown"),
                department=meta.get("college", "Unknown"), year=meta.get("year_normalized"),
                abstract=meta.get("abstract", ""), source_stem=stem, filepath=pending_key,
                student_id=student_id, status=StatusEnum.pending,
                public_pdf_consent=bool(public_pdf_consent),
            ))

        await db.commit()
        sidecar_path.unlink(missing_ok=True)

        action_word = "resubmitted" if is_resubmission else "submitted"
        indexed_chunks = len(documents) if indexed else 0
        return {
            "success": True,
            "message": f"'{meta['title']}' {action_word} — {indexed_chunks} chunks indexed across {len(pages)} pages.",
            "metadata": meta, "chunks": indexed_chunks,
            "source": stem, "resubmission": is_resubmission,
        }


# Module-level singleton
ingestor = Ingestor()