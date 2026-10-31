"""
bulk_ingest.py
--------------
Folder-driven bulk ingestion of FINISHED archive PDFs (theses/capstones)
into the repository as APPROVED research.

    python bulk_ingest.py --root <folder> --owner-email <email> [--dry-run]
                          [--limit N] [--consent] [--sleep 2]

Folder layout decides the college — not the PDF text, not the LLM:

    <root>/<CODE>/*.pdf     CODE in {CAS, CCI, CIT, COE, CEA, CGBE}

Unknown folder names are rejected with a clear message (folder skipped,
run continues).

Guarantees / behavior:
- Re-runnable: a file whose stored copy already has identical content under
  its source_stem is skipped (idempotent resume). A same-named DIFFERENT
  paper gets its stem suffixed with __<CODE>; the rename is reported.
- Archive papers default to public_pdf_consent=False (--consent overrides).
- student_id = the --owner-email account (must already exist; never created).
- department = canonical name of the FOLDER's college; college_id likewise.
  The LLM-extracted college text is recorded in the report only, to measure
  extraction accuracy later.
- One PDF at a time; Groq 429s retried with exponential backoff (max 5
  tries) and connection/JSON errors retried twice with a short wait; a
  bad file is reported as failed and the run continues (failed files
  are never written to the extraction cache).
- A failure after the bucket upload but before the DB commit rolls back the
  bucket object AND the Chroma chunks for that file — no orphans.
- --dry-run runs steps 1-3 only (existence/size/text checks + metadata
  extraction) and writes ONLY the report and the local extraction cache —
  nothing to the database, bucket, or Chroma.

Report: bulk_ingest_report.csv next to this script (one row per file).
Extraction cache: bulk_ingest_cache.json keyed by file SHA-256 and stamped
with EXTRACTION_CACHE_VERSION, so a dry run and the real run never spend
the same Groq call twice — but a prompt change invalidates old entries.

Never prints credentials: exception text passes through _safe() which
redacts anything URL-shaped or token-shaped.
"""
import argparse
import asyncio
import csv
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from database import AsyncSessionLocal, init_db
from models import College, Research, StatusEnum, User
from services.colleges import CODES, COLLEGES, resolve_college_code
from services.ingestor import ingestor
from services.storage import storage_service
from services.vectorstore import vectorstore_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

MAX_FILE_BYTES = 50 * 1024 * 1024      # 50 MB per-file cap (archive script)
MAX_LLM_TRIES = 5                      # Groq 429 retries (exponential backoff)
MAX_TRANSIENT_TRIES = 3                # total tries for connection/JSON errors
                                       # (i.e. 2 extra tries)
TRANSIENT_WAIT = 2.0                   # short fixed wait before a transient retry
DRY_RUN_REASON = "dry-run: would ingest"

# Bump whenever extraction changes meaning (prompt wording, ABSTRACT section
# slicing, ...). Cache entries stamped with another version are ignored by
# _load_cache(), so a prompt fix can never reuse metadata extracted under the
# old wording — that is exactly how stale shortened abstracts survived before.
EXTRACTION_CACHE_VERSION = 3

# Postgres varchar(n) limits for the columns bulk ingest writes
# (models.py). Values longer than this FAIL the insert
# (asyncpg StringDataRightTruncation) — we report, never truncate.
COLUMN_LIMITS = {"title": 255, "authors": 255, "filepath": 255,
                 "source_stem": 255, "department": 100}

REPORT_PATH = Path(__file__).resolve().parent / "bulk_ingest_report.csv"
CACHE_PATH = Path(__file__).resolve().parent / "bulk_ingest_cache.json"

REPORT_FIELDS = [
    "folder_code", "filename", "status", "reason", "source_stem",
    "title_extracted", "authors_extracted", "year_extracted",
    "college_extracted_raw", "college_extracted_resolved_code",
    "college_match", "legacy_name_mismatch", "abstract_chars",
    "keywords_extracted",
]


# ── Small helpers ──────────────────────────────────────────────────────

