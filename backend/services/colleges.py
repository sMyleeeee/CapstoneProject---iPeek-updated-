"""
services/colleges.py
--------------------
Canonical college registry + resolution.

Three consumers, deliberately kept separate:

1. NORMAL upload flow — resolve_college(text) maps the SAME free-text value
   confirm_upload stores in research.department to a colleges.id
   (None when unknown/unmatched: college_id stays NULL, never blocks).

2. REGISTRATION — resolve_college_name(text) must match a canonical college
   for students/faculty (400 otherwise; librarians are exempt).
   GET /auth/colleges exposes this registry to the sign-up dropdown.

3. BULK ingest (bulk_ingest.py) — the FOLDER decides the college; the LLM's
   extracted college text is only reported (via resolve_college_code) to
   measure extraction accuracy. Nothing here maps legacy names to the
   college that replaced them: "Industrial Technology" is still CIT and
   "Arts and Sciences" is still CAS. The bulk script handles legacy
   papers by folder instead.
"""
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import College

logger = logging.getLogger(__name__)

# ── Canonical seed list (code, name) ───────────────────────────────────
# Also the single source of truth for valid archive folder names.
# Update the name here if the institution renames a college —
# seed_colleges() upserts it on the next startup.
COLLEGES = [
    ("CAS",  "College of Arts and Sciences"),
    ("CCI",  "College of Computing and Informatics"),
    ("CIT",  "College of Industrial Technology"),
    ("COE",  "College of Education"),
    ("CEA",  "College of Engineering and Architecture"),
    ("CGBE", "College of Global Business and Enterprise"),
]

CODES = {code for code, _ in COLLEGES}

# Words that carry no matching signal — dropped before comparing tokens.
_FILLERS = {"college", "of", "the", "and"}

# Legacy/alternate spellings kept as aliases (already normalized).
# "College of Computer and Informatics" was an earlier spelling of CCI.
_ALIASES = [
    (frozenset({"computer", "informatics"}), "CCI"),
]

# code -> colleges.id, rebuilt by seed_colleges() during init_db().
# resolve_college() must be synchronous (it is called from async
# confirm_upload, where an implicit DB query would be illegal on asyncpg),
# so the ids are cached here. An empty registry resolves to None — safe.
_IDS: dict[str, int] = {}


def _tokens(text) -> frozenset:
    """Trim, lowercase, '&' -> 'and', drop filler words, split."""
    raw = str(text or "").strip().lower().replace("&", " and ")
    return frozenset(
        tok for tok in re.split(r"[^a-z0-9]+", raw) if tok and tok not in _FILLERS
    )


def resolve_college_code(text) -> str | None:
    """Pure text -> canonical code (no DB access). None when unresolved."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.upper() in CODES:                      # "CIT", "cit", "CGBE"...
        return raw.upper()

    toks = _tokens(raw)
    if not toks or toks == {"unknown"}:
        return None
    if len(toks) == 1:                            # "cit" as a lone token
        only = next(iter(toks)).upper()
        if only in CODES:
            return only

    for code, name in COLLEGES:                   # exact canonical name
        if toks == _tokens(name):
            return code
    for alias, code in _ALIASES:                  # legacy spellings
        if toks == alias:
            return code
    for code, name in COLLEGES:                   # token containment either
        name_toks = _tokens(name)                 # way ("engineering" -> CEA)
        if toks < name_toks or name_toks < toks:
            return code
    return None


def resolve_college(text) -> int | None:
    """NORMAL upload flow: text -> colleges.id (None when unknown/unmatched).
    Requires seed_colleges() to have run (init_db does it at startup)."""
    code = resolve_college_code(text)
    return _IDS.get(code) if code else None


def resolve_college_name(text) -> str | None:
    """text -> canonical college NAME (None when unresolved). Pure text, no
    DB access — used to store the canonical spelling on a user row."""
    code = resolve_college_code(text)
    return dict(COLLEGES).get(code) if code else None


def normalize_department(text) -> str | None:
    """Store the canonical college name when the text resolves; otherwise
    keep the typed text as-is (never blocks a profile edit — registration
    enforces a known college separately)."""
    raw = str(text or "").strip()
    if not raw:
        return None
    return resolve_college_name(raw) or raw


async def _rebuild_registry(db: AsyncSession) -> None:
    rows = (await db.execute(select(College))).scalars().all()
    _IDS.clear()
    _IDS.update({row.code: row.id for row in rows})


async def seed_colleges(db: AsyncSession) -> None:
    """Idempotent upsert of the canonical list — called from init_db().
    Re-running changes nothing when the table already matches."""
    existing = {
        row.code: row
        for row in (await db.execute(select(College))).scalars().all()
    }
    changed = 0
    for code, name in COLLEGES:
        row = existing.get(code)
        if row is None:
            db.add(College(code=code, name=name))
            changed += 1
        elif row.name != name:
            row.name = name     # e.g. CGBE placeholder -> official name
            changed += 1
    await db.commit()
    await _rebuild_registry(db)
    if changed:
        logger.info(f"Colleges seeded/updated ({changed} row(s) changed).")
