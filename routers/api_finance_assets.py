# -*- coding: utf-8 -*-
"""api_finance_assets.py — 資產儀表板（淨值快照＋持股報價，§8 階段 5）。

owner 私帳的「計分板」：Sheet 淨值快照 2021/3 起 117 列的系統版。
- 快照混合制：拍快照時**系統欄自動算**（銀行現金/應收/器材淨值/持股現值），
  手填欄（保險/外幣現金/備用金…）由前端帶上次值 —— 快照存的是**當下全貌**，
  之後不隨帳目重算（歷史就是歷史；auto 欄留系統算的子集供稽核）。
- 持股手維護、價格自動抓（services/quote_fetcher：TWSE＋Yahoo，免 key）。
  kill switch＝settings `my_ledger.quotes_enabled`；抓不到沿用 last_price。
- 匯率：refresh 時抓 USD/TWD 存 settings `my_ledger.usd_twd`（快照列也記）。

守衛：讀＝_guard level="view"（合夥人可看母公司的）；寫＝level="full"。
entity 慣例與 api_finance 相同（mine 由 finance_mine 鎖）。
"""
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from config import load_settings, save_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.schemas import HoldingPayload, NetSnapshotPayload
from routers.crm._shared import _fmt_day, _parse_shoot_date

from .api_finance import _guard

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


def _holding_value(h, usd_twd: float) -> int:
    """一列持股的台幣現值。manual_value 優先；有價有股數就算；其餘 0。"""
    if h.manual_value is not None:
        return int(h.manual_value)
    if h.last_price and h.shares:
        v = float(h.last_price) * float(h.shares)
        if (h.currency or "TWD").upper() != "TWD":
            v *= usd_twd or 0
        return round(v)
    return 0


async def _auto_buckets(session, ent: str) -> dict:
    """系統算得出來的桶。回 {桶名: 金額}＋meta。"""
    from sqlalchemy import func as fn
    from sqlalchemy import select

    from core.finance_logic import equipment_net_rows
    from db.models import (BankAccount, CrmCashEntry, CrmProject, Equipment,
                           FinanceHolding)

    # 銀行現金 = Σ(期初 + 淨流)
    flows = dict((await session.execute(
        select(CrmCashEntry.bank_account_id,
               fn.sum(fn.coalesce(CrmCashEntry.deposit, 0)
                      - fn.coalesce(CrmCashEntry.expense, 0)
                      - fn.coalesce(CrmCashEntry.bank_fee, 0)
                      - fn.coalesce(CrmCashEntry.claim, 0)))
        .where(CrmCashEntry.entity == ent,
               CrmCashEntry.bank_account_id.isnot(None))
        .group_by(CrmCashEntry.bank_account_id))).all())
    accts = (await session.execute(
        select(BankAccount).where(BankAccount.entity == ent,
                                  BankAccount.active.is_(True)))).scalars().all()
    bank_cash = sum(int(a.opening_balance or 0) + int(flows.get(a.id, 0) or 0)
                    for a in accts if (a.acct_kind or "bank") == "bank")

    receivable = int((await session.execute(
        select(fn.coalesce(fn.sum(CrmProject.amount_receivable), 0))
        .where(CrmProject.entity == ent,
               CrmProject.payment_status != "全額到帳"))).scalar_one() or 0)

    equip = (await session.execute(
        select(Equipment).where(Equipment.entity == ent))).scalars().all()
    eq_net = equipment_net_rows(
        [{"purchase_cost": e.purchase_cost, "purchase_date": e.purchase_date,
          "depreciation_months": e.depreciation_months,
          "retired_date": e.retired_date, "status": e.status, "name": e.name}
         for e in equip],
        datetime.now(timezone.utc).strftime("%Y-%m"))["net_total"]

    holdings = (await session.execute(
        select(FinanceHolding).where(FinanceHolding.entity == ent,
                                     FinanceHolding.active.is_(True))
        .order_by(FinanceHolding.sort_order, FinanceHolding.created_at))).scalars().all()
    usd_twd = float((load_settings().get("my_ledger") or {}).get("usd_twd") or 0)
    h_rows = [{
        "id": h.id, "broker": h.broker or "", "symbol": h.symbol or "",
        "name": h.name, "shares": h.shares, "currency": h.currency,
        "quote_symbol": h.quote_symbol or "", "last_price": h.last_price,
        "price_at": _fmt_day(h.price_at), "manual_value": h.manual_value,
        "value_twd": _holding_value(h, usd_twd), "note": h.note or "",
    } for h in holdings]
    return {
        "buckets": {"銀行現金": bank_cash, "應收帳款": receivable,
                    "固定資產淨值": eq_net,
                    "證券現值": sum(r["value_twd"] for r in h_rows)},
        "holdings": h_rows, "usd_twd": usd_twd,
        "bank_lines": [{"name": a.name,
                        "balance": int(a.opening_balance or 0) + int(flows.get(a.id, 0) or 0)}
                       for a in accts if (a.acct_kind or "bank") == "bank"],
    }


