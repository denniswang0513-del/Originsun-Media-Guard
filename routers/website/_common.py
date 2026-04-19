"""routers/website/_common.py
---
共用輔助：auth guard、DB factory、rate limit、client IP 擷取。

底線前綴表示 package 內部用，不對外 export。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException, Request

import core.state as state

logger = logging.getLogger(__name__)

_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
_LAST_SWEEP: list[float] = [0.0]


def client_ip(request: Request) -> str:
    """取 client IP（優先 CF-Connecting-IP、X-Forwarded-For）。"""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.split(",")[0].strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, max_per_minute: int = 30) -> None:
    """簡易 sliding-window rate limit（per-IP，記憶體內）。

    Website-api 是單一 container，記憶體夠用。每 5 分鐘清一次空 bucket
    避免 bot 掃描造成的無界記憶體成長。
    """
    ip = client_ip(request)
    now = time.time()

    if now - _LAST_SWEEP[0] > 300.0:
        cutoff = now - 60.0
        for key in list(_RATE_BUCKETS.keys()):
            b = _RATE_BUCKETS[key]
            while b and b[0] < cutoff:
                b.popleft()
            if not b:
                del _RATE_BUCKETS[key]
        _LAST_SWEEP[0] = now

    bucket = _RATE_BUCKETS[ip]
    cutoff = now - 60.0
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= max_per_minute:
        raise HTTPException(status_code=429, detail="Too many requests")
    bucket.append(now)


def require_db() -> None:
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫目前不可用")


async def get_factory() -> Callable:
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫未初始化")
    return factory


def check_admin(request: Request) -> None:
    """管理端 guard：沿用 core.auth.check_admin。

    若 core.auth 無法 import 視為安全事件（網站 container 應該有 auth 模組）——
    raise 503 而非靜默放行，防止意外 auth bypass。
    """
    try:
        from core.auth import check_admin as _ca
    except ImportError:
        logger.error("core.auth import failed in website container — refusing admin request")
        raise HTTPException(status_code=503, detail="Auth module unavailable")
    _ca(request)


def current_username(request: Request) -> str:
    """從 JWT 取使用者名稱（for audit fields like handled_by）。"""
    try:
        from core.auth import _extract_token
        payload = _extract_token(request)
        if payload:
            return payload.get("username", "admin")
    except Exception:
        pass
    return "admin"


async def admin_session(request: Request):
    """FastAPI Depends：管理端通用 guard + DB session。

    合併 check_admin + require_db + get_factory + async with session，
    讓 admin router 只需宣告：
        async def foo(session = Depends(admin_session)): ...
    """
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        yield session


async def public_session():
    """FastAPI Depends：公開端通用 DB session（不含 auth）。

    rate_limit 仍在 endpoint 自行呼叫，因為各端點的 max_per_minute 不同。
    """
    require_db()
    factory = await get_factory()
    async with factory() as session:
        yield session
