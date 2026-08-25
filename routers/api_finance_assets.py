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


async def _auto_buckets(session, ent: str, usd_twd: float) -> dict:
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
    # 🔴 匯率由呼叫端傳，**不給預設值**：R2 把它改成可選之後 save_snapshot
    # 沒傳，於是每筆美元持股乘以 0 —— 存進 row.auto 的證券現值少掉整個外幣
    # 部位，而且沒有任何跡象（/simplify 2026-08-25 第 3 輪抓到）。
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
        # 各帳戶分列（owner 2026-08-25「這些帳戶與資料要呈現」）—— 銀行現金那
        # 顆桶的逐帳戶明細，口徑同上（期初＋流水），不是第二份算法
        "bank_lines": [{"name": a.name,
                        "amount": int(a.opening_balance or 0) + int(flows.get(a.id, 0) or 0)}
                       for a in accts if (a.acct_kind or "bank") == "bank"],
        "holdings": h_rows, "usd_twd": usd_twd,
    }


@router.get("/assets/overview")
async def assets_overview(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="view")
    factory = _factory_or_503()
    from sqlalchemy import select

    from db.models import FinanceNetSnapshot
    ml = load_settings().get("my_ledger") or {}   # 一次讀完，別在同一支端點讀兩次
    fx = float(ml.get("usd_twd") or 0)
    async with factory() as session:
        auto = await _auto_buckets(session, ent, fx)
        last = (await session.execute(
            select(FinanceNetSnapshot)
            .where(FinanceNetSnapshot.entity == ent)
            .order_by(FinanceNetSnapshot.snap_date.desc()).limit(1))).scalars().first()
    return {
        **auto,
        "last_snapshot": ({"id": last.id, "date": _fmt_day(last.snap_date),
                           "buckets": last.buckets or {}, "total": int(last.total or 0),
                           "note": last.note or ""} if last else None),
        "quotes_enabled": bool(ml.get("quotes_enabled", True)),
    }


@router.get("/assets/equipment")
async def assets_equipment(request: Request, entity: str = ""):
    """固定資產清單（owner 2026-08-25：「沒看到固定資產清單」）。

    overview 只回「固定資產淨值」一顆合計，123 件的清冊在儀表板上看不到 ——
    這支把逐件明細補出來，**口徑走引擎同一份**（core.finance_logic.
    equipment_net_rows：除役出表、as_of 之後購入不列、當月即折一整月），
    所以逐件 net 加總必然等於 overview 那顆桶。

    🔴 逐件是「一件一件餵進引擎」而不是自己重抄除役/未購入的排除規則 ——
    自己抄第二份就是第 5 輪才修掉的那個病（匯入腳本抄的那份還算錯一個月）。
    123 件 × O(攤提月) 與 overview 本來就在跑的量同級。
    """
    ent = _guard(request, entity, level="view")
    from datetime import datetime, timezone

    from sqlalchemy import select

    from core.finance_logic import equipment_net_rows
    from db.models import Equipment
    factory = _factory_or_503()
    async with factory() as session:
        equip = (await session.execute(
            select(Equipment).where(Equipment.entity == ent)
            .order_by(Equipment.purchase_date.desc().nullslast()))).scalars().all()
    as_of = datetime.now(timezone.utc).strftime("%Y-%m")
    items, net_total, cost_active = [], 0, 0
    for e in equip:
        line = equipment_net_rows([{
            "purchase_cost": e.purchase_cost, "purchase_date": e.purchase_date,
            "depreciation_months": e.depreciation_months,
            "retired_date": e.retired_date, "status": e.status, "name": e.name,
        }], as_of)["lines"]
        counted = bool(line)          # 引擎沒收＝除役/未購入/無金額，不進資產
        net = line[0]["net"] if counted else 0
        accum = line[0]["accum"] if counted else None
        items.append({
            "id": e.id, "name": e.name, "category": e.category or "",
            "purchase_date": _fmt_day(e.purchase_date),
            "cost": int(e.purchase_cost or 0),
            "months": int(e.depreciation_months or 0),
            "status": e.status or "", "note": e.note or "",
            "accum": accum, "net": net, "counted": counted,
        })
        if counted:
            net_total += net
            cost_active += int(e.purchase_cost or 0)
    return {"items": items, "as_of": as_of,
            "totals": {"count": len(items),
                       "counted": sum(1 for x in items if x["counted"]),
                       "cost": cost_active, "net": net_total}}


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
    pairs = list(zip(targets, results[:-1]))
    updated = [lbl for (_h, _q, lbl), px in pairs if px]
    failed = [lbl for (_h, _q, lbl), px in pairs if not px]
    # 批次 update by primary key：原本每檔一次 SELECT ＋一次 UPDATE（2N 個來回）。
    # 順帶不用再處理「抓完價之後那筆被刪掉」—— 批次 update 對消失的 id 直接無事發生。
    rows = [{"id": hid, "last_price": px, "price_at": now}
            for (hid, _q, _l), px in pairs if px]
    if rows:
        from sqlalchemy import update
        async with factory() as session:
            await session.execute(update(FinanceHolding), rows)
            await session.commit()
    if fx:
        ml = settings.setdefault("my_ledger", {})
        ml["usd_twd"] = fx
        ml["usd_twd_at"] = now.isoformat()
        save_settings(settings)
    return {"updated": updated, "failed": failed, "usd_twd": fx}


