"""routers/api_milestones.py — 每週專案里程碑（owner 2026-09-07；示範 frontend/demo/milestones.html 定稿）。

守衛＝登入即可（owner：大家都能編）。規則 core.milestone_logic、I/O services.milestone_service，這裡只有 HTTP 形狀。
"""
from fastapi import APIRouter, Request

from core.auth import check_logged_in
from core.db_guard import db_factory_or_503
from core.schemas import MilestoneDefer, MilestoneDone, MilestoneSave
from services import milestone_service

router = APIRouter(prefix="/api/v1/milestones", tags=["milestones"])


def _who(request: Request) -> str:
    p = check_logged_in(request) or {}
    return p.get("username") or p.get("sub") or ""


@router.get("/week")
async def milestones_week(request: Request, start: str = ""):
    """這週（start＝任一天，空＝今天）：有里程碑／延過來的案 ＋ 最近有紀錄的案，各帶里程碑與工時；今天到期；人員清單。"""
    _who(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await milestone_service.week_payload(session, start)


@router.post("/week/save")
async def milestones_save(body: MilestoneSave, request: Request):
    """彈窗「儲存」：一次套用（新增／改／勾完成／刪）。"""
    who = _who(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await milestone_service.save_week(session, body.week_start, body.items, who)


@router.post("/{mid}/done")
async def milestone_done(mid: str, body: MilestoneDone, request: Request):
    """週表那條帶上直接勾完成（不用開彈窗）。"""
    who = _who(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await milestone_service.set_done(session, mid, body.done, who)


@router.post("/{mid}/defer")
async def milestone_defer(mid: str, body: MilestoneDefer, request: Request):
    who = _who(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await milestone_service.defer(session, mid, body.week_start, who)


@router.get("/project/{project_id}")
async def milestones_of_project(project_id: str, request: Request):
    """專案頁：按週列這個案的里程碑（專案檔案頁、CRM 專案詳情同一份）。"""
    _who(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return {"weeks": await milestone_service.project_milestones(session, project_id)}
