import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import generate_password, hash_password
from database import get_db
from dependencies import require_role
from models import (
    AIAnalysis, DeletionLog, Feedback, Notification, Research, ResearchVersion,
    RoleEnum, StatusEnum, User,
)
from schemas import (
    AdminUserCreate, AdminUserOut, DashboardStats, RepositoryStatusOut,
    ResearchOut, UserOut, ValidateResearch,
)
from services.otp_service import determine_role_from_email
from services.storage import sibling_object_key, storage_service
from services.vectorstore import vectorstore_service

router = APIRouter(prefix="/admin", tags=["Admin (Librarian only)"])
logger = logging.getLogger(__name__)

# Notification.message is VARCHAR(255) — keep the quoted title short enough
# that the fixed wording below always fits.
_NOTIFICATION_TITLE_CHARS = 150


def _review_notification(research: Research, approved: bool) -> Notification:
    """Built in the reviewer's transaction so the status change and the
    student's notification are committed together (or not at all)."""
    title = (research.title or "Untitled").strip()[:_NOTIFICATION_TITLE_CHARS]
    if approved:
        message = f'Your research "{title}" was approved.'
    else:
        message = (
            f'Your research "{title}" was returned with feedback. '
            f"Check My Submissions."
        )
    return Notification(
        user_id=research.student_id,
        research_id=research.id,
        message=message,
        status="unread",
    )


async def _delete_research_children(research_id: int, db: AsyncSession) -> None:
    """Detach/remove everything that points at a research row before the row
    itself is deleted.

    feedback / research_versions / ai_analysis have NOT NULL foreign keys, so
    a bare DELETE of the paper would raise instead of cascading. Notifications
    keep their message for the student's history but lose the link
    (research_id -> NULL), which is why that column is nullable.
    """
    await db.execute(delete(Feedback).where(Feedback.research_id == research_id))
    await db.execute(
        delete(ResearchVersion).where(ResearchVersion.research_id == research_id)
    )
    await db.execute(
        delete(AIAnalysis).where(AIAnalysis.research_id == research_id)
    )
    await db.execute(
        update(Notification)
        .where(Notification.research_id == research_id)
        .values(research_id=None)
    )


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    total = await db.scalar(select(func.count()).select_from(Research))
    approved = await db.scalar(select(func.count()).where(Research.status == StatusEnum.approved))
    pending = await db.scalar(select(func.count()).where(Research.status == StatusEnum.pending))
    returned = await db.scalar(select(func.count()).where(Research.status == StatusEnum.returned))

    return DashboardStats(
        total_research=total or 0,
        approved_research=approved or 0,
        ongoing_research=pending or 0,
        rejected_research=returned or 0,
    )


@router.get("/repository/pending", response_model=List[ResearchOut])
async def list_pending_research(
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).where(Research.status == StatusEnum.pending))
    return result.scalars().all()


@router.get("/repository", response_model=List[ResearchOut])
async def manage_repository(
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Research).order_by(Research.created_at.desc()))
    return result.scalars().all()


