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
from services.colleges import resolve_college
from services.vectorstore import vectorstore_service
from services.storage import storage_service

logger = logging.getLogger(__name__)


# ── ABSTRACT / KEYWORDS section extraction ────────────────────────────
# The ABSTRACT usually sits on page 5+, i.e. past the first-3000-char
# window, so the model would answer "Unknown" for it. Locate the heading
# in the FULL text and append that section — through the end of the
# keywords line, so keywords are visible too (they were previously never
# sent: the slice stopped AT "KEYWORDS"). The first 3000 characters,
# where title/authors/year/college live, stay identical.

_ABSTRACT_HEAD_RE = re.compile(r"^\s*ABSTRACT\s*$", re.I | re.M)
_ABSTRACT_HEAD_ALT_RE = re.compile(r"^\s*ABSTRACT\b[^\n]*$", re.I | re.M)
# Keyword lines in this archive read "Keywords:", "KEYWORDS", "Keyconcepts:",
# "KEY WORDS" or "Index terms:" — match them all, at the start of a line.
_KEYWORDS_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:key[ \t]*words?|key[ \t]*concepts?|index[ \t]*terms?)"
    r"[ \t]*:?[ \t]*"
)
# Fallback terminators for documents that print no keyword line at all:
# the abstract there runs straight into the front matter, so stop at the
# first thing that is clearly NOT abstract prose.
_SECTION_STOP_RE = re.compile(
    r"\n\s*(KEYWORDS|ACKNOWLEDG|DEDICATION|TABLE\s+OF\s+CONTENTS"
    r"|LIST\s+OF\s+(?:TABLES|FIGURES|APPENDICES)|APPROVAL\s+SHEET"
    r"|CHAPTER\s*1)", re.I)
# ToC-like markers ONLY — used to detect that an "ABSTRACT" heading match is
# really a table-of-contents entry (front matter lands inside the first 3000
# characters, so the FIRST "Abstract" in a PDF is often the ToC line).
# KEYWORDS / Keyconcepts / Index Terms are deliberately absent here: a
# genuine short abstract followed by its keywords line must never be
# rejected as a ToC entry.
_TOC_STOP_RE = re.compile(
    r"\n\s*(TABLE\s+OF\s+CONTENTS|LIST\s+OF|ACKNOWLEDG|APPROVAL\s+SHEET"
    r"|DEDICATION|CHAPTER\s*1)", re.I)
# The other two ToC tells, both measured on the heading's own line: dot
# leaders ("Abstract ..... 5") and a trailing page number.
_DOT_LEADER_RE = re.compile(r"(\.\s*){3,}|\.{4,}|\u2026")
# Invisible characters some PDFs emit inside headings ("\u200bAbstract\u200b").
# \s does not match them, so they defeat the ^\s*ABSTRACT\s*$ anchor and
# hide a real abstract section — strip them before any matching.
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
# Bare page-number lines ("v", "vi", "12") left where the PDF page break
# splits the abstract — extraction artifacts, not abstract prose. Matched
# against a single line (see _normalize_section).
_PAGE_NUM_LINE_RE = re.compile(r"[ \t]*(?:[ivxlcdm]+|\d{1,3})[ \t]*$", re.I)
# The running page header an abstract inherits from the page template:
#   ILOILO SCIENCE AND TECHNOLOGY UNIVERSITY      <- standalone ALL-CAPS line
#   Burgos St., La Paz, Iloilo City               <- its address line
_ADDRESS_LINE_RE = re.compile(
    r"\b(?:St\.?|Street|Ave\.?|Avenue|Rd\.?|Road|Blvd\.?|Purok|Barangay"
    r"|City|Zip|Zipcode|Philippines)\b", re.I)
ADDRESS_LINE_MAX = 60

ABSTRACT_SECTION_CAP = 2500   # longest archive abstract + keywords line = 2335
KEYWORDS_LINE_CAP = 400        # archive keyword lines run 103-143 chars
KEYWORDS_SEARCH_WINDOW = 4000  # how far past the heading to look for one
FALLBACK_SECTION_LEN = ABSTRACT_SECTION_CAP   # no keyword line: same slice size
FALLBACK_STOP_SLACK = 400      # stop-marker search past the fallback slice
TOC_HEADING_WINDOW = 300       # ToC-entry test: markers right after the heading
HEAD_REMAINDER_WINDOW = 8000   # head path: how far past the heading to find the end


