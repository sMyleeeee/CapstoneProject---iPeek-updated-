from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_UPLOAD_SIZE, PUBLIC_DIR, STAGING_DIR
from database import get_db
from dependencies import get_current_sid, is_kiosk_session, require_role
from models import Feedback, Research, StatusEnum, User
from schemas import FeedbackOut, ResearchOut, UploadConfirmIn, UploadPreviewOut
from services.ingestor import ingestor
from fastapi.responses import FileResponse

router = APIRouter(prefix="/repository", tags=["Repository"])


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
    #  - the uploader (their own paper, any status — My Submissions page).
    # Remote viewers of other people's approved papers get an IMRaD
    # summary on the detail page instead (POST /ai/{id}/imrad).
    if not (is_owner or is_librarian or (is_approved and await is_kiosk_session(sid, db))):
        raise HTTPException(
            status_code=403,
            detail="Full document viewing is only available at the Library Viewing Station.",
        )

    if is_approved:
        path = PUBLIC_DIR / f"{source_stem}.pdf"
    else:
        # Approval only COPIES the file to PUBLIC_DIR; the original
        # always still exists at research.filepath (PENDING_DIR),
        # regardless of status, so this works for pending and returned.
        path = Path(research.filepath)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Document not available for viewing.")
    return FileResponse(str(path), media_type="application/pdf")

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
        return await ingestor.confirm_upload(payload.preview_id, payload.final_values, current_user.id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/browse", response_model=List[ResearchOut])
async def browse_repository(
    current_user: User = Depends(require_role("student", "faculty", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.status == StatusEnum.approved))
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