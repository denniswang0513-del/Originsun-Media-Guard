# -*- coding: utf-8 -*-
"""轉存手續費的三個入口（owner 2026-08-24：「擬一個自動化的做法」）。

① 對帳系統 › 帳戶間轉存卡片：列出還埋在支出裡的手續費，一鍵認列
② 收支明細的編輯視窗：填匯費會自動從支出扣（總流出不變）
③ 現金流量表那句「帳戶間轉存未完全成對」後面接「去處理 →」

🔴 ② 是 owner 實際踩到的：他填了匯費 15 但支出沒減，系統認為多流出 15，
   富邦餘額憑空少了 30（兩筆）。匯費不是另外再扣一筆，是把已經扣掉的標示出來。
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
                  "modules": ["money_view", "finance", "crm_invoices"]})
TAG = "ZZ轉存UI-" + uuid.uuid4().hex[:6]
fails = []
ids = {}


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


async def seed():
    from db.models import BankAccount, CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        a = BankAccount(id=uuid.uuid4().hex[:16], entity="parent", name=TAG + "甲",
                        acct_kind="bank", opening_balance=0)
        b = BankAccount(id=uuid.uuid4().hex[:16], entity="parent", name=TAG + "乙",
                        acct_kind="bank", opening_balance=0)
        s.add_all([a, b])
        o = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent", category="轉存",
                         entry_date=datetime(2026, 8, 20), expense=35015,
                         summary=TAG + " 轉出", bank_account_id=a.id)
        i = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent", category="轉存",
                         entry_date=datetime(2026, 8, 20), deposit=35000,
                         summary=TAG + " 轉入", bank_account_id=b.id)
        s.add_all([o, i])
        await s.commit()
        return {"out": o.id, "in": i.id, "a": a.id, "b": b.id}


async def clean(d):
    from sqlalchemy import delete
    from db.models import BankAccount, CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(CrmCashEntry).where(
            CrmCashEntry.id.in_([d["out"], d["in"]])))
        await s.execute(delete(BankAccount).where(BankAccount.id.in_([d["a"], d["b"]])))
        await s.commit()


async def read_out(eid):
    from db.models import CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        e = await s.get(CrmCashEntry, eid)
        return {"expense": e.expense or 0, "bank_fee": e.bank_fee or 0}


def in_thread(fn, *a):
    """Playwright 同步 API 佔著 event loop —— asyncio.run 要換執行緒跑。"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(fn(*a))).result()


try:
    ids = asyncio.run(seed())
    print(f"[0] 種了一組待拆的轉存（{TAG}）：轉出 35,015 / 轉入 35,000")

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
        pg.wait_for_timeout(4000)

        print("")
        print("[1] 對帳系統 › 帳戶間轉存卡片")
        # 🔴 對帳系統不是財務的子視圖 —— 從「收支明細 › 對帳／匯入」就地展開
        pg.locator("[data-inv-view='cashbook']").click()
        pg.wait_for_selector("#cash-btn-recon", timeout=25000)
        pg.locator("#cash-btn-recon").click()
        pg.wait_for_selector("#finbank-xfer-card", timeout=25000)
        pg.wait_for_timeout(4000)
        txt = pg.locator("#finbank-xfer-card").inner_text()
        print("   ", txt.replace("\n", " ")[:120])
        check("帳戶間轉存" in txt, "卡片標題在")
        check(TAG in txt, "列出了那組待拆的", TAG)
        check("35,015" in txt and "35,000" in txt, "看得到目前支出與對方收到")
        check("總流出不變" in txt, "有講清楚按下去帳戶餘額不會變")

        print("")
        print("[2] 一鍵認列")
        pg.evaluate("window.confirm = () => true")
        pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#finbank-xfer-card button')]
                .find(x => x.innerText.includes('認列'));
            if (b) b.click();
        }""")
        pg.wait_for_timeout(5000)
        got = in_thread(read_out, ids["out"])
        print("    entry =", got)
        check(got["expense"] == 35000 and got["bank_fee"] == 15,
              "支出 35,000 / 匯費 15（總流出仍是 35,015）", str(got))
        txt2 = pg.locator("#finbank-xfer-card").inner_text()
        check(TAG not in txt2, "清單裡不再出現它")

        print("")
        print("[3] 🔴 收支明細的編輯視窗：填匯費會自動從支出扣")
        pg.locator("#cash-recon-back").click()      # 從對帳面板回收支清單
        pg.wait_for_timeout(2500)
        pg.click("#cash-btn-add")
        pg.wait_for_timeout(1500)
        pg.fill("#cash-f-expense", "35015")
        pg.dispatch_event("#cash-f-expense", "input")
        pg.fill("#cash-f-bank_fee", "15")
        pg.dispatch_event("#cash-f-bank_fee", "input")
        pg.wait_for_timeout(600)
        exp = pg.input_value("#cash-f-expense")
        hint = pg.evaluate(
            "() => document.getElementById('cash-f-fee-hint')?.innerText || ''")
        print("    支出格 =", exp, "| 提示 =", hint.replace("\n", " ")[:60])
        check(exp == "35000", "🔴 支出自動變成 35,000（總流出不變）", repr(exp))
        check("35,015" in hint, "旁邊寫出銀行實扣 35,015", hint[:40])
        pg.evaluate("() => { const m=document.getElementById('cash-modal');"
                    " if (m) m.style.display='none'; }")

        print("")
        print("[4] 現金流量表那句提示接得上「去處理」")
        src = pg.evaluate("""async () => {
            const r = await fetch('./tabs/finance/subviews/statements.js');
            return await r.text();
        }""")
        check("gotoTransferCard" in src, "statements.js 有跳過去的 handler")
        check("finbank-xfer-card" in src, "跳的目標就是那張卡")
        check(not errs, "整輪都沒有 JS 例外", errs[:3])
        b.close()

finally:
    print("")
    print("[清理]")
    if ids:
        asyncio.run(clean(ids))
        print("  已清掉種進去的帳戶與收支")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