async def _owned(session, model, oid: str, request: Request, label: str):
    """載入單列 → 404 → 以**該列自己的 entity** 驗 full scope。

    這一步就是授權本身，不需要前面再來一次 `_guard(query 的 entity)`：
    query 參數只說得出「使用者想動哪本帳」，說不出「這一列是誰的」，而多驗
    那一次還會在「帶了自己沒權限的 entity、但列其實是自己的」時誤 403
    （/simplify 2026-08-25）。三個端點都要，抽成一份免得第四個忘記。

    🔴 但「不用 query 的 entity 驗」不等於「進 DB 前什麼都不驗」：原本連完全
    沒帶 token 的請求都會先查一次 DB，401（id 存在）與 404（id 不存在）的差別
    就是一台免費的 id 存在性預言機，順帶給未認證者一條 DB 往返。所以先擋沒有
    任何帳本權限的人，列級的 `_guard(row.entity)` 維持在後面不動。
    """
    from core.auth import check_logged_in
    check_logged_in(request)           # 沒登入 → 401，不進 DB
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
                           **payload.model_dump())
        session.add(h)
        await session.commit()
    return {"status": "ok", "id": h.id}


@router.put("/assets/holdings/{holding_id}")
async def update_holding(holding_id: str, payload: HoldingPayload,
                         request: Request, entity: str = ""):
    from db.models import FinanceHolding
    factory = _factory_or_503()
    async with factory() as session:
        h = await _owned(session, FinanceHolding, holding_id, request, "持股")
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(h, k, v)
        h.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {"status": "ok"}


@router.delete("/assets/holdings/{holding_id}")
async def delete_holding(holding_id: str, request: Request, entity: str = ""):
    from db.models import FinanceHolding
    factory = _factory_or_503()
    async with factory() as session:
        h = await _owned(session, FinanceHolding, holding_id, request, "持股")
        await session.delete(h)
        await session.commit()
    return {"status": "ok"}


@router.get("/assets/snapshots")
async def list_snapshots(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="view")
    from sqlalchemy import select

    from db.models import FinanceNetSnapshot
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(FinanceNetSnapshot)
            .where(FinanceNetSnapshot.entity == ent)
            .order_by(FinanceNetSnapshot.snap_date))).scalars().all()
    # 只回圖表讀得到的欄：buckets 在 116 列上是 27KB（整包的 66%），而唯一的
    # 消費者只用 date/total —— 拍快照要用的上一份桶是從 /assets/overview 的
    # last_snapshot 拿的，不是這裡（/simplify 2026-08-25）。
    return {"snapshots": [{
        "id": r.id, "date": _fmt_day(r.snap_date), "total": int(r.total or 0),
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
        auto = await _auto_buckets(
            session, ent,
            float((load_settings().get("my_ledger") or {}).get("usd_twd") or 0))
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
    from db.models import FinanceNetSnapshot
    factory = _factory_or_503()
    async with factory() as session:
        r = await _owned(session, FinanceNetSnapshot, snapshot_id, request, "快照")
        await session.delete(r)
        await session.commit()
    return {"status": "ok"}
