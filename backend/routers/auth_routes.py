from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, hash_password, verify_password
from config import LOGIN_RATE_LIMIT
from database import get_db
from dependencies import get_current_user
from models import User
from schemas import PasswordUpdate, Token, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=UserOut)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where((User.username == user_in.username) | (User.email == user_in.email))
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username or email already registered")

    user = User(
        username=user_in.username,
        email=user_in.email,
        password_hash=hash_password(user_in.password),
        fullname=user_in.fullname,
        role=user_in.role,
        department=user_in.department,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact the librarian.",
        )

    # "sid" = per-login session id. Every login (device/browser) gets a
    # fresh UUID, so the kiosk lock can be scoped to ONE session and can
    # never leak into another session of the same user/role.
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value, "sid": uuid4().hex}
    )
    return Token(access_token=access_token, role=user.role, fullname=user.fullname)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Decodes the caller's JWT and returns their profile. Frontend calls
    this on every page load — the equivalent of the old system's
    GET /api/auth/me — to confirm who's logged in and drive the navbar.
    """
    return current_user


@router.put("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.fullname = payload.fullname
    current_user.email = payload.email
    current_user.contact = payload.contact
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.put("/password")
async def update_password(
    payload: PasswordUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")

    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return {"success": True}