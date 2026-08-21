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
RERANKER_MODEL   = "BAAI/bge-reranker-v2-m3"

ABSTRACT_CHUNK_SIZE     = 1000   # renamed from CHUNK_SIZE — abstract-only now, not full-page chunking
ABSTRACT_CHUNK_OVERLAP  = 100    # renamed from CHUNK_OVERLAP
RERANK_SCORE_THRESHOLD  = 0.0

RETRIEVAL_TOP_K = 20   # L2 first-stage candidate count (ChromaDB) — before reranking
RERANK_TOP_K    = 5    # how many survive the Cross-Encoder into the LLM's context

CHROMA_COLLECTION = "isatu_repository"

MAX_UPLOAD_SIZE    = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}

DISCREPANCY_THRESHOLD    = 0.85
PREVIEW_MAX_AGE_SECONDS  = 3600

LOGIN_RATE_LIMIT = "10/minute"

CORS_ORIGINS = ["http://localhost:5500"]