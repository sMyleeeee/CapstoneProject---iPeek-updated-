from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_role
from models import User
from schemas import AskIn, AskOut
from services.repository_qa import (
    RepositoryQARateLimited,
    RepositoryQAUnavailable,
    repository_qa_service,
)

router = APIRouter(prefix="/search", tags=["Repository Search"])


@router.post("/ask", response_model=AskOut)
async def ask_repository(
    payload: AskIn,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    """Single-turn, cited question answering over the whole repository.

    There is no history and no memory: each request is independent.
    """
    try:
        return await repository_qa_service.ask(
            payload.query, payload.college_id, payload.year,
            current_user.id, db,
        )
    except RepositoryQARateLimited:
        raise HTTPException(
            status_code=429,
            detail="Too many repository questions. Please wait a moment and try again.",
        )
    except RepositoryQAUnavailable:
        raise HTTPException(
            status_code=502,
            detail="The answer service is temporarily unavailable. Please try again.",
        )
