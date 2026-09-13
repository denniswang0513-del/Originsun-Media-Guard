"""routers/crm/costs.py — 專案成本：雜支登記（含分享連結）／收據儲存／財務摘要，
＋ 子表與成本估算兩支共用的小 helper（`_fmt_date`／`_get_first_group_id`／`_resolve_target_group`／`_project_misc_pct`）。

2026-09-13 從 1,784 行拆成三支（純搬移，行為不變；分層 costs ← cost_groups ← cost_lines，沒有循環）：
  costs.py        雜支／收據／財務摘要（本檔）
  cost_groups.py  成本子表（cost groups）
  cost_lines.py   成本估算 cost lines ＋ 範本 ＋ 從報價單匯入
路由仍註冊在 _shared.router 上，URL 不變；`routers/crm/__init__` 三支都 import 才會註冊。
掃原始碼的測試用 `_srcscan.costs_src()`（三支串起來），別指單一檔。
upload_expense_receipt 內以 __file__ 推 uploads 路徑的運算多包一層
os.path.dirname（檔案移深一層，維持原專案根目錄基準）。
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, UploadFile, File, Query
from core.no_store import no_store_file

from core.auth import check_admin, check_admin_or_module
from core.drive_map import to_canonical_path, to_local_path
from core.money import check_money
from core.project_folders import BLOCKED_UPLOAD_EXTS, stream_to_disk
from core.schemas import (ProjectExpensePayload, ProjectExpensePatchPayload,
                          ExpenseLinkPayload)

from ._shared import (router, public_router, _check_auth, _check_project_write_auth, money_dep, _require_db,
                      _get_factory, _fmt_day, _now, _parse_day, _mint_token_generic, _verify_token_generic)

try:
    from ._shared import (select,
                          CrmProject, CrmProjectExpense, CrmProjectStaff,
                          CrmProjectCostLine, CrmProjectCostGroup, CrmStaff,
                          CrmPaymentRequest, CrmExpenseLink)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── 雜支登記分享連結（token；給沒有帳號的現場人員）────────────
#
# 為什麼是 token 而不是「id 猜不到」：2026-08-14 的 `_crm_read_guard` 之後，
# `/public/projects/{id}/…` 那批要登入才打得到（CRM 是商務資料，master 經
# cloudflared 對外）。但現場登記雜支的是外部場記／臨時人員，他們沒有帳號。
# token 才是「發一條連結給特定一件事」的憑證 —— 這也正是影像紀錄那套的做法，
# 所以直接用同一組 `_mint_token_generic` / `_verify_token_generic`。
#
# ⚠️ 這四支掛 `public_router`（NAS 對外容器也會掛到）：token 就是憑證，沒有
# token 打不到任何東西。曝露面由 tests/unit/test_public_surface.py 列舉斷言。
EXPENSE_LINK_SCOPE = "expense_link"

# 「行政雜支」這個階段值跟 crm_project_expenses 講的是同一件事（雜支現在有自己
# 的登記區）。成本行那邊再算一次＝同一筆錢在對照表的「人員」與「雜支」兩欄各
# 出現一次，成本與毛利一起走味。
#
# 🔴 述詞收成一份，因為**整案（financial-summary）與子表（cost-groups）兩處都要
# 用同一條** —— 只濾一邊的話 Σ子表 ≠ 整案，而那正是 2026-09-05 稽核在查的東西。
# api_finance_projects._crm_costs 早就這樣濾了，costs.py 這兩處原本沒有。
# （稽核當下全庫 0 筆，所以還沒發作，但那是現況不是保證。）
ADMIN_PHASE = "行政雜支"


async def _resolve_link(session, token: str):
    """驗 token → 回 (link_row, project_id, fixed_group_id)。

    `kind == 'group'` 的連結**綁死**那個子表：登記時不讓前端指定別的
    cost_group_id —— 連結的意義就是「這一天的雜支」。
    """
    link = await _verify_token_generic(session, token, EXPENSE_LINK_SCOPE,
                                       CrmExpenseLink, "enabled", require_editable=True)
    if link.kind == "group":
        g = await session.get(CrmProjectCostGroup, link.id)
        if not g:
            raise HTTPException(status_code=404, detail="子表已刪除")
        return link, g.project_id, g.id
    return link, link.id, None


# 雜支登記連結的發／停用 —— 跟雜支本體同一把 crm_projects（owner 2026-09-08：
# 內部雜支寫入歸 crm_projects；連結是專案頁「雜支」區塊上的按鈕）。
@router.post("/expense-links", dependencies=[Depends(_check_project_write_auth)])
async def mint_expense_link(req: ExpenseLinkPayload):
    """發（或重置）一條雜支登記連結。冪等：`rotate=False` 時重用既有 token，
    所以後台重複點「複製連結」不會讓已發出去的那條失效。"""
    if req.kind not in ("project", "group"):
        raise HTTPException(status_code=400, detail="kind 只能是 project 或 group")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        model = CrmProject if req.kind == "project" else CrmProjectCostGroup
        if not await session.get(model, req.target_id):
            raise HTTPException(status_code=404, detail="找不到目標")
        token, row = await _mint_token_generic(
            session, CrmExpenseLink, req.target_id, EXPENSE_LINK_SCOPE,
            reuse_existing=not req.rotate, row_defaults={"kind": req.kind},
            on_rotate=lambda r: setattr(r, "kind", req.kind))
        row.kind = req.kind          # 既有 row 換 kind（重用路徑不走 on_rotate）
        if row.enabled is None:
            row.enabled = True
        await session.commit()
        return {"token": token, "kind": row.kind, "enabled": bool(row.enabled)}


@router.post("/expense-links/{target_id}/enabled", dependencies=[Depends(_check_project_write_auth)])
async def set_expense_link_enabled(target_id: str, enabled: bool = Query(True)):
    """停用／重新啟用一條連結（停用後那條網址回 403，不必換 token）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        row = await session.get(CrmExpenseLink, target_id)
        if not row:
            raise HTTPException(status_code=404, detail="找不到此連結")
        row.enabled = enabled
        row.updated_at = _now()
        await session.commit()
        return {"status": "ok", "enabled": enabled}


