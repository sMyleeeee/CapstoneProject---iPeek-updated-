from datetime import datetime
from typing import Optional, List

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
    password: str
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

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    fullname: str
    email: EmailStr
    contact: Optional[str] = None


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


# ---------- Research ----------

class ResearchOut(BaseModel):
    id: int
    title: str
    year: Optional[int] = None
    authors: str
    department: str
    abstract: Optional[str] = None
    source_stem: str
    status: StatusEnum
    feedback_note: Optional[str] = None
    student_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ValidateResearch(BaseModel):
    approve: bool
    feedback_message: Optional[str] = None


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


# ---------- Feedback ----------

class FeedbackOut(BaseModel):
    id: int
    research_id: int
    librarian_id: int
    message: str
    created_at: datetime

    class Config:
        from_attributes = True


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


class ChatIn(BaseModel):
    question: str
    history: Optional[List[dict]] = None


class RefineIn(BaseModel):
    query: str
    target: str  # "similarity" | "summary" | "gap"


class ChatOut(BaseModel):
    result: str
    sources: list