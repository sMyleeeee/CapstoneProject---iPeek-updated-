"""
Database configuration.

Uses SQLite through the async 'aiosqlite' driver so the whole system stays
lightweight (single file on disk, no separate DB server to install/run).
Because it's SQLAlchemy's async engine, switching to Postgres/MySQL later
only means changing DATABASE_URL - no query code has to change.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = "sqlite+aiosqlite:///./ipeek.db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)


async def _migrate_columns(conn):
    """SQLite-safe ALTER TABLE for columns added after a table already exists."""
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
