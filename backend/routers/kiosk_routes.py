"""
Session-scoped Library Viewing Station (kiosk) state.

Every self-service endpoint operates ONLY on the caller's own JWT "sid"
(per-login UUID) — there is no global flag and no role-keyed state, so
activating a lock on one session can never be observed by any other
session. The dashboard endpoints let a librarian lock/unlock a specific
kiosk session by its sid (targeted, never "all sessions").
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_sid, get_current_user, require_role
from models import KioskSession, User

router = APIRouter(prefix="/kiosk", tags=["Kiosk"])


async def _get_own_session(sid: str | None, db: AsyncSession) -> KioskSession | None:
    if not sid:
        return None
    result = await db.execute(select(KioskSession).where(KioskSession.sid == sid))
    return result.scalar_one_or_none()


async def _upsert_own(sid: str | None, user: User, db: AsyncSession, **fields) -> dict:
    if not sid:
        raise HTTPException(
            status_code=401,
            detail="This login has no session ID. Please sign in again.",
        )
    session_row = await _get_own_session(sid, db)
    if not session_row:
        session_row = KioskSession(sid=sid, user_id=user.id, **fields)
        db.add(session_row)
    else:
        for key, value in fields.items():
            setattr(session_row, key, value)
    await db.commit()
    return session_row


@router.post("/activate")
async def activate_kiosk(
    current_user: User = Depends(get_current_user),
    sid: str | None = Depends(get_current_sid),
    db: AsyncSession = Depends(get_db),
):
    """Called by library.html on load: marks THIS login session as a
    Library Viewing Station session (unlocks approved-PDF access for it)."""
    await _upsert_own(sid, current_user, db, is_kiosk=True)
    return {"ok": True}


@router.get("/status")
async def kiosk_status(
    current_user: User = Depends(get_current_user),
    sid: str | None = Depends(get_current_sid),
    db: AsyncSession = Depends(get_db),
):
    """Lock state of the CALLER's own session. A token with no sid can
    never be locked (no row), so returning unlocked for it is correct."""
    session_row = await _get_own_session(sid, db)
    return {
        "is_kiosk": bool(session_row and session_row.is_kiosk),
        "locked": bool(session_row and session_row.locked),
    }


@router.post("/lock")
async def lock_own(
    current_user: User = Depends(get_current_user),
    sid: str | None = Depends(get_current_sid),
    db: AsyncSession = Depends(get_db),
):
    """Locks the caller's own session to library.html (kiosk page button)."""
    await _upsert_own(sid, current_user, db, is_kiosk=True, locked=True, locked_by=current_user.id)
    return {"ok": True, "locked": True}


@router.post("/unlock")
async def unlock_own(
    current_user: User = Depends(get_current_user),
    sid: str | None = Depends(get_current_sid),
    db: AsyncSession = Depends(get_db),
):
    """Unlocks the caller's own session (same control as lock)."""
    session_row = await _get_own_session(sid, db)
    if session_row:
        session_row.locked = False
        session_row.locked_by = None
        await db.commit()
    return {"ok": True, "locked": False}


@router.get("/sessions")
async def list_kiosk_sessions(
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Librarian-only: kiosk-activated sessions, for the dashboard
    lock/unlock control. Lists rows — never mutates anything else."""
    result = await db.execute(select(KioskSession))
    rows = result.scalars().all()
    sessions = []
    for row in rows:
        user_result = await db.execute(select(User).where(User.id == row.user_id))
        user = user_result.scalar_one_or_none()
        sessions.append({
            "sid": row.sid,
            "user": user.fullname if user else "Unknown",
            "role": user.role.value if user else "unknown",
            "is_kiosk": bool(row.is_kiosk),
            "locked": bool(row.locked),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    return {"sessions": sessions}


@router.post("/sessions/{target_sid}/lock")
async def lock_session(
    target_sid: str,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KioskSession).where(KioskSession.sid == target_sid))
    session_row = result.scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found.")
    session_row.locked = True
    session_row.locked_by = current_user.id
    await db.commit()
    return {"ok": True, "locked": True}


@router.post("/sessions/{target_sid}/unlock")
async def unlock_session(
    target_sid: str,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(KioskSession).where(KioskSession.sid == target_sid))
    session_row = result.scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found.")
    session_row.locked = False
    session_row.locked_by = None
    await db.commit()
    return {"ok": True, "locked": False}


@router.post("/deactivate")
async def deactivate_own(
    current_user: User = Depends(get_current_user),
    sid: str | None = Depends(get_current_sid),
    db: AsyncSession = Depends(get_db),
):
    """Called by library.html on pagehide: removes THIS session's row so
    a closed Library Terminal disappears from the dashboard tracker.
    Idempotent — closing a terminal twice (or one with no row) is not an
    error. Any lock held by the row is lifted along with it."""
    session_row = await _get_own_session(sid, db)
    if not session_row:
        return {"ok": True, "removed": False}
    await db.delete(session_row)
    await db.commit()
    return {"ok": True, "removed": True}


@router.delete("/sessions/{target_sid}")
async def terminate_session(
    target_sid: str,
    current_user: User = Depends(require_role("librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Librarian-only: removes a terminal session from the tracker (and
    lifts its lock). Intended for closed/stale Library Terminal rows."""
    result = await db.execute(select(KioskSession).where(KioskSession.sid == target_sid))
    session_row = result.scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found.")
    await db.delete(session_row)
    await db.commit()
    return {"ok": True, "removed": True}
