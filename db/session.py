"""Database connection management — async SQLAlchemy + asyncpg."""

import asyncio
from typing import Optional, AsyncGenerator

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)
from sqlalchemy import text

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_database_url() -> str:
    """Read database_url from settings.json (or return default)."""
    try:
        from config import load_settings
        settings = load_settings()
        return settings.get(
            "database_url",
            "postgresql+asyncpg://originsun:originsun2026@192.168.1.132:5432/mediaguard",
        )
    except Exception:
        return "postgresql+asyncpg://originsun:originsun2026@192.168.1.132:5432/mediaguard"


async def init_db() -> bool:
    """Initialize the async engine and session factory.

    Returns True if DB is reachable, False otherwise.
    """
    global _engine, _session_factory
    try:
        url = get_database_url()
        _engine = create_async_engine(
            url,
            pool_size=5,
            max_overflow=10,
            pool_timeout=5,
            pool_recycle=1800,
            echo=False,
            connect_args={"timeout": 3},  # asyncpg connection timeout (seconds)
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

        # Test connectivity (fast fail)
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"[DB] 無法連線到 PostgreSQL: {e}")
        return False


async def close_db():
    """Dispose engine on shutdown."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def db_available() -> bool:
    """Quick check if DB is reachable."""
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    """Get the session factory (for non-FastAPI contexts like scheduler)."""
    return _session_factory