def _safe(err: Exception) -> str:
    """Exception text safe to print/store: redact anything URL-shaped
    (DATABASE_URL embeds the password) or token-shaped (JWT-style keys)."""
    text = f"{type(err).__name__}: {err}"
    text = re.sub(r"\b\w+://\S+", "[redacted-url]", text)
    text = re.sub(r"eyJ[A-Za-z0-9_\-]{10,}", "[redacted-token]", text)
    return text


def _is_rate_limit(err: Exception) -> bool:
    name = type(err).__name__.lower()
    msg = str(err).lower()
    return ("ratelimit" in name or "429" in msg
            or "rate limit" in msg or "too many requests" in msg)


def _is_daily_limit(err: Exception) -> bool:
    """Groq's tokens-per-day (TPD) cap: 'Rate limit reached … tokens per
    day (TPD): Limit 200000 …'. Waiting cannot help — the window resets
    once a day — so the run must stop instead of burning every remaining
    file on 429s."""
    return "tokens per day" in str(err).lower()


def _is_transient(err: Exception) -> bool:
    """Errors that are usually transient and worth a couple of cheap
    retries: the HTTP call to Groq never completed (APIConnectionError —
    matched by name so no SDK import is needed here) or the model's answer
    was not valid JSON (json.JSONDecodeError, e.g. a truncated reply).
    Neither means the PDF itself is bad."""
    if type(err).__name__ == "APIConnectionError":
        return True
    if isinstance(err, json.JSONDecodeError) or type(err).__name__ == "JSONDecodeError":
        return True
    return False


