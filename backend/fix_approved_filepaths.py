"""
fix_approved_filepaths.py
-------------------------
One-off repair script for the pre-fix data where approval copied pending/X
to public/X but left research.filepath pointing at pending/X (and left the
pending/ object orphaned in the bucket).

For each approved paper whose filepath starts with "pending/":
  1. Prints exactly what it will do (copy pending→public if public/ missing,
     update filepath, delete orphaned pending/ object).
  2. Asks for confirmation before touching anything.

Also scans the bucket for orphaned pending/ objects whose approved paper
already has a public/ copy, and offers to delete them.

Run from the backend folder (venv activated):
    python fix_approved_filepaths.py
"""
import asyncio
import sys

from sqlalchemy import select

from database import AsyncSessionLocal, init_db
from models import Research, StatusEnum
from services.storage import storage_service


async def main():
    await init_db()
    bucket = storage_service._bucket
    client = storage_service._client.storage.from_(bucket)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Research).where(Research.status == StatusEnum.approved)
        )
        approved = result.scalars().all()

    # ── Plan 1: fix approved papers whose filepath still says pending/ ──
    fixes = []       # (research_id, stem, pending_key, public_key, pending_exists, public_exists)
    orphans = []     # pending keys whose paper already has public/ copy and filepath=public/

    for paper in approved:
        if paper.filepath.startswith("pending/"):
            stem = paper.source_stem
            pending_key = storage_service.pending_key(stem)
            public_key = storage_service.public_key(stem)
            pending_exists = storage_service.exists(pending_key)
            public_exists = storage_service.exists(public_key)
            fixes.append((paper.id, stem, pending_key, public_key, pending_exists, public_exists))
        else:
            # filepath already public/ — check for orphaned pending object
            stem = paper.source_stem
            pending_key = storage_service.pending_key(stem)
            if storage_service.exists(pending_key):
                orphans.append((paper.id, stem, pending_key))

    # ── Plan 2: orphaned pending/ objects for papers with no approved status
    #    (covered above for approved; non-approved papers legitimately have pending/ filepath)

    print(f"\nFound {len(fixes)} approved papers with filepath still pointing at pending/:")
    for rid, stem, pk, pub, p_exists, pub_exists in fixes:
        actions = []
        if not pub_exists and p_exists:
            actions.append(f"copy {pk} → {pub}")
        elif not p_exists and not pub_exists:
            actions.append(f"WARNING: neither pending/ nor public/ object exists — filepath dangles, will set to {pub}")
        elif p_exists and pub_exists:
            actions.append(f"copy {pk} → {pub} (will overwrite existing public copy)")
        actions.append(f"set research.filepath = {pub}")
        if p_exists:
            actions.append(f"delete orphaned {pk}")
        print(f"  [{rid}] {stem}: " + "; ".join(actions))

    print(f"\nFound {len(orphans)} orphaned pending/ objects (approved paper already has public/ filepath):")
    for rid, stem, pk in orphans:
        print(f"  [{rid}] {stem}: delete {pk}")

    if not fixes and not orphans:
        print("Nothing to fix — all approved papers have correct public/ filepaths and no orphans.")
        return

    answer = input(f"\nApply these changes? (yes/no): ").strip().lower()
    if answer != "yes":
        print("Aborted — no changes made.")
        return

    fixed = orphan_removed = 0
    for rid, stem, pk, pub, p_exists, pub_exists in fixes:
        # Step 1: copy pending → public (only if pending object still exists)
        if p_exists:
            await asyncio.to_thread(storage_service.copy, pk, pub)
        # Step 2: update filepath in DB
        async with AsyncSessionLocal() as db:
            paper = await db.get(Research, rid)
            paper.filepath = pub
            await db.commit()
        # Step 3: remove orphaned pending object (non-fatal if it fails)
        if p_exists:
            try:
                await asyncio.to_thread(storage_service.remove, pk)
            except Exception as e:
                print(f"  WARNING: could not remove {pk}: {e} (file is safely in public/)")
        fixed += 1
        print(f"  Fixed [{rid}] {stem}")

    for rid, stem, pk in orphans:
        await asyncio.to_thread(storage_service.remove, pk)
        orphan_removed += 1
        print(f"  Removed orphan [{rid}] {stem}: {pk}")

    print(f"\nDone: {fixed} papers fixed, {orphan_removed} orphaned pending objects removed.")


if __name__ == "__main__":
    asyncio.run(main())
