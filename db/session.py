"""Database connection management — async SQLAlchemy + asyncpg."""
from __future__ import annotations

import os
import re
from typing import AsyncGenerator

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


def database_name(url: str | None = None) -> str:
    """DSN → 庫名。不給 url 就用 `get_database_url()`。

    庫名是 `mediaguard`（生產）還是 `mediaguard_dev` 決定了很多事：e2e 能不能
    種資料、告警要標哪個庫、這台算不算 master。之前 repo 裡有四份各寫一次的
    `rsplit("/", 1)`，差別在有沒有剝 `?query`／`#fragment`、以及讀不讀得到 env
    的 `DATABASE_URL` —— 於是 NAS 容器（DSN 從 env 來）上有些地方會判錯。
    正本放這裡，跟 `get_database_url()` 同一個地方。
    """
    raw = get_database_url() if url is None else url
    return re.sub(r"[?#].*$", "", (raw or "")).rstrip("/").rsplit("/", 1)[-1]


def _pool_sizes():
    """(pool_size, max_overflow)。**master 才需要大池，機隊不需要。**

    🔴 這是 2026-08-21 那次 503 的根因：NAS Postgres `max_connections=50`，
    實測常態 46 條在用 —— 機隊 9 台每台常態握 5 條閒置（尖峰可到 15），
    加上 master、dev、NAS 官網容器，一有突發就
    `asyncpg.TooManyConnectionsError: sorry, too many clients already`，
    而使用者在畫面上看到的是 503（還被前端寫成「需要權限」）。

    機隊 agent 對 DB 的用量是「狀態回報 + 任務歷史」等級，本來就不需要
    五條熱連線；master 要服務整個 UI（CRM／財務／官網），維持原本的 5/10。

    判定用 `core.topology.is_master_machine()`（同一個權威來源，不要另立
    第二套判準）。任何例外都落到小池 —— 猜錯方向要往「省連線」猜，
    猜成大池會把整個機隊一起拖垮。
    """
    try:
        from core.topology import is_master_machine
        if is_master_machine():
            return 5, 10
    except Exception:
        pass
    return 2, 3


async def init_db() -> bool:
    """Initialize the async engine and session factory.

    Returns True if DB is reachable, False otherwise.
    """
    if not _HAS_SQLALCHEMY:
        return False
    global _engine, _session_factory
    try:
        url = get_database_url()
        pool, overflow = _pool_sizes()
        _engine = create_async_engine(
            url,
            pool_size=pool,
            max_overflow=overflow,
            pool_timeout=5,
            pool_recycle=180,        # recycle idle conns >3min old, BEFORE a NAT/router
                                     # silently drops them (the black-hole that wedged 8000).
            pool_pre_ping=True,      # ping before checkout → discard stale/dead conns.
            echo=False,
            connect_args={
                "timeout": 3,           # asyncpg CONNECT timeout (seconds)
                "command_timeout": 8,   # per-QUERY timeout. THE root fix: without it, a query
                                        # (incl pool_pre_ping's test) on a black-holed/half-open
                                        # idle connection HANGS FOREVER — NAS still thinks the
                                        # conn is alive (no RST), packets just vanish. That hang
                                        # made db_available() stick, db_online flip false, and
                                        # the whole event loop wedge (CPU 0%, /health 3s+),
                                        # recoverable ONLY by a process restart. With it the
                                        # ping fails fast → pre_ping discards the dead conn →
                                        # reconnects → db stays available. (2026-06-18)
            },
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
