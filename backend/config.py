"""
config.py
---------
Central config for the FastAPI backend.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing from .env")

BASE_DIR   = Path(__file__).resolve().parent
PAPERS_DIR = BASE_DIR / "papers"
CHROMA_DIR = str(BASE_DIR / "chroma_db")
STAGING_DIR  = PAPERS_DIR / "staging"
PENDING_DIR  = PAPERS_DIR / "pending"
PUBLIC_DIR   = PAPERS_DIR / "public"
VERSIONS_DIR = PAPERS_DIR / "versions"
for d in (STAGING_DIR, PENDING_DIR, PUBLIC_DIR, VERSIONS_DIR):
    d.mkdir(parents=True, exist_ok=True)

LLM_MODEL        = "openai/gpt-oss-120b"
EMBEDDING_MODEL  = "jinaai/jina-embeddings-v2-small-en"
RERANKER_MODEL   = "Xenova/ms-marco-MiniLM-L-6-v2"

CHUNK_SIZE       = 800
CHUNK_OVERLAP    = 150
RETRIEVAL_TOP_K  = 10
RERANK_TOP_K     = 5
SCORE_THRESHOLD  = 0.15

CHROMA_COLLECTION = "isatu_repository"

MAX_UPLOAD_SIZE    = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}

DISCREPANCY_THRESHOLD    = 0.85
PREVIEW_MAX_AGE_SECONDS  = 3600

LOGIN_RATE_LIMIT = "10/minute"

CORS_ORIGINS = ["http://localhost:5500"]

# ─────────────────────────────────────────────────────────────────────────
# OTP-based registration (added — see services/otp_service.py)
# ─────────────────────────────────────────────────────────────────────────

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")  # the SCHOOL's sending Gmail address
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")  # Gmail App Password — not the real password
OTP_EXPIRE_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# Institutional email domains — the actual role-assignment mechanism.
# @students.isatu.edu.ph -> student
# @isatu.edu.ph          -> librarian (if on LIBRARIAN_EMAILS) else faculty
STUDENT_EMAIL_DOMAIN = "@students.isatu.edu.ph"
STAFF_EMAIL_DOMAIN   = "@isatu.edu.ph"

# Comma-separated list of actual librarian emails, set in .env.
# Any @isatu.edu.ph account NOT on this list registers as "faculty"
# instead — this is what distinguishes librarian staff from teachers/
# advisers, since the domain alone is shared by both.
LIBRARIAN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("LIBRARIAN_EMAILS", "").split(",")
    if e.strip()
}