# -*- coding: utf-8 -*-
"""從專案頁開的發票，在發票本裡要**綁好專案**（owner 2026-08-23）。

owner：「在專案表填的發票，在發票裡自動綁定好。」

ui_project_invoices.py 刻意沒有真的開票（只驗預填與摘要），所以「按下去之後
到底存成什麼」從來沒被證明過。這支真的按下「開立」，然後：
  ① 直接讀 DB —— project_id / category / 金額三欄 / 抬頭統編 / 申請人品項 存對了沒
  ② 回專案頁 —— 新的那張出現在清單、摘要跟著變
  ③ 切到帳務→發票 —— 那張票在發票本裡看得到，而且「關聯專案」是對的
  ④ 就地改品項 —— **只有那一欄變**，其他欄位一個都不能被洗掉
  ⑤ 在專案頁刪票 —— 發票本那邊也跟著不見

🔴 ②③ 缺一不可：只驗 DB 的話，「存對了但兩邊畫面都看不到」照樣過。
🔴 ④ 是這支最重要的一步（owner 2026-08-24 要求可以在專案頁補申請人/品項）。
   PUT /invoices/{id} 是**整包寫回** —— model_dump 逐欄 setattr，沒送的欄位會被
   schema 預設值蓋掉。只送要改的那一欄的話，品名/金額/抬頭/統編/專案連結會全部
   歸零，而畫面上只有品項那一格看起來變了，其餘要等對帳才會發現。同一族陷阱在
   這個 repo 咬過兩次，其中一次把 183 張、合計 10,656,093 的代開票方向翻成收款。
"""
import asyncio
import io
import sys
import threading
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"


from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["crm_projects", "crm_invoices", "crm_clients", "money_view"]})
TAG = "ZZBIND"
PROJ_NAME = TAG + " 綁定測試專案"
fails = []

def arun(coro):
    """在**獨立執行緒**跑 async —— playwright 的同步 API 佔著這條執行緒的
    event loop，直接 asyncio.run 會 RuntimeError（實測）。"""
    box = {}
    t = threading.Thread(target=lambda: box.update(v=asyncio.run(coro)))
    t.start()
    t.join()
    return box.get("v")

def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)

async def seed():
    from db.models import Client as CrmClient
    from db.models import CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        cli = CrmClient(id=uuid.uuid4().hex, short_name=TAG + "客戶",
                        full_name=TAG + "測試股份有限公司", tax_id="12345675")
        s.add(cli)
        await s.flush()
        proj = CrmProject(id=uuid.uuid4().hex, name=PROJ_NAME, status="製作",
                          client_id=cli.id, contract_amount=500000)
        s.add(proj)
        await s.commit()
        return proj.id

async def read_back():
    from sqlalchemy import select

    from db.models import CrmInvoice
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        inv = (await s.execute(select(CrmInvoice).where(
            CrmInvoice.title.like(TAG + "%")))).scalars().first()
        if not inv:
            return None
        return {"project_id": inv.project_id, "category": inv.category,
                "payment_type": inv.payment_type, "payment_status": inv.payment_status,
                "amount_total": inv.amount_total, "amount_ex_tax": inv.amount_ex_tax,
                "tax_amount": inv.tax_amount, "company_name": inv.company_name,
                "tax_id": inv.tax_id, "number": inv.invoice_number,
                "applicant": inv.applicant, "item_type": inv.item_type,
                "title": inv.title, "issue_status": inv.issue_status,
                "invoice_kind": inv.invoice_kind, "notes": inv.notes}

async def clean():
    from sqlalchemy import select

    from db.models import Client as CrmClient
    from db.models import CrmInvoice, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        for m, col in ((CrmInvoice, CrmInvoice.title),
                       (CrmProject, CrmProject.name),
                       (CrmClient, CrmClient.short_name)):
            for r in (await s.execute(select(m).where(col.like(TAG + "%")))).scalars().all():
                await s.delete(r)
        await s.commit()

