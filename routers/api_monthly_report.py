"""私帳月報（docs/MONTHLY_REPORT.md）—— owner 2026-09-17「我希望你在我更新帳戶後 自動提供一份月報給我」。

觸發：登記餘額按儲存（routers/api_balance_register.put_balance_register）之後自動產生當月那一份（同月覆蓋）；
也可以手動 POST /monthly-reports/generate。規則全部在 core/monthly_report.py（純函式），這裡只撈資料、存表、回 payload。

資料來源都是別的模組已經算好的東西，不另寫第二份算法：
- 堡壘 payload（routers.api_fortress._payload）：五層、六題、階梯、財富自由、持股（已換台幣）、負債
- 登記餘額 payload（routers.api_balance_register._register_payload）：各帳戶登記數／未補明細、各券商未拆明細、卡費
- 資產儀表板的桶（routers.api_finance_assets._auto_buckets）：銀行現金／應收／器材淨值／證券現值
- 本月收支（crm_cash_entries 本月加總；家用同 api_ledger_mobile 的口徑）
- 淨值快照（finance_net_snapshots）：走勢與第一份月報的比較基準
- 上一份月報（finance_monthly_reports）：各分項的上月數字
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from pydantic import BaseModel

from config import load_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.monthly_report import build_report
from routers.api_finance import _guard
from routers.crm._shared import _username, _validate_month

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


class GeneratePayload(BaseModel):
    month: Optional[str] = None      # 'YYYY-MM'；空＝本月


def _month_window(month: str) -> tuple:
    """'YYYY-MM' → (月初, 次月初) **naive** datetime —— 收支寫入端（_parse_day）存的就是 naive 本地 00:00，
    邊界也要 naive 才對得齊；用 UTC-aware 的話每月 1 日的明細會掉到上個月（同 api_finance._month_window）。"""
    lo = datetime.strptime(month + "-01", "%Y-%m-%d")
    hi = (lo + timedelta(days=32)).replace(day=1)
    return lo, hi


def _auto_total(auto, total) -> int:
    """快照可比的總資產：auto 四桶合計（key 不含 usd_twd）；沒有 auto 就用 total。"""
    if isinstance(auto, dict) and auto:
        vals = [v for k, v in auto.items() if k != "usd_twd" and isinstance(v, (int, float))]
        if vals:
            return int(round(sum(vals)))
    return int(total or 0)


def _row_dict(r) -> dict:
    return {"month": r.month, "basis_date": r.basis_date, "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "generated_by": r.generated_by or ""}


async def generate_report(ent: str, username: str, month: Optional[str] = None) -> dict:
    """撈齊資料 → core.monthly_report.build_report → 存進 finance_monthly_reports（同帳本同月覆蓋）→ 回 payload。"""
    from sqlalchemy import func as fn
    from sqlalchemy import or_, select
    from db.models import CrmCashEntry, FinanceMonthlyReport, FinanceNetSnapshot
    from routers.api_balance_register import _register_payload
    from routers.api_finance_assets import _auto_buckets
    from routers.api_fortress import _payload as fortress_payload, _today_tw
    from routers.api_ledger_mobile import HOUSEHOLD_TOP

    today = _today_tw()
    this_month = today.strftime("%Y-%m")
    month = _validate_month(month) if month else this_month
    # 🔴 只能產生本月：堡壘／登記餘額／資產桶都是「現在」的數字，掛在過去月份的 basis_date 底下就是假歷史，
    #    下個月的月報還會拿它當上月（差額全變 0）。過去月份以當時登記時產生的那份為準。
    if month != this_month:
        raise HTTPException(status_code=422, detail=f"月報只能用現在的數字產生本月（{this_month}）；{month} 以當時登記時產生的那份為準")
    basis = today.isoformat()
    fortress = await fortress_payload(ent)
    fx = float((load_settings().get("my_ledger") or {}).get("usd_twd") or 0)
    factory = _factory_or_503()
    async with factory() as session:
        register = await _register_payload(session, ent)
        buckets = (await _auto_buckets(session, ent, fx)).get("buckets") or {}
        lo, hi = _month_window(month)
        # 收支明細不是損益表：轉帳（轉匯與定存）兩邊各一筆、刷卡與還款各一筆、買賣股票記成支出／收入 ——
        # 「本月收入／支出」要把這些跨帳流動排掉（core.cash_taxonomy 的兩本＋「投資」項目）；
        # bank_net 是帳戶**真正**的淨流（含匯費、請款），給「登記餘額與帳上的差」用。
        cat = fn.coalesce(CrmCashEntry.category, "")
        cross = or_(cat.like("轉匯與定存%"), cat.like("信用卡%"), cat.like("%投資%"))
        dep, exp, house, bank_net = (await session.execute(
            select(fn.coalesce(fn.sum(CrmCashEntry.deposit).filter(~cross), 0),
                   fn.coalesce(fn.sum(CrmCashEntry.expense).filter(~cross), 0),
                   fn.coalesce(fn.sum(CrmCashEntry.expense).filter(cat.like(HOUSEHOLD_TOP + "%")), 0),
                   fn.coalesce(fn.sum(fn.coalesce(CrmCashEntry.deposit, 0) - fn.coalesce(CrmCashEntry.expense, 0)
                                      - fn.coalesce(CrmCashEntry.bank_fee, 0) - fn.coalesce(CrmCashEntry.claim, 0)), 0))
            .where(CrmCashEntry.entity == ent, CrmCashEntry.entry_date >= lo, CrmCashEntry.entry_date < hi))).one()
        # 快照的 total 含手填桶（預付款…），月報的總資產只有四顆自動桶：比較與走勢要用快照存的 auto 四桶合計，
        # 不然「比上次快照 +X」會被手填桶壓低。舊快照沒有 auto 才退回 total。
        snaps = (await session.execute(
            select(FinanceNetSnapshot.snap_date, FinanceNetSnapshot.total, FinanceNetSnapshot.auto)
            .where(FinanceNetSnapshot.entity == ent).order_by(FinanceNetSnapshot.snap_date))).all()
        prev_row = (await session.execute(
            select(FinanceMonthlyReport).where(FinanceMonthlyReport.entity == ent, FinanceMonthlyReport.month < month)
            .order_by(FinanceMonthlyReport.month.desc()).limit(1))).scalars().first()
        now = datetime.now()
        payload = build_report(
            month, basis, fortress, register, buckets,
            {"deposit": int(dep or 0), "expense": int(exp or 0), "household_expense": int(house or 0), "bank_net": int(bank_net or 0)},
            [{"date": d.strftime("%Y-%m-%d") if d else "", "total": _auto_total(a, t)} for d, t, a in snaps],
            prev=(prev_row.payload if prev_row else None), generated_at=now.strftime("%Y-%m-%d %H:%M"))
        row = (await session.execute(
            select(FinanceMonthlyReport).where(FinanceMonthlyReport.entity == ent,
                                               FinanceMonthlyReport.month == month))).scalars().first()
        if not row:
            row = FinanceMonthlyReport(id=uuid.uuid4().hex, entity=ent, month=month)
            session.add(row)
        row.basis_date = basis
        row.payload = payload
        row.generated_at = now
        row.generated_by = username or ""
        await session.commit()
    return payload


async def generate_quietly(ent: str, username: str) -> Optional[str]:
    """登記餘額儲存後呼叫：月報產不出來**不能**讓登記失敗（登記才是主角）。回產生的月份或 None。"""
    try:
        return (await generate_report(ent, username))["month"]
    except Exception as e:      # noqa: BLE001 — 任何錯都只記 log
        log.warning("[monthly-report] %s 自動產生失敗: %s", ent, e)
        return None


@router.get("/monthly-reports")
async def list_reports(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select
    from db.models import FinanceMonthlyReport
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceMonthlyReport).where(FinanceMonthlyReport.entity == ent)
            .order_by(FinanceMonthlyReport.month.desc()))).scalars().all()
    return {"items": [_row_dict(r) for r in rows]}


@router.get("/monthly-reports/{month}")
async def get_report(month: str, request: Request, entity: str = ""):
    ent = _guard(request, entity, level="full")
    month = _validate_month(month)
    from sqlalchemy import select
    from db.models import FinanceMonthlyReport
    factory = _factory_or_503()
    async with factory() as session:
        row = (await session.execute(
            select(FinanceMonthlyReport).where(FinanceMonthlyReport.entity == ent,
                                               FinanceMonthlyReport.month == month))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"{month} 還沒有月報；登記餘額後會自動產生，或按「重新產生」")
    return {**_row_dict(row), "report": row.payload}


@router.post("/monthly-reports/generate")
async def generate(payload: GeneratePayload, request: Request, entity: str = ""):
    """手動（重新）產生：本月或指定月份。同月覆蓋。"""
    ent = _guard(request, entity, level="full")
    report = await generate_report(ent, _username(request), payload.month)
    return {"month": report["month"], "report": report}
