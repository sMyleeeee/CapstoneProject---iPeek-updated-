import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Enum, Float, Boolean,
)
from sqlalchemy.orm import relationship

from database import Base


class RoleEnum(str, enum.Enum):
    student = "student"
    faculty = "faculty"
    librarian = "librarian"


class StatusEnum(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    returned = "returned"

class OTPVerification(Base):
    __tablename__ = "otp_verifications"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(120), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    verified = Column(Boolean, default=False, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    fullname = Column(String(150), nullable=False)
    role = Column(Enum(RoleEnum), nullable=False, default=RoleEnum.student)
    department = Column(String(100), nullable=True)
    contact = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Soft-delete flag — NEVER delete user rows (institutional record-keeping).
    # False = deactivated: cannot log in, hidden from the default user list,
    # but their submissions/review history stay intact and visible.
    is_active = Column(Boolean, default=True, nullable=False)

    research_uploaded = relationship(
        "Research", back_populates="student", foreign_keys="Research.student_id"
    )
    research_reviewed = relationship(
        "Research", back_populates="reviewer", foreign_keys="Research.reviewed_by"
    )
    feedback_sent = relationship("Feedback", back_populates="librarian")
    notifications = relationship("Notification", back_populates="user")


class Research(Base):
    __tablename__ = "research"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    year = Column(Integer, nullable=True)
    filepath = Column(String(255), nullable=False)
    source_stem = Column(String(255), unique=True, nullable=False, index=True)
    authors = Column(String(255), nullable=False)
    department = Column(String(100), nullable=False)
    abstract = Column(Text, nullable=True)
    feedback_note = Column(Text, nullable=True)
    status = Column(Enum(StatusEnum), default=StatusEnum.pending, nullable=False)
    # Author's submission-time choice for REMOTE visibility of the full PDF.
    # No server-side default: always set explicitly by confirm_upload.
    # Does NOT affect Library Kiosk (in-person) viewing.
    public_pdf_consent = Column(Boolean, nullable=False)

    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    student = relationship("User", back_populates="research_uploaded", foreign_keys=[student_id])
    reviewer = relationship("User", back_populates="research_reviewed", foreign_keys=[reviewed_by])
    feedback = relationship("Feedback", back_populates="research")
    ai_analysis = relationship("AIAnalysis", back_populates="research", uselist=False)
    versions = relationship("ResearchVersion", back_populates="research")


class ResearchVersion(Base):
    __tablename__ = "research_versions"

    id = Column(Integer, primary_key=True, index=True)
    research_id = Column(Integer, ForeignKey("research.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    filepath = Column(String(255), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    research = relationship("Research", back_populates="versions")


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    research_id = Column(Integer, ForeignKey("research.id"), nullable=False)
    librarian_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    research = relationship("Research", back_populates="feedback")
    librarian = relationship("User", back_populates="feedback_sent")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    research_id = Column(Integer, ForeignKey("research.id"), nullable=True)
    message = Column(String(255), nullable=False)
    status = Column(String(20), default="unread")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="notifications")


class DeletionLog(Base):
    __tablename__ = "deletion_log"

    id = Column(Integer, primary_key=True, index=True)
    source_stem = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    deleted_at = Column(DateTime, default=datetime.utcnow)


class AIAnalysis(Base):
    __tablename__ = "ai_analysis"

    id = Column(Integer, primary_key=True, index=True)
    research_id = Column(Integer, ForeignKey("research.id"), unique=True, nullable=False)
    summary = Column(Text, nullable=True)
    similar_studies = Column(Text, nullable=True)
    research_gaps = Column(Text, nullable=True)
    imrad = Column(Text, nullable=True)      # Introduction/Methods/Results/Discussion
    sources = Column(Text, nullable=True)   # ← NEW: JSON list, only populated by similarity search
    created_at = Column(DateTime, default=datetime.utcnow)

    research = relationship("Research", back_populates="ai_analysis")


class KioskSession(Base):
    """
    Session-scoped Library Viewing Station state, keyed by the JWT's
    per-login "sid" claim — NOT by user id and NOT by role.

    Locking one sid can only ever affect that one login session: a
    librarian's own session on their own device has a different sid, so
    it is structurally unreachable from here.
    """
    __tablename__ = "kiosk_sessions"

    sid = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_kiosk = Column(Boolean, default=False, nullable=False)
    locked = Column(Boolean, default=False, nullable=False)
    locked_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])