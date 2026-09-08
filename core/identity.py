"""core/identity.py — N0 個人帳號化：唯一的「我是誰」解析器。

登入帳號（users.username）↔ 人員檔案（crm_staff.id）的橋接查詢。
所有個人化端點（/api/v1/me/*）都經由 resolve_current_staff 取得呼叫者的
人員檔案，不各自發明查法 —— 這是 row-level own-scope 的種子
（docs/WORK_OS_BLUEPRINT.md §「Row-level 權限」），N0 只做 endpoint 級
own-scope，不建 own/team/all 通用框架。

分層原則：core 不 import routers.*（避免循環依賴）；staff_id 一律從 DB
現查而非 JWT（admin 重綁後免重新登入）。
"""
from typing import Optional

from fastapi import HTTPException, Request

from core.auth import _extract_token, check_admin_or_module


async def resolve_current_staff(request: Request) -> dict:
    """token → username → users.staff_id → crm_staff row。

    回傳 {'username': str|None, 'staff_id': str|None, 'staff': CrmStaff|None}。
    未登入 → username=None；未綁定 → staff_id=None；綁定失效（人員被刪）
    → staff=None 但 staff_id 保留，呼叫端可據此顯示「綁定失效」。
    DB 離線時安全降級為未綁定（個人頁顯示空態，不噴 500）。
    """
    result: dict = {"username": None, "staff_id": None, "staff": None}
    payload = _extract_token(request)
    if not payload:
        return result
    username = payload.get("sub") or ""
    result["username"] = username
    if not username:
        return result
    try:
        from core import state
        if not state.db_online:
            return result
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return result
        from db.models import CrmStaff, User
        async with factory() as session:
            user = await session.get(User, username)
            staff_id: Optional[str] = getattr(user, "staff_id", None) if user else None
            if not staff_id:
                return result
            result["staff_id"] = staff_id
            result["staff"] = await session.get(CrmStaff, staff_id)
    except Exception:
        # 與 _save_user_to_db 同哲學：DB 故障不擋認證路徑，降級為未綁定
        pass
    return result


async def require_zone_staff(request: Request, *sub_keys: str) -> dict:
    """「今天與這週」那一區的守衛：**總開關（me_today_zone）＋那個子視圖自己的鑰匙**兩者都要，再加綁定人員檔案。
    工作追蹤模組（timesheets）與管理員整區恆過。owner 2026-09-08：「這個區塊要有一個單獨的控制」——
    子鑰匙勾了、總開關沒開，後端也要 403，不能只靠前端不畫按鈕。"""
    from core.auth import ME_ZONE_MASTER
    check_admin_or_module(request, "timesheets", ME_ZONE_MASTER)          # 第一道：總開關
    return await require_bound_staff(request, "timesheets", *sub_keys)   # 第二道：子視圖＋綁定


async def require_bound_staff(request: Request, *modules: str) -> dict:
    """守衛（模組鑰匙由呼叫端給，可多把：任一把即可）＋ 必須綁定人員檔案：回 resolve_current_staff 的 dict，
    沒綁 → 409。個人頁（profile／leave／timesheets）與 CRM tab 的「我的一天」都走這一支，409 原句只有這裡。"""
    check_admin_or_module(request, *modules)
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return ident
