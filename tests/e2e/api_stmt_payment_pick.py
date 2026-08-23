# -*- coding: utf-8 -*-
"""對帳單匯入：支出列勾請款單（owner 2026-08-23）。

owner：「我匯入對帳單時，項目勾請款單時，可以讓我勾是哪一筆請款單的項目來對帳，
像是勾發票那樣」。

收入列掛發票早就有了，支出列掛請款單是新的。這支證明整條路真的通：
  ① preview 回得出「還沒付滿的請款單」候選
  ② apply 真的寫進分配表
  ③ 請款單的付款狀態跟著變（走 resettle_payment_requests 的容差規則）
  ④ 匯費是**整筆匯出**收一次，而且守「總流出不變」

🔴 ④ 是最容易寫錯的一條：只寫 bank_fee 不從 expense 搬出來的話，帳上會多流出
   一筆（付款側 2026-08-22 就是這樣錯過一次，v2.4.143→144）。這裡釘的是不變量。
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices", "finance"]})
API = BASE + "/api/v1"
TAG = "ZZ勾請款單-" + uuid.uuid4().hex[:6]
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def call(path, method="GET", body=None):
    r = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + T, "Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(r, timeout=120).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode("utf-8")[:300]}


def form(path, fields):
    """multipart/form-data（preview 收的是表單，不是 JSON）。"""
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    payload = ("".join(parts) + f"--{b}--\r\n").encode("utf-8")
    r = urllib.request.Request(
        API + path, method="POST", data=payload,
        headers={"Authorization": "Bearer " + T,
                 "Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=180).read())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode("utf-8")[:400]}


async def db():
    """🔴 get_session_factory() 不是 coroutine，而且 init_db() 沒跑過它會回 None。"""
    from db.session import get_session_factory, init_db
    await init_db()
    return get_session_factory()


async def read_back(pr_id, entry_id):
    from sqlalchemy import select
    from db.models import CrmCashEntry, CrmCashPaymentLink, CrmPaymentRequest
    f = await db()
    async with f() as s:
        pr = await s.get(CrmPaymentRequest, pr_id)
        ce = await s.get(CrmCashEntry, entry_id) if entry_id else None
        links = [(x.payment_request_id, int(x.amount or 0)) for x in (await s.execute(
            select(CrmCashPaymentLink).where(
                CrmCashPaymentLink.cash_entry_id == entry_id))).scalars().all()] if entry_id else []
        return ({"status": pr.payment_status, "amount": int(pr.amount or 0)} if pr else None,
                ({"expense": ce.expense, "bank_fee": ce.bank_fee,
                  "deposit": ce.deposit, "claim": ce.claim,
                  "primary": ce.payment_request_id} if ce else None),
                links)


async def cleanup(pr_ids, entry_ids):
    from sqlalchemy import delete
    from db.models import (BankStatementLine, CrmCashEntry, CrmCashPaymentLink,
                           CrmPaymentRequest)
    f = await db()
    async with f() as s:
        if entry_ids:
            await s.execute(delete(CrmCashPaymentLink).where(
                CrmCashPaymentLink.cash_entry_id.in_(entry_ids)))
            await s.execute(delete(BankStatementLine).where(
                BankStatementLine.matched_entry_id.in_(entry_ids)))
            await s.execute(delete(CrmCashEntry).where(CrmCashEntry.id.in_(entry_ids)))
        if pr_ids:
            await s.execute(delete(CrmPaymentRequest).where(
                CrmPaymentRequest.id.in_(pr_ids)))
        await s.commit()


async def find_entry(summary):
    from sqlalchemy import select
    from db.models import CrmCashEntry
    f = await db()
    async with f() as s:
        e = (await s.execute(select(CrmCashEntry).where(
            CrmCashEntry.summary == summary))).scalars().first()
        return e.id if e else None


# 🔴 要用**真的解析得出來**的版面（第一銀行「交易明細查詢」，見
#    tests/unit/test_bank_statement.py）。解析不出交易列時 preview 會提早回
#    `ok: False` —— 候選清單根本不會建，於是「沒有 payment_requests」看起來
#    像功能壞了，其實只是我餵了一段不是對帳單的字。
STMT = """
交易日期 交易時間 幣別 支出金額 存入金額 餘額 票據號碼 摘要 備註
2026/08/12 10:00:00 新臺幣 50,030.00 - 149,970.00 跨行轉帳 ZZ統一匯款
幣別 支出金額總計 存入金額總計
新臺幣 50,030.00 0.00
"""

pr_ids, entry_ids = [], []
try:
    print(f"[0] 準備：一個銀行帳戶 + 兩張未付的請款單（{TAG}）")
    accts = call("/finance/bank-accounts")
    acct_list = accts.get("accounts") or accts.get("items") or []
    if not acct_list:
        print("  !! dev 庫沒有銀行帳戶，這支跑不了"); sys.exit(1)
    acct = acct_list[0]["id"]
    print("    帳戶 =", acct_list[0].get("name") or acct)

    for amt, who in ((30000, TAG + "甲"), (20000, TAG + "乙")):
        r = call("/crm/payments", "POST", {
            "request_date": "2026-08-05", "amount": amt,
            "summary": f"{TAG}｜{who}", "category": "專案外包", "payee_name": who})
        pid = (r.get("payment") or {}).get("id") or r.get("id")
        if not pid:
            print("  !! 建請款單失敗:", r); sys.exit(1)
        pr_ids.append(pid)
    check(len(pr_ids) == 2, "兩張請款單建好了", str(pr_ids))

    print("")
    print("[1] preview 要回得出「還沒付滿的請款單」候選")
    pv = form("/finance/bank-statement/preview",
              {"bank_account_id": acct, "text": STMT})
    if pv.get("_status"):
        print("  !! preview 失敗:", pv); sys.exit(1)
    check(pv.get("ok"), "對帳單解析得出來（不然候選清單根本不會建）",
          str((pv.get("errors") or [])[:1]))
    cands = pv.get("payment_requests")
    check(cands is not None, "回應有 payment_requests 這一欄")
    ids = {c["id"] for c in (cands or [])}
    check(set(pr_ids) <= ids, "剛建的兩張都在候選裡", f"{len(cands or [])} 張候選")
    mine = next((c for c in (cands or []) if c["id"] == pr_ids[0]), {})
    check(mine.get("outstanding") == 30000, "未付金額對", str(mine.get("outstanding")))
    check("payee_name" in mine and "summary" in mine,
          "候選帶得出摘要與收款人（不然畫面上只有一串 id）")

    print("")
    print("[2] apply：一筆 50,030 的匯出，拆給兩張請款單，含 30 元跨行手續費")
    # 銀行實扣 50,030 = 兩張請款單 50,000 + 手續費 30
    summary = TAG + "｜統一匯款"
    res = call("/finance/bank-statement/apply", "POST", {
        "bank_account_id": acct,
        "rows": [{
            "date": "2026-08-12", "amount": -50030, "description": summary,
            "category": "專案外包",
            "payments": [{"payment_request_id": pr_ids[0], "amount": 30000},
                         {"payment_request_id": pr_ids[1], "amount": 20000}],
            "payment_fee": 30,
        }],
    })
    if res.get("_status"):
        print("  !! apply 失敗:", res); sys.exit(1)
    check(res.get("entries") == 1, "建了 1 筆收支", str(res.get("entries")))
    check(res.get("linked_payments") == 2, "掛上 2 張請款單",
          str(res.get("linked_payments")))

    eid = asyncio.run(find_entry(summary))
    if eid:
        entry_ids.append(eid)
    check(bool(eid), "找得到那筆收支")

    print("")
    print("[3] 分配表與付款狀態")
    pr0, ce, links = asyncio.run(read_back(pr_ids[0], eid))
    print("    entry =", ce)
    print("    links =", sorted(links, key=lambda x: -x[1]))
    check(sorted(a for _i, a in links) == [20000, 30000], "兩張各自的金額對")
    check(ce and ce["primary"] == pr_ids[0],
          "主要請款單＝金額最大那張（後端從分配表推）")
    pr1, _c, _l = asyncio.run(read_back(pr_ids[1], None))
    check(pr0 and pr0["status"] == "已付款", "30,000 那張變已付款", str(pr0))
    check(pr1 and pr1["status"] == "已付款", "20,000 那張變已付款", str(pr1))

    print("")
    print("[4] 🔴 匯費：總流出不變（不是額外多流出 30）")
    # cash_entry_flow = deposit − expense − bank_fee − claim
    flow = ((ce["deposit"] or 0) - (ce["expense"] or 0)
            - (ce["bank_fee"] or 0) - (ce["claim"] or 0))
    check(ce["bank_fee"] == 30, "手續費掛在 bank_fee", str(ce["bank_fee"]))
    check(ce["expense"] == 50000, "expense 被搬掉 30（50,030 − 30）",
          str(ce["expense"]))
    check(flow == -50030, "淨流仍是銀行實扣的 50,030", f"{flow:,}")

    print("")
    print("[5] 付滿的請款單不再出現在候選裡（免得再掛一次款）")
    pv2 = form("/finance/bank-statement/preview",
               {"bank_account_id": acct, "text": STMT})
    cands2 = pv2.get("payment_requests") or []
    # 🔴 先確認清單真的有東西 —— 只斷言「我的兩張不在裡面」的話，清單整個是空的
    #    （例如 preview 壞掉、或欄位被改名）也會過。這條本來就是這樣假通過的。
    check(len(cands2) > 0, "候選清單本身還有東西（否則下一條會假通過）",
          f"{len(cands2)} 張")
    check(not (set(pr_ids) & {c["id"] for c in cands2}), "付滿的那兩張消失了")

finally:
    print("")
    print("[清理]")
    asyncio.run(cleanup(pr_ids, entry_ids))
    left, _c, _l = asyncio.run(read_back(pr_ids[0], None)) if pr_ids else (None, None, None)
    check(left is None, "測試請款單清光")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
