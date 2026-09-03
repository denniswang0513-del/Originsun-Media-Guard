"""routers/api_crm_mobile.py — CRM 手機版（/m/crm.html）專用端點。

規劃：docs/CRM_MOBILE_PLAN.md §3。定位是「現場輸入＋快看」，不是桌機版縮小：
六支手機用端點（字彙包、首頁數字、卡片清單、專案打包、加備註、報價改狀態），
其餘寫入直接打既有 CRM 端點 —— 狀態規則在後端已經定案，手機頁不自己寫死。

守衛：讀 `check_logged_in`（router 層，跟 CRM 主 router 同一條底線）；
寫 `_check_write`（`MOBILE_WRITE_MODULES`，一期空＝只給 Lv3）。路徑落在 CRM_PREFIX 底下，所以
tests/unit/test_crm_read_guard（每支 GET 匿名要 401）與
tests/unit/test_money_visibility（每條路由要掛 MoneyRedactRoute）都會管到這裡。

錢：金額鍵一律沿用 `core.money.MONEY_FIELDS` 的名字，沒 money_view 的帳號由
route class 自動抹掉。報價／請款／發票那幾組回應帶著名單外的錢
（total / final_price / amount —— 它們只出現在整支 403 的端點上），所以專案
打包只在 `can_see_money` 時才夾帶那幾段；鍵不在＝你看不到（不是 0）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth import check_logged_in, payload_grants
from core.crm_logic import prepend_note
from core.finance_logic import (INVOICE_PASSTHROUGH_CATEGORIES, QUOTE_PENDING, QUOTE_STATUSES, VAT_PCT,
                                initial_invoice_status, project_type_vocab)
from core.hr_logic import budget_burn, day_iso
from core.ledger import hide_mine_projects, not_mine
from core.money import MoneyRedactRoute, can_see_money, viewer_has_mine_scope
from core.project_flow import LOST, PIPELINE
from core.schemas import MobileNotePayload, MobileQuoteStatusPayload

from routers.crm._shared import (CRM_PREFIX, _check_status_auth, _crm_session,
                                 _fmt_minute, _module_guard, _now,
                                 _project_or_404, _username)
from routers.crm.costs import project_financial_summary
from routers.crm.finance import _to_invoice_dict
from routers.crm.invoice_files import get_invoice_applicants
from routers.crm.payments import _to_payment_dict
from routers.crm.projects import apply_project_status, project_wire
from routers.crm.quotes import _to_quotation_dict, project_quotation_rows

try:
    from sqlalchemy import func, or_, select
    from db.models import (Client, CrmInvoice, CrmPaymentRequest, CrmProject,
                           CrmProjectExpense, CrmQuotation, Timesheet)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同 CRM 套件的 try/except
    pass

router = APIRouter(prefix=f"{CRM_PREFIX}/m", tags=["CRM 手機版"],
                   dependencies=[Depends(check_logged_in)],
                   route_class=MoneyRedactRoute)

# 寫入守衛的模組清單：一期只給 Lv3（owner＋財務）＝空 tuple（`_module_guard()` 零 key
# 等同 check_admin）。要開給助理填 ('crm_projects',) 這一行就好 ——
# /options 的 `me.can_write` 問的是同一份清單（payload_grants），按鈕會跟著一起開。
MOBILE_WRITE_MODULES: tuple[str, ...] = ()
_check_write = _module_guard(*MOBILE_WRITE_MODULES)

# 字彙（手機頁不寫死任何一份：docs/CRM_MOBILE_PLAN.md §7 坑 1）。
# 報價狀態的家在 core.finance_logic（QUOTE_STATUSES / QUOTE_PENDING，
# `quotation_stats` 的 pending 也用同一個字）；請款狀態的正本是 db/models/_crm.py 那欄的註解。
PAYMENT_STATUSES = ("未付款", "應付款", "已付款")
INVOICE_VOCAB = {
    "payment_types": ["收款", "付款"],
    "kinds": ["電子發票", "紙本發票"],
    "categories": ["專案", *INVOICE_PASSTHROUGH_CATEGORIES],   # 桌機發票本同一套三種
}
ACTIVE_STATUS = "製作"
# 「製作」之前的階段＝還在賣：簽回報價才有「啟動專案」這件事；
# 已經在製作／結案的案子按了不動（不會把結案的案子拉回製作）。
PRESALE = PIPELINE[:PIPELINE.index(ACTIVE_STATUS)]
NOTE_MAX = 500


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _slim_project(p, client_short_name: str) -> dict:
    """卡片用的瘦欄位；兩個金額鍵都在 MONEY_FIELDS 名單裡，抹除自動生效。"""
    return {
        "id": p.id, "name": p.name,
        "client_id": p.client_id or "",
        "client_short_name": client_short_name or "",
        "status": p.status or "洽詢",
        "project_type": p.project_type or "",
        "am_username": p.am_username or "",
        "contract_amount": p.contract_amount,
        "amount_received": p.amount_received,
        "shoot_date": day_iso(p.shoot_date),
        "updated_at": _iso(p.updated_at),
    }


def _company_projects():
    """手機版只看母公司案（＝桌機清單預設的 entity=parent），且不列私帳分身。
    私帳案的地方是財務管理，不是現場輸入；混進來會把公司的案子埋掉。"""
    return (select(CrmProject, Client.short_name)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(not_mine(CrmProject.entity))
            .where(CrmProject.source_project_id.is_(None)))


async def _project_visible_or_404(session, request: Request, project_id: str):
    """私帳案對沒有 mine scope 的人不存在 —— 404 不是 403（同桌機 get_project）。"""
    project = await _project_or_404(session, project_id)
    if (project.entity or "parent") == "mine" and hide_mine_projects(request):
        raise HTTPException(status_code=404, detail="專案不存在")
    return project


def _money_visible(request: Request, project) -> bool:
    """這一案的錢給不給看：money_view 之外，私帳案還要有我的帳 scope
    （同 core.money.money_dep 對帶 project_id 的錢端點做的第二道）。"""
    if not can_see_money(request):
        return False
    return (project.entity or "parent") != "mine" or viewer_has_mine_scope(request)


# ── 端點 ─────────────────────────────────────────────────────

@router.get("/options")
async def mobile_options(request: Request):
    """表單字彙一次給齊：階段、案型、客戶、報價／請款狀態、發票欄位選項、我是誰。"""
    payload = check_logged_in(request)
    async with _crm_session() as session:
        clients = (await session.execute(
            select(Client.id, Client.short_name, Client.full_name, Client.tax_id)
            .where(not_mine(Client.entity))          # 同桌機 list_clients 預設：私帳那筆不混進來
            .order_by(Client.short_name))).all()
        # 最近常用的品項：手機上打字最貴，前 20 個當選單，其餘照樣可以手打
        item_types = (await session.execute(
            select(CrmInvoice.item_type, func.count(CrmInvoice.id))
            .where(CrmInvoice.item_type.isnot(None), CrmInvoice.item_type != "")
            .group_by(CrmInvoice.item_type)
            .order_by(func.count(CrmInvoice.id).desc(), CrmInvoice.item_type)
            .limit(20))).all()
    return {
        "phases": [*PIPELINE, LOST],
        "lost_phase": LOST,                       # 前端據此在推階段前就先要「未成案原因」
        "project_types": project_type_vocab(),
        # full_name／tax_id 給發票表單：選了專案就把抬頭、統編帶進來（owner：表單要「選就好」）
        "clients": [{"id": cid, "short_name": name or "", "full_name": full or "", "tax_id": tid or ""}
                    for cid, name, full, tid in clients],
        "quote_statuses": list(QUOTE_STATUSES),
        "payment_statuses": list(PAYMENT_STATUSES),
        "invoice": {
            **INVOICE_VOCAB, "item_types": [t for t, _n in item_types],
            # 每個方向的款項狀態選項：[後端預設（登記＝錢收到了）, 未收／未付]——
            # 由 initial_invoice_status 算出來，手機頁只負責讓人選，不自己寫字
            "statuses_by_type": {pt: [initial_invoice_status(pt), initial_invoice_status(pt, unpaid=True)]
                                 for pt in INVOICE_VOCAB["payment_types"]},
            # 申請人名單跟桌機發票本同一份（settings.invoice_applicants，沒設就用實際用過的人）
            "applicants": (await get_invoice_applicants(request))["applicants"],
            # 營業稅率：發票表單未稅／含稅互推用後端的那一份，不在瀏覽器再寫一個 5
            "vat_pct": VAT_PCT,
        },
        "me": {
            "username": payload.get("username") or payload.get("sub") or "",
            "access_level": payload.get("access_level", 0),
            # 跟 _check_write 問同一份清單：零 key＝只有管理員（同 check_admin_or_module）
            "can_write": payload_grants(payload, *MOBILE_WRITE_MODULES),
            "money_view": can_see_money(request),
        },
    }


@router.get("/home")
async def mobile_home():
    """首頁兩個數字：進行中專案數、待回覆報價數（專案分頁頂端的 strip 用）。"""
    async with _crm_session() as session:
        active = (await session.execute(
            select(func.count(CrmProject.id))
            .where(not_mine(CrmProject.entity))
            .where(CrmProject.source_project_id.is_(None))
            .where(CrmProject.status == ACTIVE_STATUS))).scalar() or 0
        pending = (await session.execute(
            select(func.count(CrmQuotation.id))
            .join(CrmProject, CrmProject.id == CrmQuotation.project_id)
            .where(not_mine(CrmProject.entity))
            .where(CrmQuotation.status == QUOTE_PENDING))).scalar() or 0
    return {"projects_active": int(active), "quotes_pending": int(pending)}


@router.get("/projects")
async def mobile_projects(request: Request, phase: str = Query(""), q: str = Query(""),
                          limit: int = Query(30), offset: int = Query(0)):
    """卡片清單（30 筆一頁、往下捲再抓）。`phase` 篩階段、`q` 找案名或客戶代稱。"""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    async with _crm_session() as session:
        query = _company_projects()
        if phase:
            query = query.where(CrmProject.status == phase)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmProject.name.ilike(ql), Client.short_name.ilike(ql)))
        total = (await session.execute(
            select(func.count()).select_from(query.subquery()))).scalar() or 0
        rows = (await session.execute(
            query.order_by(CrmProject.updated_at.desc(), CrmProject.id)
            .limit(limit).offset(offset))).all()
    return {"projects": [_slim_project(p, cname) for p, cname in rows], "total": int(total)}


@router.get("/projects/{project_id}")
async def mobile_project_detail(project_id: str, request: Request):
    """專案一頁打包：本體、收款摘要、報價、請款、發票、最近雜支、工時 burn、備註。
    錢的那幾段（summary／quotes／payments／invoices）只在看得到錢時夾帶。"""
    async with _crm_session() as session:
        project = await _project_visible_or_404(session, request, project_id)
        show_money = _money_visible(request, project)
        out = {"project": await project_wire(session, project),
               "notes": project.notes or ""}
        if show_money:
            out["quotes"] = [_to_quotation_dict(q, project_name=project.name or "")
                             for q in await project_quotation_rows(session, project_id)]
            payments = (await session.execute(
                select(CrmPaymentRequest).where(CrmPaymentRequest.project_id == project_id)
                .order_by(CrmPaymentRequest.request_date.desc().nulls_last(),
                          CrmPaymentRequest.created_at.desc(), CrmPaymentRequest.id))).scalars().all()
            out["payments"] = [_to_payment_dict(p, project_name=project.name or "") for p in payments]
            invoices = (await session.execute(
                select(CrmInvoice).where(CrmInvoice.project_id == project_id)
                .order_by(CrmInvoice.invoice_date.desc().nulls_last(),
                          CrmInvoice.created_at.desc(), CrmInvoice.id))).scalars().all()
            out["invoices"] = [_to_invoice_dict(inv, project_name=project.name or "") for inv in invoices]
        expenses = (await session.execute(
            select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
            .order_by(CrmProjectExpense.created_at.desc().nulls_last(), CrmProjectExpense.id)
            .limit(5))).scalars().all()
        # `actual` 是 owner 待決的錢（core.money._PENDING_OWNER，抹除層不管它）——
        # 這裡照 money 的判定自己收掉，不讓手機頁成為第二個外流口
        out["expenses_recent"] = [{
            "id": e.id, "category": e.category or "", "sub_item": e.sub_item or "",
            "payee": e.payee or "", "created_at": _iso(e.created_at),
            **({"actual": e.actual} if show_money else {}),
        } for e in expenses]
        # 工時：只算實際（hours>0，計畫列不進），同 services/timesheet_lookup.burn_rows
        hours = (await session.execute(
            select(func.coalesce(func.sum(Timesheet.hours), 0))
            .where(Timesheet.project_id == project_id, Timesheet.hours > 0))).scalar() or 0
        # 預算直接讀專案列（services.timesheet_lookup.budgets_for 讀的也是這一欄；0＝沒預算）
        budget = project.budget_hours or None
    burn = budget_burn(hours, budget)
    out["burn"] = {"hours_used": round(float(hours), 1), "budget_hours": budget,
                   "pct": None if burn["pct"] is None else int(round(burn["pct"]))}
    if show_money:
        # 只夾 `project` 沒有的鍵（合約／應收／已收／款項狀態 _to_project_dict 已經帶了）
        s = await project_financial_summary(project_id)
        out["summary"] = {k: s.get(k) for k in (
            "ex_tax", "expense_actual", "staff_actual", "total_cost",
            "actual_profit", "profit_rate")}
    return out


@router.post("/projects/{project_id}/note")
async def mobile_add_note(project_id: str, req: MobileNotePayload, request: Request):
    """前插一行「YYYY-MM-DD HH:MM 使用者：內容」到 notes；時間是台北時間、由後端補。"""
    _check_write(request)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="備註內容不能是空的")
    if len(text) > NOTE_MAX:
        raise HTTPException(status_code=422, detail=f"備註最多 {NOTE_MAX} 字（這不是雜支欄位）")
    async with _crm_session() as session:
        project = await _project_visible_or_404(session, request, project_id)
        now = _now()
        project.notes = prepend_note(project.notes, f"{_fmt_minute(now)} {_username(request)}：{text}")
        project.updated_at = now
        await session.commit()
        notes = project.notes
    return {"status": "ok", "notes": notes}


@router.post("/quotations/{quotation_id}/status")
async def mobile_quotation_status(quotation_id: str, req: MobileQuoteStatusPayload,
                                  request: Request):
    """只改報價 status；`activate` 且專案還在售前 → 專案進「製作」
    （走 routers/crm/projects.apply_project_status，跟桌機推階段同一支）。"""
    _check_write(request)
    if req.status not in QUOTE_STATUSES:
        raise HTTPException(status_code=422, detail=f"無效的報價狀態: {req.status}")
    async with _crm_session() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
        project = await session.get(CrmProject, q.project_id) if q.project_id else None
        if project is not None and (project.entity or "parent") == "mine" and hide_mine_projects(request):
            raise HTTPException(status_code=404, detail="找不到此報價")
        q.status = req.status
        q.updated_at = _now()
        if req.activate and project is not None and (project.status or "") in PRESALE:
            # 推階段的政策跟桌機 PATCH /projects/{id}/status 同一支（ADVANCE_MODULES）：
            # 能改報價狀態不等於能把案子推進製作
            _check_status_auth(request)
            await apply_project_status(session, project, ACTIVE_STATUS)
        await session.commit()
        await session.refresh(q)
        project_out = None
        client_name = ""
        if project is not None:
            await session.refresh(project)
            client = await session.get(Client, project.client_id) if project.client_id else None
            client_name = client.short_name if client else ""
            if req.activate:
                project_out = await project_wire(session, project)
        quotation = _to_quotation_dict(q, project_name=project.name if project else "",
                                       client_short_name=client_name)
    return {"status": "ok", "quotation": quotation, "project": project_out}
