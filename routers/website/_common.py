"""routers/website/_common.py
---
共用輔助：auth guard、DB factory、rate limit、client IP 擷取。

底線前綴表示 package 內部用，不對外 export。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException, Request

import core.state as state


_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))


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

    不適合跨多個 process／container 的情境，但 Phase M 的 website-api 是單一
    container，記憶體即可。
    """
    ip = client_ip(request)
    now = time.time()
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
    """管理端 guard：沿用 core.auth.check_admin。"""
    try:
        from core.auth import check_admin as _ca
        _ca(request)
    except ImportError:
        pass


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
