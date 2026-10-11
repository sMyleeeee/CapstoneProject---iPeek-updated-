"""
services/storage.py
-------------------
Supabase Storage access wrapped as a class, per the project's OOP requirement.

Replaces the old local papers/ directories (pending/, public/, versions/) with
object keys in the EXISTING private Supabase Storage bucket named "papers":

    pending/{source_stem}.pdf     <- canonical copy written at confirm_upload
    public/{source_stem}.pdf      <- copy made when a librarian approves
    versions/{stem}_v{n}.pdf      <- old pending copy archived on resubmission

Access rules:
- The bucket stays PRIVATE. PDFs are never served from a public URL.
- The service-role key (SUPABASE_SECRET_KEY) is used ONLY server-side:
  never sent to the frontend, never returned in an API response, never logged.
- For viewing/downloading, callers ask for a short-lived SIGNED URL that is
  generated here and consumed server-side (the bytes are proxied through our
  own authenticated endpoints) — the signed URL itself never reaches a client.
"""
import logging

from supabase import Client, create_client

from config import SUPABASE_URL, SUPABASE_SECRET_KEY, STORAGE_BUCKET

logger = logging.getLogger(__name__)

# Signed URLs only ever live server-side for the duration of one proxied
# download, so a short TTL is plenty.
SIGNED_URL_TTL_SECONDS = 300


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
        return f"pending/{stem}.pdf"

    @staticmethod
    def public_key(stem: str) -> str:
        return f"public/{stem}.pdf"

    @staticmethod
    def version_key(stem: str, version_number: int) -> str:
        return f"versions/{stem}_v{version_number}.pdf"

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
