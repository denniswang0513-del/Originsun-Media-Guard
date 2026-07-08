"""
db_guard.py — 共用 DB 守門 helper。

Work OS Phase N 的 8 個新 router 各自複製了同一段「db_online 檢查 → 取
session factory → 503」樣板；統一到此。既有 routers/crm/_shared.py 有 async
版（_get_factory/_require_db）；這裡是 sync 版（router 端點直接呼叫用）。
"""

from fastapi import HTTPException  # type: ignore

import core.state as state


def db_factory_or_503():
    """回 async session factory；DB 離線或 factory 不可用時 raise HTTPException 503。"""
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫離線")
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫離線")
    return factory
