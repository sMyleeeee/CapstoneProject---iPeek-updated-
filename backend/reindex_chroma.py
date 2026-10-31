"""
reindex_chroma.py
-----------------
MANUAL, one-time-per-deploy script — NOT part of the FastAPI app and never
triggered on startup (that would reintroduce the startup memory spike that
lazy loading already fixed).

Rebuilds the local ChromaDB index from what actually persists in Supabase:
for every APPROVED paper in the database it downloads the PDF from the
private Supabase Storage bucket, extracts text, and re-embeds using the
EXACT same ingestion pipeline as a normal upload (Ingestor.build_documents
+ Ingestor.index_documents — no duplicated logic).

Idempotent: each paper's chunks are delete-then-add by source stem, so
running it twice never creates duplicate entries.

Run from the backend folder (venv activated), after a fresh deploy wiped
the local chroma_db/ directory:

    python reindex_chroma.py
"""
import asyncio
import logging

from sqlalchemy import select

from database import AsyncSessionLocal, init_db
from models import Research, StatusEnum
from services.ingestor import ingestor
from services.storage import storage_service
from services.vectorstore import vectorstore_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reindex")


async def reindex() -> None:
    await init_db()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Research).where(Research.status == StatusEnum.approved)
        )
        papers = result.scalars().all()

    total = len(papers)
    if total == 0:
        logger.info(
            "No approved papers found in the database — nothing to re-embed. "
            "(Expected right after a clean-slate migration.)"
        )
        return

    logger.info(f"Found {total} approved papers. Starting ChromaDB re-embed...")
    done = failed = 0

    for paper in papers:
        try:
            pdf_bytes = await asyncio.to_thread(storage_service.download, paper.filepath)
            if not pdf_bytes:
                raise ValueError(f"PDF object not found in Storage: {paper.filepath}")

            pages = await asyncio.to_thread(ingestor._extract_pages, pdf_bytes)
            if not pages:
                raise ValueError("No extractable text in PDF (scanned/image-only?).")

            meta = {
                "title": paper.title, "authors": paper.authors,
                "year_normalized": paper.year, "college": paper.department,
                "keywords": "Unknown", "abstract": paper.abstract or "",
            }
            documents = await asyncio.to_thread(
                ingestor.build_documents, pages, meta, paper.source_stem
            )
            if not await ingestor.index_documents(paper.source_stem, documents):
                raise ValueError("Vector indexing failed (see logs above).")

            done += 1
            logger.info(f"Re-embedded {done}/{total}: {paper.title}")
        except Exception as e:
            failed += 1
            logger.error(f"FAILED ({done + failed}/{total}): '{paper.title}' — {e}")

    chunk_count = await vectorstore_service.get_chunk_count()
    logger.info(
        f"Done. Re-embedded {done}/{total} papers ({failed} failed). "
        f"ChromaDB now holds {chunk_count} chunks."
    )


if __name__ == "__main__":
    asyncio.run(reindex())
