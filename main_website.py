"""main_website.py
---
NAS website-api container 入口（精簡版 FastAPI）。

僅載入 `routers/website/*`，連同一個 PostgreSQL（共享 JWT secret、CORS 允許
Windows `192.168.1.107:8000` 與 `originsun-studio.com`）。

啟動（容器內）：
    uvicorn main_website:app --host 0.0.0.0 --port 8001

與 Windows `main.py` 並存：兩者都連 NAS Postgres，但：
- main.py：CRM / Backup / Transcode / 官網管理 Tab 前端（不掛 website routers）
- main_website.py：ONLY website routers
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("website-api")


async def _periodic_db_check():
    """每 60s 重探 DB；offline 時「重建連線池」而非只重試,讓 init 失敗 /
    postgres 重啟 / idle timeout / ProactorEventLoop wedge 都能自我修復。
    沒這個迴圈,容器要重啟才會復活,所有 endpoint 卡 503。對齊 main.py:_periodic_db_health。

    關鍵:db_available() 用現有 engine/pool,若 pool wedge 掉會一直失敗,光重試救不回;
    所以 offline 就先 dispose 壞 engine 再 init_db() 重建。全部 wait_for 包住,
    避免恢復檢查本身卡死 event loop。
    """
    import core.state as state
    from db.session import init_db, db_available, close_db

    while True:
        try:
            await asyncio.sleep(60)
            if state.db_online:
                ok = await asyncio.wait_for(db_available(), timeout=8)
                if not ok:
                    print("[website-api] DB 中斷,下一輪重建連線池")
                    state.db_online = False
            else:
                try:
                    await asyncio.wait_for(close_db(), timeout=8)
                except Exception:
                    pass
                ok = await asyncio.wait_for(init_db(), timeout=15)
                state.db_online = bool(ok)
                if ok:
                    print("[website-api] DB 連線恢復（已重建連線池）")
        except asyncio.CancelledError:
            raise
        except Exception:
            state.db_online = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        from db.session import init_db, get_session_factory
        from db.migrations_website import run_website_migrations
        from db.seed_website import seed_website_if_empty
        import core.state as state

        # init_db 回傳連線是否成功；必須顯式寫回 state.db_online
        # （與 main.py startup 模式一致 — _common.require_db 靠這個 flag）
        ok = await init_db()
        state.db_online = bool(ok)

        if state.db_online:
            factory = get_session_factory()
            if factory:
                await run_website_migrations(factory)
                await seed_website_if_empty(factory)
            print("[website-api] startup OK (DB online)")
        else:
            print("[website-api] startup WITHOUT DB — 60s 後自動重試")
    except Exception as e:
        print(f"[website-api] startup failed: {e}")

    db_check_task = asyncio.create_task(_periodic_db_check())
    try:
        yield
    finally:
        db_check_task.cancel()
        try:
            await db_check_task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(
    title="Originsun Website API",
    version="1.0",
    lifespan=_lifespan,
)

_allowed_origins = [
    o.strip() for o in os.environ.get(
        "WEBSITE_CORS_ORIGINS",
        "http://localhost:4321,http://127.0.0.1:4321,"
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://192.168.1.107:8000,"
        # 對外網站 + admin Tab 都走 cloudflared，三個 hostname 都要白
        "https://originsun-studio.com,https://www.originsun-studio.com,"
        "https://test.originsun-studio.com,"
        "https://preview.originsun-studio.com,"
        "https://foundry.originsun-studio.com",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "website-api"}


from routers.website import router as _website_router
app.include_router(_website_router)
