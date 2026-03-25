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

        # Auto-create tables (idempotent) + seed roles + migrate users
        from db.models import Base
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed_default_roles()
        await _migrate_users_to_rbac()

        return True
    except Exception as e:
        print(f"[DB] 無法連線到 PostgreSQL: {e}")
        return False


# ── RBAC seed & migration ──

_DEFAULT_ROLES = [
    {"name": "admin",     "access_level": 3, "modules": ["backup","verify","transcode","concat","report","transcribe","tts","projects"], "description": "系統管理員"},
    {"name": "producer",  "access_level": 2, "modules": ["backup","verify","report","transcode","concat","transcribe","projects"],       "description": "製片"},
    {"name": "editor",    "access_level": 1, "modules": ["transcode","concat","verify","transcribe"],                                    "description": "剪輯師"},
    {"name": "cameraman", "access_level": 1, "modules": ["backup","verify","report"],                                                    "description": "攝影師"},
    {"name": "assistant", "access_level": 1, "modules": ["backup","verify"],                                                             "description": "助理"},
    {"name": "viewer",    "access_level": 0, "modules": ["report"],                                                                      "description": "唯讀"},
]


async def _seed_default_roles():
    """Insert default roles if roles table is empty (idempotent). Also sync to roles.json."""
    if _session_factory is None:
        return
    try:
        from sqlalchemy import select, func as sa_func
        from sqlalchemy.dialects.postgresql import insert
        from db.models import Role
        async with _session_factory() as session:
            count = (await session.execute(select(sa_func.count()).select_from(Role))).scalar() or 0
            seeded = False
            if count == 0:
                for r in _DEFAULT_ROLES:
                    stmt = insert(Role).values(**r).on_conflict_do_nothing(index_elements=["name"])
                    await session.execute(stmt)
                await session.commit()
                seeded = True
                print(f"[DB] 已寫入 {len(_DEFAULT_ROLES)} 個預設角色")

            # Sync DB roles → roles.json only when seeded or file missing
            import os
            from core.auth import save_roles_json, _role_to_dict, _ROLES_JSON
            if seeded or not os.path.exists(_ROLES_JSON):
                result = await session.execute(select(Role))
                save_roles_json([_role_to_dict(r) for r in result.scalars().all()])
    except Exception as e:
        print(f"[DB] seed_default_roles 失敗: {e}")


async def _migrate_users_to_rbac():
    """Migrate existing users from role string to role_id (idempotent, bulk UPDATE)."""
    if _session_factory is None:
        return
    try:
        from sqlalchemy import select, update, exists
        from db.models import User, Role
        async with _session_factory() as session:
            # Quick check: any users need migration?
            needs_migration = (await session.execute(
                select(exists().where(User.role_id.is_(None), User.role.isnot(None)))
            )).scalar()
            if not needs_migration:
                return

            # Build name → id mapping
            roles_result = await session.execute(select(Role))
            role_map = {r.name: r.id for r in roles_result.scalars().all()}

            # Bulk update per role name
            migrated = 0
            for role_name, role_id in role_map.items():
                result = await session.execute(
                    update(User)
                    .where(User.role == role_name, User.role_id.is_(None))
                    .values(role_id=role_id)
                )
                migrated += result.rowcount
            await session.commit()
            if migrated:
                print(f"[DB] 已遷移 {migrated} 個使用者到 RBAC role_id")
    except Exception as e:
        print(f"[DB] migrate_users_to_rbac 失敗: {e}")


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
