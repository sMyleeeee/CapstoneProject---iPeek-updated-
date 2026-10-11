"""
Database configuration.

Connects to the Supabase Postgres session pooler via DATABASE_URL from .env.
SQLAlchemy's async engine needs an async driver scheme, so a plain
"postgresql://..." URL (psycopg2-style) is normalized to "postgresql+psycopg://".
"""
import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

load_dotenv()  # ensure .env is read no matter which module is imported first

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing from .env")
# Supabase hands out plain "postgresql://..." URLs (psycopg2-style). The async
# engine needs an async driver; asyncpg is used because psycopg's async mode
# cannot run on Windows' default ProactorEventLoop.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, future=True, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    """FastAPI dependency: yields a request-scoped async DB session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Create tables on startup if they don't exist yet, then apply any
    lightweight column migrations (create_all never alters existing tables)."""
    # Import models so every table is registered on Base.metadata, regardless
    # of which entrypoint calls init_db (app startup, seed, reindex script).
    import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)


async def _migrate_columns(conn):
    """SQLite-safe ALTER TABLE for columns added after a table already exists.

    No-op on Postgres: those late columns are part of the models, so
    create_all builds them on a fresh database from the start, and PRAGMA
    (SQLite-only) doesn't exist there anyway."""
    if conn.dialect.name != "sqlite":
        return

    from sqlalchemy import text

    rows = await conn.execute(text("PRAGMA table_info(users)"))
    existing = {row[1] for row in rows}
    if "is_active" not in existing:
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
        ))

    rows = await conn.execute(text("PRAGMA table_info(ai_analysis)"))
    existing = {row[1] for row in rows}
    if "imrad" not in existing:
        await conn.execute(text(
            "ALTER TABLE ai_analysis ADD COLUMN imrad TEXT"
        ))

    # Legacy rows predate the consent gate — backfill FALSE (privacy-
    # conservative: summary-only remotely until resubmitted with consent).
    # New submissions always get an explicit value from confirm_upload.
    rows = await conn.execute(text("PRAGMA table_info(research)"))
    existing = {row[1] for row in rows}
    if "public_pdf_consent" not in existing:
        await conn.execute(text(
            "ALTER TABLE research ADD COLUMN public_pdf_consent BOOLEAN NOT NULL DEFAULT 0"
        ))
