# -*- coding: utf-8 -*-
"""收款匯費走完整條寫入路徑（owner 2026-08-23）。

客戶匯 149,900、銀行只入 149,870。三件事必須同時成立，缺一就有人要手動找差額：
  ① 帳戶只增加 149,870（淨流 = 銀行說的數字，否則對帳工作台永遠配不上）
  ② 發票認列收到 149,900 → 收齊，不是尚欠 30
  ③ 那 30 元落在 bank_fee（管理費用），不是憑空消失

源碼掃描擋得住「忘了補 deposit」，擋不住「補了但寫錯欄位」。這支打真的
端點、讀真的 DB。跑在 dev 8001 / mediaguard_dev。
"""
import asyncio
import json
import sys
import io as _io
import urllib.error
import urllib.request
import uuid
from datetime import datetime

sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

TOK = create_token({'sub': 'admin', 'username': 'admin', 'access_level': 3,
                    'modules': ['finance', 'crm_invoices']})
BASE = "http://localhost:8001/api/v1/finance"
#: 關聯面板那組端點掛在 /crm 底下（同一個 app，不同前綴）
CRM = "http://localhost:8001/api/v1/crm"
TAG = "ZZFEE"
fails = []


def call(path, method="GET", body=None):
    r = urllib.request.Request(
        (BASE if path.startswith("/bank-statement") or path.startswith("/cash-entries") is False
         else CRM) + path, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=180) as f:
            return json.loads(f.read())
    except urllib.error.HTTPError as e:
        raise AssertionError(f"HTTP {e.code} {path}: {e.read().decode('utf-8')[:300]}")


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra else ""))
    if not ok:
        fails.append(label)


async def seed():
    """一張 149,900 的未收發票 + 一個乾淨的銀行帳戶。"""
    from sqlalchemy import select

    from db.models import BankAccount, CrmInvoice
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        acct = (await s.execute(select(BankAccount).where(
            BankAccount.entity == "parent",
            BankAccount.acct_kind == "bank"))).scalars().first()
        if not acct:
            acct = BankAccount(id=uuid.uuid4().hex[:16], entity="parent",
                               name=TAG + "銀行", acct_kind="bank", opening_balance=0)
            s.add(acct)
            await s.flush()
        inv = CrmInvoice(
            id=uuid.uuid4().hex, entity="parent", payment_type="收款",
            category="專案", invoice_number=TAG + "0001",
            invoice_date=datetime(2026, 6, 30), title=TAG + " 尾款",
            company_name=TAG + "大學", amount_total=149900,
            amount_ex_tax=142762, tax_amount=7138, payment_status="未收款")
        s.add(inv)
        await s.commit()
        return acct.id, inv.id


async def read_back(entry_id, invoice_id):
    from sqlalchemy import select

    from db.models import CrmCashEntry, CrmCashInvoiceLink, CrmInvoice
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        e = await s.get(CrmCashEntry, entry_id)
        inv = await s.get(CrmInvoice, invoice_id)
        links = (await s.execute(select(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.cash_entry_id == entry_id))).scalars().all()
        return ({"deposit": e.deposit, "expense": e.expense, "bank_fee": e.bank_fee,
                 "claim": e.claim} if e else None,
                inv.payment_status if inv else None,
                [ln.amount for ln in links])


async def clean():
    from sqlalchemy import select

    from db.models import (BankAccount, BankStatementLine, CrmCashEntry,
                           CrmCashInvoiceLink, CrmInvoice)
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        ents = (await s.execute(select(CrmCashEntry).where(
            CrmCashEntry.summary.like(TAG + "%")))).scalars().all()
        for e in ents:
            for ln in (await s.execute(select(CrmCashInvoiceLink).where(
                    CrmCashInvoiceLink.cash_entry_id == e.id))).scalars().all():
                await s.delete(ln)
            for ln in (await s.execute(select(BankStatementLine).where(
                    BankStatementLine.matched_entry_id == e.id))).scalars().all():
                await s.delete(ln)
            await s.delete(e)
        for i in (await s.execute(select(CrmInvoice).where(
                CrmInvoice.invoice_number.like(TAG + "%")))).scalars().all():
            await s.delete(i)
        for a in (await s.execute(select(BankAccount).where(
                BankAccount.name.like(TAG + "%")))).scalars().all():
            await s.delete(a)
        await s.commit()


