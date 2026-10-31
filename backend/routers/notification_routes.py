"""
routers/notification_routes.py
------------------------------
In-app notifications for the person who submitted a paper (approve / return
outcomes). Rows are created inside the SAME transaction as the status change
in admin_routes.validate_research, so a paper can never end up approved with
no notification (or the reverse).

Every query filters on user_id == the caller: ownership is part of the WHERE
clause, never an after-the-fact check, so one user's notification ids are
indistinguishable from ids that do not exist.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_user
from models import Notification, User
from schemas import NotificationListOut, NotificationOut

router = APIRouter(prefix="/notifications", tags=["Notifications"])

UNREAD = "unread"
READ = "read"


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Newest first.

    unread_count counts ALL of the caller's unread rows (ignoring `limit`
    and `unread_only`), so the bell badge never depends on how many items
    were fetched.
    """
    unread_count = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == current_user.id,
            Notification.status != READ,
        )
    )

    stmt = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        stmt = stmt.where(Notification.status != READ)
    result = await db.execute(
        stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    return NotificationListOut(
        unread_count=unread_count or 0,
        items=[NotificationOut.model_validate(n) for n in result.scalars().all()],
    )


@router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.status = READ
    await db.commit()
    await db.refresh(notification)
    return notification


@router.post("/read-all")
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Idempotent: re-running it reports 0 updated."""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == current_user.id,
            Notification.status != READ,
        )
        .values(status=READ)
    )
    await db.commit()
    return {"updated": result.rowcount or 0}
