# -*- coding: utf-8 -*-
"""收支明細兩件事（owner 2026-08-24）：

① 「需要有個地方讓我調整銀行匯費」→ 關聯發票面板每一張各給一格匯費
   （匯出行是對每一張發票的匯款各扣一次，一顆整筆的按鈕表達不了）
② 「沒有填分類的，標注一個小紅點讓我們知道要調整」

🔴 ① 最重要的一條是**往返**：存完重開，那格要畫得回來。原本連結表沒有 fee 欄，
   面板每次載入都是空的 → 按一下儲存就送 fee=0，deposit 退回去、bank_fee 被
   清掉，而且畫面上完全看不出來（api_receipt_fee.py [2d] 釘資料那側）。
"""
import asyncio
import sys
import uuid
from datetime import datetime

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
TAG = "ZZ匯費UI-" + uuid.uuid4().hex[:6]
fails = []
ids = {}


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


async def seed():
    from db.models import CrmCashEntry, CrmInvoice
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        inv = CrmInvoice(id=uuid.uuid4().hex[:16], entity="parent",
                         invoice_date=datetime(2026, 8, 1), title=TAG + " 尾款",
                         invoice_number="ZZ" + uuid.uuid4().hex[:8].upper(),
                         company_name=TAG, amount_total=149900, payment_type="收款")
        # 收款列（有分類）＋ 一列**沒有分類**的（小紅點要標它）
        got = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                           entry_date=datetime(2026, 8, 1), deposit=149870,
                           summary=TAG + " 匯入款", category="專案")
        nocat = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                             entry_date=datetime(2026, 8, 1), expense=38781,
                             summary=TAG + " 網路繳費")      # category 留空
        s.add_all([inv, got, nocat])
        await s.commit()
        return {"inv": inv.id, "got": got.id, "nocat": nocat.id}


async def clean(d):
    from sqlalchemy import delete
    from db.models import CrmCashEntry, CrmCashInvoiceLink, CrmInvoice
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.cash_entry_id.in_([d["got"], d["nocat"]])))
        await s.execute(delete(CrmCashEntry).where(
            CrmCashEntry.id.in_([d["got"], d["nocat"]])))
        await s.execute(delete(CrmInvoice).where(CrmInvoice.id == d["inv"]))
        await s.commit()


def in_thread(coro_fn, *a):
    """🔴 Playwright 的同步 API 佔著 event loop —— 在 `with sync_playwright()`
    區塊裡直接 asyncio.run 會炸 "cannot be called from a running event loop"。
    丟到另一條執行緒跑。"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn(*a))).result()


async def read_entry(eid):
    from db.models import CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        e = await s.get(CrmCashEntry, eid)
        return {"deposit": e.deposit, "bank_fee": e.bank_fee} if e else None


try:
    ids = asyncio.run(seed())
    print(f"[0] 種了：發票 149,900、收款 149,870、一列沒分類的支出（{TAG}）")

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1700, "height": 1200})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_crm_invoices')")
        pg.wait_for_timeout(3000)
        pg.locator("[data-inv-view='cashbook']").click()
        # 收支明細是 lazy-load（fetch html + import js）—— 先等工具列出現，
        # 那才是「子視圖載完了」的證據；直接等清單會在還沒載完時就逾時
        pg.wait_for_selector("#cash-btn-recon", timeout=25000)
        pg.wait_for_selector("#cash-list-body .crm-row", timeout=25000)
        pg.fill("#cash-search", TAG)
        pg.wait_for_timeout(3000)

        print("")
        print("[1] 沒填分類的那一列有小紅點")
        dots = pg.evaluate("""() => [...document.querySelectorAll('#cash-list-body .crm-row')]
            .map(r => ({ name: r.querySelector('.crm-row-name')?.innerText || '',
                         dot: !!r.querySelector('span[title*="還沒填分類"]') }))""")
        for d in dots:
            print("   ", d)
        nocat = next((d for d in dots if "網路繳費" in d["name"]), None)
        withcat = next((d for d in dots if "匯入款" in d["name"]), None)
        check(nocat and nocat["dot"], "沒分類那列有紅點")
        check(withcat and not withcat["dot"], "🔴 有分類那列**沒有**紅點（不然等於全部都在叫）")

        print("")
        print("[2] 關聯發票面板每一張各有一格匯費")
        pg.evaluate(f"window._cashSelect('{ids['got']}')")
        pg.wait_for_timeout(2500)
        # 掛上那張發票
        pg.fill("#cash-alloc-search", "ZZ")
        pg.wait_for_timeout(2000)
        pg.evaluate("""() => {
            const r = document.querySelector('#cash-alloc-results div');
            if (r) r.click();
        }""")
        pg.wait_for_timeout(1200)
        n_fee = pg.evaluate(
            "() => document.querySelectorAll('#cash-alloc-box [data-alloc-fee]').length")
        check(n_fee == 1, "掛上的那張有一格匯費", str(n_fee))
        tip = pg.evaluate(
            "() => document.querySelector('#cash-alloc-box [data-alloc-fee]')?.title || ''")
        check("匯出行" in tip, "那格說得出它是什麼", tip[:40])

        print("")
        print("[3] 填 30 存下去 → deposit 補成 149,900、淨流不變")
        pg.evaluate("""() => {
            const a = document.querySelector('#cash-alloc-box [data-alloc-i]');
            a.value = '149900'; a.dispatchEvent(new Event('change'));
        }""")
        pg.wait_for_timeout(600)
        pg.evaluate("""() => {
            const f = document.querySelector('#cash-alloc-box [data-alloc-fee]');
            f.value = '30'; f.dispatchEvent(new Event('change'));
        }""")
        pg.wait_for_timeout(600)
        pg.click("#cash-alloc-save")
        pg.wait_for_timeout(3500)
        e1 = in_thread(read_entry, ids["got"])
        print("    entry =", e1)
        check(e1["deposit"] == 149900, "deposit 補成客戶實付的 149,900", str(e1["deposit"]))
        check(e1["bank_fee"] == 30, "匯費掛上了", str(e1["bank_fee"]))
        check((e1["deposit"] or 0) - (e1["bank_fee"] or 0) == 149870,
              "淨流入仍是銀行說的 149,870")

        print("")
        print("[4] 🔴 重開面板：那格要畫得回來（不然按一次儲存就抹掉）")
        pg.evaluate("window._cashSelect('')")
        pg.wait_for_timeout(800)
        pg.evaluate(f"window._cashSelect('{ids['got']}')")
        pg.wait_for_timeout(2500)
        shown = pg.evaluate(
            "() => document.querySelector('#cash-alloc-box [data-alloc-fee]')?.value || ''")
        check(shown == "30", "重開時那格還是 30", repr(shown))
        pg.click("#cash-alloc-save")
        pg.wait_for_timeout(3500)
        e2 = in_thread(read_entry, ids["got"])
        print("    entry =", e2)
        check(e2["deposit"] == 149900 and e2["bank_fee"] == 30,
              "原樣重存一次，匯費沒被抹掉", str(e2))
        check(not errs, "整輪都沒有 JS 例外", errs[:3])
        b.close()

finally:
    print("")
    print("[清理]")
    if ids:
        asyncio.run(clean(ids))
        print("  已清掉種進去的資料")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