def _normalize_section(section: str) -> str:
    """Strip PDF-extraction artifacts from an ABSTRACT section, so the model
    is shown the abstract prose and nothing else:
    - the running page header an abstract inherits from the page template
      (ALL-CAPS institution line + its address line) plus the bare page
      number ("v", "vi", "12") under it, together with the blank lines
      around them — the two halves of the sentence join back together;
    - hard line wraps: a line ending in "-" continues the word (keep the
      hyphen, drop the break), every other wrap becomes a space.
    The paragraph break in front of the keywords line always survives, so
    the model can tell abstract apart from keywords.
    """
    lines = section.split("\n")
    marked: set[int] = set()
    blank = lambda i: not lines[i].strip()

    def adjacent_blank(i: int) -> bool:
        return (i > 0 and blank(i - 1)) or (i + 1 < len(lines) and blank(i + 1))

    # a) running page header — never the section's own first line.
    for i in range(1, len(lines)):
        s = lines[i].strip()
        if len(s) < 6 or s != s.upper() or not any(c.isalpha() for c in s):
            continue
        j = i + 1
        while j < len(lines) and blank(j):
            j += 1
        # A page break shows as blank lines around it, or as the header
        # block's own address/page-number lines underneath.
        is_header = adjacent_blank(i) or (
            j < len(lines)
            and (_ADDRESS_LINE_RE.search(lines[j]) or _PAGE_NUM_LINE_RE.match(lines[j]))
        )
        if not is_header:
            continue
        marked.add(i)
        if (j < len(lines) and len(lines[j].strip()) < ADDRESS_LINE_MAX
                and _ADDRESS_LINE_RE.search(lines[j])):
            marked.add(j)

    # b) bare page-number lines: at a page break, or inside the header block
    #    we just identified ("...University \nBurgos St. ... \nvi \nbody").
    for i, line in enumerate(lines):
        if not _PAGE_NUM_LINE_RE.match(line):
            continue
        if (adjacent_blank(i)
                or (i > 0 and i - 1 in marked)
                or (i + 1 in marked)):
            marked.add(i)

    # c) the blank lines around everything we drop, so the halves rejoin.
    for i in list(marked):
        for j in range(i - 1, -1, -1):
            if not blank(j):
                break
            marked.add(j)
        for j in range(i + 1, len(lines)):
            if not blank(j):
                break
            marked.add(j)

    text = ""
    for i, line in enumerate(lines):
        if i in marked:
            continue
        part = line.strip()
        if not part:
            if text and not text.endswith("\n"):
                text += "\n"
            continue
        if not text or text.endswith("\n"):
            text += part
        elif _KEYWORDS_LINE_RE.match(part):
            text += "\n" + part       # keywords always start their own line
        elif text.endswith("-") and part[:1].islower():
            text += part              # wrapped word: "community-" + "managed"
        else:
            text += " " + part
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _is_toc_heading(combined: str, m) -> bool:
    """True when this ABSTRACT match is a table-of-contents entry rather
    than the real heading. Three tells, cheapest first:
    - the heading's own line carries dot leaders ("Abstract ..... 5");
    - the heading's own line IS a bare page number ("Abstract 5");
    - a ToC-like marker (_TOC_STOP_RE — never KEYWORDS) starts a line
      within TOC_HEADING_WINDOW chars after the heading, e.g.
        Abstract
        CHAPTER 1
        INTRODUCTION ... 1
    A real heading is a lone "Abstract" line followed by prose, so it
    trips none of these."""
    line_start = combined.rfind("\n", 0, m.start()) + 1
    line_end = combined.find("\n", m.start())
    line = combined[line_start:line_end if line_end != -1 else len(combined)]
    if _DOT_LEADER_RE.search(line) or _PAGE_NUM_LINE_RE.match(line.strip()):
        return True
    return bool(_TOC_STOP_RE.search(combined, m.end(),
                                    m.end() + TOC_HEADING_WINDOW))


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

    def _extract_metadata(self, pages: list[dict], fallback_name: str,
                          raise_on_error: bool = False) -> dict:
        """raise_on_error=False (default) keeps the preview flow's
        best-effort behavior: any failure yields the fallback dict.
        The bulk-ingest script passes True so a transient Groq error
        (429) surfaces and can be retried with backoff instead of
        silently being recorded as 'Unknown' metadata."""
        combined = "\n".join(p["text"] for p in pages)
        # Invisible characters ride along with some headings ("​Abstract​")
        # and would hide the section from every regex below.
        combined = _ZERO_WIDTH_RE.sub("", combined)
        combined_head = combined[:3000]

        abstract_block = ""
        section_path = ""           # "keywords" | "fallback" | "head" | ""
        may_be_truncated = False    # section hit its cap with no end marker

        # Every place the document prints "ABSTRACT", in order. The first
        # one is often the table-of-contents entry (title page, approval
        # sheet and ToC all sit inside the first 3000 characters), so
        # ToC-looking candidates are skipped and the REAL abstract wins —
        # wherever in the document it is.
        candidates: dict = {}
        for _rx in (_ABSTRACT_HEAD_RE, _ABSTRACT_HEAD_ALT_RE):
            for _cm in _rx.finditer(combined):
                candidates.setdefault(_cm.start(), _cm)
        m = next((candidates[pos] for pos in sorted(candidates)
                  if not _is_toc_heading(combined, candidates[pos])), None)

        if m and m.start() >= len(combined_head):
            start = m.start()
            # Prefer: heading -> end of the keywords line (so keywords are
            # actually sent). Cap at ABSTRACT_SECTION_CAP; fall back to the
            # legacy fixed slice when the document prints no keyword line.
            rel = _KEYWORDS_LINE_RE.search(
                combined[start + 30:start + 30 + KEYWORDS_SEARCH_WINDOW])
            if rel:
                kw_end = start + 30 + rel.end()
                rest = combined[kw_end:]
                blank = re.search(r"\n[ \t]*\n", rest)
                kw_end += min(blank.start() if blank else len(rest),
                              KEYWORDS_LINE_CAP)
                section_end = min(kw_end, start + ABSTRACT_SECTION_CAP)
                section_path = "keywords"
            else:
                section_end = start + FALLBACK_SECTION_LEN
                section_path = "fallback"
            section = combined[start:section_end]
            if not rel:                      # legacy stop words, no keyword line
                # Search a little past the cut so a stop word straddling the
                # boundary ("TABLE OF CONTE…") is still recognised, then cut
                # right where the non-abstract content begins.
                window = combined[start:start + FALLBACK_SECTION_LEN
                                  + FALLBACK_STOP_SLACK]
                stop = _SECTION_STOP_RE.search(window, 40)
                if stop:
                    section = combined[start:start + stop.start()]
                else:
                    # The slice ends on the cap, not on anything that marks
                    # the abstract's real end — it may be cut mid-abstract.
                    # Report-only: the stored abstract is never changed here.
                    may_be_truncated = True
            section = _normalize_section(section)
            if section:
                abstract_block = f"\n\nABSTRACT + KEYWORDS section of the document:\n{section}"
        elif m:
            # Heading inside the first 3000 characters: the head already
            # carries the START of the abstract, but front matter plus a
            # long abstract routinely run past char 3000. Find where the
            # abstract really ends (keywords line or next section heading)
            # and append ONLY the missing remainder, so nothing the model
            # already saw in the head is duplicated.
            section_path = "head"
            start = m.start()
            window = combined[start + 30:start + 30 + HEAD_REMAINDER_WINDOW]
            rel = _KEYWORDS_LINE_RE.search(window)
            stop = _SECTION_STOP_RE.search(window, 40)
            ends = [x.start() for x in (rel, stop) if x]
            if ends and start + 30 + min(ends) > len(combined_head):
                remainder = _normalize_section(
                    combined[len(combined_head):start + 30 + min(ends)])
                if remainder:
                    abstract_block = (
                        "\n\nABSTRACT (continued after the first 3000 "
                        f"characters):\n{remainder}")

        doc_label = "first 3000 characters" + (
            " + ABSTRACT + KEYWORDS section" if abstract_block else "")

        prompt = f"""
Extract metadata from this ISAT-U academic thesis document.
Return ONLY valid JSON with these exact keys. Use "Unknown" if not found.

{{
  "title":    "Full research title",
  "authors":  "All authors comma-separated",
  "year":     "4-digit year e.g. 2024",
  "college":  "Full college name e.g. College of Industrial Technology",
  "abstract": "VERBATIM copy of the ENTIRE abstract from the ABSTRACT section below - every sentence, in the document's own wording. Never summarize, never shorten, never drop a sentence.",
  "keywords": "VERBATIM copy of every keyword printed on the Keywords/Keyconcepts line below, comma-separated. Never abbreviate, shorten or invent a keyword; \"Unknown\" only if the document prints none."
}}

"abstract" and "keywords" are direct quotations of the document text below: reproduce them exactly as printed, never paraphrase. Only the line wraps are already joined for you.

Document ({doc_label}):
{combined_head}{abstract_block}
"""
        try:
            resp = self._llm.invoke(prompt).content.strip()
            if "```" in resp:
                resp = resp.split("```")[1]
                if resp.startswith("json"):
                    resp = resp[4:]
            meta = json.loads(resp.strip())
            # Report-only extraction facts (bulk_ingest's report/console).
            # Keys, not columns: the stored abstract is never altered
            # because of them.
            meta["abstract_section_path"] = section_path
            meta["abstract_may_be_truncated"] = may_be_truncated
        except Exception as e:
            if raise_on_error:
                raise    # bulk ingest: surface the real error (e.g. 429)
            logger.warning(f"Metadata extraction failed, using defaults: {e}")
            meta = {
                "title": fallback_name, "authors": "Unknown", "year": "Unknown",
                "college": "Unknown", "abstract": combined_head[:200], "keywords": "Unknown",
            }
        meta["year_normalized"] = self._normalize_year(meta.get("year", "Unknown"))
        meta["abstract"] = self._clean_abstract(meta.get("abstract"))
        return meta

    @staticmethod
    def _clean_abstract(text) -> str:
        """The ABSTRACT heading itself sometimes survives as the first token
        of the copied abstract ("ABSTRACT This study examines …") — it is a
        heading, not prose, so drop it. Nothing else is altered: the field
        stays a verbatim copy of the document's abstract."""
        raw = str(text or "")
        cleaned = re.sub(r"^\s*ABSTRACT\b[\s:.\-–—]*", "", raw, count=1,
                         flags=re.I).strip()
        return cleaned if cleaned else raw

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

    def _validated_metadata(self, ai_meta: dict, final_values: dict) -> dict:
        """Merge the uploader's edits over the AI extraction, rejecting
        values the DB cannot store (a ValueError becomes a 400 in the route).

        Only these keys are read: title, authors (alias "members"),
        department, year, abstract. Anything else in final_values is
        ignored, so a client can never set server-owned columns
        (source_stem, filepath, status, reviewed_by) through this payload.

        Blank fields are not errors — they simply keep the extracted value,
        which is also what the preview screen shows.
        """
        meta = dict(ai_meta)

        def _text(value) -> str:
            return str(value).strip() if value is not None else ""

        title = _text(final_values.get("title"))
        if title:
            if len(title) > 255:
                raise ValueError("Title must be 255 characters or fewer.")
            meta["title"] = title

        # "members" is the historical name in the upload form; both are
        # accepted, and an explicit "authors" wins when both are sent.
        authors = _text(final_values.get("authors")) or _text(final_values.get("members"))
        if authors:
            if len(authors) > 255:
                raise ValueError("Authors must be 255 characters or fewer.")
            meta["authors"] = authors

        department = _text(final_values.get("department"))
        if department:
            if len(department) > 100:
                raise ValueError("Department must be 100 characters or fewer.")
            meta["college"] = department

        abstract = _text(final_values.get("abstract"))
        if abstract:
            meta["abstract"] = abstract

        year_raw = _text(final_values.get("year"))
        if year_raw:
            try:
                year = int(year_raw)
            except ValueError:
                year = self._normalize_year(year_raw)
            max_year = datetime.now(timezone.utc).year + 1
            if year is None or not (1990 <= year <= max_year):
                raise ValueError(f"Year must be a number between 1990 and {max_year}.")
            meta["year_normalized"] = year

        return meta

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

        meta = self._validated_metadata(ai_meta, final_values)

        # Same value both branches below store in research.department —
        # resolve it while we have it. "Unknown"/unmatched -> None (NULL),
        # which never blocks a submission.
        college_id = resolve_college(meta.get("college", "Unknown"))

        documents = await asyncio.to_thread(self.build_documents, pages, meta, stem)

        indexed = await self.index_documents(stem, documents)

        if is_resubmission and existing:
            existing.title = meta.get("title", stem)
            existing.authors = meta.get("authors", "Unknown")
            existing.department = meta.get("college", "Unknown")
            existing.college_id = college_id
            existing.year = meta.get("year_normalized")
            existing.abstract = meta.get("abstract", "")
            existing.status = StatusEnum.pending
            existing.feedback_note = None
            existing.public_pdf_consent = bool(public_pdf_consent)
        else:
            db.add(Research(
                title=meta.get("title", stem), authors=meta.get("authors", "Unknown"),
                department=meta.get("college", "Unknown"), college_id=college_id,
                year=meta.get("year_normalized"),
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