@router.get("/assets/overview")
async def assets_overview(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="view")
    factory = _factory_or_503()
    from sqlalchemy import select

    from db.models import FinanceNetSnapshot
    async with factory() as session:
        auto = await _auto_buckets(session, ent)
        last = (await session.execute(
            select(FinanceNetSnapshot)
            .where(FinanceNetSnapshot.entity == ent)
            .order_by(FinanceNetSnapshot.snap_date.desc()).limit(1))).scalars().first()
    return {
        **auto,
        "last_snapshot": ({"id": last.id, "date": _fmt_day(last.snap_date),
                           "buckets": last.buckets or {}, "total": int(last.total or 0),
                           "note": last.note or ""} if last else None),
        "quotes_enabled": bool((load_settings().get("my_ledger") or {})
                               .get("quotes_enabled", True)),
    }


@router.post("/assets/quotes/refresh")
async def refresh_quotes(request: Request, entity: str = ""):
    """逐檔抓報價寫回 holdings；順帶更新 USD/TWD。失敗檔沿用舊價並列名。"""
    ent = _guard(request, entity, level="full")
    settings = load_settings()
    if not (settings.get("my_ledger") or {}).get("quotes_enabled", True):
        raise HTTPException(status_code=409, detail="報價抓取已停用（my_ledger.quotes_enabled）")
    from sqlalchemy import select

    from db.models import FinanceHolding
    from services.quote_fetcher import fetch_quote, fetch_usd_twd
    factory = _factory_or_503()
    # 先只撈「要抓價的 (id, 代號)」就放掉 session —— 網路抓價一檔最長 8 秒，
    # 抓完才開第二個 session 寫回。原本整段包在一個 session 裡：pool 連線被
    # 釘著陪網路 I/O 等好幾秒（/simplify 2026-08-24）。順帶 gather 平行抓：
    # 總時長 ≈ 最慢的一檔，而不是逐檔相加。
    async with factory() as session:
        targets = [(h.id, h.quote_symbol, h.symbol or h.name)
                   for h in (await session.execute(
                       select(FinanceHolding)
                       .where(FinanceHolding.entity == ent,
                              FinanceHolding.active.is_(True)))).scalars().all()
                   if (h.quote_symbol or "").strip()]
    results = await asyncio.gather(
        *[asyncio.to_thread(fetch_quote, qs) for _hid, qs, _n in targets],
        asyncio.to_thread(fetch_usd_twd))
    fx = results[-1]
    now = datetime.now(timezone.utc)
    updated, failed = [], []
    async with factory() as session:
        for (hid, _qs, label), px in zip(targets, results[:-1]):
            if px:
                h = await session.get(FinanceHolding, hid)
                if h is not None:
                    h.last_price = px
                    h.price_at = now
                updated.append(label)
            else:
                failed.append(label)
        await session.commit()
    if fx:
        ml = settings.setdefault("my_ledger", {})
        ml["usd_twd"] = fx
        ml["usd_twd_at"] = now.isoformat()
        save_settings(settings)
    return {"updated": updated, "failed": failed, "usd_twd": fx}


async def _owned(session, model, oid: str, request: Request, label: str):
    """載入單列 → 404 → 以**該列自己的 entity** 再驗一次 full scope。

    update/delete 的 query entity 只驗了「你有權動哪本帳」，列真正屬於哪本
    要看資料 —— 這一步三個端點都要，抽成一份免得第四個端點忘記。"""
    row = await session.get(model, oid)
    if not row:
        raise HTTPException(status_code=404, detail=f"{label}不存在")
    _guard(request, row.entity or "parent", level="full")
    return row


