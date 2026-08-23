# -*- coding: utf-8 -*-
"""專案詳情的「發票」分頁在真瀏覽器裡真的能用（owner 2026-08-23）。

證明的是這個入口跟發票本的**差別**，不是「字串在畫面上」：
  1. 分頁打得開，摘要（已開／已收／尚欠）算得出來
  2. 「開發票」視窗的預設值真的從專案與客戶帶過來（抬頭是客戶**全名**）
  3. 稅額即時算，而且跟共用規則一致（含稅 166,950 → 未稅 159,000）
  4. 換到另一個專案，分頁內容跟著換（lazy 分頁的老坑）

不實際開票 —— 寫入那條路由是既有的 POST /crm/invoices（發票本已在用）。

dev 庫沒有掛專案的發票（生產有 74 張），所以自己種：客戶（全名≠代稱，才驗得出
抬頭有沒有拿錯）＋ 專案（有合約金額，才驗得出「還能開」）＋ 兩張發票。
"""
import asyncio
import io
import sys
import uuid
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"


from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["crm_projects", "crm_invoices", "crm_clients", "money_view"]})
TAG = "ZZPI"
fails = []

async def seed():
    from db.models import Client as CrmClient, CrmInvoice, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        # 代稱與全名刻意不同 —— 抬頭拿錯就看得出來
        cli = CrmClient(id=uuid.uuid4().hex, short_name=TAG + "泛亞",
                        full_name=TAG + "泛亞工程顧問股份有限公司", tax_id="12345675")
        s.add(cli)
        await s.flush()
        proj = CrmProject(id=uuid.uuid4().hex, name=TAG + " 形象影片", status="製作",
                          client_id=cli.id, contract_amount=500000)
        s.add(proj)
        await s.flush()
        for n, amt, st in ((1, 300000, "已收款"), (2, 100000, "未收款")):
            s.add(CrmInvoice(
                id=uuid.uuid4().hex, entity="parent", payment_type="收款",
                category="專案", invoice_number=f"{TAG}{n:04d}",
                invoice_date=datetime(2026, 7, n + 1), title=f"{TAG} 第{n}期款",
                company_name=cli.full_name, tax_id=cli.tax_id,
                amount_total=amt, amount_ex_tax=round(amt / 1.05),
                tax_amount=amt - round(amt / 1.05),
                project_id=proj.id, payment_status=st))
        # 第二個專案：用來驗「換專案時分頁跟著換」
        other = CrmProject(id=uuid.uuid4().hex, name=TAG + " 另一個案子", status="製作")
        s.add(other)
        await s.commit()
        return proj.id, other.id, cli.short_name

async def clean():
    from sqlalchemy import select

    from db.models import Client as CrmClient, CrmInvoice, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        for m, col in ((CrmInvoice, CrmInvoice.invoice_number),
                       (CrmProject, CrmProject.name),
                       (CrmClient, CrmClient.short_name)):
            for r in (await s.execute(select(m).where(col.like(TAG + "%")))).scalars().all():
                await s.delete(r)
        await s.commit()

asyncio.run(clean())
PID, OTHER_ID, SHORT = asyncio.run(seed())

