"""routers/api_project_flow.py — 專案工作流（階段 × 五軌進度）

規格正本 `docs/PROPOSAL_PLANNER.md` §14。範本與判定邏輯的正本在
`core.project_flow`（純函式、有單元測試）；這裡只做 DB 讀取與聚合。

🔴 **這支不儲存任何別處已有的東西**（owner 2026-08-14「各自工作、資料放在
正確的位置」）：報價住報價管理、發票住帳務、素材住影像紀錄、歸檔清單與專案
回顧住 `crm_projects.archive_checklist / review_kpta`（完稿結案分頁）——
這裡查詢時把它們聚合成燈號，**不搬不抄**。整個功能唯一的新儲存是手動勾選
（`crm_projects.flow_checks`，階段二才寫）。

守衛：讀取用 `proposal_auth`（admin / preprod_proposals / preprod_plan /
crm_projects）—— 公司內部進度透明，而且提案頁本來就是這個閘。
⚠️ 寫入端（階段二）的守衛尚未定案，見 §14.10 校正 4：CRM 後端目前整片是
`check_admin`（Lv3），開模組級寫入要 owner 拍板。

⚠️ 這些端點**不在**對外白名單 —— 內部進度、人名、金額訊號都不給客戶
`?t=` 連結看（tests/unit/test_public_surface.py 守著）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from core import project_archive as pa
from core import project_flow as pf
from routers.api_proposals import _plan_started, proposal_auth

router = APIRouter(prefix="/api/v1/projects", tags=["專案工作流"])


def _require_db():
    from core import state
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫目前不可用")


async def _factory():
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫未初始化")
    return factory


async def _count(session, model, *where) -> int:
    """COUNT(*) —— 只問「有沒有」的訊號一律走這條，不把整批列拉回來。"""
    from sqlalchemy import func, select
    stmt = select(func.count()).select_from(model)
    for w in where:
        stmt = stmt.where(w)
    return int((await session.execute(stmt)).scalar() or 0)


async def _gather_facts(session, project) -> tuple[dict, dict]:
    """各正本 → AUTO 訊號的 bool（None ＝ 略過）。回 (facts, detail)。

    detail 是給 UI hover 用的「為什麼亮」—— 燈要能自己解釋，不然沒人信它。
    """
    from sqlalchemy import or_, select
    from db.models import (CrmInvoice, CrmProjectShowcase, CrmProjectStaff,
                           CrmQuotation, FootageIndex, PortalReviewLink,
                           PreprodBrief, PreprodBriefTemplate, PreprodLocationUsage,
                           PreprodProposal, PreprodReferenceLink, ProjectMediaFile,
                           Timesheet)

    pid = project.id
    f: dict = {}
    d: dict = {}

    # ── 商務軌 ──
    f["client"] = bool(project.client_id)

    quote_rows = (await session.execute(
        select(CrmQuotation.status).where(CrmQuotation.project_id == pid))).scalars().all()
    f["quote"] = len(quote_rows) > 0
    f["quote_won"] = any((s or "") == "已簽核" for s in quote_rows)
    if quote_rows:
        d["quote"] = f"報價單 {len(quote_rows)} 版"

    inv_rows = (await session.execute(
        select(CrmInvoice.payment_status)
        .where(CrmInvoice.project_id == pid,
               CrmInvoice.payment_type == "收款",
               CrmInvoice.issue_status != "作廢"))).scalars().all()
    f["invoiced"] = len(inv_rows) > 0
    # 結清＝有收款發票且沒有一張停在「未收款」。沒開過發票不算結清（否則
    # 一個還沒請款的案子會顯示「款項結清」）。
    f["settled"] = bool(inv_rows) and not any((s or "") == "未收款" for s in inv_rows)
    if inv_rows:
        unpaid = sum(1 for s in inv_rows if (s or "") == "未收款")
        d["invoiced"] = f"收款發票 {len(inv_rows)} 張"
        d["settled"] = "全部收訖" if not unpaid else f"還有 {unpaid} 張未收款"

    # ── 企劃軌（一專案可並行多筆提案 → 訊號聚合所有衛星）──
    props = (await session.execute(
        select(PreprodProposal.id, PreprodProposal.status, PreprodProposal.plan)
        .where(PreprodProposal.project_id == pid))).all()
    f["proposal"] = len(props) > 0
    f["ideation"] = any(_plan_started(p.plan) for p in props)
    statuses = {(p.status or "") for p in props}
    f["pitched"] = bool(statuses & {"已提案", "入圍", "成案"})
    f["won"] = "成案" in statuses
    if props:
        d["proposal"] = f"提案 {len(props)} 筆"
    prop_ids = [p.id for p in props]
    f["brief"] = bool(prop_ids) and await _count(
        session, PreprodBrief, PreprodBrief.proposal_id.in_(prop_ids)) > 0

    # ── 製作軌 ──
    f["staffed"] = await _count(session, CrmProjectStaff,
                                CrmProjectStaff.project_id == pid) > 0
    f["footage_in"] = await _count(session, ProjectMediaFile,
                                   ProjectMediaFile.project_id == pid) > 0
    # ⚠ Sheet 同步的工時列 project_id 可能還沒對映到（見 db.models.Timesheet），
    # 所以名稱也認 —— 只認得 id 的話，團隊明明在填工時卻不亮燈。
    ts_where = [Timesheet.project_id == pid]
    if (project.name or "").strip():
        ts_where.append(Timesheet.project_name == project.name.strip())
    f["timesheet"] = await _count(session, Timesheet, or_(*ts_where)) > 0
    f["approved"] = await _count(session, PortalReviewLink,
                                 PortalReviewLink.project_id == pid,
                                 PortalReviewLink.status == "已核准") > 0

    # ── 交付軌（讀既有的歸檔清單 / KPTA / 上架作品，不另存一份）──
    arch_rows = pa.rows(project.archive_checklist)
    by_key = {r["key"]: r for r in arch_rows}
    final_row = by_key.get("final")
    f["final"] = bool(final_row and final_row["status"] in (pa.DONE, pa.NA))
    f["archived"] = pa.progress_of(arch_rows)["ready"]
    d["archived"] = f"{pa.progress_of(arch_rows)['done']}/{len(arch_rows)} 項已收"

    kpta = pa.kpta(project.review_kpta)
    f["retro"] = any((v or "").strip() for v in kpta.values())

    works = (await session.execute(
        select(CrmProjectShowcase).where(CrmProjectShowcase.project_id == pid))).scalars().all()
    f["work_ready"], f["published"], wd = _work_signals(works, project)
    d.update(wd)

    # ── 收割軌（PARA：可重用的東西升級進 Resources 了嗎）──
    f["h_tpl"] = await _count(session, PreprodBriefTemplate,
                              PreprodBriefTemplate.source_project_id == pid) > 0
    f["h_refs"] = await _count(session, PreprodReferenceLink,
                               PreprodReferenceLink.target_type == "crm_project",
                               PreprodReferenceLink.target_id == pid) > 0
    f["h_loc"] = await _count(session, PreprodLocationUsage,
                              PreprodLocationUsage.project_id == pid) > 0
    f["h_footage"] = await _count(session, FootageIndex,
                                  FootageIndex.project_id == pid) > 0
    return f, d


def _work_signals(works, project):
    """1:N 作品 → (上架素材完成度, 官網上線, detail)。

    「不上官網」的專案不該卡在這兩盞燈上 → 回 None（＝略過）。
    多作品時全部齊了才算完成 —— 有一支沒素材就是還沒好。
    """
    from core.crm_logic import work_completeness
    stage = (project.website_prod_stage or "")
    if not works:
        return (None, None, {}) if stage == "不上官網" else (False, False, {})
    if stage == "不上官網" or all((w.prod_stage or "") == "不上官網" for w in works):
        return None, None, {"work_ready": "標記為不上官網"}

    ready = 0
    for w in works:
        c = work_completeness(video_url=w.video_url, gallery=w.gallery,
                              cover_url=w.cover_url, description=w.description,
                              credits=w.credits)
        if all(c.values()):
            ready += 1
    published = sum(1 for w in works if bool(w.published))
    detail = {"work_ready": f"{ready}/{len(works)} 支作品素材齊全",
              "published": f"{published}/{len(works)} 支已上線"}
    return ready == len(works), published == len(works), detail


@router.get("/{project_id}/flow")
async def get_project_flow(project_id: str, request: Request):
    """專案工作流：階段（單線）+ 五軌進度（多方前進）+ 推進建議。"""
    proposal_auth(request)
    _require_db()
    factory = await _factory()
    async with factory() as session:
        from db.models import CrmProject
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="專案不存在")

        facts, detail = await _gather_facts(session, project)
        facts["_detail"] = detail
        built = pf.build(facts, getattr(project, "flow_checks", None))
        stage = pf.stage_view(project.status)
        return {
            "project_id": project.id,
            "project_name": project.name,
            "stage": stage,
            "tracks": built["tracks"],
            "missing": pf.missing_for(built["_state"], stage["next"]) if stage["next"] else [],
        }
