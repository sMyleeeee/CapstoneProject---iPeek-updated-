"""
services/storage.py
-------------------
Supabase Storage access wrapped as a class, per the project's OOP requirement.

Replaces the old local papers/ directories (pending/, public/, versions/) with
object keys in the EXISTING private Supabase Storage bucket named "papers":

    pending/{safe stem}.pdf          <- canonical copy written at confirm_upload
    public/{safe stem}.pdf           <- copy made when a librarian approves
    versions/{safe stem}_v{n}.pdf    <- old pending copy archived on resubmission

The {safe stem} is produced by safe_object_key() below. Supabase Storage
rejects object keys containing non-ASCII characters (InvalidKey), so
research.source_stem stays unique and human-readable while only the bucket
key is sanitized; research.filepath always stores the real key.

Access rules:
- The bucket stays PRIVATE. PDFs are never served from a public URL.
- The service-role key (SUPABASE_SECRET_KEY) is used ONLY server-side:
  never sent to the frontend, never returned in an API response, never logged.
- For viewing/downloading, callers ask for a short-lived SIGNED URL that is
  generated here and consumed server-side (the bytes are proxied through our
  own authenticated endpoints) — the signed URL itself never reaches a client.
"""
import hashlib
import logging
import re
import unicodedata

from supabase import Client, create_client

from config import SUPABASE_URL, SUPABASE_SECRET_KEY, STORAGE_BUCKET

logger = logging.getLogger(__name__)

# Signed URLs only ever live server-side for the duration of one proxied
# download, so a short TTL is plenty.
SIGNED_URL_TTL_SECONDS = 300

# ── Object-key sanitation ────────────────────────────────────────────────
# Supabase Storage's documented "File name restrictions": only ASCII
# alphanumerics, whitespace, and a small punctuation set are accepted — any
# other character makes uploads fail with InvalidKey (non-ASCII names such
# as en dashes, curly quotes and ñ always fail). We keep a deliberately
# conservative subset of that set so already-ingested keys are unaffected.
MAX_OBJECT_KEY_LEN = 200
_ALLOWED_CLASS = r"A-Za-z0-9 ._'&(),\-"
_UNSAFE = re.compile(rf"[^{_ALLOWED_CLASS}]")
_REPEAT_DASH = re.compile(r"-{2,}")
_REPEAT_SPACE = re.compile(r" {2,}")

# Typographic characters folded to ASCII before the allowed-set filter:
# en/em/figure dashes, curly quotes, ellipsis, NBSP, ...
_FOLD = {
    0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-",
    0x2015: "-", 0x2212: "-", 0x00AD: "-",
    0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x2032: "'",
    0x201C: "'", 0x201D: "'", 0x201E: "'", 0x201F: "'", 0x2033: "'",
    0x2026: "...", 0x00A0: " ", 0x2022: "-", 0x00B7: "-",
}

# A paper's object moves between two prefixes; versions/ has no sibling.
_SIBLING_PREFIX = {"pending/": "public/", "public/": "pending/"}


