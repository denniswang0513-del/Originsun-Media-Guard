# -*- coding: utf-8 -*-
"""api_finance_projects.py — 逐案損益（帳本視角的專案清單／單案明細）。

owner 2026-08-24：「跟我有關的專案我都要看到」。/my-ledger.html 是固定
entity 的帳本頁（刻意沒有 CRM 專案管理），但它缺的正是 owner 最在意的維度 ——
逐案的錢。這支端點提供**帳本視角**的專案彙總：一列一案，帶合約／已收／應收／
掛帳支出／未付應付／淨額。

與 CRM 的 /crm/projects 分工：那支是**專案管理**（階段、派工、客戶、看板），
這支是**逐案的錢**（原 Sheet「結案總表」那張表的系統版），欄位口徑對齊帳本。
兩支都按 entity 過濾，錢的牆一樣在 core/ledger（require_entity）。

口徑（與 Sheet 對照）：
- 營收(含稅) = crm_projects.contract_amount
- 已收 / 應收 = amount_received / amount_receivable
- 掛帳支出   = 掛在該案的收支明細 expense 合計（階段 3 按案碼回掛的 612 筆）
- 未付應付   = 該案 crm_payment_requests 未付款者合計（委外／代開稅款）
- 實收       = 營收 − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來
- 檢查       = 實收 − Σ工項（應為 0）
  這兩條是 2026-08-24 對 402 案反推驗證出來的 Sheet 算式（實收 395/402、
  檢查 397/402 吻合；不吻合的是 Sheet 自己帳不平的那幾案，照實顯示不修）。
  算式正本在本檔 `compute()` —— 前端只顯示，不自己算第二份。
- 掛帳支出   = 掛在該案的收支明細 expense 合計（階段 3 按案碼回掛的 612 筆）
- 未付應付   = 該案 crm_payment_requests 未付款者合計（委外／代開稅款）

工項與費用欄存在 `crm_projects.ledger_detail`（JSONB），可由 UI 編輯；
工項清單預設十項、settings `my_ledger.income_items` 可覆寫。
"""
from fastapi import APIRouter, HTTPException, Request

from core.db_guard import db_factory_or_503 as _factory_or_503
from core.schemas import LedgerDetailPayload
from routers.crm._shared import _fmt_day

from .api_finance import _guard

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

# 工項（收入拆分）預設清單 —— 對齊 owner 原 Sheet 的欄序。
# settings `my_ledger.income_items` 可覆寫（owner 要自己加工項時改那裡）。
DEFAULT_INCOME_ITEMS = ["前期製作", "動態攝影", "剪輯", "調光", "動態效果",
                        "平面攝影", "諮詢", "教學", "錄混音", "其他"]

# ledger_detail 的費用欄（鍵 → 中文標籤，UI 照這個順序畫）
COST_FIELDS = [
    ("outsource", "委外費用"),
    ("tax_fee", "稅金"),
    ("buy_invoice", "買發票"),
    ("invoice_fee", "發票代辦費"),
    ("personal_tax", "個人稅款"),
    ("misc", "雜支"),
    ("shareholder", "股東往來"),
]


def income_items() -> list:
    from config import load_settings
    items = (load_settings().get("my_ledger") or {}).get("income_items")
    return [str(x) for x in items if str(x).strip()] if items else list(DEFAULT_INCOME_ITEMS)


def norm_detail(raw) -> dict:
    """ledger_detail → 固定形狀（缺鍵補 0、split 只留數字）。"""
    d = raw if isinstance(raw, dict) else {}
    out = {k: int(d.get(k) or 0) for k, _label in COST_FIELDS}
    split = d.get("split") if isinstance(d.get("split"), dict) else {}
    out["split"] = {str(k): int(v) for k, v in split.items()
                    if isinstance(v, (int, float)) and int(v)}
    return out


def compute(contract: int, d: dict) -> tuple:
    """(實收, 檢查)。算式正本 —— 前端只顯示，不自己算第二份。

    實收 = 營收(含稅) − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來
    檢查 = 實收 − Σ工項（應為 0；Sheet 本來就有幾案不為 0，照實顯示）
    """
    net = (contract - d["outsource"] - d["invoice_fee"]
           - d["personal_tax"] - d["misc"] - d["shareholder"])
    return net, net - sum(d["split"].values())


