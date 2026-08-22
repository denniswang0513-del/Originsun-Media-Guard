# -*- coding: utf-8 -*-
"""收支明細的「關聯請款單」區：真的在瀏覽器點一輪。

API 全綠但畫面沒接上，這個 repo 咬過兩次。所以搜尋、挑選、存檔、
「認列成匯費」那顆都要真的按下去。
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
if ":8000" in BASE:
    sys.path.insert(0, r"C:\OriginsunAgent")
    os.chdir(r"C:\OriginsunAgent")
T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def api(m, p, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + "/api/v1" + p, data=d, method=m, headers={
        "Authorization": "Bearer " + T, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def setup():
    from db.session import init_db, get_session_factory
    from db.models import CrmCashEntry, CrmPaymentRequest
    await init_db()
    async with get_session_factory()() as s:
        ap = CrmPaymentRequest(
            id=uuid.uuid4().hex[:16], entity="parent", amount=8000,
            summary="ZZUI分配測試", payee_name="ZZUI收款人", category="專案外包",
            request_date=datetime(2026, 8, 1), payment_status="未付款")
        e = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                         entry_date=datetime(2026, 8, 20), expense=8010,
                         summary="ZZUI匯款", category="請款單", payee="ZZUI收款人")
        s.add(ap)
        s.add(e)
        await s.commit()
        return ap.id, e.id


async def teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import CrmCashEntry, CrmCashPaymentLink, CrmPaymentRequest
    await init_db()
    async with get_session_factory()() as s:
        for e in (await s.execute(select(CrmCashEntry).where(
                CrmCashEntry.summary.like("ZZUI%")))).scalars().all():
            for ln in (await s.execute(select(CrmCashPaymentLink).where(
                    CrmCashPaymentLink.cash_entry_id == e.id))).scalars().all():
                await s.delete(ln)
            await s.delete(e)
        for a in (await s.execute(select(CrmPaymentRequest).where(
                CrmPaymentRequest.summary.like("ZZUI%")))).scalars().all():
            await s.delete(a)
        await s.commit()


asyncio.run(teardown())
ap_id, entry_id = asyncio.run(setup())

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1600, "height": 1200})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_crm_invoices')")   # 財務管理
        pg.wait_for_timeout(3000)
        pg.evaluate("""() => {
            const el = document.querySelector('[data-inv-view="cashbook"]');
            if (el) el.click();
        }""")
        pg.wait_for_timeout(4000)

        print("[1] 找到那筆匯款並開詳情")
        # 🔴 一定要框在 #cash-list-body 裡面 —— 這個 SPA 有一堆隱藏的表格，
        #    全頁找 tr 會抓到別的 tab 的列（第一次就抓到提案清單去了）。
        # 🔴 列是 div.crm-row 不是 <tr>（這個表是 div 排版的），而且一定要框在
        #    #cash-list-body 裡面 —— 這個 SPA 有一堆隱藏表格，全頁找 tr 會抓到
        #    別的 tab 的列（第一次就抓到提案清單去了）。
        pg.wait_for_selector("#cash-list-body .crm-row", timeout=20000)
        row = pg.locator("#cash-list-body .crm-row", has_text="ZZUI匯款").first
        check(row.count() > 0, "列出現在收支明細裡")
        row.click()
        pg.wait_for_timeout(2500)
        check(pg.locator("#cash-pay-box").count() == 1, "有「關聯請款單」區")
        check(not errs, "沒有 JS 例外", errs[:2])

        print("")
        print("[2] 搜尋並掛上請款單")
        pg.fill("#cash-pay-search", "ZZUI收款人")
        pg.wait_for_timeout(1200)
        hit = pg.locator("#cash-pay-results [data-pay-add]").first
        check(hit.count() > 0, "搜得到那張請款單")
        hit.click()
        pg.wait_for_timeout(600)
        pg.click("#cash-pay-save")
        pg.wait_for_timeout(3000)
        check(not errs, "存檔沒有 JS 例外", errs[:2])
        st, d = api("GET", f"/crm/cash-entries/{entry_id}/payments")
        check(len(d.get("items") or []) == 1, "後端收到一張", len(d.get("items") or []))
        check((d.get("check") or {}).get("state") == "fee", "判為手續費",
              (d.get("check") or {}).get("state"))

        print("")
        print("[3] 🔴「認列成匯費」那顆真的按得到")
        txt = pg.inner_text("#cash-pay-box")
        check("認列成匯費" in txt, "按鈕出現了", txt[:100])
        # 🔴 狀態列也要斷言。前一版只看按鈕，結果狀態列整條是空的
        #    （後端 msg→message 改名後前端沒跟上）也照樣 ALL PASS。
        check("實付 $8,010" in txt, "狀態列有實付金額")
        check("跨行手續費" in txt, "狀態列有判讀語")
        pg.click("#cash-pay-fee")
        pg.wait_for_timeout(3000)
        check(not errs, "認列沒有 JS 例外", errs[:2])
        st, d = api("GET", "/crm/cash-entries?limit=3000")
        row2 = next((x for x in (d.get("items") or d.get("entries") or [])
                     if x["id"] == entry_id), None)
        check(row2 and int(row2.get("bank_fee") or 0) == 10,
              "bank_fee 真的寫進去了", row2 and row2.get("bank_fee"))

        print("")
        print("[4] 請款單狀態跟著變")
        st, d = api("GET", "/crm/payments")
        aps = {x["id"]: x for x in (d.get("payments") or d.get("items") or [])}
        check(aps.get(ap_id, {}).get("payment_status") == "已付款", "標成已付款",
              aps.get(ap_id, {}).get("payment_status"))
        b.close()
finally:
    print("")
    print("[清理]")
    asyncio.run(teardown())
    st, d = api("GET", "/crm/payments")
    left = [x for x in (d.get("payments") or d.get("items") or [])
            if (x.get("summary") or "").startswith("ZZUI")]
    check(not left, "測試資料清光", len(left))

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