class DailyLimitReached(Exception):
    """Raised when the Groq daily token quota is exhausted; main() stops
    the run cleanly instead of failing every remaining file."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_title(stem: str) -> str:
    """Readable fallback title from the filename."""
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


def _length_warnings(values: dict, limits: dict = COLUMN_LIMITS) -> list[str]:
    """Report-ONLY guard for the varchar(n) columns in models.py: a value
    longer than its column aborts the whole insert (asyncpg
    StringDataRightTruncation), so flag it in the report row and the console
    instead of discovering it at commit time. Nothing is ever truncated here."""
    warnings = []
    for label, value in values.items():
        limit = limits.get(label)
        if limit and len(value) > limit:
            warnings.append(f"length warning: {label} {len(value)} > {limit} chars")
    return warnings


class Report:
    """CSV written row-by-row and flushed immediately, so an interrupted
    run still leaves a complete report of everything processed so far."""

    def __init__(self, path: Path):
        self._fh = path.open("w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._fh, fieldnames=REPORT_FIELDS,
                                      extrasaction="ignore")
        self._writer.writeheader()
        self.rows: list[dict] = []

    def add(self, **row) -> dict:
        clean = {k: row.get(k, "") for k in REPORT_FIELDS}
        self._writer.writerow(clean)
        self._fh.flush()
        self.rows.append(clean)
        return clean

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def _load_cache() -> dict:
    """Load the local extraction cache, keeping ONLY entries stamped with the
    current EXTRACTION_CACHE_VERSION. Anything else (older prompt/section
    slicing) is dropped instead of silently reused — a stale abstract must
    never outlive the prompt that produced it."""
    try:
        if CACHE_PATH.exists():
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                fresh = {sha: entry for sha, entry in data.items()
                         if isinstance(entry, dict)
                         and entry.get("cache_version") == EXTRACTION_CACHE_VERSION}
                stale = len(data) - len(fresh)
                if stale:
                    print(f"INFO: ignoring {stale} stale extraction-cache "
                          f"entr{'y' if stale == 1 else 'ies'} "
                          f"(cache_version != {EXTRACTION_CACHE_VERSION}); "
                          "they will be re-extracted.")
                return fresh
    except Exception as e:
        print(f"WARNING: extraction cache unreadable ({type(e).__name__}) — starting fresh.")
    return {}


def _save_cache(cache: dict) -> None:
    """Persist after every successful extraction so an interrupted run
    keeps its Groq progress. Local disk only — allowed in --dry-run."""
    try:
        tmp = CACHE_PATH.parent / (CACHE_PATH.name + ".tmp")
        tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except Exception as e:
        print(f"WARNING: could not write extraction cache ({type(e).__name__}) — non-fatal.")


async def _extract_with_retry(pages: list[dict], stem: str) -> dict:
    """Same extractor the upload preview uses, plus retries for the errors
    that are usually transient:
    - Groq 429: up to MAX_LLM_TRIES attempts (2, 4, 8, 16 s waits);
    - APIConnectionError / JSONDecodeError: up to MAX_TRANSIENT_TRIES
      attempts in total (2 extra tries, short fixed TRANSIENT_WAIT).
    A daily-quota hit stops the run, and any other error propagates
    immediately (file marked failed, nothing cached). The counters are
    per-file and never reset, so one file can never spin forever."""
    tries_rate_limit = 0
    tries_transient = 0
    while True:
        try:
            return await asyncio.to_thread(
                ingestor._extract_metadata, pages, stem, raise_on_error=True,
            )
        except Exception as e:
            if _is_daily_limit(e):
                # Not a transient 429: no amount of waiting refills a daily
                # quota. Stop the whole run rather than retrying every
                # remaining file into the same wall.
                raise DailyLimitReached(_safe(e)) from e
            if _is_rate_limit(e):
                tries_rate_limit += 1
                if tries_rate_limit < MAX_LLM_TRIES:
                    wait = 2 * (2 ** (tries_rate_limit - 1))
                    print(f"    Groq rate limit — retry {tries_rate_limit}/{MAX_LLM_TRIES} in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
            elif _is_transient(e):
                tries_transient += 1
                if tries_transient < MAX_TRANSIENT_TRIES:
                    print(f"    transient {type(e).__name__} — retry "
                          f"{tries_transient}/{MAX_TRANSIENT_TRIES - 1} extra "
                          f"in {TRANSIENT_WAIT:g}s...")
                    await asyncio.sleep(TRANSIENT_WAIT)
                    continue
            raise


# ── Per-file pipeline ──────────────────────────────────────────────────

async def process_file(code: str, path: Path, ctx) -> None:
    """Steps 1-3 always (checks + extraction); step 4-5 only when not
    --dry-run. Writes exactly one report row and bumps ctx.stats."""
    db, report, cache, stats = ctx.db, ctx.report, ctx.cache, ctx.stats
    filename = path.name
    stem = path.stem                     # same stem rule as preview_upload
    notes: list[str] = []

    def finish(status: str, reason: str, final_stem: str = "", **extra):
        report.add(folder_code=code, filename=filename, status=status,
                    reason=reason, source_stem=final_stem or stem, **extra)
        stats[code][status] += 1
        print(f"  [{code}] {filename}: {status}"
              + (f" — {reason}" if reason else ""))

    # Step: size guard (before hashing/upload work)
    try:
        size = path.stat().st_size
    except OSError as e:
        finish("failed", f"cannot read file: {_safe(e)}")
        return
    if size > MAX_FILE_BYTES:
        finish("skipped", f"file too large ({size // (1024 * 1024)} MB > 50 MB)")
        return

    pdf_bytes = path.read_bytes()
    sha = _sha256(pdf_bytes)

    # ── Step 1: source_stem exists? (re-run safety / collision) ────────
    final_stem = stem
    existing = (await db.execute(
        select(Research).where(Research.source_stem == stem)
    )).scalar_one_or_none()
    if existing:
        stored = await asyncio.to_thread(storage_service.download, existing.filepath)
        if stored and _sha256(stored) == sha:
            finish("skipped", "already ingested (source_stem exists with identical content)",
                   stem)
            return
        if not stored:
            finish("skipped",
                   f"source_stem '{stem}' already exists (stored copy unreadable — "
                   "rename this file to ingest it)", stem)
            return
        renamed = f"{stem}__{code}"
        clash = (await db.execute(
            select(Research).where(Research.source_stem == renamed)
        )).scalar_one_or_none()
        if clash:
            finish("skipped",
                   f"source_stem collision: '{stem}' and '{renamed}' already taken")
            return
        final_stem = renamed
        notes.append(f"source_stem collision — stored as '{renamed}'")

    # ── Step: extractable text? ────────────────────────────────────────
    pages = await asyncio.to_thread(ingestor._extract_pages, pdf_bytes)
    if not pages:
        finish("skipped", "no extractable text (scanned/image-only or invalid PDF)",
               final_stem)
        return

    # ── Step 3: metadata (cache first — never double-spend Groq) ───────
    # Every failure path returns above/below the cache write: a file whose
    # extraction failed (after the retries above) is never stored, so a
    # later run re-extracts it instead of reusing partial/garbage metadata.
    cached = cache.get(sha)
    if cached and isinstance(cached.get("meta"), dict):
        meta = cached["meta"]
    else:
        try:
            meta = await _extract_with_retry(pages, stem)
        except DailyLimitReached:
            raise                      # run-level stop: handled in main()
        except Exception as e:
            finish("failed", f"metadata extraction failed: {_safe(e)}", final_stem)
            return
        if not isinstance(meta, dict) or not meta:
            # Defensive: nothing usable came back — treat as a failed file
            # and keep it out of the cache entirely.
            finish("failed", "metadata extraction returned no usable metadata",
                   final_stem)
            return
        cache[sha] = {
            "meta": meta, "filename": filename,
            "cache_version": EXTRACTION_CACHE_VERSION,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_cache(cache)
    if "year_normalized" not in meta:     # defensive: old/partial cache
        meta["year_normalized"] = ingestor._normalize_year(meta.get("year", "Unknown"))

    # ── Report-only abstract-section warning ─────────────────────────
    # A fallback slice (no keyword line) that ends on its cap may have cut
    # the abstract mid-sentence. Flag it in the reason/console only — the
    # stored abstract is never altered because of it.
    if meta.get("abstract_may_be_truncated"):
        notes.append("abstract may be truncated")

    # ── Report-only extraction measurements (folder still wins) ────────
    raw_title = str(meta.get("title") or "").strip()
    title = raw_title if raw_title and raw_title != "Unknown" else _clean_title(stem)
    if title != raw_title:
        notes.append("title fallback to filename (extraction returned Unknown)")

    # ── Report-only column-length guard (models.py varchar limits) ─────
    # Flag, never truncate: a value longer than its column would abort the
    # insert at commit time (asyncpg StringDataRightTruncation), so it has
    # to be visible in the report first.
    dept_name = ctx.colleges[code].name if code in ctx.colleges else dict(COLLEGES)[code]
    notes.extend(_length_warnings({
        "title": title,
        "authors": str(meta.get("authors") or ""),
        "source_stem": final_stem,
        "filepath": storage_service.public_key(final_stem),
        "department": dept_name,
    }))

    raw_college = str(meta.get("college") or "")
    resolved_code = resolve_college_code(raw_college)
    extracted = dict(
        title_extracted=raw_title or "Unknown",
        authors_extracted=str(meta.get("authors") or ""),
        year_extracted=str(meta.get("year") or ""),
        college_extracted_raw=raw_college,
        college_extracted_resolved_code=resolved_code or "",
        college_match="yes" if resolved_code == code else "no",
        legacy_name_mismatch="yes" if (
            (code == "CGBE" and resolved_code == "CIT")
            or (code == "CCI" and resolved_code == "CAS")
        ) else "no",
        abstract_chars=len(str(meta.get("abstract") or "")),
        keywords_extracted=str(meta.get("keywords") or ""),
    )

    # Which slice fed the model — visible in the console for both modes.
    if meta.get("abstract_section_path") == "fallback":
        print(f"    abstract section: fallback (no keyword line), "
              f"{extracted['abstract_chars']} chars")

    # ── Dry run: extraction + report only — stop here. ─────────────────
    if ctx.args.dry_run:
        reason = DRY_RUN_REASON + (f"; {'; '.join(notes)}" if notes else "")
        finish("skipped", reason, final_stem, **extracted)
        return

    # ── Steps 4-5: upload straight to public/, index, commit. ─────────
    public_key = storage_service.public_key(final_stem)
    try:
        await asyncio.to_thread(storage_service.upload, public_key, pdf_bytes)

        # Chroma metadata mirrors confirm_upload: college = canonical name.
        index_meta = dict(meta)
        index_meta["title"] = title
        index_meta["college"] = ctx.colleges[code].name
        documents = await asyncio.to_thread(
            ingestor.build_documents, pages, index_meta, final_stem
        )
        if not await ingestor.index_documents(final_stem, documents):
            raise RuntimeError("vector indexing failed (see logs above)")

        db.add(Research(
            title=title,
            authors=str(meta.get("authors") or "Unknown"),
            department=ctx.colleges[code].name,   # FOLDER decides college
            college_id=ctx.colleges[code].id,
            year=meta.get("year_normalized"),
            abstract=str(meta.get("abstract") or ""),
            source_stem=final_stem,
            filepath=public_key,
            student_id=ctx.owner_id,
            status=StatusEnum.approved,
            public_pdf_consent=bool(ctx.args.consent),
        ))
        await db.commit()
    except Exception as e:
        # No orphans: undo DB row, bucket object, and Chroma chunks.
        # remove() treats a missing object as success, so this is also
        # safe when the failure happened before/inside the upload.
        try:
            await db.rollback()
        except Exception:
            pass
        await asyncio.to_thread(storage_service.remove, public_key)
        try:
            await vectorstore_service.delete_by_source(final_stem)
        except Exception:
            print(f"    WARNING: Chroma cleanup for '{final_stem}' failed (non-fatal).")
        finish("failed", _safe(e), final_stem, **extracted)
        return

    finish("ingested", "; ".join(notes), final_stem, **extracted)


# ── Setup / main ───────────────────────────────────────────────────────

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bulk_ingest.py",
        description="Folder-driven bulk ingest of archive PDFs "
                    "(folder name decides the college).",
    )
    p.add_argument("--root", required=True,
                   help="Folder containing <CODE>/*.pdf subfolders")
    p.add_argument("--owner-email", required=True,
                   help="Existing account that becomes student_id (never created)")
    p.add_argument("--dry-run", action="store_true",
                   help="Extract + report only; no DB/bucket/Chroma writes")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N files (0 = no limit)")
    p.add_argument("--consent", action="store_true",
                   help="Set public_pdf_consent=True (default: False)")
    p.add_argument("--sleep", type=float, default=2.0,
                   help="Seconds to sleep between files (default: 2)")
    return p.parse_args(argv)


def print_summary(report: Report, stats: dict, args) -> None:
    print("\n=== bulk_ingest summary ===")
    print(f"Mode: {'DRY RUN (no DB/bucket/Chroma writes)' if args.dry_run else 'LIVE'}"
          f" | consent={args.consent} | limit={args.limit or 'all'}")
    print(f"{'college':<10}{'ingested':>10}{'skipped':>10}{'failed':>9}{'total':>8}")
    order = [code for code, _ in COLLEGES] + [
        k for k in stats if k not in {c for c, _ in COLLEGES}
    ]
    totals = Counter()
    for key in order:
        c = stats.get(key)
        if not c:
            continue
        row_total = sum(c.values())
        totals.update(c)
        print(f"{key:<10}{c['ingested']:>10}{c['skipped']:>10}"
              f"{c['failed']:>9}{row_total:>8}")
    print(f"{'TOTAL':<10}{totals['ingested']:>10}{totals['skipped']:>10}"
          f"{totals['failed']:>9}{sum(totals.values()):>8}")
    if args.dry_run:
        would = sum(1 for r in report.rows
                    if str(r.get("reason", "")).startswith(DRY_RUN_REASON))
        print(f"Would ingest: {would} file(s) "
              f"(status=skipped, reason='{DRY_RUN_REASON}')")
    print(f"Report: {REPORT_PATH}")
    print(f"Extraction cache: {CACHE_PATH}")


async def main(argv=None) -> int:
    args = parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: --root folder not found: {root}")
        return 1

    # Real runs guarantee schema + colleges seed + college_id column.
    # Dry runs stay strictly read-only (no DDL, no seed).
    if not args.dry_run:
        await init_db()

    # Owner: looked up BEFORE any file work, both modes; never created.
    try:
        async with AsyncSessionLocal() as db:
            owner = (await db.execute(
                select(User).where(func.lower(User.email)
                                   == args.owner_email.strip().lower())
            )).scalar_one_or_none()
    except Exception as e:
        print(f"ERROR: cannot read the users table ({type(e).__name__}). "
              "Run the app once so init_db() creates/seeds the schema.")
        print(f"  detail: {_safe(e)}")
        return 1
    if owner is None:
        print(f"ERROR: no user with email '{args.owner_email}'. "
              "Create/verify the account first — this script never creates users.")
        return 1
    owner_id = owner.id

    # Research table probe (dry runs skip init_db, so fail with a hint).
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(select(Research).limit(1))
    except Exception as e:
        print(f"ERROR: cannot read the research table ({type(e).__name__}). "
              "Run the app once so init_db() creates/seeds the schema.")
        print(f"  detail: {_safe(e)}")
        return 1

    # Colleges (real runs only — dry-run reporting resolves codes from the
    # static seed list, no table needed).
    colleges: dict[str, College] = {}
    if not args.dry_run:
        async with AsyncSessionLocal() as db:
            colleges = {c.code: c for c in (await db.execute(
                select(College))).scalars().all()}
        missing = CODES - set(colleges)
        if missing:
            print(f"ERROR: colleges table incomplete (missing {sorted(missing)}). "
                  "Run the app once so init_db() seeds it.")
            return 1

    # ── Folder scan: unknown folders rejected with a message, continue ─
    dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: p.name)
    unknown_dirs = [p for p in dirs if p.name not in CODES]
    work: list[tuple[str, Path]] = []
    for folder in dirs:
        pdfs = sorted((f for f in folder.iterdir()
                       if f.is_file() and f.suffix.lower() == ".pdf"),
                      key=lambda f: f.name)
        if folder.name not in CODES:
            print(f"WARNING: skipping unknown folder '{folder.name}' "
                  f"(expected one of: {', '.join(sorted(CODES))})")
        else:
            work.extend((folder.name, f) for f in pdfs)
    if args.limit and args.limit > 0:
        work = work[:args.limit]

    print(f"Root: {root}")
    print(f"Owner: {args.owner_email} (id={owner_id})")
    print(f"Files queued: {len(work)}"
          + (f" | unknown folders rejected: {[d.name for d in unknown_dirs]}"
             if unknown_dirs else ""))
    if not dirs:
        print("WARNING: --root contains no folders.")

    report = Report(REPORT_PATH)
    stats: dict[str, Counter] = defaultdict(Counter)
    cache = _load_cache()

    # Unknown-folder files still get report rows (visible, never processed).
    for folder in unknown_dirs:
        for f in sorted(folder.iterdir(),
                        key=lambda x: x.name):
            if f.is_file() and f.suffix.lower() == ".pdf":
                report.add(folder_code=folder.name, filename=f.name,
                           status="skipped",
                           reason=f"unknown folder '{folder.name}' "
                                  f"(expected {', '.join(sorted(CODES))})",
                           source_stem=f.stem)
                stats[folder.name]["skipped"] += 1

    ctx = argparse.Namespace(
        db=None, report=report, cache=cache, stats=stats,
        owner_id=owner_id, colleges=colleges, args=args,
    )
    for i, (code, path) in enumerate(work):
        # One session per file: a file's connection can sit inside an open
        # transaction through minutes of Groq backoff, and the Supabase
        # pooler drops connections it sees idle-in-transaction (asyncpg:
        # "connection was closed in the middle of operation"). With the old
        # single shared session, that one drop poisoned EVERY later file
        # with PendingRollbackError; a fresh session per file confines any
        # damage to the file that was actually in flight.
        try:
            async with AsyncSessionLocal() as db:
                ctx.db = db
                try:
                    await process_file(code, path, ctx)
                except Exception:
                    await db.rollback()   # never leave a session dirty
                    raise
        except DailyLimitReached:
            left = len(work) - i
            print(f"\nSTOPPED: Groq daily token limit reached — {left} file(s) "
                  f"left unprocessed (this file included). The extraction "
                  "cache keeps everything already done; re-run later to finish.")
            report.add(folder_code=code, filename=path.name, status="skipped",
                       reason="run stopped: Groq daily token limit reached",
                       source_stem=path.stem)
            stats[code]["skipped"] += 1
            break
        except Exception as e:               # one bad file never aborts
            report.add(folder_code=code, filename=path.name,
                       status="failed", reason=_safe(e),
                       source_stem=path.stem)
            stats[code]["failed"] += 1
            print(f"  [{code}] {path.name}: failed — {_safe(e)}")
        if i < len(work) - 1 and args.sleep > 0:
            await asyncio.sleep(args.sleep)

    report.close()
    print_summary(report, stats, args)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
