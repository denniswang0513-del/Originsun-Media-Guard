"""main_website.py
---
NAS website-api container 入口（精簡版 FastAPI）。

僅載入 `routers/website/*`，連同一個 PostgreSQL（共享 JWT secret、CORS 允許
Windows `192.168.1.11:8000` 與 `originsun-studio.com`）。

啟動（容器內）：
    uvicorn main_website:app --host 0.0.0.0 --port 8001

與 Windows `main.py` 並存：兩者都連 NAS Postgres，但：
- main.py：CRM / Backup / Transcode / 官網管理 Tab 前端（不掛 website routers）
- main_website.py：ONLY website routers
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("website-api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        from db.session import init_db, get_session_factory
        from db.migrations_website import run_website_migrations
        from db.seed_website import seed_website_if_empty
        import core.state as state

        await init_db()
        if state.db_online:
            factory = get_session_factory()
            if factory:
                await run_website_migrations(factory)
                await seed_website_if_empty(factory)
        logger.info("website-api startup complete")
    except Exception as e:
        logger.error("startup failed: %s", e)
    yield


app = FastAPI(
    title="Originsun Website API",
    version="1.0",
    lifespan=_lifespan,
)

_allowed_origins = [
    o.strip() for o in os.environ.get(
        "WEBSITE_CORS_ORIGINS",
        "http://localhost:4321,http://127.0.0.1:4321,"
        "http://192.168.1.11:8000,"
        "https://originsun-studio.com,https://www.originsun-studio.com,"
        "https://preview.originsun-studio.com",
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
