from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator

from models import RoleEnum, StatusEnum


# ---------- Shared email normalization ----------
# Any schema needing an "email" field inherits from this instead of
# BaseModel directly. Lowercases + strips whitespace so
# "SomeOne@isatu.edu.ph" and "someone@isatu.edu.ph" are always treated
# as the same address — before they ever reach a route or the DB.

class _EmailNormalizedBase(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


# ---------- Users / Auth ----------

class UserCreate(_EmailNormalizedBase):
    # NO password field — the password is generated server-side (after OTP
    # verification) and emailed to the user. A client-sent "password" is
    # ignored (Pydantic's default extra="ignore").
    fullname: str
    department: Optional[str] = None
    # No username field — the institutional email becomes the username
    # server-side. No role field — derived from the email domain.


class UserOut(BaseModel):
    id: int
    username: str
    email: EmailStr
    fullname: str
    role: RoleEnum
    department: Optional[str] = None
    contact: Optional[str] = None
    is_active: bool = True
    # Derived from users.department, not a column: the canonical college
    # this account belongs to (null for librarians and for department
    # text that matches no canonical college). Filled in by /auth/me.
    college_id: Optional[int] = None
    college_code: Optional[str] = None

    class Config:
        from_attributes = True


class AdminUserCreate(_EmailNormalizedBase):
    """Librarian-created account. NO password field (generated server-side)
    and NO role field — role is derived from the email domain, same as the
    OTP self-registration flow."""
    fullname: str
    department: Optional[str] = None


class AdminUserOut(BaseModel):
    """Returned ONCE at creation: the plaintext generated password is only
    in this response — never logged, never emailed, never retrievable again."""
    user: UserOut
    generated_password: str


class UserUpdate(BaseModel):
    # Email is accepted for backward compatibility but can never be changed
    # (see update_me) — sending a different one is a 400.
    fullname: str
    email: Optional[EmailStr] = None
    contact: Optional[str] = None
    department: Optional[str] = None


class PasswordUpdate(BaseModel):
    current_password: str
    new_password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: RoleEnum
    fullname: str


# ---------- OTP registration ----------

class OTPRequest(_EmailNormalizedBase):
    pass


class OTPVerify(_EmailNormalizedBase):
    code: str


# ---------- Self-service password reset (email OTP) ----------

class PasswordResetRequest(_EmailNormalizedBase):
    """Step 1: ask for a reset code. The response is always generic."""


class PasswordResetConfirm(_EmailNormalizedBase):
    """Step 2: verify the code; a new password is generated and emailed."""
    code: str


# ---------- Research ----------

class ResearchOut(BaseModel):
    id: int
    title: str
    year: Optional[int] = None
    authors: str
    department: str
    college_id: Optional[int] = None
    abstract: Optional[str] = None
    source_stem: str
    status: StatusEnum
    feedback_note: Optional[str] = None
    public_pdf_consent: bool = False
    student_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ValidateResearch(BaseModel):
    approve: bool
    feedback_message: Optional[str] = None


# ---------- Repository browse filters ----------

class CollegeRefOut(BaseModel):
    """Just enough to identify a college (no counts)."""
    id: int
    code: str
    name: str


class CollegeFilterOut(CollegeRefOut):
    """One canonical college plus how many APPROVED papers it holds.

    Every seeded college is listed (count 0 included) so the filter UI is
    stable before anything is approved.
    """
    count: int = 0


class RepositoryFiltersOut(BaseModel):
    years: list[int]              # approved papers' years, newest first
    latest_year: Optional[int] = None
    colleges: list[CollegeFilterOut]
    # The caller's own college (the full object, so the frontend can pre-select
    # it without a lookup), or null for librarians / an unresolved department.
    user_college: Optional[CollegeRefOut] = None


class RepositoryStatusOut(BaseModel):
    """Dashboard health card for the librarian view."""
    status: str                            # "ok" | "degraded"
    approved: int
    pending: int
    returned: int
    chunks_indexed: Optional[int] = None   # null when Chroma is unreadable
    message: str


class UploadPreviewOut(BaseModel):
    preview_id: str
    warnings: list
    has_warnings: bool
    resubmission: bool
    ai_metadata: dict
    source: str
    filename: str


class UploadConfirmIn(BaseModel):
    preview_id: str
    final_values: dict
    # Required, no default — the API must reject a confirm that omits the
    # author's explicit public-PDF choice (422 from FastAPI).
    public_pdf_consent: bool


# ---------- Feedback ----------

class FeedbackOut(BaseModel):
    id: int
    research_id: int
    librarian_id: int
    message: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Notifications ----------

class NotificationOut(BaseModel):
    id: int
    research_id: Optional[int] = None   # null once the paper is deleted
    message: str
    status: str                          # "unread" | "read"
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationListOut(BaseModel):
    """`unread_count` covers ALL of the caller's rows, not just the page
    returned in `items`, so the bell badge stays correct when a limit is
    applied."""
    unread_count: int
    items: list[NotificationOut]


# ---------- Dashboard ----------

class DashboardStats(BaseModel):
    total_research: int
    approved_research: int
    ongoing_research: int
    rejected_research: int


# ---------- AI ----------

class AIAnalysisOut(BaseModel):
    id: int
    research_id: int
    summary: Optional[str] = None
    similar_studies: Optional[str] = None
    research_gaps: Optional[str] = None

    class Config:
        from_attributes = True


class RefineIn(BaseModel):
    query: str
    target: str  # "similarity" | "summary" | "gap" | "imrad"


class AnalysisOut(BaseModel):
    result: str
    sources: list