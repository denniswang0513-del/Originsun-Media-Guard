# -*- coding: utf-8 -*-
"""現金流量表的每一條線，都要等於點進去看到的明細加總。

🔴 為什麼要有這支（2026-08-22 /simplify 第 4 輪發現）：
build_cashflow 排除了掛在**非現金帳戶**（股東往來）上的收支，但鑽取那側只擋
「未掛帳戶」—— 於是表上寫 −1,532,503、點進去的明細加總是 −755,503。
使用者點進去就會看到兩個數字打架，而這種不一致**沒有任何錯誤訊號**。

做法：種一個股東往來帳戶 ＋ 一筆 777,000 的收入進去，前後兩次快照必須一模一樣。
（dev 庫本來沒有股東往來帳戶 —— 不種的話這條路根本沒被走到，會假綠。）
"""
import asyncio, json, urllib.request, urllib.parse, uuid, sys
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from datetime import datetime
from core.auth import create_token

TOK = create_token({'sub':'admin','username':'admin','access_level':3,'modules':['finance']})

def get(p):
    r = urllib.request.Request("http://localhost:8001/api/v1/finance" + p,
                               headers={"Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(r, timeout=120) as f:
        return json.loads(f.read())

ACTS = ("operating", "investing", "financing")


def snap():
    """三條線各自的（表上數字, 鑽取加總, 鑽取筆數）。"""
    cf = get("/statements?period=2026-01..2026-08&entity=parent")["cf"]
    out = {}
    for act in ACTS:
        dd = get("/statements/drilldown?kind=" + urllib.parse.quote("cash." + act)
                 + "&period=2026-01..2026-08&entity=parent")
        out[act] = (cf[act], dd["total"], dd["count"])
    return out

async def seed():
    from db.session import init_db, get_session_factory
    from db.models import BankAccount, CrmCashEntry
    await init_db()
    async with get_session_factory()() as s:
        a = BankAccount(id=uuid.uuid4().hex[:16], entity="parent", name="ZZ股東往來測試",
                        acct_kind="shareholder_loan", opening_balance=0)
        s.add(a); await s.flush()
        e = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                         entry_date=datetime(2026, 3, 10), deposit=777000,
                         summary="ZZ股東匯入測試", category="其他收入",
                         bank_account_id=a.id)
        s.add(e); await s.commit()
        return a.id, e.id

async def seed_transfer_fee():
    """一筆轉存：本金 5,000 是內部移動（不算活動），15 元跨行手續費是真的流出去。

    生產 2026 有十筆這種列，共 150 元 —— 表上算了、鑽取整筆跳過。
    dev 庫不一定有，所以自己種一筆。
    """
    from db.session import init_db, get_session_factory
    from db.models import BankAccount, CrmCashEntry
    from sqlalchemy import select
    await init_db()
    async with get_session_factory()() as s:
        bank = (await s.execute(select(BankAccount).where(
            BankAccount.entity == "parent",
            BankAccount.acct_kind == "bank"))).scalars().first()
        if not bank:
            return None
        e = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                         entry_date=datetime(2026, 3, 11), expense=5000,
                         bank_fee=15, summary="ZZ股東轉存手續費測試",
                         category="轉存", bank_account_id=bank.id)
        s.add(e); await s.commit()
        return e.id


async def clean():
    from db.session import init_db, get_session_factory
    from db.models import BankAccount, CrmCashEntry
    from sqlalchemy import select
    await init_db()
    async with get_session_factory()() as s:
        for e in (await s.execute(select(CrmCashEntry).where(
                CrmCashEntry.summary.like("ZZ股東%")))).scalars().all():
            await s.delete(e)
        for a in (await s.execute(select(BankAccount).where(
                BankAccount.name.like("ZZ股東%")))).scalars().all():
            await s.delete(a)
        await s.commit()

asyncio.run(clean())
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


try:
    before = snap()
    print("[1] 每條線本來就要跟自己的鑽取相符")
    for act in ACTS:
        tbl, tot, n = before[act]
        check(tbl == tot, f"{act}：表 {tbl:,} == 鑽取 {tot:,}", f"{n} 筆")

    print("")
    print("[2] 🔴 種一筆股東往來的 777,000 進去，兩側都不該動")
    asyncio.run(seed())
    after = snap()
    for act in ACTS:
        check(before[act] == after[act], f"{act} 沒被影響",
              f"{before[act]} → {after[act]}")

    print("")
    print("[3] 🔴 轉存的跨行手續費：本金不算活動，但 15 元真的出去了")
    if asyncio.run(seed_transfer_fee()):
        fee = snap()
        tbl0, tot0, n0 = after["operating"]
        tbl1, tot1, n1 = fee["operating"]
        check(tbl1 == tot1, f"營運：表 {tbl1:,} == 鑽取 {tot1:,}", f"{n1} 筆")
        check(tbl1 - tbl0 == -15, "表上少了 15（手續費）", f"{tbl0:,} → {tbl1:,}")
        check(n1 - n0 == 1, "明細多了一列（就是那筆手續費）", f"{n0} → {n1}")
    else:
        check(False, "找不到可用的銀行帳戶，這段沒測到")
finally:
    print("")
    print("[清理]")
    asyncio.run(clean())
    check(snap() == before, "清乾淨且回到原本的數字")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
