from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_role
from models import Research, User
from schemas import ChatIn, ChatOut, RefineIn
from services.rag import rag_service

router = APIRouter(prefix="/ai", tags=["AI Analysis"])


async def _get_research_or_404(research_id: int, db: AsyncSession) -> Research:
    result = await db.execute(select(Research).where(Research.id == research_id))
    research = result.scalar_one_or_none()
    if not research:
        raise HTTPException(status_code=404, detail="Research not found")
    return research


@router.post("/{research_id}/similarity", response_model=ChatOut)
async def analyze_similarity(
    research_id: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    research = await _get_research_or_404(research_id, db)
    query = f"{research.title} {research.abstract or ''}"
    return await rag_service.get_similar_studies(query, research_id, db)


@router.post("/{research_id}/summary", response_model=ChatOut)
async def analyze_summary(
    research_id: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    research = await _get_research_or_404(research_id, db)
    query = f"{research.title} {research.abstract or ''}"
    return await rag_service.get_summary(query, research_id, db)


@router.post("/{research_id}/gaps", response_model=ChatOut)
async def analyze_gaps(
    research_id: int,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    research = await _get_research_or_404(research_id, db)
    query = f"{research.title} {research.abstract or ''}"
    return await rag_service.get_research_gaps(query, research_id, db)


@router.post("/{research_id}/refine", response_model=ChatOut)
async def refine_analysis(
    research_id: int,
    payload: RefineIn,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    research = await _get_research_or_404(research_id, db)
    refined = f"{research.title} {research.abstract or ''} User focus: {payload.query}"
    return await rag_service.refine(refined, payload.target, research_id, db)


@router.post("/chat", response_model=ChatOut)
async def chat(
    payload: ChatIn,
    current_user: User = Depends(require_role("student", "librarian")),
    db: AsyncSession = Depends(get_db),
):
    return await rag_service.chat(payload.question, payload.history or [], db)