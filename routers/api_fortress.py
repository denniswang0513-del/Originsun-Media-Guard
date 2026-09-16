"""routers/api_fortress.py — 私帳「堡壘」（docs/FORTRESS_PLAN.md）。

一支 GET 回整頁（桌機分頁與手機頁同一份），三支預留清單的寫入，一支設定。
規則全在 core/fortress_logic.py；這裡只負責把私帳現有的數字撈出來：
  帳戶餘額＝期初＋掛帳流水（同 /bank-accounts 與 /assets/overview 那條式子）、證券現值（同 _holding_value）、
  信用卡欠款（同 /card-summary）、貸款下一期（同 /loans/upcoming 的查詢）、生活支出＝近 6 個完整月的月平均（口徑見 core.fortress_logic.need_category_ok）。
守衛：每支 `_guard`＝`require_entity(request, "mine", level="full")`（私帳＝finance_mine 指名制；entity 一律鎖 mine，空字串會落到 parent）。
跟 NAS office-api 一起掛（main_office._ROUTER_MODULES）：主控關機手機照看、照記預留。沒有排程、不 import notifier。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from config import load_settings, save_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.fortress_logic import (NEED_SAMPLE_MONTHS, build, merge_settings, monthly_need_from_rows,
                                 normalize_settings, pick_loan_dues)
from core.ledger import require_entity

try:
    from sqlalchemy import func as fn
    from sqlalchemy import select
    from db.models import BankAccount, CrmCashEntry, FinanceFortressEarmark, FinanceHolding, FinanceLoan, FinanceLoanPayment
except ImportError:  # DB 套件不存在的 agent 環境 — 同其他 router 的 try/except
    pass

router = APIRouter(prefix="/api/v1/finance/fortress", tags=["私帳堡壘"])
#: 這功能整個是私帳的（docs/FORTRESS_PLAN.md §0.3）—— entity 一律鎖 mine
MINE = "mine"
_TW = ZoneInfo("Asia/Taipei")
#: 貸款下一期往前看多遠（只取每筆貸款最近的一期）
LOAN_HORIZON_DAYS = 400
SETTINGS_KEY = "fortress"
#: 金額上限：欄位是 32 位元整數，超過會變成資料庫的 500 而不是好好的 422
MAX_AMOUNT = 2_000_000_000


class EarmarkPayload(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    amount: int = Field(ge=0, le=MAX_AMOUNT)
    due_date: Optional[str] = None      # YYYY-MM-DD
    note: Optional[str] = None


class EarmarkPatch(BaseModel):
    label: Optional[str] = Field(default=None, max_length=128)
    amount: Optional[int] = Field(default=None, ge=0, le=MAX_AMOUNT)
    due_date: Optional[str] = None
    note: Optional[str] = None
    paid: Optional[bool] = None


class FortressSettingsPatch(BaseModel):
    account_layers: Optional[dict] = None
    account_flags: Optional[dict] = None
    holdings_layer: Optional[int] = None
    targets: Optional[dict] = None
    monthly_need_override: Optional[int] = None
    war: Optional[dict] = None


def _guard(request: Request, entity: str = "") -> str:
    """一律鎖私帳。🔴 不能直接把 query 的 entity 丟給 require_entity：空字串會落到 parent
    （core/ledger.require_entity），於是有帳務鑰匙但沒有 finance_mine 的管理員會拿到一個
    「母公司版堡壘」，還會把 finance.fortress.parent 寫進設定 —— 那是沒人要的東西。"""
    if entity and entity != MINE:
        raise HTTPException(status_code=422, detail="堡壘只有私帳有")
    return require_entity(request, MINE, level="full")


def _today_tw() -> date:
    return datetime.now(_TW).date()


def _fmt_day(d) -> str:
    return d.strftime("%Y-%m-%d") if d else ""


def _parse_day(s: Optional[str]):
    if not s:
        return None
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="到期日要是 YYYY-MM-DD")


def _load_cfg(ent: str) -> dict:
    return normalize_settings(((load_settings().get("finance") or {}).get(SETTINGS_KEY) or {}).get(ent))


def _save_cfg(ent: str, cfg: dict) -> None:
    s = load_settings()
    s.setdefault("finance", {}).setdefault(SETTINGS_KEY, {})[ent] = normalize_settings(cfg)
    save_settings(s)


# ── 撈私帳現有的數字 ─────────────────────────────────────────────
async def _accounts(session, ent: str) -> tuple:
    """銀行／現金帳戶（期初＋流水；信用卡與股東帳戶不算，卡欠款走預留）＋證券（一列一檔）→ (帳戶, 警告)。"""
    from core.finance_logic import CARD_KIND, is_shareholder_kind
    from routers.api_finance_assets import _holding_value
    flows = dict((await session.execute(
        select(CrmCashEntry.bank_account_id,
               fn.sum(fn.coalesce(CrmCashEntry.deposit, 0) - fn.coalesce(CrmCashEntry.expense, 0)
                      - fn.coalesce(CrmCashEntry.bank_fee, 0) - fn.coalesce(CrmCashEntry.claim, 0)))
        .where(CrmCashEntry.entity == ent, CrmCashEntry.bank_account_id.isnot(None))
        .group_by(CrmCashEntry.bank_account_id))).all())
    accts = (await session.execute(
        select(BankAccount).where(BankAccount.entity == ent, BankAccount.active.is_(True))
        .order_by(BankAccount.sort_order, BankAccount.created_at))).scalars().all()
    out = []
    for a in accts:
        kind = a.acct_kind or "bank"
        if kind == CARD_KIND or is_shareholder_kind(kind):
            continue
        out.append({"id": a.id, "name": a.name, "kind": "cash" if kind == "cash" else "bank",
                    "balance": int(a.opening_balance or 0) + int(flows.get(a.id, 0) or 0), "currency": "TWD"})
    fx = float((load_settings().get("my_ledger") or {}).get("usd_twd") or 0)
    holdings = (await session.execute(
        select(FinanceHolding).where(FinanceHolding.entity == ent, FinanceHolding.active.is_(True))
        .order_by(FinanceHolding.sort_order, FinanceHolding.created_at))).scalars().all()
    for h in holdings:
        out.append({"id": h.id, "name": h.name or h.symbol or "持股", "kind": "holding",
                    "balance": _holding_value(h, fx), "currency": (h.currency or "TWD").upper()})
    # 🔴 沒有匯率時 _holding_value 會把每一筆外幣持股算成 0（api_finance_assets 對這個坑有紅字註解）。
    #    不能無聲吞掉：第 5 層會少掉整個外幣部位，而畫面上什麼跡象都沒有。
    warnings = []
    n_fx = sum(1 for a in out if a["currency"] != "TWD")
    if n_fx and not fx:
        warnings.append(f"美元匯率還沒設定，{n_fx} 筆外幣持股現在一律算 0 —— 到資產儀表板按一次「更新報價」就會有。")
    return out, warnings


async def _auto_earmarks(session, ent: str, today: date) -> list:
    """自動帶入的預留：貸款該付的那幾期（逾期全留＋未來最近一期，見 pick_loan_dues）＋信用卡目前欠款（>0 才列）。"""
    out = []
    horizon = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=LOAN_HORIZON_DAYS)
    rows = (await session.execute(
        select(FinanceLoanPayment, FinanceLoan.name)
        .join(FinanceLoan, FinanceLoan.id == FinanceLoanPayment.loan_id)
        .where(FinanceLoan.entity == ent, FinanceLoanPayment.status != "paid", FinanceLoanPayment.due_date <= horizon)
        .order_by(FinanceLoanPayment.due_date, FinanceLoanPayment.period_no))).all()
    by_key = {(p.loan_id, _fmt_day(p.due_date)): (p, name) for p, name in rows}
    dues = pick_loan_dues([(p.loan_id, _fmt_day(p.due_date), int(p.principal_due or 0) + int(p.interest_due or 0))
                           for p, _name in rows], today.isoformat())
    for loan_id, due, amount, overdue in dues:
        p, name = by_key[(loan_id, due)]
        out.append({"id": f"loan:{p.id}", "label": f"{name} {'逾期未繳' if overdue else '下一期'}", "amount": amount,
                    "due_date": due, "source": "loan", "source_ref": loan_id, "paid": False, "note": ""})
    from routers.api_finance_card import _card_cfg, _card_numbers, _card_view
    cfg = _card_cfg(ent)
    outstanding = int(_card_view(cfg, await _card_numbers(ent, cfg)).get("outstanding") or 0)
    if outstanding > 0:
        out.append({"id": "card:outstanding", "label": "信用卡目前欠款", "amount": outstanding, "due_date": "",
                    "source": "card", "source_ref": "", "paid": False, "note": ""})
    return out


async def _manual_earmarks(session, ent: str) -> list:
    rows = (await session.execute(
        select(FinanceFortressEarmark).where(FinanceFortressEarmark.entity == ent)
        .order_by(FinanceFortressEarmark.due_date, FinanceFortressEarmark.created_at))).scalars().all()
    return [{"id": r.id, "label": r.label, "amount": int(r.amount or 0), "due_date": _fmt_day(r.due_date),
             "source": "manual", "source_ref": "", "paid": bool(r.paid_at), "note": r.note or ""} for r in rows]


async def _monthly_need_auto(session, ent: str, today: date) -> tuple:
    """近 NEED_SAMPLE_MONTHS 個**完整**月（不含本月）的生活支出月平均 → (平均, 有資料的月份數)。
    哪些分類算、分母怎麼取都在 core.fortress_logic（純規則，可測）；這裡只負責把（分類, 月份, 支出）撈出來。"""
    first_this = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    y, m = today.year, today.month - NEED_SAMPLE_MONTHS
    while m <= 0:
        y, m = y - 1, m + 12
    lo = datetime(y, m, 1, tzinfo=timezone.utc)
    month = fn.to_char(CrmCashEntry.entry_date, "YYYY-MM")
    rows = (await session.execute(
        select(CrmCashEntry.category, month, fn.coalesce(fn.sum(CrmCashEntry.expense), 0))
        .where(CrmCashEntry.entity == ent, CrmCashEntry.entry_date >= lo, CrmCashEntry.entry_date < first_this,
               fn.coalesce(CrmCashEntry.expense, 0) > 0)
        .group_by(CrmCashEntry.category, month))).all()
    return monthly_need_from_rows(rows)


async def _payload(ent: str) -> dict:
    today = _today_tw()
    factory = _factory_or_503()
    async with factory() as session:
        accounts, warnings = await _accounts(session, ent)
        earmarks = await _auto_earmarks(session, ent, today) + await _manual_earmarks(session, ent)
        need, need_months = await _monthly_need_auto(session, ent, today)
    out = build(accounts, earmarks, need, _load_cfg(ent), today.isoformat(),
                need_months=need_months, warnings=warnings)
    out["entity"] = ent
    return out


# ── 端點 ──────────────────────────────────────────────────────────
@router.get("")
async def get_fortress(request: Request, entity: str = ""):
    ent = _guard(request, entity)
    return await _payload(ent)


@router.put("/settings")
async def put_settings(payload: FortressSettingsPatch, request: Request, entity: str = ""):
    """帳戶分層／旗標、證券層別、目標倍數、必要支出覆寫、戰爭假設。桌機改；回整頁。"""
    ent = _guard(request, entity)
    _save_cfg(ent, merge_settings(_load_cfg(ent), payload.model_dump(exclude_unset=True)))
    return await _payload(ent)


@router.post("/earmarks")
async def add_earmark(payload: EarmarkPayload, request: Request, entity: str = ""):
    ent = _guard(request, entity)
    factory = _factory_or_503()
    async with factory() as session:
        label = payload.label.strip()
        if not label:
            raise HTTPException(status_code=422, detail="項目名稱不能是空白")
        session.add(FinanceFortressEarmark(id=uuid.uuid4().hex, entity=ent, label=label,
                                           amount=int(payload.amount), due_date=_parse_day(payload.due_date),
                                           note=(payload.note or "").strip() or None))
        await session.commit()
    return await _payload(ent)


@router.put("/earmarks/{earmark_id}")
async def edit_earmark(earmark_id: str, payload: EarmarkPatch, request: Request, entity: str = ""):
    ent = _guard(request, entity)
    factory = _factory_or_503()
    data = payload.model_dump(exclude_unset=True)
    async with factory() as session:
        row = (await session.execute(select(FinanceFortressEarmark).where(
            FinanceFortressEarmark.id == earmark_id, FinanceFortressEarmark.entity == ent))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="預留不存在（自動帶入的貸款／信用卡不能改，到貸款與卡帳那邊改）")
        if "label" in data and data["label"] is not None:
            row.label = data["label"].strip() or row.label
        if "amount" in data and data["amount"] is not None:
            row.amount = int(data["amount"])
        if "due_date" in data:
            row.due_date = _parse_day(data["due_date"])
        if "note" in data:
            row.note = (data["note"] or "").strip() or None
        if "paid" in data and data["paid"] is not None:
            row.paid_at = datetime.now(timezone.utc) if data["paid"] else None
        await session.commit()
    return await _payload(ent)


@router.delete("/earmarks/{earmark_id}")
async def delete_earmark(earmark_id: str, request: Request, entity: str = ""):
    ent = _guard(request, entity)
    factory = _factory_or_503()
    async with factory() as session:
        row = (await session.execute(select(FinanceFortressEarmark).where(
            FinanceFortressEarmark.id == earmark_id, FinanceFortressEarmark.entity == ent))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="預留不存在")
        await session.delete(row)
        await session.commit()
    return await _payload(ent)