asyncio.run(clean())
acct_id, inv_id = asyncio.run(seed())
try:
    print("[1] 匯入一列：銀行入帳 149,870，發票分配 149,900（其中匯費 30）")
    call("/bank-statement/apply", "POST", {
        "bank_account_id": acct_id,
        "rows": [{"date": "2026-06-30", "amount": 149870,
                  "description": TAG + " IGER 尾款", "category": "設計服務收入",
                  "invoices": [{"invoice_id": inv_id, "amount": 149900, "fee": 30}]}],
    })
    from sqlalchemy import select as _sel

    from db.models import CrmCashEntry
    from db.session import get_session_factory, init_db

    async def find_entry():
        await init_db()
        async with get_session_factory()() as s:
            e = (await s.execute(_sel(CrmCashEntry).where(
                CrmCashEntry.summary.like(TAG + "%")))).scalars().first()
            return e.id if e else None

    eid = asyncio.run(find_entry())
    check(eid is not None, "收支列建起來了")
    entry, status, link_amts = asyncio.run(read_back(eid, inv_id))
    print("    entry =", entry, "| 發票狀態 =", status, "| 分配 =", link_amts)

    print("")
    print("[2] 🔴 三件事要同時成立")
    flow = ((entry["deposit"] or 0) - (entry["expense"] or 0)
            - (entry["bank_fee"] or 0) - (entry["claim"] or 0))
    check(flow == 149870, "帳戶只增加 149,870（淨流 = 銀行說的數字）", f"淨流 {flow:,}")
    check(entry["deposit"] == 149900, "deposit 補成客戶實付 149,900",
          f"{entry['deposit']:,}")
    check(entry["bank_fee"] == 30, "那 30 元落在 bank_fee", str(entry["bank_fee"]))
    check(link_amts == [149900], "發票認列收到 149,900", str(link_amts))
    check(status in ("已收款", "已收"), "發票標成收齊，不是尚欠 30", str(status))

    print("")
    print("[2b] 🔴 關聯面板重存一次，匯費不能被抹掉")
    # 對帳單匯入那條路先做了收款側匯費，但 PUT /cash-entries/{id}/invoices
    # （關聯面板／編輯視窗）當時還把 fee 靜默丟掉 —— 重存一次 deposit 的補回值
    # 就沒了，帳戶淨流悄悄變回含匯費的數字，而那一列從此在對帳工作台配不上。
    call(f"/cash-entries/{eid}/invoices", "PUT",
         {"items": [{"invoice_id": inv_id, "amount": 149900, "fee": 30}]})
    again, status2, links2 = asyncio.run(read_back(eid, inv_id))
    print("    entry =", again)
    flow2 = ((again["deposit"] or 0) - (again["expense"] or 0)
             - (again["bank_fee"] or 0) - (again["claim"] or 0))
    check(again["deposit"] == 149900, "deposit 還是 149,900", str(again["deposit"]))
    check(again["bank_fee"] == 30, "bank_fee 還是 30", str(again["bank_fee"]))
    check(flow2 == 149870, "淨流仍是銀行說的 149,870", f"{flow2:,}")
    check(links2 == [149900], "分配沒變", str(links2))

    print("")
    print("[2c] 重存時把匯費改成 0 → deposit 要退回銀行原本的數字")
    call(f"/cash-entries/{eid}/invoices", "PUT",
         {"items": [{"invoice_id": inv_id, "amount": 149870, "fee": 0}]})
    zero, _s, _l = asyncio.run(read_back(eid, inv_id))
    check(zero["deposit"] == 149870, "deposit 退回 149,870", str(zero["deposit"]))
    check(not zero["bank_fee"], "bank_fee 清掉了", str(zero["bank_fee"]))

    print("")
    print("[2d] 🔴 匯費要能**往返**：重新載入 → 原樣存回去，不能掉")
    # 這是 owner 2026-08-24 要「有個地方調整銀行匯費」時挖出來的真 bug：
    # crm_cash_invoice_links 沒有 fee 欄 → 面板每次載入那格都是空的 →
    # 按一下儲存就送 fee=0，deposit 退回去、bank_fee 被清掉，畫面上完全看不出來。
    # 實測過的回退：帶 fee 存完 149,900/30，不帶 fee 重存一次變回 149,870/None。
    call(f"/cash-entries/{eid}/invoices", "PUT",
         {"items": [{"invoice_id": inv_id, "amount": 149900, "fee": 30}]})
    got = call(f"/cash-entries/{eid}/invoices")
    fees = [x.get("fee") for x in (got.get("items") or [])]
    check(fees == [30], "重新載入時那格帶得回來", str(fees))
    # 面板重存＝把載回來的原樣送回去
    call(f"/cash-entries/{eid}/invoices", "PUT",
         {"items": [{"invoice_id": x["invoice_id"], "amount": x["amount"],
                     "fee": x.get("fee", 0)} for x in got["items"]]})
    again2, _s2, _l2 = asyncio.run(read_back(eid, inv_id))
    check(again2["deposit"] == 149900, "deposit 沒退回去", str(again2["deposit"]))
    check(again2["bank_fee"] == 30, "bank_fee 沒被清掉", str(again2["bank_fee"]))

    print("")
    print("[3] 沒有匯費的列不受影響（回歸）")
    call("/bank-statement/apply", "POST", {
        "bank_account_id": acct_id,
        "rows": [{"date": "2026-06-29", "amount": 50000,
                  "description": TAG + " 無匯費列", "category": "設計服務收入",
                  "invoices": []}],
    })

    async def find_plain():
        await init_db()
        async with get_session_factory()() as s:
            e = (await s.execute(_sel(CrmCashEntry).where(
                CrmCashEntry.summary.like(TAG + " 無匯費列%")))).scalars().first()
            return (e.deposit, e.bank_fee) if e else None
    dep, bf = asyncio.run(find_plain())
    check((dep, bf) == (50000, None), "deposit 50,000、bank_fee 空", f"{dep}, {bf}")
finally:
    print("")
    print("[清理]")
    asyncio.run(clean())

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
