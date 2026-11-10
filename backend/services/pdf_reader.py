"""
services/pdf_reader.py
----------------------
Server-side rendering for the view-only PDF reader.

Students never receive the stored PDF file: this service opens the PDF in
memory (bytes fetched from the PRIVATE bucket), stamps every page with a
light diagonal watermark (viewer email + today's date) and renders one page
as a JPEG. The raw bytes stay inside the process, so signed storage URLs and
object keys never reach a response.

A small in-memory LRU caches PDF BYTES only (never rendered images) keyed by
(object_key, row version) so repeated page requests do not re-download the
same object from Supabase Storage.
"""
import asyncio
import logging
import threading
from collections import OrderedDict
from datetime import date
from typing import Optional

import fitz  # PyMuPDF

from config import PDF_READER_CACHE_SIZE, PDF_READER_DPI
from services.storage import storage_service

logger = logging.getLogger(__name__)


class _PDFByteCache:
    """Tiny bounded LRU. Thread-safe because rendering runs in to_thread."""

    def __init__(self, max_entries: int):
        self._max_entries = max(1, max_entries)
        self._items: "OrderedDict[tuple, bytes]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> Optional[bytes]:
        with self._lock:
            data = self._items.get(key)
            if data is not None:
                self._items.move_to_end(key)
            return data

    def put(self, key: tuple, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._items[key] = data
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)


def _watermark(page: "fitz.Page", viewer_email: str) -> None:
    """Stamp a light 45-degree watermark before the page is rasterized.

    The text is drawn into the page content (not a screen overlay), so it is
    baked into the JPEG the viewer receives. A single page cannot be used
    without it.
    """
    text = f"{viewer_email}  {date.today().isoformat()}"
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        return

    fontsize = max(14.0, min(rect.width, rect.height) / 26.0)
    color = (0.78, 0.78, 0.78)
    matrix = fitz.Matrix(1, 1).prerotate(45)

    # A few bands across the page make the watermark hard to crop out while
    # keeping the underlying text readable.
    for row in range(3):
        y = rect.height * (row + 1) / 4.0
        for col in (0.04, 0.52):
            point = fitz.Point(rect.x0 + rect.width * col, y)
            page.insert_text(
                point,
                text,
                fontsize=fontsize,
                color=color,
                morph=(point, matrix),
                fill_opacity=0.35,
            )


def _read_info(pdf_bytes: bytes) -> Optional[dict]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # noqa: BLE001 - malformed PDFs are a client-facing 404
        logger.warning(f"Could not open PDF for reader info: {e}")
        return None

    try:
        if doc.page_count < 1:
            return None
        rect = doc[0].rect
        scale = PDF_READER_DPI / 72.0
        return {
            "page_count": doc.page_count,
            "width": round(rect.width * scale),
            "height": round(rect.height * scale),
            "watermark": True,
        }
    finally:
        doc.close()


def _render_page(pdf_bytes: bytes, page_number: int, viewer_email: str) -> Optional[bytes]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not open PDF for page render: {e}")
        return None

    try:
        if page_number < 1 or page_number > doc.page_count:
            return None
        page = doc[page_number - 1]
        _watermark(page, viewer_email)
        pix = page.get_pixmap(dpi=PDF_READER_DPI, colorspace=fitz.csRGB, alpha=False)
        return pix.tobytes("jpeg", jpg_quality=82)
    finally:
        doc.close()


class PDFReaderService:
    def __init__(self, cache_size: int = PDF_READER_CACHE_SIZE):
        self._cache = _PDFByteCache(cache_size)

    @staticmethod
    def cache_key(object_key: str, version=None) -> tuple:
        return (object_key, "" if version is None else str(version))

    async def get_document(self, object_key: str, version=None) -> bytes:
        """Return PDF bytes from the LRU, else download them server-side."""
        key = self.cache_key(object_key, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        data = await asyncio.to_thread(storage_service.download, object_key)
        if data:
            self._cache.put(key, data)
        return data or b""

    async def get_info(self, object_key: str, version=None) -> Optional[dict]:
        data = await self.get_document(object_key, version)
        if not data:
            return None
        return await asyncio.to_thread(_read_info, data)

    async def render_page(
        self, object_key: str, page_number: int, viewer_email: str, version=None
    ) -> Optional[bytes]:
        data = await self.get_document(object_key, version)
        if not data:
            return None
        return await asyncio.to_thread(_render_page, data, page_number, viewer_email)


pdf_reader_service = PDFReaderService()
