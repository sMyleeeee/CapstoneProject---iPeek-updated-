import threading
import time
from collections import deque
from typing import List, Optional
from pathlib import Path

import asyncio

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    ENFORCE_PDF_CONSENT, MAX_UPLOAD_SIZE, PDF_READER_RATE_LIMIT, STAGING_DIR,
)
from database import get_db
from dependencies import require_role
from models import College, Feedback, Research, RoleEnum, StatusEnum, User
from schemas import (
    CollegeRefOut, FeedbackOut, ReaderInfoOut, RepositoryFiltersOut,
    ResearchDetailOut, ResearchOut, UploadConfirmIn, UploadPreviewOut,
)
from services.colleges import resolve_college
from services.ingestor import ingestor
from services.pdf_reader import pdf_reader_service
from services.storage import storage_service

router = APIRouter(prefix="/repository", tags=["Repository"])

# ── View-only reader helpers ────────────────────────────────────────────────
# Per-user sliding window: pages rendered per minute. Kept in-process
# (single local backend) so one student cannot exhaust the library's CPU.
_PAGE_WINDOW_SECONDS = 60
_page_hits: "dict[int, deque[float]]" = {}
_page_lock = threading.Lock()

_ACCESS_MESSAGES = {
    "not_approved": "This paper is not approved for public viewing.",
    "not_consented": "The author has not allowed public viewing of this paper.",
}


def _pdf_view_access(research: Research, current_user: User) -> tuple[bool, Optional[str]]:
    """Owner and librarians always; everyone else only an APPROVED paper
    whose author allowed public viewing (unless the consent gate is off)."""
    if current_user.role == RoleEnum.librarian:
        return True, None
    if research.student_id == current_user.id:
        return True, None
    if research.status != StatusEnum.approved:
        return False, "not_approved"
    if ENFORCE_PDF_CONSENT and not research.public_pdf_consent:
        return False, "not_consented"
    return True, None


def _pdf_forbidden(reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"detail": _ACCESS_MESSAGES[reason], "reason": reason},
    )


def _page_rate_limited(user_id: int) -> bool:
    limit = PDF_READER_RATE_LIMIT
    if limit <= 0:
        return False
    now = time.monotonic()
    cutoff = now - _PAGE_WINDOW_SECONDS
    with _page_lock:
        window = _page_hits.setdefault(user_id, deque())
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            return True
        window.append(now)
        return False


async def _reader_page_count(research: Research) -> Optional[int]:
    info = await pdf_reader_service.get_info(research.filepath, research.updated_at)
    return info["page_count"] if info else None


async def _research_detail_out(
    research: Research, current_user: User, db: AsyncSession,
) -> ResearchDetailOut:
    allowed, reason = _pdf_view_access(research, current_user)
    page_count = await _reader_page_count(research) if allowed else None

    college = None
    if research.college_id is not None:
        row = await db.execute(select(College).where(College.id == research.college_id))
        c = row.scalar_one_or_none()
        if c:
            college = CollegeRefOut(id=c.id, code=c.code, name=c.name)

    return ResearchDetailOut(
        **ResearchOut.model_validate(research).model_dump(),
        college=college,
        can_view_pdf=allowed,
        pdf_block_reason=reason,
        page_count=page_count,
    )


async def _load_research(research_id: int, db: AsyncSession) -> Research:
    result = await db.execute(select(Research).where(Research.id == research_id))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")
    return research


async def _stream_private_pdf(object_key: str) -> Response:
    """Generate a short-lived signed URL for a PRIVATE bucket object and
    proxy its bytes through this authenticated endpoint.

    The signed URL is server-side only: the client keeps hitting our
    same-origin /repository/... routes with its JWT, so nothing about the
    consent/ownership gating changes and the URL itself is never exposed.
    """
    signed_url = await asyncio.to_thread(
        storage_service.create_signed_url, object_key
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(signed_url)
    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Document not available for viewing.")
    return Response(
        content=resp.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{Path(object_key).name}"'},
    )


@router.get("/upload/preview/{preview_id}/pdf")
async def get_preview_pdf(
    preview_id: str,
    current_user: User = Depends(require_role("student", "librarian")),
):
    """
    Serves the STAGED file for a not-yet-confirmed upload, so the
    uploader can open/verify the actual PDF (not just the AI-extracted
    metadata) before confirming. preview_id is a random UUID, and the
    staged file is deleted automatically once confirmed or after it
    expires (PREVIEW_MAX_AGE_SECONDS) - there is no Research row yet
    at this point, so this can't be gated by ownership the way the
    real /{source_stem}/pdf route is.
    """
    path = STAGING_DIR / f"{preview_id}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preview file not found or has expired.")
    return FileResponse(str(path), media_type="application/pdf")


