# -*- coding: utf-8 -*-
"""逐案損益的費用欄怎麼顯示（甲案，owner 2026-08-28 拍板）。

CRM 專案帳目有成本行時，「委外費用／行政雜支」＝**CRM 合計 ＋ 手填那幾筆**。

🔴 這支盯的是「輸入框裡放哪個數字」：放合計的話，使用者一存檔就把 CRM 算出來
   的數字存成一份副本 —— CRM 那邊之後改了，這裡停在舊數字而且完全看不出來。
   所以框裡是手填那部分（改得動），CRM 那部分在標籤上、合計在下面的小字。
   資料那半在 tests/unit/test_ledger_project.py。
"""
import asyncio, sys, uuid
from datetime import datetime
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token
from playwright.sync_api import sync_playwright
from tests.e2e._guard import refuse_prod_seed
BASE = "http://127.0.0.1:8001"
refuse_prod_seed(BASE)
T = create_token({"sub":"admin","username":"admin","access_level":3,
                  "modules":["money_view","crm_projects","finance","finance_mine"]})
TAG = "ZZ甲案-" + uuid.uuid4().hex[:6]

async def seed():
    from db.models import Client, CrmProject, CrmProjectCostLine
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        c = Client(id=uuid.uuid4().hex[:16], short_name=TAG, full_name=TAG)
        p = CrmProject(id=uuid.uuid4().hex[:16], entity="mine", name=TAG + "案",
                       client_id=c.id, status="製作", contract_amount=100000,
                       completion_date=datetime(2026, 4, 30),
                       ledger_detail={"outsource": 5000})       # 手填 5,000
        l = CrmProjectCostLine(id=uuid.uuid4().hex[:16], project_id=p.id,
                               phase="現場拍攝", item_name="動態攝影",
                               sort_order=0, actual_amount=12000)   # CRM 12,000
        s.add_all([c, p, l])
        await s.commit()
        return c.id, p.id, l.id

async def clean(ids):
    from sqlalchemy import delete
    from db.models import Client, CrmProject, CrmProjectCostLine
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(CrmProjectCostLine).where(CrmProjectCostLine.id == ids[2]))
        await s.execute(delete(CrmProject).where(CrmProject.id == ids[1]))
        await s.execute(delete(Client).where(Client.id == ids[0]))
        await s.commit()

ids = asyncio.run(seed())
fails = []
def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra else ""))
    if not ok: fails.append(label)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); ctx = b.new_context(viewport={"width":1700,"height":1200})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
        # 逐案損益是私帳專屬子視圖（fin-nav-mine-only）—— 帳本由頁面隱形 pin，
        # my-ledger.html 就是這樣設的（那頁只有真登入一條路，不適合自動化）
        ctx.add_init_script("window._finEntity = 'mine'")
        pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_crm_invoices')"); pg.wait_for_timeout(3500)
        pg.locator("#finance-nav [data-subview='projects']").first.click()
        pg.wait_for_selector("#fpl-list-body .crm-row, #fpl-list .crm-row", timeout=25000)
        pg.wait_for_timeout(1500)
        pg.evaluate(f"window._finProjLedger.open('{ids[1]}')")
        pg.wait_for_timeout(2500)
        got = pg.evaluate("""() => {
            const el = document.getElementById('fpl-c-outsource');
            const row = el && el.closest('tr');
            return { val: el ? el.value : null, ro: el ? el.readOnly : null,
                     text: row ? row.innerText.replace(/\s+/g, ' ') : null };
        }""")
        print("   ", got)
        check(got["val"] == "5000", "輸入框放的是手填 5,000（不是合計）", str(got["val"]))
        check(got["ro"] is False, "手填那格改得動（甲案不再鎖）")
        check("＋CRM 12,000" in (got["text"] or ""), "標籤標出 CRM 那部分")
        check("合計 17,000" in (got["text"] or ""), "合計 17,000 看得見")
        check(not errs, "沒有 JS 錯誤", "; ".join(errs[:2]))
        b.close()
finally:
    asyncio.run(clean(ids))
print("ALL PASS" if not fails else "FAILED: " + "; ".join(fails))
sys.exit(1 if fails else 0)