asyncio.run(clean())
PID = asyncio.run(seed())

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
    pg.evaluate("(id) => window._projSelect && window._projSelect(id)", PID)
    pg.wait_for_timeout(2500)
    pg.locator("#proj-detail-tabs .crm-tab[data-tab='invoices']").click()
    pg.wait_for_timeout(3000)

    print("[1] 從專案頁開一張票（含稅 166,950）")
    pg.click("#proj-detail-invoices button:has-text('開發票')")
    pg.wait_for_timeout(1500)
    pg.fill("#proj-inv-title", TAG + " 第一期款")
    # 申請人與品項（owner 2026-08-24：「這裡要可以填申請人跟品項」）——
    # 申請人的選項只能來自伺服器那份清單，所以直接讀下拉裡的第二個選項來選
    APPLICANT = pg.eval_on_selector(
        "#proj-inv-applicant", "s => s.options.length > 1 ? s.options[1].value : ''")
    check(bool(APPLICANT), "開票視窗的申請人下拉有讀到伺服器清單", APPLICANT)
    if APPLICANT:
        pg.select_option("#proj-inv-applicant", APPLICANT)
    pg.fill("#proj-inv-item", "影片製作")
    pg.fill("#proj-inv-amount", "166950")
    pg.fill("#proj-inv-number", TAG[:2] + "88880001")
    pg.dispatch_event("#proj-inv-amount", "input")
    pg.wait_for_timeout(400)
    pg.click("#proj-inv-modal button:has-text('開立')")
    pg.wait_for_timeout(3500)
    check(not pg.locator("#proj-inv-modal").is_visible(), "視窗關了（存成功）")

    print("")
    print("[2] 🔴 直接讀 DB —— 存成什麼")
    row = arun(read_back())
    check(row is not None, "發票真的建起來了")
    if row:
        print("     ", row)
        check(row["project_id"] == PID, "🔴 project_id 綁到這個專案")
        check(row["category"] == "專案", "category=專案")
        check(row["payment_type"] == "收款", "方向=收款")
        check(row["payment_status"] in ("未收款", "應收款"), "款項狀態由後端定案",
              str(row["payment_status"]))
        check(row["amount_total"] == 166950, "含稅 166,950", str(row["amount_total"]))
        check(row["amount_ex_tax"] == 159000, "未稅 159,000", str(row["amount_ex_tax"]))
        check(row["tax_amount"] == 7950, "稅額 7,950", str(row["tax_amount"]))
        check(row["company_name"].endswith("股份有限公司"), "抬頭是客戶全名",
              str(row["company_name"]))
        check(row["tax_id"] == "12345675", "統編", str(row["tax_id"]))
        check(row["applicant"] == APPLICANT, "申請人存進去了", str(row["applicant"]))
        check(row["item_type"] == "影片製作", "品項存進去了", str(row["item_type"]))

    print("")
    print("[3] 專案頁的清單與摘要跟著更新")
    txt = pg.locator("#proj-detail-invoices").inner_text()
    check(TAG + " 第一期款" in txt, "新的那張出現在清單裡")
    check("166,950" in txt, "已開發票 166,950")
    check("333,050" in txt, "還能開 333,050（500,000 − 166,950）", txt[:200])

    print("")
    print("[4] 🔴 切到帳務→發票，那張票在發票本裡而且掛著專案")
    pg.evaluate("window.switchTab('tab_crm_invoices')")
    pg.wait_for_timeout(3000)
    pg.locator("[data-inv-view='invoices']").click()
    pg.wait_for_timeout(3000)
    pg.fill("#inv-search", TAG)
    pg.wait_for_timeout(3000)
    book = pg.locator("#inv-list-body").inner_text()
    check(TAG + " 第一期款" in book, "發票本找得到它", book[:120])
    check(PROJ_NAME in book, "🔴 發票本的清單上看得到關聯專案", book[:200])

    print("")
    print("[5] 發票本可以只看這個案子的票")
    pg.fill("#inv-search", "")
    pg.wait_for_timeout(2500)
    # 🔴 這顆下拉被 select-upgrade 換成可搜尋元件，原生 select 是隱藏的 ——
    #    select_option 會等到逾時。設值＋派 change 事件測的是同一條線路。
    pg.evaluate("""(id) => {
        const s = document.getElementById('inv-filter-project');
        s.value = id; s.dispatchEvent(new Event('change', { bubbles: true }));
    }""", PID)
    pg.wait_for_timeout(3000)
    # 快速新增有兩列（電子＋紙本，class 帶 inv-qa）—— 只數真的發票列
    only = pg.locator("#inv-list-body .crm-row:not(.inv-qa):not(.inv-qa-paper)").count()
    txt2 = pg.locator("#inv-list-body").inner_text()
    check(TAG + " 第一期款" in txt2, "篩選後還在", txt2[:80])
    check(only == 1, "清單只剩這個案子的那一張", str(only))
    pg.evaluate("""() => {
        const s = document.getElementById('inv-filter-project');
        s.value = ''; s.dispatchEvent(new Event('change', { bubbles: true }));
    }""")
    pg.wait_for_timeout(2500)
    print("")
    print("[6] 🔴 回專案頁就地改品項 —— 只有那一欄變，其他欄位一個都不能被洗掉")
    # PUT /invoices/{id} 是整包寫回（model_dump 逐欄 setattr）。只送要改的那一欄
    # 的話，品名/金額/抬頭/統編/專案連結會全部變成 schema 預設值 —— 而畫面上
    # 只有品項那一格看起來變了，其他要等對帳才會發現。這一步就是在證明沒有。
    pg.evaluate("window.switchTab('tab_crm_projects')")
    pg.wait_for_timeout(3000)
    pg.evaluate("(id) => window._projSelect && window._projSelect(id)", PID)
    pg.wait_for_timeout(2500)
    pg.locator("#proj-detail-tabs .crm-tab[data-tab='invoices']").click()
    pg.wait_for_timeout(3000)
    cell = pg.locator("#proj-detail-invoices [data-inv-meta='item_type']").first
    check(cell.input_value() == "影片製作", "品項那一格顯示的是存進去的值",
          cell.input_value())
    sel = pg.locator("#proj-detail-invoices [data-inv-meta='applicant']").first
    check(sel.input_value() == APPLICANT, "申請人那一格選的是存進去的人",
          sel.input_value())
    cell.fill("展場攝影")
    cell.dispatch_event("change")
    pg.wait_for_timeout(3000)
    after = arun(read_back())
    check(after and after["item_type"] == "展場攝影", "品項改成功",
          str(after and after["item_type"]))
    if row and after:
        washed = [k for k, v in row.items() if k != "item_type" and after[k] != v]
        check(not washed, "🔴 其他欄位一個都沒被整包寫回洗掉",
              "被洗掉的：" + str({k: (row[k], after[k]) for k in washed}) if washed else "")

    print("")
    print("[7] 🔴 在專案頁刪票 —— 發票本那邊也要不見（同一張票，同一支端點）")
    pg.on("dialog", lambda d: d.accept())
    pg.locator("#proj-detail-invoices button:has-text('刪除')").first.click()
    pg.wait_for_timeout(3500)
    gone = arun(read_back())
    check(gone is None, "DB 裡真的沒了", str(gone))
    txt3 = pg.locator("#proj-detail-invoices").inner_text()
    check(TAG + " 第一期款" not in txt3, "專案頁清單上不見了")
    pg.evaluate("window.switchTab('tab_crm_invoices')")
    pg.wait_for_timeout(3000)
    pg.locator("[data-inv-view='invoices']").click()
    pg.wait_for_timeout(3000)
    pg.fill("#inv-search", TAG)
    pg.wait_for_timeout(3000)
    book2 = pg.locator("#inv-list-body").inner_text()
    check(TAG + " 第一期款" not in book2, "🔴 發票本那一頁也跟著不見了", book2[:150])

    check(not errs, "整輪都沒有 JS 例外", str(errs[:3]))
    b.close()

asyncio.run(clean())
print("")
print("[已清理]")
print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
