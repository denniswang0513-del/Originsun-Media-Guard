"""登記餘額（owner 2026-09-17「先讓我登記我的帳戶資產，明細我後面補」）。

一張表填完「今天看到的餘額」按一次儲存：
- 銀行／現金帳戶：寫 bank_accounts.anchor_balance／anchor_date（基準點）。之後餘額 = 登記餘額 + 基準日之後的流水，
  基準日當天與之前的明細只當歷史 —— 補多少舊明細都不動今天的數字（規則只有 core.finance_logic.derive_balance 一份，
  六個算餘額的地方都經 routers.api_finance._balances_by_account）。
- 證券戶：寫該家的「未拆明細」列（總市值 − 已拆明細的市值，手填市值），之後拆明細時它自然縮小。
- 信用卡：不在這裡（PUT /card-summary 的 derive_opening_from 本來就是「現在實際欠多少」）。
- 每個帳戶另留一筆對帳紀錄（finance 對帳表，同帳戶同月覆蓋）當歷史。

同一個 prefix /api/v1/finance，拆成獨立檔是因為 api_finance.py 已到單次讀取上限（tests/unit/test_files_stay_readable.py）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from config import load_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.finance_logic import reconciliation_diff
from core.schemas import BalanceRegisterPayload
from routers.api_finance import _balances_by_account, _bank_dict, _guard
from routers.crm._shared import _parse_day, _username

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


PLUG_NAME = "未拆明細"            # 證券戶「總市值 − 已拆明細」那一列的名字（symbol 空、手填市值）
REGISTER_KINDS = ("bank", "cash")  # 信用卡走 card_ledger（PUT /card-summary derive_opening_from）、股東往來不是資產


def _is_plug(h) -> bool:
    return h.name == PLUG_NAME and not (h.symbol or "")


def _plug_of(holdings, broker):
    return next((h for h in holdings if (h.broker or "") == broker and _is_plug(h)), None)


async def _register_payload(session, ent: str) -> dict:
    """GET 的整包：銀行／現金帳戶（帳上算的、上次登記、未補明細）＋證券戶（分券商）＋信用卡未繳。"""
    from sqlalchemy import select
    from db.models import BankAccount, FinanceHolding
    from routers.api_finance_assets import _holding_value
    from routers.api_fortress import _card_outstanding
    rows = (await session.execute(
        select(BankAccount).where(BankAccount.entity == ent, BankAccount.active.is_(True))
        .order_by(BankAccount.sort_order, BankAccount.created_at))).scalars().all()
    bal = await _balances_by_account(session, entity=ent)
    accounts = []
    for b in rows:
        if (b.acct_kind or "bank") not in REGISTER_KINDS:
            continue
        d = _bank_dict(b)
        r = bal[b.id]
        accounts.append({"id": b.id, "name": b.name, "acct_kind": d["acct_kind"], "bank_name": d["bank_name"],
                         "balance": r["balance"], "unfilled": r["unfilled"],
                         "anchor_balance": d["anchor_balance"], "anchor_date": d["anchor_date"],
                         # 帳上算到基準日的數字（登記 − 未補）；沒登記就是現在帳上算的
                         "booked": (r["balance"] if r["unfilled"] is None else int(b.anchor_balance) - r["unfilled"])})
    fx = float((load_settings().get("my_ledger") or {}).get("usd_twd") or 0)
    holdings = (await session.execute(
        select(FinanceHolding).where(FinanceHolding.entity == ent, FinanceHolding.active.is_(True))
        .order_by(FinanceHolding.sort_order, FinanceHolding.created_at))).scalars().all()
    brokers = {}
    for h in holdings:
        g = brokers.setdefault(h.broker or "", {"broker": h.broker or "", "count": 0, "detail": 0, "plug": 0,
                                                 "plug_note": "", "total": 0})
        if _is_plug(h):
            g["plug"] = int(h.manual_value or 0)
            g["plug_note"] = h.note or ""
        else:
            g["count"] += 1
            g["detail"] += _holding_value(h, fx)
    for g in brokers.values():
        g["total"] = g["detail"] + g["plug"]
    return {"date": datetime.now().strftime("%Y-%m-%d"), "accounts": accounts,
            "brokers": sorted(brokers.values(), key=lambda g: -g["total"]),
            "card_outstanding": await _card_outstanding(ent), "usd_twd": fx, "entity": ent}


@router.get("/balance-register")
async def get_balance_register(request: Request, entity: str = ""):
    ent = _guard(request, entity, level="full")
    factory = _factory_or_503()
    async with factory() as session:
        return await _register_payload(session, ent)


@router.put("/balance-register")
async def put_balance_register(payload: BalanceRegisterPayload, request: Request, entity: str = ""):
    """登記餘額：帳戶寫基準點、證券戶寫「未拆明細」列、每個帳戶留一筆對帳紀錄（同帳戶同月覆蓋）。
    只動送來的帳戶／券商；回 GET 那一整包。"""
    from sqlalchemy import select
    from db.models import BankAccount, BankReconciliation, FinanceHolding
    from routers.api_finance_assets import _holding_value
    ent = _guard(request, entity, level="full")
    day = _parse_day(payload.date)
    if day is None:
        raise HTTPException(status_code=422, detail="date 要填（YYYY-MM-DD）")
    if day > datetime.now() + timedelta(days=1):
        raise HTTPException(status_code=422, detail="基準日不能是未來")
    factory = _factory_or_503()
    async with factory() as session:
        if payload.accounts:
            ids = [ln.id for ln in payload.accounts]
            rows = {b.id: b for b in (await session.execute(
                select(BankAccount).where(BankAccount.entity == ent, BankAccount.id.in_(ids)))).scalars().all()}
            for ln in payload.accounts:
                b = rows.get(ln.id)
                if not b:
                    raise HTTPException(status_code=404, detail=f"帳戶不存在或不在這本帳：{ln.id}")
                if (b.acct_kind or "bank") not in REGISTER_KINDS:
                    raise HTTPException(status_code=422, detail=f"{b.name} 不是銀行／現金帳戶，不能登記餘額")
                # null ＝ 取消登記：兩欄一起清空，餘額回到期初＋全部流水（derive_balance 沒有基準點那條）
                b.anchor_balance = int(ln.balance) if ln.balance is not None else None
                b.anchor_date = day if ln.balance is not None else None
                b.updated_at = datetime.now()
            await session.flush()
            # 歷史：每次登記留一筆對帳紀錄（system_balance＝帳上算到基準日的數字、diff＝還沒補的明細）
            bal = await _balances_by_account(session, entity=ent)
            month = payload.date[:7]
            for ln in payload.accounts:
                if ln.balance is None:
                    continue
                r = bal[ln.id]
                system = int(ln.balance) - int(r["unfilled"] or 0)
                rd = reconciliation_diff(system, int(ln.balance))
                row = (await session.execute(
                    select(BankReconciliation).where(BankReconciliation.bank_account_id == ln.id,
                                                     BankReconciliation.month == month))).scalar_one_or_none()
                if not row:
                    row = BankReconciliation(id=uuid.uuid4().hex, bank_account_id=ln.id, month=month,
                                             statement_balance=0, system_balance=0, diff=0, status="diff")
                    session.add(row)
                row.statement_balance = int(ln.balance)
                row.system_balance = system
                row.diff, row.status = rd["diff"], rd["status"]
                row.note = f"登記餘額 {payload.date}"
                row.reconciled_by = _username(request)
                row.reconciled_at = datetime.now()
        if payload.brokers:
            fx = float((load_settings().get("my_ledger") or {}).get("usd_twd") or 0)
            holdings = (await session.execute(
                select(FinanceHolding).where(FinanceHolding.entity == ent,
                                             FinanceHolding.active.is_(True)))).scalars().all()
            for br in payload.brokers:
                name = (br.broker or "").strip()
                plug = _plug_of(holdings, name)
                detail = sum(_holding_value(h, fx) for h in holdings
                             if (h.broker or "") == name and h is not plug)
                gap = int(br.total) - detail
                if plug is None:
                    if gap == 0:
                        continue
                    plug = FinanceHolding(id=uuid.uuid4().hex, entity=ent, broker=name, symbol="", name=PLUG_NAME,
                                          currency="TWD", quote_symbol="", sort_order=9999, active=True)
                    session.add(plug)
                plug.manual_value = gap
                plug.note = f"登記於 {payload.date}：總市值 {int(br.total):,} − 已拆明細 {detail:,}"
                plug.updated_at = datetime.now()
        await session.commit()
        return await _register_payload(session, ent)