def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 1200})
    ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(6000)
    pg.evaluate("window.switchTab('tab_crm_projects')")
    pg.wait_for_timeout(4000)

    print("[1] 開種好的那個專案（合約 500,000／已開 400,000／已收 300,000）")
    pid = PID
    pg.evaluate("(id) => window._projSelect && window._projSelect(id)", pid)
    pg.wait_for_timeout(2500)

    print("")
    print("[2] 發票分頁打得開")
    tab = pg.locator("#proj-detail-tabs .crm-tab[data-tab='invoices']")
    check(tab.count() == 1, "分頁按鈕在", str(tab.count()))
    tab.click()
    pg.wait_for_timeout(3000)
    host = pg.locator("#proj-detail-invoices")
    check(host.is_visible(), "分頁內容顯示了")
    txt = host.inner_text()
    for word in ("已開發票", "已收", "尚欠", "開發票"):
        check(word in txt, f"摘要有「{word}」")
    check("$" in txt, "有金額")
    check("400,000" in txt, "已開 400,000（兩張加總）")
    check("300,000" in txt, "已收 300,000")
    check("100,000" in txt, "尚欠 100,000")
    check("還能開" in txt and "100,000" in txt, "還能開 100,000（合約 500,000 − 已開 400,000）")
    rows = pg.locator("#proj-detail-invoices tbody tr").count()
    check(rows > 0, "發票清單有列", str(rows))
    check("帳務 → 發票" in txt, "有告訴人編輯要去哪一頁")

    print("")
    print("[3] 🔴 開發票視窗的預設值從專案與客戶帶過來")
    pg.click("#proj-detail-invoices button:has-text('開發票')")
    pg.wait_for_timeout(1500)
    check(pg.locator("#proj-inv-modal").is_visible(), "視窗開了")
    title = pg.input_value("#proj-inv-title")
    company = pg.input_value("#proj-inv-company")
    date = pg.input_value("#proj-inv-date")
    check(bool(title), "品名預填了專案名", title[:20])
    check(bool(date), "日期預填了今天", date)
    # 抬頭要嘛是客戶全名、要嘛留空（客戶沒填全名）—— 不可以是代稱
    check(company.endswith("股份有限公司"), "抬頭是客戶**全名**", company)
    check(company != SHORT, "不是客戶代稱", f"代稱={SHORT!r} 抬頭={company!r}")
    check(pg.input_value("#proj-inv-taxid") == "12345675", "統編也帶了",
          pg.input_value("#proj-inv-taxid"))

    print("")
    print("[4] 稅額即時算，跟共用規則一致")
    pg.fill("#proj-inv-amount", "166950")
    pg.dispatch_event("#proj-inv-amount", "input")
    pg.wait_for_timeout(500)
    calc = pg.locator("#proj-inv-calc").inner_text()
    check("159,000" in calc, "含稅 166,950 → 未稅 159,000", calc)
    check("7,950" in calc, "稅額 7,950", calc)
    pg.select_option("#proj-inv-mode", "ex")
    pg.wait_for_timeout(500)
    calc = pg.locator("#proj-inv-calc").inner_text()
    check("175,298" in calc, "切未稅 166,950 → 含稅 175,298", calc)

    print("")
    print("[5] 沒填金額不能開立")
    pg.fill("#proj-inv-amount", "")
    pg.dispatch_event("#proj-inv-amount", "input")
    pg.click("#proj-inv-modal button:has-text('開立')")
    pg.wait_for_timeout(800)
    check(pg.locator("#proj-inv-err").is_visible(), "擋下來並說原因",
          pg.locator("#proj-inv-err").inner_text())
    pg.click("#proj-inv-modal button:has-text('取消')")
    pg.wait_for_timeout(500)
    check(not pg.locator("#proj-inv-modal").is_visible(), "取消關得掉")

    print("")
    print("[6] 🔴 換到另一個專案，分頁內容要跟著換")
    before = pg.locator("#proj-detail-invoices").inner_text()[:400]
    pg.evaluate("(id) => window._projSelect && window._projSelect(id)", OTHER_ID)
    pg.wait_for_timeout(3500)
    after = pg.locator("#proj-detail-invoices").inner_text()
    check(after[:400] != before, "內容換了（沒殘留上一案）")
    check("還沒有發票" in after, "另一個案子顯示「還沒有發票」", after[:60])
    check("400,000" not in after, "🔴 上一案的金額沒有殘留")
    check(not errs, "整輪都沒有 JS 例外", str(errs[:3]))
    b.close()

asyncio.run(clean())
print("")
print("[已清理種進去的測試資料]")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