async def _rollups(session, ent: str, project_ids=None):
    """{project_id: (掛帳支出, 掛帳收入, 未付應付)}。一次聚合，不逐案 N+1。"""
    from sqlalchemy import func as fn
    from sqlalchemy import select

    from db.models import CrmCashEntry, CrmPaymentRequest
    cash_q = (select(CrmCashEntry.project_id,
                     fn.coalesce(fn.sum(CrmCashEntry.expense), 0),
                     fn.coalesce(fn.sum(CrmCashEntry.deposit), 0))
              .where(CrmCashEntry.entity == ent,
                     CrmCashEntry.project_id.isnot(None))
              .group_by(CrmCashEntry.project_id))
    ap_q = (select(CrmPaymentRequest.project_id,
                   fn.coalesce(fn.sum(CrmPaymentRequest.amount), 0))
            .where(CrmPaymentRequest.entity == ent,
                   CrmPaymentRequest.project_id.isnot(None),
                   CrmPaymentRequest.payment_status != "已付款")
            .group_by(CrmPaymentRequest.project_id))
    if project_ids is not None:
        cash_q = cash_q.where(CrmCashEntry.project_id.in_(project_ids))
        ap_q = ap_q.where(CrmPaymentRequest.project_id.in_(project_ids))
    cash = {pid: (int(e or 0), int(d or 0))
            for pid, e, d in (await session.execute(cash_q)).all()}
    ap = {pid: int(a or 0) for pid, a in (await session.execute(ap_q)).all()}
    return cash, ap


@router.get("/project-ledger")
async def project_ledger(request: Request, entity: str = "", q: str = "",
                         unpaid_only: bool = False):
    """本帳本的逐案損益清單（含合計）。q 搜專案名/客戶；unpaid_only 只看未收清。"""
    ent = _guard(request, entity, level="full")
    from sqlalchemy import or_, select

    from db.models import Client, CrmProject
    factory = _factory_or_503()
    async with factory() as session:
        query = (select(CrmProject, Client.short_name)
                 .outerjoin(Client, Client.id == CrmProject.client_id)
                 .where(CrmProject.entity == ent)
                 .order_by(CrmProject.updated_at.desc()))
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmProject.name.ilike(ql),
                                    Client.short_name.ilike(ql)))
        if unpaid_only:
            query = query.where(CrmProject.payment_status != "全額到帳")
        rows = (await session.execute(query)).all()
        cash, ap = await _rollups(session, ent, [p.id for p, _ in rows] or None)

    items, tot = [], {"contract": 0, "received": 0, "receivable": 0,
                      "spent": 0, "ap_open": 0, "net": 0,
                      "outsource": 0, "invoice_fee": 0, "unbalanced": 0}
    for p, cname in rows:
        spent, _cash_in = cash.get(p.id, (0, 0))
        ap_open = ap.get(p.id, 0)
        contract = int(p.contract_amount or 0)
        detail = norm_detail(p.ledger_detail)
        net, check = compute(contract, detail)
        items.append({
            "id": p.id, "name": p.name, "client": cname or "",
            "status": p.status or "", "type": p.project_type or "",
            "close_month": _fmt_day(p.completion_date)[:7] if p.completion_date else "",
            "contract": contract,
            "received": int(p.amount_received or 0),
            "receivable": int(p.amount_receivable or 0),
            "payment_status": p.payment_status or "",
            "spent": spent, "ap_open": ap_open,
            "detail": detail, "net": net, "check": check,
        })
        for k, v in (("contract", contract), ("received", int(p.amount_received or 0)),
                     ("receivable", int(p.amount_receivable or 0)),
                     ("spent", spent), ("ap_open", ap_open), ("net", net),
                     ("outsource", detail["outsource"]),
                     ("invoice_fee", detail["invoice_fee"])):
            tot[k] += v
        if check:
            tot["unbalanced"] += 1
    return {"projects": items, "totals": tot, "count": len(items),
            "income_items": income_items(),
            "cost_fields": [{"key": k, "label": lb} for k, lb in COST_FIELDS]}