@public_router.get("/public/expense/{token}")
async def get_expense_link_info(token: str):
    """連結首屏：專案名 +（專案連結才有的）子表清單。

    金額欄（子表預算）交給 MoneyRedactRoute —— 匿名一律沒有 money_view，
    所以現場的人看得到「哪個專案、哪一天」，看不到預算數字。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        link, project_id, group_id = await _resolve_link(session, token)
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        out = {"kind": link.kind,
               "project": {"id": proj.id, "name": proj.name},
               "cost_groups": [], "group": None}
        if link.kind == "group":
            g = await session.get(CrmProjectCostGroup, group_id)
            out["group"] = {"id": g.id, "name": g.name,
                            "shoot_date": _fmt_date(g.shoot_date),
                            "notes": g.notes or "",
                            "budget_amount": g.budget_amount,
                            "misc_budget_amount": g.misc_budget_amount}
        else:
            groups = (await session.execute(
                select(CrmProjectCostGroup)
                .where(CrmProjectCostGroup.project_id == project_id)
                .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
            )).scalars().all()
            out["cost_groups"] = [{"id": g.id, "name": g.name,
                                   "shoot_date": _fmt_date(g.shoot_date)} for g in groups]
        return out


@public_router.get("/public/expense/{token}/expenses")
async def list_expense_link_expenses(token: str):
    """這條連結範圍內已登記的雜支（子表連結只看那一天的）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        _link, project_id, group_id = await _resolve_link(session, token)
        q = select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        if group_id:
            q = q.where(CrmProjectExpense.cost_group_id == group_id)
        rows = (await session.execute(q)).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


@public_router.post("/public/expense/{token}/expenses")
async def add_expense_link_expense(token: str, req: ProjectExpensePayload):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        _link, project_id, group_id = await _resolve_link(session, token)
        if group_id:
            req.cost_group_id = group_id     # 連結綁死那一天，不吃前端指定的
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@public_router.post("/public/expense/{token}/receipts/{expense_id}")
async def upload_expense_link_receipt(token: str, expense_id: str,
                                      file: UploadFile = File(...)):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        _link, project_id, _gid = await _resolve_link(session, token)
    return await _save_receipt(project_id, expense_id, file)


# ── Project Expense (雜支) Endpoints ────────────────────────