@router.post("/assets/holdings")
async def create_holding(payload: HoldingPayload, request: Request,
                         entity: str = ""):
    ent = _guard(request, entity, level="full")
    if not (payload.name or "").strip():
        raise HTTPException(status_code=422, detail="名稱必填")
    from db.models import FinanceHolding
    factory = _factory_or_503()
    async with factory() as session:
        h = FinanceHolding(id=uuid.uuid4().hex, entity=ent,
                           **payload.model_dump(exclude={"entity"}))
        session.add(h)
        await session.commit()
    return {"status": "ok", "id": h.id}


@router.put("/assets/holdings/{holding_id}")
async def update_holding(holding_id: str, payload: HoldingPayload,
                         request: Request, entity: str = ""):
    _guard(request, entity, level="full")
    from db.models import FinanceHolding
    factory = _factory_or_503()
    async with factory() as session:
        h = await _owned(session, FinanceHolding, holding_id, request, "持股")
        for k, v in payload.model_dump(exclude_unset=True, exclude={"entity"}).items():
            setattr(h, k, v)
        h.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {"status": "ok"}


@router.delete("/assets/holdings/{holding_id}")
async def delete_holding(holding_id: str, request: Request, entity: str = ""):
    _guard(request, entity, level="full")
    from db.models import FinanceHolding
    factory = _factory_or_503()
    async with factory() as session:
        h = await _owned(session, FinanceHolding, holding_id, request, "持股")
        await session.delete(h)
        await session.commit()
    return {"status": "ok"}


@router.get("/assets/snapshots")
async def list_snapshots(request: Request, entity: str = "", limit: int = 500):
    ent = _guard(request, entity, level="view")
    from sqlalchemy import select

    from db.models import FinanceNetSnapshot
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceNetSnapshot)
            .where(FinanceNetSnapshot.entity == ent)
            .order_by(FinanceNetSnapshot.snap_date)
            .limit(max(1, min(limit, 2000))))).scalars().all()
    return {"snapshots": [{
        "id": r.id, "date": _fmt_day(r.snap_date), "total": int(r.total or 0),
        "buckets": r.buckets or {}, "note": r.note or "",
    } for r in rows]}


@router.post("/assets/snapshots")
async def save_snapshot(payload: NetSnapshotPayload, request: Request,
                        entity: str = ""):
    """拍快照（同帳本同日 upsert）。total 由後端加總 —— 前端算的合計不收。"""
    ent = _guard(request, entity, level="full")
    d = _parse_shoot_date(payload.snap_date)
    if not d:
        raise HTTPException(status_code=422, detail="快照日期無法解析")
    buckets = {k: int(v) for k, v in (payload.buckets or {}).items()
               if isinstance(v, (int, float)) and str(k).strip()}
    if not buckets:
        raise HTTPException(status_code=422, detail="至少要有一個資產桶")
    from sqlalchemy import select

    from db.models import FinanceNetSnapshot
    factory = _factory_or_503()
    async with factory() as session:
        auto = await _auto_buckets(session, ent)
        row = (await session.execute(
            select(FinanceNetSnapshot)
            .where(FinanceNetSnapshot.entity == ent,
                   FinanceNetSnapshot.snap_date == d))).scalars().first()
        if row is None:
            row = FinanceNetSnapshot(id=uuid.uuid4().hex, entity=ent, snap_date=d)
            session.add(row)
        row.buckets = buckets
        row.total = sum(buckets.values())
        row.auto = {**auto["buckets"], "usd_twd": auto["usd_twd"]}
        row.note = (payload.note or "").strip() or None
        await session.commit()
        return {"status": "ok", "id": row.id, "total": int(row.total)}


@router.delete("/assets/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, request: Request, entity: str = ""):
    _guard(request, entity, level="full")
    from db.models import FinanceNetSnapshot
    factory = _factory_or_503()
    async with factory() as session:
        r = await _owned(session, FinanceNetSnapshot, snapshot_id, request, "快照")
        await session.delete(r)
        await session.commit()
    return {"status": "ok"}
