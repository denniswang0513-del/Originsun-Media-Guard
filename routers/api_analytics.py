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


_CONFIG_KEY = "analytics.dashboard_config"


async def _load_settings_dict():
    from services.website import settings_service
    factory = db_factory_or_503()
    async with factory() as session:
        return await settings_service.get_all_settings(session)


async def _load_creds():
    """回 (property_id_raw, service_account_json_raw) — 從 website_settings。"""
    s = await _load_settings_dict()
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


async def _creds_or_400(settings: dict):
    from services import ga_service
    try:
        return ga_service.parse_credentials(
            settings.get("analytics.ga_property_id"),
            settings.get("analytics.ga_service_account_json"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/realtime")
async def realtime(request: Request):
    """即時在線活躍人數 + 熱門頁面 Top 5。"""
    _guard(request)
    from services import ga_service
    settings = await _load_settings_dict()
    pid, sad = await _creds_or_400(settings)
    try:
        return await asyncio.to_thread(ga_service.realtime, pid, sad)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GA 資料讀取失敗：{e}")


@router.get("/summary")
async def summary(request: Request):
    """依後台設定回：選定指標的今日/近 N 天 + 趨勢 + 熱門頁面（標題或路徑）。"""
    _guard(request)
    from services import ga_service
    settings = await _load_settings_dict()
    pid, sad = await _creds_or_400(settings)
    cfg = settings.get(_CONFIG_KEY)
    try:
        return await asyncio.to_thread(ga_service.summary, pid, sad, cfg)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GA 資料讀取失敗：{e}")


@router.get("/page-titles")
async def page_titles(request: Request):
    """{正規化路徑: 舊站頁面標題} — 轉址管理對照用。未設 GA 回空 map（不報錯）。"""
    _guard(request)
    from services import ga_service
    settings = await _load_settings_dict()
    try:
        pid, sad = ga_service.parse_credentials(
            settings.get("analytics.ga_property_id"),
            settings.get("analytics.ga_service_account_json"))
    except ValueError:
        return {"titles": {}, "configured": False}
    try:
        titles = await asyncio.to_thread(ga_service.page_titles, pid, sad)
        return {"titles": titles, "configured": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GA 資料讀取失敗：{e}")


@router.get("/config")
async def get_config(request: Request):
    """儀表板顯示設定 + 指標目錄（給「⚙️ 設定」modal 用）。"""
    _guard(request)
    from services import ga_service
    settings = await _load_settings_dict()
    return {
        "config": ga_service.normalize_config(settings.get(_CONFIG_KEY)),
        "catalog": [{"name": k, "label": v["label"]} for k, v in ga_service.METRIC_CATALOG.items()],
        "windows": list(ga_service._ALLOWED_WINDOWS),
    }


@router.put("/config")
async def put_config(request: Request):
    """存儀表板顯示設定（analytics.dashboard_config）— 純後台、不觸發官網 rebuild。"""
    _guard(request)
    from services import ga_service
    from services.website import settings_service
    body = await request.json()
    cfg = ga_service.normalize_config(body)   # 正規化防呆後才存
    factory = db_factory_or_503()
    async with factory() as session:
        await settings_service.update_settings(session, {_CONFIG_KEY: cfg}, updated_by="ga-dashboard")
    return {"ok": True, "config": cfg}
