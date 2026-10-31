from typing import List, Optional
from pathlib import Path

import asyncio

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_UPLOAD_SIZE, STAGING_DIR
from database import get_db
from dependencies import get_current_sid, is_kiosk_session, require_role
from models import College, Feedback, Research, RoleEnum, StatusEnum, User
from schemas import (
    FeedbackOut, RepositoryFiltersOut, ResearchOut, UploadConfirmIn,
    UploadPreviewOut,
)
from services.colleges import resolve_college
from services.ingestor import ingestor
from services.storage import storage_service
from fastapi.responses import FileResponse

router = APIRouter(prefix="/repository", tags=["Repository"])


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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
    sid: str | None = Depends(get_current_sid),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.source_stem == source_stem))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Document not available for viewing.")

    is_owner = research.student_id == current_user.id
    is_librarian = current_user.role.value == "librarian"
    is_approved = research.status == StatusEnum.approved

    # Privacy rule: full PDFs of APPROVED research are only served to
    #  - Library Viewing Station sessions (kiosk-activated sid),
    #  - librarians (review workflow needs every status),
    #  - the uploader (their own paper, any status — My Submissions page),
    #  - remote viewers, but ONLY if the author gave submission-time
    #    consent (public_pdf_consent == True).
    # Remote viewers without consent get the IMRaD summary on the detail
    # page instead (POST /ai/{id}/imrad). The kiosk path is unaffected by
    # consent — in-person supervised viewing always sees the full PDF.
    remote_ok = is_approved and (
        research.public_pdf_consent or await is_kiosk_session(sid, db)
    )
    if not (is_owner or is_librarian or remote_ok):
        raise HTTPException(
            status_code=403,
            detail="Full document viewing is only available at the Library Viewing Station.",
        )

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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
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

    # Librarians are not tied to a college; a student/faculty member's own
    # department resolves to a college only when the text matches a
    # canonical name (unmatched free text -> null, never an error).
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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
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
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Research).where(Research.status == StatusEnum.approved)
    if q:
        query = query.where(or_(Research.title.ilike(f"%{q}%"), Research.authors.ilike(f"%{q}%")))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{source_stem}/detail", response_model=ResearchOut)
async def get_research_detail(
    source_stem: str,
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.source_stem == source_stem))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")
    return research


@router.get("/my-uploads", response_model=List[ResearchOut])
async def my_uploads(
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.student_id == current_user.id))
    return result.scalars().all()


@router.get("/{research_id}/feedback", response_model=List[FeedbackOut])
async def check_feedback(
    research_id: int,
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
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