def _ascii_fold(text: str) -> str:
    """Unicode-normalize and strip diacritics (ñ->n, é->e, ...), then map the
    common typographic characters to their ASCII equivalents."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return folded.translate(_FOLD)


def safe_object_key(stem: str, ext: str = ".pdf", prefix: str = "") -> str:
    """Build a bucket key that always passes Supabase Storage's file-name
    restrictions, from a human-readable stem.

    - ASCII-folds Unicode (en dashes, curly quotes, accents) and replaces any
      remaining character outside the conservative allowed set with "-";
    - collapses repeated dashes/spaces and trims surrounding whitespace;
    - caps the whole key (prefix and extension included) at
      MAX_OBJECT_KEY_LEN, keeping the extension;
    - if that changed the stem, appends a short stable hash of the ORIGINAL
      stem so two distinct files can never map to the same key.

    Already-safe stems (including single spaces) are returned unchanged. The
    output is itself safe, so calling this again is a no-op (idempotent) and
    previously stored keys stay valid.
    """
    safe = _REPEAT_SPACE.sub(
        " ", _REPEAT_DASH.sub("-", _UNSAFE.sub("-", _ascii_fold(stem)))
    ).strip()
    if not safe:
        safe = "file"

    budget = MAX_OBJECT_KEY_LEN - len(prefix) - len(ext)
    if safe != stem or len(safe) > budget:
        digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
        safe = safe[: max(0, budget - 9)].rstrip(" -") + "-" + digest
    return f"{prefix}{safe}{ext}"


def sibling_object_key(object_key: str) -> str | None:
    """The counterpart copy of a key when a paper moves between pending/ and
    public/. Returns None for an empty key or any other prefix (e.g.
    versions/ or a malformed value), so the caller only touches that exact
    key and never rebuilds one from source_stem."""
    for prefix, other in _SIBLING_PREFIX.items():
        if object_key.startswith(prefix):
            return other + object_key[len(prefix):]
    return None


class StorageService:
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
            raise ValueError(
                "SUPABASE_URL / SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY) "
                "are missing from .env"
            )
        self._client: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
        self._bucket = STORAGE_BUCKET
        logger.info(f"Supabase Storage ready — private bucket: '{self._bucket}'")

    # ── Key helpers ─────────────────────────────────────────────────

    @staticmethod
    def pending_key(stem: str) -> str:
        return safe_object_key(stem, ".pdf", "pending/")

    @staticmethod
    def public_key(stem: str) -> str:
        return safe_object_key(stem, ".pdf", "public/")

    @staticmethod
    def version_key(stem: str, version_number: int) -> str:
        return safe_object_key(f"{stem}_v{version_number}", ".pdf", "versions/")

    # ── Operations (all blocking supabase-py calls — wrap in to_thread) ──

    def upload(self, key: str, data: bytes) -> None:
        """Upload/replace an object. Bucket is private; upsert avoids
        collisions on legitimate re-runs (e.g. resubmission overwriting)."""
        self._client.storage.from_(self._bucket).upload(
            key, data, file_options={"content-type": "application/pdf", "x-upsert": "true"}
        )
        logger.info(f"Uploaded object: {key}")

    def download(self, key: str) -> bytes:
        """Fetch an object's bytes. Returns b'' if the key doesn't exist so
        callers can mirror the old `if not path.exists(): 404` behavior."""
        try:
            return self._client.storage.from_(self._bucket).download(key)
        except Exception as e:
            logger.warning(f"Storage download failed for '{key}': {e}")
            return b""

    def exists(self, key: str) -> bool:
        return bool(self.download(key))

    def copy(self, from_key: str, to_key: str) -> None:
        """Server-side copy within the bucket. Implemented as download+upload
        so it works identically across supabase-py versions (the copy API
        signature changed between releases)."""
        data = self.download(from_key)
        if not data:
            raise ValueError(f"Cannot copy '{from_key}' — object not found.")
        self.upload(to_key, data)

    def remove(self, key: str) -> None:
        """Delete one object. Missing objects are not an error (mirrors the
        old Path.unlink(missing_ok=True) behavior)."""
        try:
            self._client.storage.from_(self._bucket).remove([key])
            logger.info(f"Removed object: {key}")
        except Exception as e:
            logger.warning(f"Storage remove failed for '{key}' (ignored): {e}")

    def create_signed_url(self, key: str) -> str:
        """Short-lived signed URL for a private object. Server-side use only —
        hand the URL to our own byte-proxying response, never to a client."""
        resp = self._client.storage.from_(self._bucket).create_signed_url(
            key, SIGNED_URL_TTL_SECONDS
        )
        return resp["signedURL"] if isinstance(resp, dict) else resp.signedURL


# Module-level singleton — created once at import, reused across requests
storage_service = StorageService()