@router.get("/projects/{project_id}/expenses", dependencies=[Depends(money_dep)])
async def list_project_expenses(project_id: str, group_id: Optional[str] = Query(None)):
    """列出專案雜支。
    - 無 group_id：回整個專案的雜支 + 多一層 grouped_by_group
    - 有 group_id：只回該子表的雜支

    🔴 零用金整合後這支有**兩個**消費端（CRM 帳務子視圖、/project.html 的
    「支出」分頁），payload 因此帶了誰墊的 / 會計項目 / 請款狀態。不另開一支
    「專案的零用金」端點 —— 那會讓「這個案子花了多少」有兩個互相矛盾的答案。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        if group_id:
            q = q.where(CrmProjectExpense.cost_group_id == group_id)
        rows = (await session.execute(q)).scalars().all()

        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()
        _pct = await _project_misc_pct(session, project_id)

        # 墊錢的人：一次查完，不在迴圈裡逐筆撈
        sids = {e.staff_id for e in rows if e.staff_id}
        names = dict((await session.execute(
            select(CrmStaff.id, CrmStaff.name).where(CrmStaff.id.in_(sids))
        )).all()) if sids else {}

        # 這一列請過款沒 —— 走**硬連結** `expense_id`（同 api_finance_projects
        # 的 `claimed`），不是人名＋金額目測：同一案同金額的雜支很常見
        # （8/10 那趟的住宿／飲食／交通），目測分不出誰請過。
        # 一次撈這批列的，不是整個專案的（同 project_names_map 的理由）。
        eids = [e.id for e in rows]
        claims = {eid: (pstatus or "", ppay) for eid, pstatus, ppay in (
            await session.execute(
                select(CrmPaymentRequest.expense_id, CrmPaymentRequest.payment_status,
                       CrmPaymentRequest.id)
                .where(CrmPaymentRequest.expense_id.in_(eids)))
        ).all()} if eids else {}

    def _e_to_dict(e):
        claim_status, claim_pid = claims.get(e.id, ("", ""))
        return {
            "id": e.id, "category": e.category,
            "cost_group_id": e.cost_group_id or "",
            "estimated": e.estimated, "actual": e.actual,
            "sub_item": e.sub_item or "", "payee": e.payee or "",
            "advance_id": e.advance_id or "",
            "receipt_url": e.receipt_url or "", "notes": e.notes or "",
            "created_at": _fmt_date(e.created_at),
            # 零用金（docs/PETTY_CASH_PLAN.md §3.5）
            # 🔴 專案成本認列用 expense_date（消費日），不是 created_at（登記日）
            # 也不是請款/匯款日 —— 否則毛利會隨「誰拖著沒請款」漂移。
            "expense_date": _fmt_date(e.expense_date),
            "item": e.item or "",
            "staff_id": e.staff_id or "",
            "staff_name": names.get(e.staff_id, "") if e.staff_id else (e.payee or ""),
            "invoice_no": e.invoice_no or "",
            "has_invoice": bool(e.has_invoice),
            "status": e.status or "",
            "claim_id": e.claim_id or "",
            # 送請款（owner 2026-09-02「雜支可以送請款進請款單」）：
            # 一列一張，硬連結記在請款單的 `expense_id` 上。重複請款由
            # `POST /payments` 的 409 守著（前端換按鈕擋不住雙擊／兩個分頁）。
            "payment_id": claim_pid,
            "payment_status": claim_status,
        }

    expenses = [_e_to_dict(e) for e in rows]
    grouped_by_group = [{
        "group_id": g.id, "group_name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "misc_budget_amount": g.misc_budget_amount,   # 可為 None＝未設，不是 0
        "expenses": [_e_to_dict(e) for e in rows if e.cost_group_id == g.id],
    } for g in groups]
    # 雜支預算：跨子表加總。全部未設 → None（畫面要畫「未設」而不是「預算 0」）。
    # ⚠ 同語義的 SQL 版在 financial-summary 的 misc_budget_total（SUM 忽略
    # NULL）—— 改「0 是否視為未設」這類語義時兩處要一起動。
    from core.crm_logic import misc_budget_total_of
    misc_budget_total = misc_budget_total_of([(g.budget_amount, g.misc_budget_amount) for g in groups], _pct)
    return {"expenses": expenses, "grouped_by_group": grouped_by_group,
            "misc_budget_total": misc_budget_total}


def _guard_claimed(e):
    """🔴 已進零用金請款單的列，專案頁不准改也不准刪。

    claim_id 一設，這筆的金額就是某張請款單 total_claim 的一部分 —— 從這裡
    改金額或刪列，單子總額會無聲地對不上（員工那邊看到的還是舊數字）。
    要動就先去零用金頁把那張請款單退回。
    """
    if e.claim_id:
        raise HTTPException(
            status_code=409,
            detail="這筆已進零用金請款單，改了金額單子就對不上。"
                   "請到零用金頁把該請款單退回後再處理。")


async def _create_expense(session, project_id: str, req, advance_id=None, payee_override=None):
    """建立專案雜支的共用 helper。"""
    cost_group_id = await _resolve_target_group(session, project_id, req.cost_group_id)
    e = CrmProjectExpense(
        id=uuid.uuid4().hex, project_id=project_id,
        cost_group_id=cost_group_id,
        category=req.category, estimated=req.estimated,
        actual=req.actual, sub_item=req.sub_item or None,
        payee=payee_override or req.payee or None,
        advance_id=advance_id or req.advance_id or None,
        notes=req.notes, created_at=_now(),
        expense_date=_parse_day(req.expense_date),
    )
    session.add(e)
    await session.commit()
    return e


@router.post("/advance/{advance_id}/expenses")
async def add_advance_expense(advance_id: str, req: ProjectExpensePayload, request: Request):
    """公開端點：透過預支款 ID 登記支出（不需登入）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        adv = await session.get(CrmPaymentRequest, advance_id)
        if not adv or not adv.is_advance:
            raise HTTPException(status_code=404, detail="找不到此預支款")
        if not adv.project_id:
            raise HTTPException(status_code=400, detail="此預支款未綁定專案")
        e = await _create_expense(session, adv.project_id, req, advance_id=advance_id, payee_override=adv.payee_name)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.post("/public/projects/{project_id}/expenses")
async def add_public_project_expense(project_id: str, req: ProjectExpensePayload, request: Request):
    """公開端點：透過專案 ID 登記雜支（不需登入）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.get("/public/cost-groups/{group_id}/info")
async def get_public_cost_group_info(group_id: str, request: Request):
    """公開端點：取得子表 + 所屬專案資訊（不需登入，供 /group-expense.html 使用）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        proj = await session.get(CrmProject, g.project_id)
    return {
        "group": {
            "id": g.id, "name": g.name,
            "shoot_date": _fmt_date(g.shoot_date),
            "notes": g.notes or "",
            "budget_amount": g.budget_amount,
            "misc_budget_amount": g.misc_budget_amount,
        },
        "project": {"id": proj.id, "name": proj.name} if proj else None,
    }


@router.get("/public/cost-groups/{group_id}/expenses")
async def list_public_cost_group_expenses(group_id: str, request: Request):
    """公開端點：列出該子表最近 20 筆已登記雜支（前端只渲染 10 筆，多撈一些保留彈性）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.cost_group_id == group_id)
            .order_by(CrmProjectExpense.created_at.desc())
            .limit(20)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


@router.post("/public/cost-groups/{group_id}/expenses")
async def add_public_cost_group_expense(group_id: str, req: ProjectExpensePayload, request: Request):
    """公開端點：登記雜支到指定子表（強制 cost_group_id = URL 參數，防呼叫端注入）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        req.cost_group_id = group_id  # 覆寫 payload 以強制歸屬
        e = await _create_expense(session, g.project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}, "project_id": g.project_id}


