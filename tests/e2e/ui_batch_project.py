# -*- coding: utf-8 -*-
"""請款單的「批次掛專案」在真瀏覽器裡真的能用（owner 2026-08-23）。

要證明的是**互動**，不是「字串在畫面上」：
  1. 平常點列＝開詳情；開了批次模式＝點列變成選取
  2. Shift 點第二列＝把中間整段一起選起來（而且是加選，不是 toggle）
  3. 操作列會算出「已選幾張 / 合計多少」
  4. 「只看未掛專案」真的縮小清單
  5. 「建議：」欄與「選同建議」（owner 要的是確認，不是自動套用）
  6. 離開批次模式回到原本的行為

不實際寫入 —— 掛專案那條路由已經有 api_batch_project.py 打真的端點驗過。
種一組保證配得到的資料（案名與摘要共用一段字），否則 dev 庫的命中是浮動的。
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
if ":8000" in BASE:
    sys.path.insert(0, r"C:\OriginsunAgent")


from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
TAG = "ZZSUG"
SUGG_NAME = TAG + "太赫茲年度大會紀錄"
fails = []

async def seed():
    """一個案子 ＋ 三張摘要帶「ZZSUG太赫茲」的請款單 —— 建議必中。"""
    from db.models import CrmPaymentRequest, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        s.add(CrmProject(id=uuid.uuid4().hex, name=SUGG_NAME, status="製作"))
        for i in range(3):
            s.add(CrmPaymentRequest(
                id=uuid.uuid4().hex, entity="parent",
                request_date=datetime(2026, 8, 20), amount=10000 + i,
                summary=TAG + "太赫茲", category="專案外包",
                payee_name=TAG + "廠商", payment_status="應付款"))
        await s.commit()

async def clean():
    from sqlalchemy import select

    from db.models import CrmPaymentRequest, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        for m, col in ((CrmPaymentRequest, CrmPaymentRequest.summary),
                       (CrmProject, CrmProject.name)):
            for r in (await s.execute(select(m).where(col.like(TAG + "%")))).scalars().all():
                await s.delete(r)
        await s.commit()

asyncio.run(clean())
asyncio.run(seed())

def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)

def shown(pg, sel):
    return pg.evaluate(
        "(s) => { const e = document.querySelector(s);"
        " return !!e && getComputedStyle(e).display !== 'none'; }", sel)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 1200})
    ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(6000)
    pg.evaluate("window.switchTab('tab_crm_invoices')")
    pg.wait_for_timeout(3000)
    # 分頁切換走財務側欄（tab 重整之後 #inv-view-* 那組按鈕不可見）
    pg.locator("[data-inv-view='payments']").click()
    pg.wait_for_selector("#pay-btn-batch", timeout=25000)
    pg.wait_for_timeout(3000)

    rows = "#pay-list-body .pay-row"
    n0 = pg.locator(rows).count()
    print(f"[1] 請款清單載入（{n0} 列）")
    check(n0 > 0, "有列")
    check(not shown(pg, "#pay-batch-bar"), "操作列預設收起來")

    print("")
    print("[2] 開啟批次模式 → 點列變成選取（不是開詳情）")
    pg.click("#pay-btn-batch")
    pg.wait_for_timeout(800)
    check(shown(pg, "#pay-batch-bar"), "操作列出現")
    pg.locator(rows).nth(1).click()
    pg.wait_for_timeout(500)
    check(not shown(pg, "#pay-detail-panel"), "詳情面板沒被打開（批次模式下點列＝選取）")
    txt = pg.locator("#pay-batch-count").inner_text()
    check("已選 1 張" in txt, "算出已選 1 張", txt)

    print("")
    print("[3] 🔴 Shift 點第 5 列 → 中間整段一起選起來")
    pg.locator(rows).nth(4).click(modifiers=["Shift"])
    pg.wait_for_timeout(500)
    txt = pg.locator("#pay-batch-count").inner_text()
    check("已選 4 張" in txt, "第 2~5 列共 4 張", txt)
    check("合計 $" in txt, "有算金額合計", txt)
    picked = pg.evaluate(
        "() => [...document.querySelectorAll('#pay-list-body .pay-row')]"
        ".filter(r => r.style.background).length")
    check(picked == 4, "畫面上 4 列被標起來", str(picked))

    print("")
    print("[4] 清除選取 / 全選")
    pg.click("#pay-batch-none")
    pg.wait_for_timeout(400)
    check("點列選取" in pg.locator("#pay-batch-count").inner_text(), "清乾淨了")
    pg.click("#pay-batch-all")
    pg.wait_for_timeout(600)
    check(f"已選 {n0} 張" in pg.locator("#pay-batch-count").inner_text(),
          f"全選 {n0} 張", pg.locator("#pay-batch-count").inner_text())
    pg.click("#pay-batch-none")
    pg.wait_for_timeout(400)

    print("")
    print("[5] 沒選專案就按「掛上去」→ 要出聲，不能靜默")
    pg.locator(rows).nth(0).click()
    pg.wait_for_timeout(300)
    pg.click("#pay-batch-apply")
    pg.wait_for_timeout(1200)
    toast = pg.evaluate(
        "() => document.body.innerText.includes('先選一個專案')")
    check(toast, "提示「先選一個專案」出現了（crmToast 真的接上）")

    print("")
    print("[5b] 🔴 建議欄：機器排候選，人確認（不是自動套用）")
    pg.check("#pay-filter-unassigned")
    pg.wait_for_timeout(3500)
    pg.fill("#pay-search", TAG + "太赫茲")
    pg.wait_for_timeout(3000)
    body = pg.locator("#pay-list-body").inner_text()
    check("建議：" in body, "有標示成「建議：」", body[:80])
    check(SUGG_NAME in body, "建議的是種好的那個案子", body[:120])
    n_sugg = pg.locator(rows).count()
    check(n_sugg == 3, "三張都給了建議", str(n_sugg))

    print("")
    print("[5c] 「選同建議」把同一個建議的整組選起來")
    pg.locator(rows).nth(0).click()
    pg.wait_for_timeout(400)
    pg.click("#pay-batch-samesugg")
    pg.wait_for_timeout(700)
    txt = pg.locator("#pay-batch-count").inner_text()
    check("已選 3 張" in txt, "三張一起選起來了", txt)
    picked = pg.evaluate("() => document.getElementById('pay-batch-project').value")
    check(bool(picked), "專案下拉也帶好了（仍要人自己按「掛上去」）", str(bool(picked)))
    pg.fill("#pay-search", "")
    pg.wait_for_timeout(2500)
    pg.click("#pay-batch-none")
    pg.uncheck("#pay-filter-unassigned")
    pg.wait_for_timeout(2500)

    print("")
    print("[6] 只看未掛專案")
    pg.click("#pay-batch-exit")
    pg.wait_for_timeout(500)
    pg.check("#pay-filter-unassigned")
    pg.wait_for_timeout(3000)
    n1 = pg.locator(rows).count()
    check(n1 <= n0, f"清單縮小或持平（{n0} → {n1}）")
    check(n1 > 0, "還有東西可掛（不然這功能就沒必要）", str(n1))

    print("")
    print("[7] 離開批次模式後點列回到開詳情")
    pg.uncheck("#pay-filter-unassigned")
    pg.wait_for_timeout(2500)
    check(not shown(pg, "#pay-batch-bar"), "操作列收起來了")
    pg.locator(rows).nth(0).click()
    pg.wait_for_timeout(1200)
    check(shown(pg, "#pay-detail-panel"), "詳情面板打開了")
    check(not errs, "整輪都沒有 JS 例外", str(errs[:3]))
    b.close()

asyncio.run(clean())
print("")
print("[已清理種進去的測試資料]")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
