"""Database connection management — async SQLAlchemy + asyncpg."""
from __future__ import annotations

import asyncio
import os
from typing import Optional, AsyncGenerator

_DEFAULT_DB_URL = "postgresql+asyncpg://originsun:originsun2026@192.168.1.132:5432/mediaguard"

try:
    from sqlalchemy.ext.asyncio import (
        create_async_engine,
        async_sessionmaker,
        AsyncSession,
        AsyncEngine,
    )
    from sqlalchemy import text
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False
    create_async_engine = None
    async_sessionmaker = None
    AsyncSession = None
    AsyncEngine = None
    text = None

_engine = None
_session_factory = None


def get_database_url() -> str:
    """env DATABASE_URL（NAS container）→ settings.json（master）→ default。"""
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url
    try:
        from config import load_settings
        return load_settings().get("database_url", _DEFAULT_DB_URL)
    except Exception:
        return _DEFAULT_DB_URL


async def init_db() -> bool:
    """Initialize the async engine and session factory.

    Returns True if DB is reachable, False otherwise.
    """
    if not _HAS_SQLALCHEMY:
        return False
    global _engine, _session_factory
    try:
        url = get_database_url()
        _engine = create_async_engine(
            url,
            pool_size=5,
            max_overflow=10,
            pool_timeout=5,
            pool_recycle=600,        # recycle conns >10min old (NAS may drop idle TCP)
            pool_pre_ping=True,      # ping before checkout → discard stale/dead conns so a
                                     # dropped connection can't fail db_available() and flip
                                     # state.db_online false (which blanks DB-gated views).
            echo=False,
            connect_args={"timeout": 3},  # asyncpg connection timeout (seconds)
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

        # Test connectivity (fast fail)
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        # Auto-create tables (idempotent). RBAC v2: no role seed/migrate — perms
        # are per-user (db.models.User.modules/access_level), backfilled in main.py startup.
        from db.models import Base
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

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


def get_session_factory():
    """Get the session factory (for non-FastAPI contexts like scheduler)."""
    return _session_factory
