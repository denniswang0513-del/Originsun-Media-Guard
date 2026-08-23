# -*- coding: utf-8 -*-
"""收支明細的「關聯發票」區：真的在瀏覽器點一輪。

🔴 為什麼補這支（2026-08-22）：付款側那個面板是這個面板的複本，要把兩份
合成一份工廠之前，發現**收款側完全沒有 UI e2e** —— 等於重寫時沒有任何訊號。
單元測試只掃得到原始碼字串，掃不出「按鈕綁錯 state」「存檔打錯端點」。

覆蓋：搜尋 → 挑一張 → 預帶金額是「尚欠」不是面額 → 存檔 → 重開載得回來。
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
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

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
    """一張 100,000 的發票、已收 60,000（靠一筆舊收款掛上去），
    再開一筆新的 40,000 收款 —— 預帶金額應該是尚欠的 40,000 而不是面額。"""
    from db.session import init_db, get_session_factory
    from db.models import CrmCashEntry, CrmCashInvoiceLink, CrmInvoice
    await init_db()
    async with get_session_factory()() as s:
        inv = CrmInvoice(
            id=uuid.uuid4().hex[:16], entity="parent", title="ZZINV分配測試",
            invoice_number="ZZ-88001", amount_total=100000, amount_ex_tax=95238,
            tax_amount=4762, company_name="ZZINV客戶", payment_type="收款",
            payment_status="未收款", issue_status="已開立",
            invoice_date=datetime(2026, 8, 1))
        old = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                           entry_date=datetime(2026, 8, 5), deposit=60000,
                           summary="ZZINV第一期", category="應收發票")
        new = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                           entry_date=datetime(2026, 8, 20), deposit=40000,
                           summary="ZZINV第二期", category="應收發票")
        s.add_all([inv, old, new])
        await s.flush()
        s.add(CrmCashInvoiceLink(id=uuid.uuid4().hex, cash_entry_id=old.id,
                                 invoice_id=inv.id, amount=60000))
        await s.commit()
        return inv.id, new.id

async def teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import CrmCashEntry, CrmCashInvoiceLink, CrmInvoice
    await init_db()
    async with get_session_factory()() as s:
        for e in (await s.execute(select(CrmCashEntry).where(
                CrmCashEntry.summary.like("ZZINV%")))).scalars().all():
            for ln in (await s.execute(select(CrmCashInvoiceLink).where(
                    CrmCashInvoiceLink.cash_entry_id == e.id))).scalars().all():
                await s.delete(ln)
            await s.delete(e)
        for i in (await s.execute(select(CrmInvoice).where(
                CrmInvoice.title.like("ZZINV%")))).scalars().all():
            for ln in (await s.execute(select(CrmCashInvoiceLink).where(
                    CrmCashInvoiceLink.invoice_id == i.id))).scalars().all():
                await s.delete(ln)
            await s.delete(i)
        await s.commit()

def open_cashbook(pg):
    pg.evaluate("window.switchTab('tab_crm_invoices')")
    pg.wait_for_timeout(3000)
    pg.evaluate("""() => {
        const el = document.querySelector('[data-inv-view="cashbook"]');
        if (el) el.click();
    }""")
    # 🔴 列是 div.crm-row 不是 <tr>，而且一定要框在 #cash-list-body 裡
    #    （這個 SPA 有一堆隱藏表格，全頁找會抓到別的 tab 的列）。
    pg.wait_for_selector("#cash-list-body .crm-row", timeout=30000)

asyncio.run(teardown())
inv_id, entry_id = asyncio.run(setup())

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
        open_cashbook(pg)

        print("[1] 找到那筆收款並開詳情")
        row = pg.locator("#cash-list-body .crm-row", has_text="ZZINV第二期").first
        check(row.count() > 0, "列出現在收支明細裡")
        row.click()
        pg.wait_for_timeout(2500)
        check(pg.locator("#cash-alloc-box").count() == 1, "有「關聯發票」區")

        print("")
        print("[2] 搜尋並掛上發票")
        pg.fill("#cash-alloc-search", "ZZ-88001")
        pg.wait_for_timeout(900)
        hits = pg.inner_text("#cash-alloc-results")
        check("ZZINV分配測試" in hits, "搜得到那張發票", hits[:80])
        check("尚欠 $40,000" in hits, "候選列有標尚欠多少", hits[:120])
        pg.click("#cash-alloc-results [data-alloc-add]")
        pg.wait_for_timeout(600)

        print("")
        print("[3] 🔴 預帶的是尚欠 40,000，不是面額 100,000")
        amt = pg.input_value("#cash-alloc-box [data-alloc-i='0']")
        check(str(amt) == "40000", "預帶尚欠金額", amt)

        print("")
        print("[4] 存檔")
        pg.click("#cash-alloc-save")
        pg.wait_for_timeout(3000)
        check(not errs, "存檔沒有 JS 例外", errs[:2])
        txt = pg.inner_text("#cash-alloc-box")
        check("實收 $40,000" in txt, "狀態列有實收金額", txt[-90:])
        check("相符" in txt, "判為相符", txt[-90:])

        print("")
        print("[5] 發票收齊了")
        st, d = api("GET", "/crm/invoices")
        got = {x["id"]: x for x in (d.get("invoices") or d.get("items") or [])}
        me = got.get(inv_id, {})
        check(int(me.get("collected") or 0) == 100000, "實收合計 100,000",
              me.get("collected"))
        check(bool(me.get("settled")), "標成已收齊", me.get("settled"))

        print("")
        print("[6] 重開載得回來")
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        open_cashbook(pg)
        pg.locator("#cash-list-body .crm-row", has_text="ZZINV第二期").first.click()
        pg.wait_for_timeout(3000)
        txt2 = pg.inner_text("#cash-alloc-box")
        check("ZZINV分配測試" in txt2, "掛著的那張載回來了", txt2[:80])
        check(not errs, "重開沒有 JS 例外", errs[:2])
        b.close()
finally:
    print("")
    print("[清理]")
    asyncio.run(teardown())
    st, d = api("GET", "/crm/invoices")
    left = [x for x in (d.get("invoices") or d.get("items") or [])
            if (x.get("title") or "").startswith("ZZINV")]
    check(not left, "測試資料清光", len(left))

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
