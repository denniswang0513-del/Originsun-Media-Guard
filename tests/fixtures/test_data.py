"""
test_data.py — /test skill 共用 fixture：seed + cleanup。

所有測試資料 name 開頭加 `__test_` prefix，cleanup 嚴格 prefix 比對避免誤刪。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import text


TEST_PREFIX = "__test_"


def _short_uid() -> str:
    return uuid.uuid4().hex[:8]


# ── async DB 操作（透過 main app 的 session_factory） ──

async def _get_session_factory():
    """每次呼叫建獨立 engine — 避免 asyncpg 連線跨 asyncio.run() loop 衝突。

    每個 fixture 用 asyncio.run() 跑會開新 event loop；上次 loop 留下的
    asyncpg 連線會炸 'another operation is in progress'。所以我們不共用
    db.session._engine，每次都新建一個 disposed-after-call 的 engine。
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from db.session import get_database_url
    url = get_database_url()
    engine = create_async_engine(
        url,
        pool_size=2, max_overflow=2, pool_timeout=5,
        pool_recycle=300, echo=False,
        connect_args={"timeout": 3},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # 不 dispose — caller 用完後 fixture-end pytest 拆 loop 一併清掉
    return factory


# ── 同步 SQL helper（用 subprocess 觸發 / cleanup script 用）──

async def cleanup_all_test_data():
    """清掃所有 __test_ prefix 資料 — staff / project / showcase / token。

    順序：先清 child（showcase / project_staff），再清 project / staff。
    """
    factory = await _get_session_factory()
    if not factory:
        return {"skipped": True, "reason": "DB unavailable"}

    counts = {}
    async with factory() as session:
        # 1. 收集 __test_ prefix 的 staff id
        rows = await session.execute(text(
            f"SELECT id FROM crm_staff WHERE name LIKE '{TEST_PREFIX}%'"
        ))
        staff_ids = [r[0] for r in rows]

        # 2. 收集 __test_ prefix 的 project id
        rows = await session.execute(text(
            f"SELECT id FROM crm_projects WHERE name LIKE '{TEST_PREFIX}%'"
        ))
        proj_ids = [r[0] for r in rows]

        # 3. 清 project_staff（外鍵）
        if staff_ids or proj_ids:
            await session.execute(text(
                "DELETE FROM crm_project_staff WHERE staff_id = ANY(:sids) OR project_id = ANY(:pids)"
            ), {"sids": staff_ids, "pids": proj_ids})

        # 4. 清 showcase
        if proj_ids:
            r = await session.execute(text(
                "DELETE FROM crm_project_showcase WHERE id = ANY(:pids)"
            ), {"pids": proj_ids})
            counts["showcase"] = r.rowcount or 0

        # 5. 清 project category links
        if proj_ids:
            await session.execute(text(
                "DELETE FROM website_project_categories WHERE project_id = ANY(:pids)"
            ), {"pids": proj_ids})

        # 6. 清 project
        if proj_ids:
            r = await session.execute(text(
                "DELETE FROM crm_projects WHERE id = ANY(:pids)"
            ), {"pids": proj_ids})
            counts["project"] = r.rowcount or 0

        # 7. 清 staff
        if staff_ids:
            r = await session.execute(text(
                "DELETE FROM crm_staff WHERE id = ANY(:sids)"
            ), {"sids": staff_ids})
            counts["staff"] = r.rowcount or 0

        await session.commit()
    return counts


# ── seed helpers（在 test setup 內 await 呼叫） ──

async def seed_test_staff(name_suffix: str = "", role: str = "演員") -> dict:
    """建一筆 __test_<rand>_<suffix> staff，回傳 dict 含 id + name。"""
    factory = await _get_session_factory()
    if not factory:
        raise RuntimeError("DB unavailable for seed")
    from db.models import CrmStaff
    sid = uuid.uuid4().hex
    name = f"{TEST_PREFIX}{_short_uid()}{('_' + name_suffix) if name_suffix else ''}"
    now = datetime.now(timezone.utc)
    async with factory() as session:
        s = CrmStaff(
            id=sid, name=name, role=role,
            status="專案", resume_visible=False,
            created_at=now, updated_at=now,
        )
        session.add(s)
        await session.commit()
    return {"id": sid, "name": name, "role": role}


async def seed_test_project(name_suffix: str = "") -> dict:
    """建一筆 __test_<rand>_<suffix> project，回傳 dict 含 id + name。"""
    factory = await _get_session_factory()
    if not factory:
        raise RuntimeError("DB unavailable for seed")
    from db.models import CrmProject
    pid = uuid.uuid4().hex
    name = f"{TEST_PREFIX}{_short_uid()}{('_' + name_suffix) if name_suffix else ''}"
    now = datetime.now(timezone.utc)
    async with factory() as session:
        p = CrmProject(
            id=pid, name=name, status="製作",
            client_id="__test_dummy",  # soft FK，不檢查；cleanup 也不會碰真客戶
            public=False, public_sort_order=0,
            created_at=now, updated_at=now,
        )
        session.add(p)
        await session.commit()
    return {"id": pid, "name": name}


async def seed_test_showcase_with_token(project_id: str) -> dict:
    """建立 showcase row + mint token，回傳 dict 含 token + url。"""
    factory = await _get_session_factory()
    if not factory:
        raise RuntimeError("DB unavailable for seed")
    from db.models import CrmProjectShowcase
    from core.auth import create_token

    SHOWCASE_EDIT_SCOPE = "showcase_edit"
    token = create_token({"sub": project_id, "scope": SHOWCASE_EDIT_SCOPE}, expires_days=1)

    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, edit_token=token, editable=True)
            session.add(sc)
        else:
            sc.edit_token = token
            sc.editable = True
        await session.commit()
    return {"token": token, "project_id": project_id}


async def make_admin_token() -> str:
    """跨 process 用同個 jwt_secret 簽 admin token（給 admin endpoint 測試用）。"""
    from core.auth import create_token
    return create_token({"sub": "admin", "role": "admin", "access_level": 3})


# ── 同步 wrapper for pytest fixture 使用 ──

def run_async(coro):
    """在同步 fixture 中跑 async 邏輯。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已有 loop（通常是 pytest-asyncio），直接 return coroutine 給 await
            return coro
    except RuntimeError:
        pass
    return asyncio.run(coro)
