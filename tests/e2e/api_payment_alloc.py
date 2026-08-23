# -*- coding: utf-8 -*-
"""一筆匯款掛多張請款單（owner 2026-08-22）端對端。

要證明的：
  ① 一筆匯款掛得了**多張**請款單，掛滿的自動標已付款
  ② 跨行手續費（8,010 對 8,000）判成 fee 而不是「還有單沒掛」，且認列得進匯費
  ③ 分次支付不會被誤標已付款；連結拿掉會退回未付款並清掉付款日
  ④ 主要請款單（payment_request_id）跟著同步 —— 分類的硬連結靠它

自己建 ZZ 資料，跑完刪光。
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
fails = []

def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)

def call(m, p, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + p, data=d, method=m, headers={
        "Authorization": "Bearer " + T, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except Exception:
            return e.code, b.decode("utf-8", "replace")

async def setup():
    from db.session import init_db, get_session_factory
    from db.models import CrmCashEntry, CrmPaymentRequest
    from datetime import datetime
    await init_db()
    async with get_session_factory()() as s:
        aps = []
        for amt, who in ((8000, "ZZ收款甲"), (5000, "ZZ收款甲"), (30000, "ZZ收款乙")):
            a = CrmPaymentRequest(
                id=uuid.uuid4().hex[:16], entity="parent", amount=amt,
                summary=f"ZZ分配測試 {amt}", payee_name=who, category="專案外包",
                request_date=datetime(2026, 8, 1), payment_status="未付款")
            s.add(a)
            aps.append(a)
        # 匯款 13,010 = 8,000 + 5,000 + 10 手續費
        e1 = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                          entry_date=datetime(2026, 8, 20), expense=13010,
                          summary="ZZ合併匯款", category="請款單", payee="ZZ收款甲")
        # 分次支付：先付 10,000（單是 30,000）
        e2 = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                          entry_date=datetime(2026, 8, 20), expense=10000,
                          summary="ZZ分次支付", category="請款單", payee="ZZ收款乙")
        s.add(e1)
        s.add(e2)
        await s.commit()
        return [a.id for a in aps], e1.id, e2.id

async def teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import CrmCashEntry, CrmCashPaymentLink, CrmPaymentRequest
    await init_db()
    async with get_session_factory()() as s:
        ents = (await s.execute(select(CrmCashEntry).where(
            CrmCashEntry.summary.like("ZZ%")))).scalars().all()
        for e in ents:
            for ln in (await s.execute(select(CrmCashPaymentLink).where(
                    CrmCashPaymentLink.cash_entry_id == e.id))).scalars().all():
                await s.delete(ln)
            await s.delete(e)
        for a in (await s.execute(select(CrmPaymentRequest).where(
                CrmPaymentRequest.summary.like("ZZ分配測試%")))).scalars().all():
            await s.delete(a)
        await s.commit()

asyncio.run(teardown())
(ap8, ap5, ap30), e1, e2 = asyncio.run(setup())

try:
    print("[1] 一筆匯款掛兩張請款單")
    st, r = call("PUT", f"/crm/cash-entries/{e1}/payments", {"items": [
        {"payment_request_id": ap8, "amount": 8000},
        {"payment_request_id": ap5, "amount": 5000}]})
    check(st == 200, "掛得上去", st)
    check(len(r.get("items") or []) == 2, "兩張都在", len(r.get("items") or []))

    print("")
    print("[2] 🔴 差的 10 元判成手續費，不是「還有單沒掛」")
    chk = r.get("check") or {}
    check(chk.get("state") == "fee", "判為 fee", chk.get("state"))
    check(chk.get("fee") == 10, "算出 10 元", chk.get("fee"))
    check("手續費" in (chk.get("message") or ""), "訊息講得出來", chk.get("message"))
    # 回傳形狀與收款側對齊（前端才能共用同一支狀態列）
    check(chk.get("actual") == 13010, "有實付金額", chk.get("actual"))

    print("")
    print("[3] 掛滿的請款單自動標已付款")
    st, d = call("GET", "/crm/payments")
    aps = {x["id"]: x for x in (d.get("payments") or d.get("items") or [])}
    check(aps.get(ap8, {}).get("payment_status") == "已付款", "8,000 那張已付款",
          aps.get(ap8, {}).get("payment_status"))
    check(aps.get(ap5, {}).get("payment_status") == "已付款", "5,000 那張已付款",
          aps.get(ap5, {}).get("payment_status"))

    print("")
    print("[4] 認列成匯費 → 寫進 bank_fee")
    st, r = call("PUT", f"/crm/cash-entries/{e1}/payments", {"items": [
        {"payment_request_id": ap8, "amount": 8000},
        {"payment_request_id": ap5, "amount": 5000}], "fee": 10})
    check(st == 200, "存得起來", st)
    st, d = call("GET", "/crm/cash-entries?limit=3000")
    row = next((x for x in (d.get("items") or d.get("entries") or [])
                if x["id"] == e1), None)
    check(row and int(row.get("bank_fee") or 0) == 10, "bank_fee = 10",
          row and row.get("bank_fee"))
    # 🔴 總流出不能變 —— bank_fee 是外加在 expense 之上的，只寫 bank_fee 不從
    #    expense 扣掉的話，帳上會變成流出 8,020 而銀行只少 8,010（我第一版就是
    #    這樣，而且原本的斷言只看 bank_fee 所以抓不到）。
    check(row and int(row.get("expense") or 0) == 13000,
          "expense 從 13,010 搬成 13,000", row and row.get("expense"))
    check(row and int(row.get("expense") or 0) + int(row.get("bank_fee") or 0) == 13010,
          "🔴 總流出仍然是 13,010（銀行實際少的錢）",
          row and (row.get("expense"), row.get("bank_fee")))
    # 再按一次要冪等（總流出還是一樣，不會被扣兩次）
    call("PUT", f"/crm/cash-entries/{e1}/payments", {"items": [
        {"payment_request_id": ap8, "amount": 8000},
        {"payment_request_id": ap5, "amount": 5000}], "fee": 10})
    st, d = call("GET", "/crm/cash-entries?limit=3000")
    row = next((x for x in (d.get("items") or d.get("entries") or [])
                if x["id"] == e1), None)
    check(row and int(row.get("expense") or 0) + int(row.get("bank_fee") or 0) == 13010,
          "重複認列是冪等的", row and (row.get("expense"), row.get("bank_fee")))
    check(row and row.get("payment_request_id") == ap8,
          "主要請款單 = 金額最大那張", row and row.get("payment_request_id"))

    print("")
    print("[5] 🔴 分次支付不會被誤標已付款")
    st, r = call("PUT", f"/crm/cash-entries/{e2}/payments",
                 {"items": [{"payment_request_id": ap30, "amount": 10000}]})
    check(st == 200, "掛得上去", st)
    check((r.get("check") or {}).get("state") == "ok", "10,000 對 10,000 是 ok",
          (r.get("check") or {}).get("state"))
    st, d = call("GET", "/crm/payments")
    aps = {x["id"]: x for x in (d.get("payments") or d.get("items") or [])}
    check(aps.get(ap30, {}).get("payment_status") == "應付款",
          "30,000 那張還是應付款（只付了 1/3）",
          aps.get(ap30, {}).get("payment_status"))
    it = (r.get("items") or [{}])[0]
    check(it.get("request_open") == 20000, "畫面看得到還欠 20,000",
          it.get("request_open"))

    print("")
    print("[6] 🔴 連結拿掉 → 退回未付款並清掉付款日")
    st, r = call("PUT", f"/crm/cash-entries/{e1}/payments", {"items": []})
    check(st == 200, "清空成功", st)
    st, d = call("GET", "/crm/payments")
    aps = {x["id"]: x for x in (d.get("payments") or d.get("items") or [])}
    check(aps.get(ap8, {}).get("payment_status") == "未付款", "退回未付款",
          aps.get(ap8, {}).get("payment_status"))
    check(not aps.get(ap8, {}).get("payment_date"), "付款日清掉了",
          aps.get(ap8, {}).get("payment_date"))
    st, d = call("GET", "/crm/cash-entries?limit=3000")
    row = next((x for x in (d.get("items") or d.get("entries") or [])
                if x["id"] == e1), None)
    check(not row.get("payment_request_id"), "主要請款單也清掉了",
          row.get("payment_request_id"))

    print("")
    print("[7] 一定錯的要擋")
    st, r = call("PUT", f"/crm/cash-entries/{e2}/payments",
                 {"items": [{"payment_request_id": "不存在", "amount": 100}]})
    check(st == 404, "單不存在 → 404", st)
    st, r = call("PUT", f"/crm/cash-entries/{e2}/payments", {"items": [
        {"payment_request_id": ap30, "amount": 100},
        {"payment_request_id": ap30, "amount": 200}]})
    check(st == 422 and "重複" in str(r.get("detail", "")), "重複 → 422",
          (st, r.get("detail")))
    st, r = call("PUT", f"/crm/cash-entries/{e2}/payments",
                 {"items": [{"payment_request_id": ap30, "amount": 0}]})
    check(st == 422, "金額 0 → 422", st)

    print("")
    print("[8] 金額對不上**不擋**（合併匯款的實務）")
    st, r = call("PUT", f"/crm/cash-entries/{e2}/payments",
                 {"items": [{"payment_request_id": ap30, "amount": 30000}]})
    check(st == 200, "掛超過實付照樣存得起來", st)
    check((r.get("check") or {}).get("state") == "over", "但判讀說 over",
          (r.get("check") or {}).get("state"))
finally:
    print("")
    print("[清理]")
    asyncio.run(teardown())
    st, d = call("GET", "/crm/payments")
    left = [x for x in (d.get("payments") or d.get("items") or [])
            if (x.get("summary") or "").startswith("ZZ分配測試")]
    check(not left, "測試請款單清光", len(left))

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