@router.get("/{source_stem}/pdf")
async def get_pdf(
    source_stem: str,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Raw PDF bytes for librarian review only.

    Students must never receive the stored file (the adviser's decision is
    view-only). They use /repository/{id}/reader, which serves watermarked
    page images instead. Owners are no exception: My Submissions uses the
    same reader.
    """
    if current_user.role != RoleEnum.librarian:
        raise HTTPException(
            status_code=403,
            detail=(
                "Raw PDF downloads are librarian-only. "
                "Use the view-only reader instead."
            ),
        )

    result = await db.execute(select(Research).where(Research.source_stem == source_stem))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Document not available for viewing.")

    # filepath is authoritative: pending/{stem}.pdf for non-approved,
    # public/{stem}.pdf for approved (approval MOVES the object and updates
    # filepath in the same operation).
    return await _stream_private_pdf(research.filepath)

@router.post("/upload/preview", response_model=UploadPreviewOut)
async def upload_preview(
    file: UploadFile = File(...),
    title: str = Form(""),
    department: str = Form(""),
    year: str = Form(""),
    members: str = Form(""),
    abstract: str = Form(""),
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await ingestor.preview_upload(
            file, {"title": title, "department": department, "year": year, "members": members, "abstract": abstract}, db,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload/confirm")
async def upload_confirm(
    payload: UploadConfirmIn,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ingestor.confirm_upload(
            payload.preview_id, payload.final_values, payload.public_pdf_consent,
            current_user.id, db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/filters", response_model=RepositoryFiltersOut)
async def repository_filters(
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Everything the browse page needs to build its filter controls in one
    call: the years that actually exist among approved papers (newest
    first) and every canonical college with its approved count.

    Only APPROVED papers are counted — the browse list is approved-only.
    Colleges with no approved papers still appear (count 0) so the control
    does not change shape as the archive grows.
    """
    year_rows = (await db.execute(
        select(Research.year)
        .where(Research.status == StatusEnum.approved, Research.year.is_not(None))
        .distinct()
    )).scalars().all()
    years = sorted({int(y) for y in year_rows if y is not None}, reverse=True)

    count_rows = (await db.execute(
        select(Research.college_id, func.count())
        .where(Research.status == StatusEnum.approved)
        .group_by(Research.college_id)
    )).all()
    counts = {cid: n for cid, n in count_rows if cid is not None}

    college_rows = (await db.execute(
        select(College).order_by(College.name)
    )).scalars().all()
    colleges = [
        {"id": c.id, "code": c.code, "name": c.name, "count": counts.get(c.id, 0)}
        for c in college_rows
    ]

    # Librarians are not tied to a college; a student's own department
    # resolves to a college only when the text matches a canonical name
    # (unmatched free text -> null, never an error).
    department = None if current_user.role == RoleEnum.librarian else current_user.department
    own_id = resolve_college(department)
    own_college = next((c for c in colleges if c["id"] == own_id), None)

    return RepositoryFiltersOut(
        years=years,
        latest_year=years[0] if years else None,
        colleges=colleges,
        user_college=own_college,
    )


@router.get("/browse", response_model=List[ResearchOut])
async def browse_repository(
    college_id: Optional[int] = None,
    year: Optional[int] = None,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Approved papers only, optionally narrowed by college and/or year
    (the two filters advertised by GET /repository/filters)."""
    query = select(Research).where(Research.status == StatusEnum.approved)
    if college_id is not None:
        query = query.where(Research.college_id == college_id)
    if year is not None:
        query = query.where(Research.year == year)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/search", response_model=List[ResearchOut])
async def search_research(
    q: Optional[str] = None,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Research).where(Research.status == StatusEnum.approved)
    if q:
        query = query.where(or_(Research.title.ilike(f"%{q}%"), Research.authors.ilike(f"%{q}%")))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{source_stem}/detail", response_model=ResearchDetailOut)
async def get_research_detail(
    source_stem: str,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.source_stem == source_stem))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")
    return await _research_detail_out(research, current_user, db)


@router.get("/my-uploads", response_model=List[ResearchOut])
async def my_uploads(
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.student_id == current_user.id))
    return result.scalars().all()


@router.get("/{research_id}/feedback", response_model=List[FeedbackOut])
async def check_feedback(
    research_id: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Research).where(Research.id == research_id, Research.student_id == current_user.id)
    )
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")

    fb_result = await db.execute(select(Feedback).where(Feedback.research_id == research_id))
    return fb_result.scalars().all()


# ── View-only PDF reader ───────────────────────────────────────────────────
# IMPORTANT: these routes never return `research.filepath`, a signed URL or
# any storage identifier — only dimensions, a page count and JPEG bytes.

@router.get("/{research_id}", response_model=ResearchDetailOut)
async def get_research_by_id(
    research_id: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Detail by numeric id, including the reader gate."""
    research = await _load_research(research_id, db)
    return await _research_detail_out(research, current_user, db)


@router.get("/{research_id}/reader", response_model=ReaderInfoOut)
async def get_reader_info(
    research_id: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    research = await _load_research(research_id, db)
    allowed, reason = _pdf_view_access(research, current_user)
    if not allowed:
        return _pdf_forbidden(reason)

    info = await pdf_reader_service.get_info(research.filepath, research.updated_at)
    if not info:
        raise HTTPException(status_code=404, detail="Document not available for viewing.")
    return ReaderInfoOut(**info)


@router.get("/{research_id}/reader/pages/{page_number}")
async def get_reader_page(
    research_id: int,
    page_number: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    research = await _load_research(research_id, db)
    allowed, reason = _pdf_view_access(research, current_user)
    if not allowed:
        return _pdf_forbidden(reason)

    if _page_rate_limited(current_user.id):
        raise HTTPException(
            status_code=429,
            detail="Too many page requests. Please slow down.",
            headers={"Retry-After": str(_PAGE_WINDOW_SECONDS)},
        )

    info = await pdf_reader_service.get_info(research.filepath, research.updated_at)
    if not info:
        raise HTTPException(status_code=404, detail="Document not available for viewing.")
    if page_number < 1 or page_number > info["page_count"]:
        raise HTTPException(status_code=404, detail="Page out of range.")

    jpeg = await pdf_reader_service.render_page(
        research.filepath, page_number, current_user.email, research.updated_at
    )
    if not jpeg:
        raise HTTPException(status_code=404, detail="Page not available.")

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'inline; filename="page-{page_number}.jpg"',
            "X-Content-Type-Options": "nosniff",
        },
    )