@router.get("/project-ledger/{project_id}")
async def project_ledger_detail(project_id: str, request: Request,
                                entity: str = ""):
    """單案明細：掛帳的收支逐筆 + 未付應付逐張 + 匯入時保留的原始備註。"""
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select

    from db.models import Client, CrmCashEntry, CrmPaymentRequest, CrmProject
    factory = _factory_or_503()
    async with factory() as session:
        p = await session.get(CrmProject, project_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此專案")
        if (p.entity or "parent") != ent:
            raise HTTPException(status_code=403, detail="這個專案不屬於目前的帳本")
        client = await session.get(Client, p.client_id) if p.client_id else None
        entries = (await session.execute(
            select(CrmCashEntry)
            .where(CrmCashEntry.project_id == project_id,
                   CrmCashEntry.entity == ent)
            .order_by(CrmCashEntry.entry_date))).scalars().all()
        pays = (await session.execute(
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.project_id == project_id,
                   CrmPaymentRequest.entity == ent)
            .order_by(CrmPaymentRequest.created_at))).scalars().all()
    _d = norm_detail(p.ledger_detail)
    _net, _check = compute(int(p.contract_amount or 0), _d)
    return {
        "project": {
            "id": p.id, "name": p.name, "client": client.short_name if client else "",
            "status": p.status or "", "type": p.project_type or "",
            "close_month": _fmt_day(p.completion_date)[:7] if p.completion_date else "",
            "contract": int(p.contract_amount or 0),
            "received": int(p.amount_received or 0),
            "receivable": int(p.amount_receivable or 0),
            "payment_status": p.payment_status or "",
            "detail": _d, "net": _net, "check": _check,
            # 匯入時把案碼/案源/税別等收在這裡（純文字）—— 逐案對照 Sheet 用
            "notes": p.notes or "",
        },
        "income_items": income_items(),
        "cost_fields": [{"key": k, "label": lb} for k, lb in COST_FIELDS],
        "entries": [{
            "id": e.id, "date": _fmt_day(e.entry_date), "summary": e.summary,
            "category": e.category or "", "deposit": int(e.deposit or 0),
            "expense": int(e.expense or 0), "status": e.status or "",
        } for e in entries],
        "payments": [{
            "id": x.id, "summary": x.summary, "amount": int(x.amount or 0),
            "category": x.category or "", "payee": x.payee_name or "",
            "payment_status": x.payment_status or "",
            "payment_date": _fmt_day(x.payment_date),
        } for x in pays],
    }


@router.put("/project-ledger/{project_id}")
async def update_project_ledger(project_id: str, payload: LedgerDetailPayload,
                                request: Request, entity: str = ""):
    """編輯單案的工項與費用欄。回存後的明細與重算的實收/檢查。

    🔴 只寫有送的欄（exclude_unset）—— 整包寫回會把沒送的欄洗成 0。
    split 有送＝整份取代（工項是一組值）；0 值在 norm_detail 會被清掉，
    等於「把這個工項刪掉」，那正是使用者把格子清空的意思。
    """
    ent = _guard(request, entity, level="full")
    from db.models import CrmProject
    from routers.crm._shared import _now
    factory = _factory_or_503()
    async with factory() as session:
        p = await session.get(CrmProject, project_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此專案")
        if (p.entity or "parent") != ent:
            raise HTTPException(status_code=403, detail="這個專案不屬於目前的帳本")
        data = payload.model_dump(exclude_unset=True)
        contract = data.pop("contract_amount", None)
        if contract is not None:
            p.contract_amount = int(contract)
        d = norm_detail(p.ledger_detail)
        for k, v in data.items():
            if v is None:
                continue
            d[k] = ({str(x): int(y) for x, y in v.items()
                     if isinstance(y, (int, float))} if k == "split" else int(v))
        d = norm_detail(d)          # 再正規化一次（清掉 0 值工項）
        p.ledger_detail = d
        p.updated_at = _now()
        await session.commit()
        net, check = compute(int(p.contract_amount or 0), d)
    return {"status": "ok", "detail": d, "net": net, "check": check,
            "contract": int(contract) if contract is not None else None}
