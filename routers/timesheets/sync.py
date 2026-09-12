# -*- coding: utf-8 -*-
"""routers/timesheets/sync.py —— 工作追蹤 API 的一段：Sheet 進來的路：Apps Script 推 /ingest、主控端定時拉 /pull、週一 digest

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
import core.state as state
import secrets
from fastapi import HTTPException, Request
from core.auth import check_admin
from core.db_guard import db_factory_or_503
from core.schemas import TimesheetDigestSettings, TimesheetIngestRequest, TimesheetPullSettings
from services import timesheet_digest, timesheet_puller
from services.timesheet_ingest import ingest
from ._shared import _CANDS_CACHE, _MAX_ROWS_PER_CALL, _get_or_create_ingest_token, router


@router.get("/ingest_token")
async def get_ingest_token(request: Request):
    """取同步 token（admin）— 貼進 Apps Script 的 TOKEN 常數。"""
    check_admin(request)
    return {"token": _get_or_create_ingest_token()}


@router.post("/ingest")
async def ingest_rows(req: TimesheetIngestRequest, request: Request):
    """Apps Script 批次上行。冪等：row_hash 重複的列自動跳過。"""
    expected = _get_or_create_ingest_token()
    got = request.headers.get("X-Timesheet-Token", "")
    if not got or not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="X-Timesheet-Token 無效")
    if len(req.rows) > _MAX_ROWS_PER_CALL:
        raise HTTPException(status_code=422, detail=f"單次上限 {_MAX_ROWS_PER_CALL} 列，分批送")
    if not state.db_online:
        # Apps Script 端會重試，給明確訊息（刻意不同於通用 503）
        raise HTTPException(status_code=503, detail="資料庫離線，稍後重送（Apps Script 會重試）")
    factory = db_factory_or_503()
    # 寫入規則（去重／手填優先／對映）只有 services.timesheet_ingest 那一份 —— 拉取 runner 也走它
    async with factory() as session:
        _CANDS_CACHE["val"] = None       # 工時變了，類似專案候選重算
        return await ingest(session, req.rows, req.source)


# ── 主控端定時拉 Sheet／週一 digest（services；取代 Apps Script 推）──────────

@router.get("/pull")
async def get_pull(request: Request):
    check_admin(request)
    return timesheet_puller.get_pull_settings()


@router.put("/pull")
async def put_pull(req: TimesheetPullSettings, request: Request):
    """改 enabled／sheet_id／cron（admin）；cron 錯 → 422。"""
    check_admin(request)
    try:
        return timesheet_puller.update_pull_settings(req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"cron 格式錯誤：{e}")


@router.post("/pull")
async def run_pull_now(request: Request):
    """立刻拉一次（admin；不看 enabled，dev 也能手動測）。"""
    check_admin(request)
    return await timesheet_puller.run_pull(force=True)


@router.get("/digest")
async def get_digest(request: Request):
    check_admin(request)
    return timesheet_digest.settings.get()


@router.put("/digest")
async def put_digest(req: TimesheetDigestSettings, request: Request):
    check_admin(request)
    try:
        return timesheet_digest.settings.update(req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"cron 格式錯誤：{e}")


@router.post("/digest")
async def send_digest_now(request: Request, preview: bool = False):
    """立刻算上週 digest；preview=1 只回文字不發送。"""
    check_admin(request)
    if preview:
        factory = db_factory_or_503()
        async with factory() as session:
            d = await timesheet_digest.build_digest(session)
        return {"status": "preview", "text": d["text"]}
    return await timesheet_digest.send_digest(force=True)