@router.post("/public/cost-groups/{group_id}/receipts/{expense_id}")
async def upload_public_cost_group_receipt(group_id: str, expense_id: str, request: Request, file: UploadFile = File(...)):
    """公開端點：上傳收據到指定子表的 expense。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        e = await session.get(CrmProjectExpense, expense_id)
        if not e or e.cost_group_id != group_id:
            raise HTTPException(status_code=404, detail="此雜支不屬於此子表")
    return await _save_receipt(g.project_id, expense_id, file)


@router.get("/public/projects/{project_id}/info")
async def get_public_project_info(project_id: str, request: Request):
    """公開端點：取得專案名稱 + 子表列表（不需登入，供公開雜支頁選子表）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()
    return {
        "id": proj.id, "name": proj.name,
        "cost_groups": [{
            "id": g.id, "name": g.name,
            "shoot_date": _fmt_date(g.shoot_date),
        } for g in groups],
    }


@router.get("/public/projects/{project_id}/expenses")
async def list_public_project_expenses(project_id: str, request: Request):
    """公開端點：列出專案雜支（不需登入）。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


# ── 雜支本體（內部路）：owner 2026-09-08 拍板歸 crm_projects（做專案的人自己登，
# 跟同畫面的預算表一致）。留管理員的只有刪雜支與收據根目錄（ADMIN_ONLY_ACTIONS）。
@router.post("/projects/{project_id}/expenses")
async def add_project_expense(project_id: str, req: ProjectExpensePayload, request: Request):
    _check_project_write_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.patch("/project-expenses/{expense_id}")
async def patch_project_expense(expense_id: str, req: ProjectExpensePatchPayload, request: Request):
    """部分更新雜支欄位（供前端 inline edit 使用）。僅動 payload 有給的欄位。"""
    _check_project_write_auth(request)
    _require_db()
    data = req.model_dump(exclude_unset=True)  # 只要有帶就進，None 也算
    if not data:
        return {"status": "ok"}
    factory = await _get_factory()
    _NULLABLE_TEXT = {"sub_item", "payee", "notes", "advance_id", "cost_group_id"}
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        _guard_claimed(e)
        if "payee" in data and e.staff_id:
            raise HTTPException(status_code=409,
                                detail="這筆是零用金列，收款人就是請款員工本人，"
                                       "專案頁不能改。")
        for key, val in data.items():
            if key == "expense_date":
                # 字串日期要走 _parse_day（setattr 塞字串進 timestamptz 會炸）
                e.expense_date = _parse_day(val)
                continue
            if key in _NULLABLE_TEXT and val == "":
                val = None
            setattr(e, key, val)
        await session.commit()
    return {"status": "ok"}


@router.patch("/project-expenses/link-advance")
async def link_expenses_to_advance(request: Request):
    """批次綁定/解除雜支與預支款。body: {expense_ids: [...], advance_id: "..." 或 ""}"""
    _check_project_write_auth(request)
    _require_db()
    body = await request.json()
    expense_ids = body.get("expense_ids", [])
    advance_id = body.get("advance_id")
    if not expense_ids:
        raise HTTPException(status_code=400, detail="缺少 expense_ids")
    factory = await _get_factory()
    async with factory() as session:
        for eid in expense_ids:
            e = await session.get(CrmProjectExpense, eid)
            if e:
                _guard_claimed(e)   # 已進請款單的列，預支綁定也不准從這裡動
                e.advance_id = advance_id if advance_id else None
        await session.commit()
    return {"status": "ok", "linked": len(expense_ids)}


@router.put("/project-expenses/{expense_id}")
async def update_project_expense(expense_id: str, req: ProjectExpensePayload, request: Request):
    _check_project_write_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        _guard_claimed(e)
        if e.staff_id and (req.payee or None) != (e.payee or None):
            raise HTTPException(status_code=409,
                                detail="這筆是零用金列，收款人就是請款員工本人，"
                                       "專案頁不能改。")
        e.category = req.category
        e.estimated = req.estimated
        e.actual = req.actual
        e.sub_item = req.sub_item or None
        e.payee = req.payee or None
        e.advance_id = req.advance_id or None
        e.notes = req.notes
        # 沒帶＝保留原值（schema 有預設值，整包覆寫會把日期洗掉 —— v2.0 的坑）
        e.expense_date = _parse_day(req.expense_date) or e.expense_date
        if req.cost_group_id:
            e.cost_group_id = req.cost_group_id
        await session.commit()
    return {"status": "ok"}


@router.delete("/project-expenses/{expense_id}")
async def delete_project_expense(expense_id: str, request: Request):
    _check_auth(request)      # 刪雜支留管理員（ADMIN_ONLY_ACTIONS，owner 2026-09-08）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        _guard_claimed(e)
        await session.delete(e)
        await session.commit()
    return {"status": "ok"}


@router.post("/project-expenses/{expense_id}/receipt")
async def upload_expense_receipt(expense_id: str, request: Request, file: UploadFile = File(...)):
    """預支款雜支登記頁（`/advance-expense.html`）的收據上傳。

    🔴 2026-09-10：改成走跟其他三個入口**同一支** `_save_receipt`。原本這支自己寫了
       一份：`await file.read()` 整檔進記憶體（沒有上限）、檔名只有 `{expense_id}.ext`
       （看不出是誰哪天的什麼）、寫死 `<repo>/uploads/receipts`（後台把根目錄指到 NAS
       也沒用）、而且存進 DB 的是 `/uploads/…` **網址**不是路徑 —— 那是第四種形狀，
       前端每個顯示收據的地方都要多認一種。生產 0 筆是這個形狀，所以直接收掉。
    """
    _check_project_write_auth(request)
    _require_db()
    # 這支原本的副檔名**白名單**比 _save_receipt 的黑名單嚴。改走共用不是放寬的理由，
    # 所以留在這裡（這頁只會拍照或選 PDF）。
    ext = os.path.splitext(file.filename or "img.jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"):
        raise HTTPException(status_code=400, detail="不支援的檔案格式")
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        project_id = e.project_id
    return await _save_receipt(project_id, expense_id, file)


# ── Per-Project Receipt Storage ────────────────────────────

@router.post("/projects/{project_id}/receipts/{expense_id}")
async def upload_project_receipt(project_id: str, expense_id: str, request: Request, file: UploadFile = File(...)):
    """上傳收據到專案收據資料夾。"""
    _check_project_write_auth(request)
    return await _save_receipt(project_id, expense_id, file)


@router.post("/public/projects/{project_id}/receipts/{expense_id}")
async def upload_project_receipt_public(project_id: str, expense_id: str, request: Request, file: UploadFile = File(...)):
    """公開端點：上傳收據。"""
    _check_project_write_auth(request)   # 內部路（?project=／?group=／預支款頁要登入＋crm_projects）；外部一律走 ?t= 連結（public_router）
    return await _save_receipt(project_id, expense_id, file)


# 收據就是照片或 PDF —— 比提案資產夾小一個量級就夠
_MAX_RECEIPT_BYTES = 30 * 1024 * 1024


def _receipts_root() -> str:
    """收據儲存根目錄的**設定正本**（master 視角，就是後台那格填的字）：
    settings.receipts_root 優先，留空＝老預設 uploads/receipts（主控機本機）。
    子表自己的 receipt_path 仍最優先 —— 這裡只管兩條 fallback。

    ⚠ 這個值**不能直接拿去開檔**，要先過 `_receipt_dir()`／`to_local_path()`。
    """
    from config import load_settings
    root = (load_settings().get("receipts_root") or "").strip()
    return root or os.path.join(os.getcwd(), "uploads", "receipts")


def _receipt_dir(proj_name: str, group=None, day: str = "") -> str:
    """這筆收據該落在**這台機器看得到的**哪個資料夾。

    優先序（跟以前一樣）：子表自己的 `receipt_path` → `{根}/{專案}/{子表}`
    → 沒有專案（零用金公司支出）`{根}/_零用金/{年月}`。

    🔴 這支是**唯一**算收據資料夾的地方。原本寫檔算一次（用 `_receipts_root()`）、
       列清單又各自算一次（寫死 `os.getcwd()/uploads/receipts`）—— 後台把根目錄
       指到 NAS 之後，檔案存進 NAS 而清單去本機的 uploads 找，**收據頁永遠是空的**
       而且不會有任何錯誤。
    🔴 一律 `to_local_path`：設定與 `receipt_path` 存的都是 master 視角的路徑
       （UNC 或磁碟代號）。NAS 的容器上不翻譯就會 `makedirs` 出一個名字帶反斜線的
       資料夾在 `/app` 底下 —— 上傳回 200、DB 記下一條沒有任何一台讀得到的路徑，
       全程沒有一行 error。（同 `routers/crm/invoice_files._invoices_write_root`）
    """
    custom = getattr(group, "receipt_path", None) if group is not None else None
    if custom:
        return to_local_path(custom)
    root = to_local_path(_receipts_root())
    if proj_name:
        sub = (getattr(group, "name", None) if group is not None else None) or "main"
        return os.path.join(root, proj_name, sub)
    return os.path.join(root, "_零用金", day[:7] or "nodate")


def _stored_receipt_path(local_path: str) -> str:
    """寫完檔要存進 DB 的字串＝canonical UNC（`drive_map.to_canonical_path`）。
    收檔那台若存自己的本機路徑，別台就讀不到 —— NAS 存 `/share/…`，master 是
    Windows，翻不回去。"""
    return to_canonical_path(local_path)


async def _save_receipt(project_id: str, expense_id: str, file: UploadFile):
    import re as _re
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        # project_id 可為 None —— 零用金的公司層級支出（行政/業務推廣）沒有專案，
        # 但一樣要收據。收據儲存只有這一條路徑，不為它另開一份。
        proj = await session.get(CrmProject, project_id) if project_id else None
        if project_id and not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        exp = await session.get(CrmProjectExpense, expense_id)
        if not exp:
            raise HTTPException(status_code=404, detail="找不到此支出")

        cg = await session.get(CrmProjectCostGroup, exp.cost_group_id) if exp.cost_group_id else None
        # 🔴 日期進資料夾/檔名前走 _fmt_day 台北歸一 —— aware timestamptz 面值
        # strftime 會差一天（填 08-19 檔名變 20260818，2026-08-19 實測踩到）
        day = _fmt_day(exp.expense_date or exp.created_at)   # '' ＝ 無日期
        # 資料夾規則只有 _receipt_dir 一份（列清單那兩支也用它）
        base = _receipt_dir((proj.name or project_id) if proj else "", cg, day)
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"收據資料夾無法使用：{e}")

        # Build filename: date_category_subitem_payee_id.ext
        date_str = day.replace("-", "") or "nodate"
        cat = _re.sub(r'[\\/:*?"<>|]', '', exp.category or "misc")
        sub = _re.sub(r'[\\/:*?"<>|]', '', exp.sub_item or "")
        payee = _re.sub(r'[\\/:*?"<>|]', '', exp.payee or "")
        parts = [date_str, cat]
        if sub:
            parts.append(sub)
        if payee:
            parts.append(payee)
        parts.append(expense_id[:8])
        ext = os.path.splitext(file.filename or ".jpg")[1]
        filename = "_".join(parts) + ext

        filepath = os.path.join(base, filename)
        # 🔴 這條路徑有兩個**免登入**入口（手機版 /expense.html 讓外部人員登記
        # 雜支，那是產品設計）。免登入就更不能少了這兩道：
        #   1. 串流 + 上限 —— 原本是 `await file.read()`，整個檔進記憶體：
        #      拿到合法連結的人傳一個 2GB 的檔，生產 agent 就吃 2GB RAM。
        #   2. 副檔名黑名單 —— 副檔名直接沿用上傳者給的檔名，沒擋的話可以寫進
        #      .exe/.bat 之類的東西（收據只會是圖或 PDF）。
        if ext.lower() in BLOCKED_UPLOAD_EXTS:
            raise HTTPException(status_code=400, detail=f"不接受的檔案格式：{ext}")
        written = await asyncio.to_thread(stream_to_disk, file.file, filepath,
                                          _MAX_RECEIPT_BYTES)
        if written < 0:
            raise HTTPException(
                status_code=413,
                detail=f"收據檔超過 {_MAX_RECEIPT_BYTES // (1024 * 1024)}MB 上限")

        # 存進 DB 的是 canonical（哪台讀都翻得回自己的視角），回給前端的也是它 ——
        # 前端拿它去打 /receipt-file?path=，那支自己會翻譯。
        stored = _stored_receipt_path(filepath)
        exp.receipt_url = stored
        await session.commit()

    return {"status": "ok", "path": stored, "receipt_url": stored, "filename": filename}


@router.get("/projects/{project_id}/receipts")
async def list_project_receipts(project_id: str, request: Request):
    """列出專案下所有子表的收據檔（依 cost_group 聚合）。"""
    _check_project_write_auth(request)   # 第二批：收據清單跟雜支寫入同一把（crm_projects）
    check_money(request)                 # 收據＝金額：還要看得到錢（同專案頁預算結算的 moneyGate）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()

    result_groups = []
    for g in groups:
        # 🔴 跟寫檔同一份規則（原本這裡寫死 os.getcwd()/uploads/receipts —— 後台把
        #    根目錄指到 NAS 之後，檔案存進 NAS 而這裡去本機找，清單永遠是空的）
        base = _receipt_dir(proj.name or project_id, g)
        result_groups.append({
            "cost_group_id": g.id, "cost_group_name": g.name,
            "path": base, "receipts": _list_receipt_files(base),
        })
    return {"groups": result_groups}


@router.get("/cost-groups/{group_id}/receipts")
async def list_cost_group_receipts(group_id: str, request: Request):
    """列出單一子表收據資料夾內的所有檔案。"""
    _check_project_write_auth(request)   # 第二批：收據清單跟雜支寫入同一把（crm_projects）
    check_money(request)                 # 收據＝金額：還要看得到錢（同專案頁預算結算的 moneyGate）
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        proj = await session.get(CrmProject, g.project_id)

    base = _receipt_dir((proj.name if proj else g.project_id), g)
    return {"receipts": _list_receipt_files(base), "path": base}


def _list_receipt_files(base: str) -> list:
    """資料夾裡的收據檔。`path` 回 canonical —— 前端拿它打 `/receipt-file?path=`，
    而那支可能在**另一台**上跑（office-api），本機路徑過去讀不到。"""
    if not base or not os.path.isdir(base):
        return []
    out = []
    for fn in sorted(os.listdir(base)):
        fp = os.path.join(base, fn)
        if os.path.isfile(fp):
            out.append({"filename": fn, "path": _stored_receipt_path(fp),
                        "size": os.path.getsize(fp)})
    return out


@router.get("/receipt-file")
async def serve_receipt(path: str = Query(""), request: Request = None):
    """提供收據檔案下載/檢視（限定 uploads/、receipts_root 或子表 receipt_path）。

    🔴 白名單比對要在**翻譯之後**：DB 存的是 master 視角的路徑，而這支也在 NAS 的
       office-api 上跑。翻譯前比對＝拿兩個不同視角的字串比，可能誤放行也可能把好的
       擋掉（同 `invoice_files._local_invoice_path`）。
    🔴 前綴比對要帶 `os.sep`：純 startswith 會讓「…_舊」那種相鄰同名目錄通過。
    """
    # 收據影像＝金額：登入之外還要一把看得到它的鑰匙（員工頁自己的零用金／福委、專案頁、帳務、審核；2026-09-08 稽核）
    check_admin_or_module(request, 'money_view', 'crm_projects', 'crm_invoices', 'finance_approve', 'me_petty', 'me_benefits')
    if not path:
        raise HTTPException(status_code=404, detail="檔案不存在")
    # 相容：2026-09-10 之前 /advance-expense.html 存的是 `/uploads/receipts/x.jpg`
    # 這種**網址**而不是路徑（靠靜態掛載直接開）。改走這支之後要認得它。
    raw = path
    if raw.startswith("/uploads/"):
        raw = os.path.join(os.getcwd(), raw.lstrip("/").replace("/", os.sep))
    abs_path = os.path.abspath(to_local_path(raw))
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="檔案不存在")

    allowed = [os.path.join(os.getcwd(), "uploads"), _receipts_root()]
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        allowed += [rp for rp in (await session.execute(
            select(CrmProjectCostGroup.receipt_path)
            .where(CrmProjectCostGroup.receipt_path.isnot(None))
        )).scalars().all() if rp]
    if not any(_within(abs_path, a) for a in allowed):
        raise HTTPException(status_code=403, detail="無權存取此路徑")
    return no_store_file(abs_path)


def _within(abs_path: str, root: str) -> bool:
    """`abs_path` 在 `root` 底下嗎（root 也要翻成這台的視角再比）。"""
    ar = os.path.abspath(to_local_path(root or ""))
    return bool(ar) and (abs_path == ar or abs_path.startswith(ar + os.sep))



@router.get("/petty/receipts-root")
async def get_receipts_root(request: Request):
    """收據根目錄設定（admin 專用）。

    ⚠ 刻意不走 settings/load 整包 —— 那條回應對機密欄位是遮罩過的，前端拿
    整包改一鍵再存回會把真密碼洗成遮罩值（2026-07-10 settings 外洩修補後的
    既定風險）。這裡 server 端只讀寫這一個鍵。
    """
    check_admin_or_module(request, 'finance_approve')   # 第二批：審核者要看得到根目錄（設定仍限管理員）
    from config import load_settings
    return {"receipts_root": (load_settings().get("receipts_root") or ""),
            "default": os.path.join(os.getcwd(), "uploads", "receipts"),
            "effective": _receipts_root()}


@router.post("/petty/receipts-root")
async def set_receipts_root(request: Request):
    check_admin(request)
    from config import load_settings, save_settings
    body = await request.json()
    root = (body.get("receipts_root") or "").strip()
    if root:
        try:
            os.makedirs(root, exist_ok=True)   # 多半是 NAS 上還沒建的資料夾，順手建
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{e}")
    # settings.json 寫入也要包 —— agent 自己會定期寫 settings，撞到檔案佔用
    # 會炸成裸 500（2026-08-19 owner 第一次存 NAS 路徑就中獎，重按即成功）
    try:
        s = load_settings()
        s["receipts_root"] = root
        save_settings(s)
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"設定檔忙碌中，請再按一次儲存（{e}）")
    return {"status": "ok", "receipts_root": root, "effective": _receipts_root()}


@router.get("/projects/{project_id}/financial-summary", dependencies=[Depends(money_dep)])
async def project_financial_summary(project_id: str):
    """專案財務摘要：含稅/未稅/毛利/雜支/外包 預估vs實際。
    多子表後另附：allocated_budget_sum / groups_count / groups_missing_budget_count。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        from sqlalchemy import func as sa_func

        exp_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectExpense.estimated), 0),
                   sa_func.coalesce(sa_func.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.project_id == project_id)
        )).first()
        expense_estimated = exp_row[0] if exp_row else 0
        expense_actual = exp_row[1] if exp_row else 0

        staff_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectStaff.cost), 0),
                   sa_func.coalesce(sa_func.sum(sa_func.coalesce(CrmProjectStaff.actual_cost, CrmProjectStaff.cost)), 0))
            .where(CrmProjectStaff.project_id == project_id)
        )).first()
        staff_estimated = staff_row[0] if staff_row else 0
        staff_actual = staff_row[1] if staff_row else 0

        costline_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectCostLine.estimated_amount), 0),
                   sa_func.coalesce(sa_func.sum(CrmProjectCostLine.actual_amount), 0))
            # 行政雜支階段不算人員成本（述詞說明見 ADMIN_PHASE）
            .where(CrmProjectCostLine.project_id == project_id,
                   CrmProjectCostLine.phase != ADMIN_PHASE)
        )).first()
        costline_estimated = costline_row[0] if costline_row else 0
        costline_actual = costline_row[1] if costline_row else 0
        # 🔴 人力成本的正本是成本子表（cost lines）；派工表 crm_project_staff 已退場（2026-09-04 拿掉 UI）。
        # 之前毛利只算派工 → 有子表沒派工的案毛利虛高（東仁社宅：API 95%、預算結算畫面 20%，owner 2026-09-05 抓到）。
        # 子表有數字就以子表為準；完全沒子表的舊案才退回派工。
        if costline_actual or costline_estimated:
            staff_actual = costline_actual
            staff_estimated = costline_estimated

        # 跨子表彙總（預算分配）：一案的子表是個位數，撈那兩欄在 Python 算就好（一趟，不另外聚合）
        # 子表預算含委外與雜支（owner 2026-09-05）：雜支是預算裡的信封，不外加
        from core.crm_logic import misc_budget_total_of
        _g_rows = (await session.execute(
            select(CrmProjectCostGroup.budget_amount, CrmProjectCostGroup.misc_budget_amount)
            .where(CrmProjectCostGroup.project_id == project_id))).all()
        allocated_budget_sum = sum(int(b or 0) for b, _mi in _g_rows)
        groups_count = len(_g_rows)
        groups_missing_budget_count = sum(1 for b, mi in _g_rows if b is None and mi is None)
        # 整案預估雜支＝各子表預計雜支加總（owner 2026-09-05 統一口徑；規則正本 core.crm_logic.misc_budget_total_of）
        misc_budget_total = misc_budget_total_of(_g_rows, project.misc_budget_pct)

    from core.crm_logic import project_margin
    contract = project.contract_amount or 0
    tax_rate = project.tax_rate or 5
    # 毛利公式單一來源（與財務儀表板 Top/Bottom 共用，見 core.crm_logic.project_margin）
    m = project_margin(contract, tax_rate, expense_actual, staff_actual)
    ex_tax = m["ex_tax"]
    profit_target = int(ex_tax * (project.profit_target_pct or 20) / 100)
    from core.finance_logic import load_margin_model, margin_for_type, suggested_budget_hours
    _model = load_margin_model("mine")      # 毛利表只有私帳設定頁在維護；母帳案 entity='parent' 拿到的是出廠預設表
    _type_margin = margin_for_type(_model, project.project_type)
    _suggested_hours = suggested_budget_hours(contract, tax_rate, _type_margin, _model["daily_cost"], _model["hours_per_day"])
    from core.crm_logic import effective_misc_pct
    _mpct = effective_misc_pct(project.misc_budget_pct)
    misc_budget = int(ex_tax * _mpct / 100)
    outsource_budget = ex_tax - profit_target - misc_budget

    total_cost = m["cost"]
    actual_profit = m["margin"]
    profit_rate = round(actual_profit / ex_tax * 100) if ex_tax > 0 else 0
    # 收款方式（owner 2026-09-13 第 4 點）：後期代開的案，母帳合約額不是公司的營收，公司只拿代辦費。
    # 這次**不動**毛利／管線的算法，只多給一個數字讓摘要講一句「公司實際收入＝代辦費 N」。
    from core.finance_logic import PASSTHROUGH_FEE_RATES, MINE_LINK_INVOICE_CATEGORY
    from core.ledger_project import BILLING_LABELS, billing_mode_of
    from config import load_settings
    _bm = billing_mode_of(project.billing_mode)
    _company_income = None
    if _bm == "passthrough" and contract:
        _rates = load_settings().get("invoice_fee_rates") or {}
        _pct = float(_rates.get(MINE_LINK_INVOICE_CATEGORY, PASSTHROUGH_FEE_RATES[MINE_LINK_INVOICE_CATEGORY]))
        _company_income = round(contract * _pct / 100)

    return {
        "billing_mode": _bm, "billing_mode_label": BILLING_LABELS[_bm],
        "company_income": _company_income,
        "contract_amount": contract, "ex_tax": ex_tax,
        "profit_target": profit_target, "profit_target_pct": project.profit_target_pct or 20,
        "misc_budget": misc_budget, "misc_budget_pct": _mpct,
        "outsource_budget": outsource_budget,
        # 私帳設定的預期毛利表（owner 2026-09-03）：這個案型預期多少毛利、換算成多少工時預算
        "type_margin_pct": _type_margin, "suggested_budget_hours": _suggested_hours,
        "expense_estimated": expense_estimated, "expense_actual": expense_actual,
        "staff_estimated": staff_estimated, "staff_actual": staff_actual,
        "total_cost": total_cost, "actual_profit": actual_profit, "profit_rate": profit_rate,
        "payment_status": project.payment_status or "未到帳",
        "amount_receivable": project.amount_receivable,
        "amount_received": project.amount_received,
        "transfer_fee": project.transfer_fee,
        "costline_estimated": costline_estimated,
        "costline_actual": costline_actual,
        # 多子表擴充
        "allocated_budget_sum": allocated_budget_sum,
        "groups_count": groups_count,
        "groups_missing_budget_count": groups_missing_budget_count,
        # 預估雜支的正本：子表「雜支預算」手動加總；None＝全部未設
        # （前端退回 misc_budget 的 % 自動推算並標示「自動」）
        "misc_budget_total": misc_budget_total,
    }




