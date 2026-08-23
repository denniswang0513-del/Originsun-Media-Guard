# -*- coding: utf-8 -*-
"""帳戶間轉存的跨行手續費一鍵認列（owner 2026-08-24：「不太可能每次都逐一填寫」）。

一筆跨行轉存在帳上是兩列。本金是內部搬錢，但銀行收的手續費是真的離開公司了 ——
沒拆出來的話，現金流量表就會「期初＋淨流 ≠ 期末」差那幾十塊（生產實測差 30）。

🔴 這支最重要的一條是 [4]：**再按一次不可以再減一次**。
   2026-08-24 差點造成損害的第一版判準是「支出裡看起來有零頭就減掉」——
   那會把 37 筆早就拆好的歷史各再減 15（共 555 元，一路改到 2024 年）。
   判準必須跟**配對的那一列**比，而且要對重複執行免疫。
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "finance"]})
API = BASE + "/api/v1/finance"
TAG = "ZZ轉存-" + uuid.uuid4().hex[:6]
fails = []
ids = {}


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(path, method="GET", body=None):
    r = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + T, "Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(r, timeout=180).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode("utf-8")[:300]}


async def seed():
    """兩個帳戶 + 一組『手續費還埋在支出裡』的轉存 + 一組已經拆好的（不可被動）。"""
    from db.models import BankAccount, CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        a = BankAccount(id=uuid.uuid4().hex[:16], entity="parent",
                        name=TAG + "甲行", acct_kind="bank", opening_balance=0)
        b = BankAccount(id=uuid.uuid4().hex[:16], entity="parent",
                        name=TAG + "乙行", acct_kind="bank", opening_balance=0)
        s.add_all([a, b])
        mk = lambda **kw: CrmCashEntry(  # noqa: E731
            id=uuid.uuid4().hex[:16], entity="parent", category="轉存",
            entry_date=datetime(2026, 8, 20), **kw)
        # ① 手續費還埋在支出裡：銀行實扣 35,015，對方收到 35,000
        bad_out = mk(expense=35015, summary=TAG + " 待拆-轉出", bank_account_id=a.id)
        bad_in = mk(deposit=35000, summary=TAG + " 待拆-轉入", bank_account_id=b.id)
        # ② 已經拆好的：支出 20,000 + 匯費 15，對方收到 20,000
        ok_out = mk(expense=20000, bank_fee=15, summary=TAG + " 已拆-轉出",
                    bank_account_id=a.id)
        ok_in = mk(deposit=20000, summary=TAG + " 已拆-轉入", bank_account_id=b.id)
        s.add_all([bad_out, bad_in, ok_out, ok_in])
        await s.commit()
        return {"bad_out": bad_out.id, "bad_in": bad_in.id,
                "ok_out": ok_out.id, "ok_in": ok_in.id, "a": a.id, "b": b.id}


async def read(eid):
    from db.models import CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        e = await s.get(CrmCashEntry, eid)
        return {"expense": e.expense or 0, "bank_fee": e.bank_fee or 0,
                "out": (e.expense or 0) + (e.bank_fee or 0)} if e else None


async def clean(d):
    from sqlalchemy import delete
    from db.models import BankAccount, CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(CrmCashEntry).where(CrmCashEntry.id.in_(
            [d["bad_out"], d["bad_in"], d["ok_out"], d["ok_in"]])))
        await s.execute(delete(BankAccount).where(BankAccount.id.in_([d["a"], d["b"]])))
        await s.commit()


try:
    ids = asyncio.run(seed())
    print(f"[0] 種了兩組轉存（{TAG}）：一組待拆、一組已拆好")

    print("")
    print("[1] 看得出「還埋在支出裡」的是哪一組")
    d = call("/transfer-pairs")
    if d.get("_status"):
        print("  !!", d); sys.exit(1)
    mine = [p for p in (d.get("fee_inside") or []) if TAG in (p["out"]["summary"] or "")]
    check(len(mine) == 1, "只挑出待拆的那一組", str(len(mine)))
    if mine:
        check(mine[0]["fee"] == 15, "算出手續費 15", str(mine[0]["fee"]))
        check(mine[0]["out"]["expense"] == 35015, "指到支出 35,015 那一列")
    ok_flagged = [p for p in (d.get("fee_inside") or [])
                  if "已拆" in (p["out"]["summary"] or "")]
    check(not ok_flagged, "🔴 已經拆好的那組**沒有**被判成要拆")

    print("")
    print("[2] 一鍵認列 —— 總流出不變")
    before = asyncio.run(read(ids["bad_out"]))
    r = call("/transfer-pairs/recognize-fee", "POST", {"entry_ids": [ids["bad_out"]]})
    check(not r.get("_status"), "呼叫成功", str(r.get("_body", "")))
    after = asyncio.run(read(ids["bad_out"]))
    print("    before =", before, "→ after =", after)
    check(after["expense"] == 35000, "支出 35,015 → 35,000", str(after["expense"]))
    check(after["bank_fee"] == 15, "匯費 15", str(after["bank_fee"]))
    check(after["out"] == before["out"] == 35015,
          "🔴 總流出不變（帳戶餘額不會動）", f"{before['out']} → {after['out']}")

    print("")
    print("[3] 已經拆好的那組分毫未動")
    ok_now = asyncio.run(read(ids["ok_out"]))
    check(ok_now == {"expense": 20000, "bank_fee": 15, "out": 20015},
          "支出 20,000 / 匯費 15 原封不動", str(ok_now))

    print("")
    print("[4] 🔴 再按一次不可以再減一次（重複執行免疫）")
    call("/transfer-pairs/recognize-fee", "POST", {})     # 不指定 = 全部
    again = asyncio.run(read(ids["bad_out"]))
    ok_again = asyncio.run(read(ids["ok_out"]))
    check(again == after, "剛拆好的那組沒有被再減一次", str(again))
    check(ok_again == ok_now, "本來就拆好的那組也沒被動", str(ok_again))

    print("")
    print("[5] 認列完就不該再出現在待辦清單裡")
    d2 = call("/transfer-pairs")
    mine2 = [p for p in (d2.get("fee_inside") or []) if TAG in (p["out"]["summary"] or "")]
    check(not mine2, "待拆清單裡沒有它了", str(len(mine2)))

finally:
    print("")
    print("[清理]")
    if ids:
        asyncio.run(clean(ids))
        print("  已清掉種進去的帳戶與收支")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