@router.post("/repository/{research_id}/validate", response_model=ResearchOut)
async def validate_research(
    research_id: int,
    payload: ValidateResearch,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """
    Librarian-only (NOT admin) — matches the deliberate access decision
    from the original Flask system: approve/return is librarian-specific,
    distinct from admin's broader account/repository management.
    """
    result = await db.execute(select(Research).where(Research.id == research_id))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")

    if payload.approve:
        # Approval MOVES the PDF from pending/ to public/ in the bucket,
        # updating research.filepath in the same transaction. Order matters:
        # copy first, commit DB, then remove pending — so a failure never
        # loses the file.
        pending_key = research.filepath  # pending/{safe stem}.pdf
        # Destination is the counterpart of the stored key — never rebuilt
        # from source_stem (the key is sanitized, source_stem is not).
        public_key = sibling_object_key(pending_key)
        if public_key is None:
            raise HTTPException(
                status_code=409,
                detail="Cannot approve — stored document is not in the pending area.",
            )

        # Step 1: copy pending → public (raise if it fails; paper stays pending)
        try:
            await asyncio.to_thread(storage_service.copy, pending_key, public_key)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Approval failed — could not move PDF to public storage: {e}",
            )

        # Step 2: update filepath + status and commit
        research.filepath = public_key
        research.status = StatusEnum.approved
        research.reviewed_by = current_user.id
        if payload.feedback_message:
            research.feedback_note = payload.feedback_message
            db.add(Feedback(
                research_id=research.id,
                librarian_id=current_user.id,
                message=payload.feedback_message,
            ))
        db.add(_review_notification(research, approved=True))
        await db.commit()
        await db.refresh(research)

        # Step 3: remove pending object — failure here is logged but non-fatal
        # (the file is safely in public/ now; orphan cleanup is the fix script's job)
        try:
            await asyncio.to_thread(storage_service.remove, pending_key)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"Approved research {research.id} but failed to remove pending object "
                f"'{pending_key}': {e}. The orphan in pending/ is harmless and can be "
                f"removed manually."
            )
        return research

    # Not approving — returned / rejected
    research.status = StatusEnum.returned
    research.reviewed_by = current_user.id
    if payload.feedback_message:
        research.feedback_note = payload.feedback_message
        db.add(Feedback(
            research_id=research.id,
            librarian_id=current_user.id,
            message=payload.feedback_message,
        ))
    db.add(_review_notification(research, approved=False))
    await db.commit()
    await db.refresh(research)
    return research