# ── 子表／成本估算共用的小 helper（cost_groups／cost_lines／petty／project_links 都從這裡拿）──

def _fmt_date(dt) -> Optional[str]:
    """Datetime → 'YYYY-MM-DD'（台北歸一，見 _shared._fmt_day），None 時回 None。"""
    return _fmt_day(dt) or None




async def _get_first_group_id(session, project_id: str) -> Optional[str]:
    """專案的首張子表 id（依 sort_order + created_at）。純查詢，無副作用。"""
    return (await session.execute(
        select(CrmProjectCostGroup.id)
        .where(CrmProjectCostGroup.project_id == project_id)
        .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        .limit(1)
    )).scalar_one_or_none()




async def _resolve_target_group(session, project_id: str, supplied: Optional[str]) -> str:
    """回傳目標子表 id：供給者優先，否則取首張（主表）。
    若專案完全沒有子表（migration 漏網 / create_project 失敗），
    自我修復建立主表（flush，不 commit，交由外層 commit）。"""
    if supplied:
        return supplied
    gid = await _get_first_group_id(session, project_id)
    if gid:
        return gid
    gid = uuid.uuid4().hex
    session.add(CrmProjectCostGroup(id=gid, project_id=project_id, name="主表", sort_order=0))
    await session.flush()
    return gid




async def _project_misc_pct(session, project_id: str):
    """專案雜支比（%），沒有就 5。子表預設雜支與整案加總都用它。"""
    from core.crm_logic import effective_misc_pct
    p = await session.get(CrmProject, project_id)
    return effective_misc_pct(p.misc_budget_pct if p is not None else None)
