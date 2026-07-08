"""
api_analytics.py — GA4 流量儀表板 API（後台官網管理 › 儀表板用）。

讀 website_settings 的 analytics.ga_property_id + analytics.ga_service_account_json
→ services.ga_service 打 GA4 Data API → 回即時在線 / 今日 / 近 7 天 / 熱門頁面。
未設定憑證時 /status 回 configured=false（前端顯示引導，不報錯）。

守衛：管理員 OR website_admin 模組。跑在 master 8000（有對外網路 + 憑證在 DB）。
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _guard(request: Request):
    check_admin_or_module(request, "website_admin")


async def _load_creds():
    """回 (property_id_raw, service_account_json_raw) — 從 website_settings。"""
    from services.website import settings_service
    factory = db_factory_or_503()
    async with factory() as session:
        s = await settings_service.get_all_settings(session)
    return s.get("analytics.ga_property_id"), s.get("analytics.ga_service_account_json")


@router.get("/status")
async def status(request: Request):
    """憑證是否備妥（前端據此顯示數據卡 or 引導卡）。"""
    _guard(request)
    from services import ga_service
    prop, sa = await _load_creds()
    try:
        ga_service.parse_credentials(prop, sa)
        return {"configured": True}
    except ValueError as e:
        return {"configured": False, "reason": str(e)}


async def _report(request: Request, fn_name: str):
    _guard(request)
    from services import ga_service
    prop, sa = await _load_creds()
    try:
        pid, sad = ga_service.parse_credentials(prop, sa)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return await asyncio.to_thread(getattr(ga_service, fn_name), pid, sad)
    except Exception as e:
        # GA API 權限/資源設定錯會走這裡（ga_service 已把 Google 錯誤訊息抽出）
        raise HTTPException(status_code=502, detail=f"GA 資料讀取失敗：{e}")


@router.get("/realtime")
async def realtime(request: Request):
    """即時在線活躍人數 + 熱門頁面 Top 5。"""
    return await _report(request, "realtime")


@router.get("/summary")
async def summary(request: Request):
    """今日 + 近 7 天訪客/工作階段/瀏覽 + 7 天趨勢 + 熱門頁面。"""
    return await _report(request, "summary")