@router.get("/repository/status", response_model=RepositoryStatusOut)
async def repository_status(
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Health card for the librarian dashboard: how many chunks are in the
    vector store and how many papers the archive holds.

    chunks_indexed is null (not 0) when ChromaDB cannot be read, so the UI
    can say "unavailable" instead of claiming an empty index — a failed
    read and a genuinely empty store are different problems.
    """
    approved = await db.scalar(
        select(func.count()).where(Research.status == StatusEnum.approved)
    )
    pending = await db.scalar(
        select(func.count()).where(Research.status == StatusEnum.pending)
    )
    returned = await db.scalar(
        select(func.count()).where(Research.status == StatusEnum.returned)
    )

    try:
        chunks = await vectorstore_service.count_chunks()
        status = "ok"
        message = f"{chunks} chunks indexed in the vector store."
    except Exception as e:
        chunks = None
        status = "degraded"
        message = "Vector store is unavailable — search and AI features may fail."
        logger.error(f"Repository status: chunk count failed: {e}")

    return RepositoryStatusOut(
        status=status,
        approved=approved or 0,
        pending=pending or 0,
        returned=returned or 0,
        chunks_indexed=chunks,
        message=message,
    )


@router.delete("/repository/{research_id}")
async def remove_research(
    research_id: int,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Librarian-only, matching the original system's delete restriction."""
    result = await db.execute(select(Research).where(Research.id == research_id))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")

    db.add(DeletionLog(
        source_stem=research.source_stem,
        title=research.title,
        deleted_by=current_user.id,
    ))
    from services.rag import rag_service
    await rag_service.clear_cache_for(research.id, db)   # or research_id inside the bulk loop
    await vectorstore_service.delete_by_source(research.source_stem)

    # Remove every bucket object tied to this paper: the authoritative
    # filepath, its pending/public counterpart (if any), and all version
    # archives. Keys come from the DB (filepath), never rebuilt from
    # source_stem. remove() ignores missing objects.
    keys_to_remove = set()
    if research.filepath:
        keys_to_remove.add(research.filepath)
        sibling = sibling_object_key(research.filepath)
        if sibling:
            keys_to_remove.add(sibling)
    version_result = await db.execute(
        select(ResearchVersion).where(ResearchVersion.research_id == research.id)
    )
    for v in version_result.scalars().all():
        if v.filepath:
            keys_to_remove.add(v.filepath)
    for key in keys_to_remove:
        await asyncio.to_thread(storage_service.remove, key)

    await _delete_research_children(research.id, db)
    await db.delete(research)
    await db.commit()
    return {"detail": "Research removed"}


@router.post("/repository/bulk-delete")
async def bulk_delete_research(
    research_ids: List[int],
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk delete — same guarantees as single delete, applied to each id."""
    deleted, failed = [], []
    for rid in research_ids:
        result = await db.execute(select(Research).where(Research.id == rid))
        research = result.scalar_one_or_none()
        if not research:
            failed.append(rid)
            continue

        db.add(DeletionLog(
            source_stem=research.source_stem,
            title=research.title,
            deleted_by=current_user.id,
        ))
        from services.rag import rag_service
        await rag_service.clear_cache_for(research.id, db)   # or research_id inside the bulk loop
        await vectorstore_service.delete_by_source(research.source_stem)
        keys_to_remove = set()
        if research.filepath:
            keys_to_remove.add(research.filepath)
            sibling = sibling_object_key(research.filepath)
            if sibling:
                keys_to_remove.add(sibling)
        version_result = await db.execute(
            select(ResearchVersion).where(ResearchVersion.research_id == research.id)
        )
        for v in version_result.scalars().all():
            if v.filepath:
                keys_to_remove.add(v.filepath)
        for key in keys_to_remove:
            await asyncio.to_thread(storage_service.remove, key)
        await _delete_research_children(research.id, db)
        await db.delete(research)
        deleted.append(rid)

    await db.commit()
    return {"deleted": deleted, "failed": failed}


async def _get_manageable_user(user_id: int, current_user: User, db: AsyncSession) -> User:
    """Shared guard for user-management actions.

    The target must exist, must be a student/faculty account (librarian
    accounts are never manageable through this UI — no locking out other
    librarians or yourself), and must not be the acting librarian."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == RoleEnum.librarian:
        raise HTTPException(status_code=403, detail="Librarian accounts cannot be managed here.")
    if user.id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot manage your own account here.")
    return user


@router.get("/users", response_model=List[UserOut])
async def manage_users(
    include_inactive: bool = False,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Lists manageable accounts: students AND faculty (librarians never
    listed). Deactivated users are hidden by default but stay retrievable
    via ?include_inactive=true for record lookups. No user rows are ever
    deleted — deactivation is a flag flip only."""
    stmt = select(User).where(User.role.in_([RoleEnum.student, RoleEnum.faculty]))
    if not include_inactive:
        stmt = stmt.where(User.is_active == True)  # noqa: E712
    stmt = stmt.order_by(User.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/users/{user_id}/deactivate", response_model=UserOut)
async def deactivate_user(
    user_id: int,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """SOFT-delete: sets is_active=False. Never issues a SQL DELETE and
    never touches the user's submitted research, reviews, or feedback —
    the submission history stays intact and attributed."""
    user = await _get_manageable_user(user_id, current_user, db)
    user.is_active = False
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}/reactivate", response_model=UserOut)
async def reactivate_user(
    user_id: int,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_manageable_user(user_id, current_user, db)
    user.is_active = True
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def admin_create_user(
    payload: AdminUserCreate,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Creates an account on someone's behalf. No password is accepted or
    chosen here — a cryptographically random one is generated server-side,
    hashed with the same bcrypt path as normal registrations, and returned
    in plaintext exactly ONCE (this response) for manual hand-off. It is
    never logged or emailed. Role stays domain-derived: only student/faculty
    can be created; allowlisted librarian emails are rejected (the single
    librarian account is configured via env, not this UI)."""
    try:
        role = determine_role_from_email(payload.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if role == RoleEnum.librarian:
        raise HTTPException(
            status_code=400,
            detail="Librarian accounts are managed via environment configuration only.",
        )

    result = await db.execute(
        select(User).where(
            or_(
                func.lower(User.username) == payload.email,
                func.lower(User.email) == payload.email,
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username or email already registered")

    generated_password = generate_password()
    user = User(
        username=payload.email,
        email=payload.email,
        password_hash=hash_password(generated_password),
        fullname=payload.fullname,
        role=role,
        department=payload.department,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return AdminUserOut(user=UserOut.model_validate(user), generated_password=generated_password)