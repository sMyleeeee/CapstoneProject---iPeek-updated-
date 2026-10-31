import logging
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, generate_password, hash_password, verify_password
from config import LOGIN_RATE_LIMIT
from database import get_db
from dependencies import get_current_user
from models import College, RoleEnum, User
from schemas import (
    CollegeRefOut, OTPRequest, OTPVerify, PasswordResetConfirm,
    PasswordResetRequest, PasswordUpdate, Token, UserCreate, UserOut, UserUpdate,
)
from services.colleges import (
    normalize_department, resolve_college, resolve_college_code,
    resolve_college_name,
)
from services.otp_service import determine_role_from_email, otp_service

router = APIRouter(prefix="/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

# Identical for unknown, deactivated and existing accounts so the endpoint
# can never be used to discover which addresses have an account.
RESET_REQUEST_MESSAGE = "If the account exists, a code was sent."


@router.get("/colleges", response_model=list[CollegeRefOut])
@limiter.limit(LOGIN_RATE_LIMIT)
async def list_colleges(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public list of the canonical colleges (id, code, name) — powers the
    required college dropdown on the registration pane.

    No auth and no counts: every seeded college is returned, sorted by name,
    so the client can show the choices BEFORE the OTP flow starts. This is
    the same registry `register` resolves `department` against."""
    rows = (await db.execute(select(College).order_by(College.name))).scalars().all()
    return [CollegeRefOut(id=c.id, code=c.code, name=c.name) for c in rows]


@router.post("/register/request-otp")
@limiter.limit(LOGIN_RATE_LIMIT)
async def request_otp(
    payload: OTPRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 1 of registration: validate the institutional email and send
    a 6-digit OTP. No account is created here and no password is chosen
    anywhere — role and password are both decided server-side later."""
    try:
        await otp_service.request_otp(payload.email, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Verification code sent."}


@router.post("/register/verify-otp")
@limiter.limit(LOGIN_RATE_LIMIT)
async def verify_otp(
    payload: OTPVerify,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Step 2 of registration: check the OTP code. Marks the email as
    verified — the account itself is created by POST /register, which
    generates and emails the password in the same request."""
    try:
        await otp_service.verify_otp(payload.email, payload.code, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Email verified."}


@router.post("/register", response_model=UserOut)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    """Step 3 of registration: creates the account for a VERIFIED email.
    There is no client-supplied password (any sent "password" field is
    ignored by the schema) and no client-supplied role — the password is
    generated server-side at this moment, hashed with bcrypt, and the
    plaintext is emailed to the verified address exactly once. The
    plaintext is never returned in the response and never stored.

    Students and faculty MUST pick a known college: `department` has to
    resolve to one of the seeded colleges (GET /auth/colleges) or the
    request is a 400. Librarians are not tied to a college and are exempt.
    """
    try:
        role = determine_role_from_email(user_in.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validated BEFORE the OTP is touched, so a bad/missing college never
    # consumes a verified email. The stored value is always the canonical
    # college NAME — free text is never persisted for students/faculty.
    if role == RoleEnum.librarian:
        department = normalize_department(user_in.department)
    else:
        department = resolve_college_name(user_in.department)
        if not department:
            raise HTTPException(
                status_code=400,
                detail="Please select a valid college from the list.",
            )

    if not await otp_service.is_email_verified(user_in.email, db):
        raise HTTPException(
            status_code=400,
            detail="Email not verified. Please enter the code we sent you.",
        )

    result = await db.execute(
        select(User).where(
            or_(
                func.lower(User.username) == user_in.email,
                func.lower(User.email) == user_in.email,
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username or email already registered")

    # Generated HERE, after verification — never at step 1, never stored
    # in plaintext. Only the bcrypt hash is persisted on the user row.
    password = generate_password()
    user = User(
        username=user_in.email,
        email=user_in.email,
        password_hash=hash_password(password),
        fullname=user_in.fullname,
        role=role,
        # Canonical college name for students/faculty (validated above);
        # librarians may keep free text or nothing at all.
        department=department,
    )
    db.add(user)

    # Email BEFORE commit: if delivery fails, roll the account back and
    # unconsume the OTP so the same code can be retried — a user must
    # never end up with a password nobody knows.
    try:
        await otp_service.send_generated_password_email(user_in.email, password)
    except ValueError as e:
        await db.rollback()
        await otp_service.unconsume(user_in.email, db)
        raise HTTPException(status_code=500, detail=str(e))

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
    # Case-insensitive: registration always stores a lowercase email as both
    # username and email, so a mixed-case login must still match.
    identifier = (form_data.username or "").strip().lower()
    result = await db.execute(
        select(User).where(
            or_(
                func.lower(User.username) == identifier,
                func.lower(User.email) == identifier,
            )
        )
    )
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


def _with_college(user: User) -> UserOut:
    """UserOut plus the account's college, derived from users.department
    (there is no college column on users). Librarians are never tied to a
    college and unmatched department text resolves to null."""
    department = None if user.role == RoleEnum.librarian else user.department
    return UserOut.model_validate(user).model_copy(update={
        "college_id": resolve_college(department),
        "college_code": resolve_college_code(department),
    })


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Decodes the caller's JWT and returns their profile. Frontend calls
    this on every page load — the equivalent of the old system's
    GET /api/auth/me — to confirm who's logged in and drive the navbar.
    Also carries college_id/college_code so the browse page can offer
    "my college" as the default filter.
    """
    return _with_college(current_user)


@router.put("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Editable: fullname, contact, department. NOT editable: email (it is
    the login identifier and is fixed at registration) and role (never read
    from the payload)."""
    if payload.email and payload.email.strip().lower() != current_user.email.strip().lower():
        raise HTTPException(status_code=400, detail="Email cannot be changed.")

    current_user.fullname = payload.fullname
    current_user.contact = payload.contact
    if payload.department is not None:
        current_user.department = normalize_department(payload.department)

    await db.commit()
    await db.refresh(current_user)
    return _with_college(current_user)



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
    # Server-side strength (the frontend meter is display-only): must mix
    # letters and numbers — rejects weak passwords regardless of client.
    if not re.search(r"[A-Za-z]", payload.new_password) or not re.search(
        r"\d", payload.new_password
    ):
        raise HTTPException(
            status_code=400,
            detail="New password must mix letters and numbers.",
        )

    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return {"success": True}


# ── Self-service password reset (email OTP → generated password) ──────────
# No admin reset exists: recovery is entirely self-service. The OTP table has
# no "purpose" column on purpose — an address is either registered (so this
# reset flow applies) or not (so it is the registration flow), never both.
# Existing JWTs stay valid until they expire; the reset does not revoke them.


@router.post("/password/reset/request-otp")
@limiter.limit(LOGIN_RATE_LIMIT)
async def request_password_reset_otp(
    payload: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Sends a reset code when an ACTIVE account exists for the address.
    The response is always the same generic message, whether or not the
    account exists, so accounts cannot be discovered."""
    email = payload.email.strip().lower()
    result = await db.execute(
        select(User).where(
            func.lower(User.email) == email,
            User.is_active == True,  # noqa: E712
        )
    )
    if result.scalar_one_or_none():
        try:
            await otp_service.request_otp(email, db)
        except ValueError as e:
            # Swallowed on purpose: the caller must not learn whether the
            # address exists, that a cooldown is active, or that SMTP failed.
            logger.warning(f"Password-reset code not sent: {e}")
    return {"message": RESET_REQUEST_MESSAGE}


@router.post("/password/reset/confirm")
@limiter.limit(LOGIN_RATE_LIMIT)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Verifies the code, generates a new password, emails it to the
    registered address and deletes the code. The new password is NEVER in
    the response and never in the logs."""
    email = payload.email.strip().lower()
    try:
        await otp_service.verify_otp(email, payload.code, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await db.execute(
        select(User).where(
            func.lower(User.email) == email,
            User.is_active == True,  # noqa: E712
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        # Account vanished/deactivated between request and confirm — the
        # code is burned and nothing is disclosed about the account.
        await otp_service.consume(email, db)
        raise HTTPException(
            status_code=400,
            detail="This reset request is no longer valid. Please request a new code.",
        )

    new_password = generate_password()
    user.password_hash = hash_password(new_password)

    try:
        await otp_service.send_password_reset_email(email, new_password)
    except ValueError:
        # Roll back so the OLD password keeps working, and let the same code
        # be retried (mirrors the registration flow).
        await db.rollback()
        await otp_service.unconsume(email, db)
        raise HTTPException(
            status_code=500,
            detail="We couldn't email your new password, so your current password "
                   "is still active. Please try again.",
        )

    # Single-use code: delete the OTP rows in the same commit that stores
    # the new hash.
    await otp_service.consume(email, db)
    await db.commit()
    return {"message": "Your password has been reset. Check your email for the new password."}