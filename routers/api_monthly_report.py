"""私帳月報（docs/MONTHLY_REPORT.md）—— owner 2026-09-17「我希望你在我更新帳戶後 自動提供一份月報給我」。

觸發：登記餘額按儲存（routers/api_balance_register.put_balance_register）之後自動產生當月那一份（同月覆蓋）；
也可以手動 POST /monthly-reports/generate。規則全部在 core/monthly_report.py（純函式），這裡只撈資料、存表、回 payload。

資料來源都是別的模組已經算好的東西，不另寫第二份算法：
- 堡壘 payload（routers.api_fortress._payload）：五層、六題、階梯、財富自由、持股（已換台幣）、負債
- 登記餘額 payload（routers.api_balance_register._register_payload）：各帳戶登記數／未補明細、各券商未拆明細、卡費
- 資產儀表板的桶（routers.api_finance_assets._auto_buckets）：銀行現金／應收／器材淨值／證券現值
- 本月收支（財務三表同一套分類 core.finance_logic.cashflow_lines：營業活動的流入／流出；家用同 api_ledger_mobile 的口徑）
- 淨值快照（finance_net_snapshots）：走勢與第一份月報的比較基準
- 上一份月報（finance_monthly_reports）：各分項的上月數字
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from pydantic import BaseModel

from core.db_guard import db_factory_or_503 as _factory_or_503
from core.finance_logic import cashflow_lines
from core.monthly_report import TREND_POINTS, build_report
from routers.api_finance import _guard
from routers.crm._shared import _username, _validate_month

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


class GeneratePayload(BaseModel):
    month: Optional[str] = None      # 'YYYY-MM'；空＝本月


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
    from sqlalchemy import select
    from db.models import CrmCashEntry, FinanceMonthlyReport, FinanceNetSnapshot
    from routers.api_balance_register import _register_payload
    from routers.api_finance_assets import _auto_buckets
    from routers.api_fortress import _payload as fortress_payload, _today_tw
    from routers.api_ledger_mobile import HOUSEHOLD_TOP
    from services.finance_statements import _load_inputs

    today = _today_tw()
    this_month = today.strftime("%Y-%m")
    month = _validate_month(month) if month else this_month
    # 🔴 只能產生本月：堡壘／登記餘額／資產桶都是「現在」的數字，掛在過去月份的 basis_date 底下就是假歷史，
    #    下個月的月報還會拿它當上月（差額全變 0）。過去月份以當時登記時產生的那份為準。
    if month != this_month:
        raise HTTPException(status_code=422, detail=f"月報只能用現在的數字產生本月（{this_month}）；{month} 以當時登記時產生的那份為準")
    basis = today.isoformat()
    # 堡壘那包自己開 session，跟下面的撈資料互不相依 → 並行；卡費它已經掃過一次（ladder.liabilities.card），登記包沿用
    fortress_task = asyncio.ensure_future(fortress_payload(ent))
    factory = _factory_or_503()
    async with factory() as session:
        fortress = await fortress_task
        card = ((fortress.get("ladder") or {}).get("liabilities") or {}).get("card")
        register = await _register_payload(session, ent, card=card)
        buckets = (await _auto_buckets(session, ent, register["usd_twd"])).get("buckets") or {}
        # 本月收入／支出跟財務三表**同一套分類**（core.finance_logic.cashflow_lines）：配對的轉存、預支、投資／籌資活動
        # 都由它判，月報不再自己用分類名猜「哪些是跨帳流動」。營業活動的流入＝收入、流出＝支出；家用另從分類挑。
        inputs = await _load_inputs(session, ent)
        rows, _stats = cashflow_lines(inputs["cash_entries"], [month], cat_map=inputs["cat_map"],
                                      accounts=inputs["accounts"], bank_accounts=inputs["bank_accounts"])
        op = [r for r in rows if r["activity"] == "operating"]
        dep = sum(r["amount"] for r in op if r["amount"] > 0)
        exp = -sum(r["amount"] for r in op if r["amount"] < 0)
        house = -sum(r["amount"] for r in op
                     if r["amount"] < 0 and str(r["entry"].get("category") or "").startswith(HOUSEHOLD_TOP))
        # 快照的 total 含手填桶（預付款…），月報的總資產只有四顆自動桶：比較與走勢要用快照存的 auto 四桶合計，
        # 不然「比上次快照 +X」會被手填桶壓低。舊快照沒有 auto 才退回 total。
        # 只撈走勢要的最後 N 筆（build_report 只用最後 TREND_POINTS 筆＋基準日前最近一筆）
        snaps = list(reversed((await session.execute(
            select(FinanceNetSnapshot.snap_date, FinanceNetSnapshot.total, FinanceNetSnapshot.auto)
            .where(FinanceNetSnapshot.entity == ent).order_by(FinanceNetSnapshot.snap_date.desc())
            .limit(TREND_POINTS + 1))).all()))
        prev_row = (await session.execute(
            select(FinanceMonthlyReport).where(FinanceMonthlyReport.entity == ent, FinanceMonthlyReport.month < month)
            .order_by(FinanceMonthlyReport.month.desc()).limit(1))).scalars().first()
        # bank_net 是帳戶**真正**的淨流（含匯費、請款），給「登記餘額與帳上的差」用 —— 時間窗要跟 Δ現金一樣：
        # 上一份月報的基準日（不含）到今天，不是月初到今天；沒有上一份就不算（register_gap 本來就要 Δ現金）
        bank_net = None
        if prev_row and prev_row.basis_date:
            bank_net = int((await session.execute(
                select(fn.coalesce(fn.sum(fn.coalesce(CrmCashEntry.deposit, 0) - fn.coalesce(CrmCashEntry.expense, 0)
                                          - fn.coalesce(CrmCashEntry.bank_fee, 0) - fn.coalesce(CrmCashEntry.claim, 0)), 0))
                .where(CrmCashEntry.entity == ent,
                       CrmCashEntry.entry_date > datetime.strptime(prev_row.basis_date, "%Y-%m-%d")))).scalar_one() or 0)
        now = datetime.now()
        payload = build_report(
            month, basis, fortress, register, buckets,
            {"deposit": int(dep or 0), "expense": int(exp or 0), "household_expense": int(house or 0), "bank_net": bank_net},
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
    except Exception:      # noqa: BLE001 — 任何錯都只記 log（帶 traceback，不然 build_report 裡的 KeyError 只剩一行看不懂）
        log.exception("[monthly-report] %s 自動產生失敗", ent)
        return None


@router.get("/monthly-reports")
async def list_reports(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select
    from db.models import FinanceMonthlyReport
    factory = _factory_or_503()
    async with factory() as session:
        # 清單只要四個欄位：不要把每個月整包 payload（JSONB）都拖回來只為了填下拉
        rows = (await session.execute(
            select(FinanceMonthlyReport.month, FinanceMonthlyReport.basis_date,
                   FinanceMonthlyReport.generated_at, FinanceMonthlyReport.generated_by)
            .where(FinanceMonthlyReport.entity == ent).order_by(FinanceMonthlyReport.month.desc()))).all()
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
    """手動（重新）產生本月（同月覆蓋）。帶了不是本月的 month → 422：過去月份以當時登記時產生的那份為準。"""
    ent = _guard(request, entity, level="full")
    report = await generate_report(ent, _username(request), payload.month)
    return {"month": report["month"], "report": report}
