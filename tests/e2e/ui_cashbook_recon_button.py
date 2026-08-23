# -*- coding: utf-8 -*-
"""收支明細的「對帳／匯入」按鈕：真的按下去、真的在這一頁展開（owner 2026-08-22）。

owner：「這兩個按鈕我希望整合在一起，對帳系統按了之後就直接在收支表這裡對帳，
不要像現在跳轉至銀行帳戶；銀行帳戶的這個功能直接移到收支表就可以了。」

所以這支要證明三件事：
  1. 工具列只剩**一顆**入口（匯入 CSV 收進面板裡了）
  2. 按下去是**就地展開**——收支清單讓位、面板出現，而不是跳到別的子視圖
  3. 對帳系統四塊（上傳對帳單／分類規則／工作台／核對餘額）都在面板裡

🔴 第 2 點是這一版的重點斷言：只驗「四塊字在畫面上」的話，舊版（跳到銀行
   帳戶）也會過 —— 那等於只測了伺服器活著。
"""
import sys

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
if ":8000" in BASE:
    sys.path.insert(0, r"C:\OriginsunAgent")
T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
fails = []


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
    pg.wait_for_timeout(4000)
    pg.evaluate("window.switchTab('tab_crm_invoices')")
    pg.wait_for_timeout(3000)
    pg.locator("[data-inv-view='cashbook']").click()
    pg.wait_for_timeout(5000)

    print("[1] 工具列只剩一顆入口")
    # 收支明細是 lazy-load（fetch html + import js），固定 sleep 會撞到還沒載完
    pg.wait_for_selector("#cash-btn-recon", timeout=20000)
    btn = pg.locator("#cash-btn-recon")
    check(btn.count() == 1, "按鈕在", btn.count())
    check(btn.is_visible(), "看得見")
    check("對帳" in btn.inner_text() and "匯入" in btn.inner_text(),
          "文字是對帳／匯入", btn.inner_text())
    # 匯入 CSV 這時候應該在（藏著的）面板裡，不在工具列上
    check(not pg.locator("#cash-btn-import").is_visible(), "匯入 CSV 還沒露出來")
    check(not shown(pg, "#cash-recon-panel"), "面板預設收著")

    print("")
    print("[2] 🔴 按下去是就地展開，不是跳頁")
    btn.click()
    pg.wait_for_timeout(6000)
    check(not errs, "沒有 JS 例外", errs[:2])
    check(shown(pg, "#cash-recon-panel"), "面板展開了")
    check(not shown(pg, "#cash-list-panel"), "收支清單讓位")
    mount = pg.inner_text("#cash-recon-mount")
    for must in ("對帳系統", "上傳對帳單", "分類規則", "開啟對帳工作台", "核對餘額"):
        check(must in mount, f"「{must}」在面板裡")

    print("")
    print("[3] 匯入 CSV 就在同一個面板裡，而且打得開")
    check(pg.locator("#cash-btn-import").is_visible(), "匯入 CSV 露出來了")
    pg.click("#cash-btn-import")
    pg.wait_for_timeout(1200)
    check(shown(pg, "#cash-import-modal"), "CSV 匯入視窗開得起來")
    pg.evaluate("() => { const m = document.getElementById('cash-import-modal');"
                " if (m) m.style.display = 'none'; }")

    print("")
    print("[4] 分類規則點得開，而且看得到方向欄")
    pg.click("#cash-recon-mount button:has-text('分類規則')")
    pg.wait_for_timeout(2500)
    t2 = pg.inner_text("body")
    check("關鍵字" in t2 and "方向" in t2, "規則視窗有方向欄")
    check("rule-dir" in pg.content(), "新增表單有方向下拉")

    print("")
    print("[4b] 挑發票視窗的匯費欄有掛上（owner 2026-08-23）")
    # 開不到那個視窗（要先傳一份對帳單），但 handler 掛不掛得上是整支模組有沒有
    # 順利跑完的證據 —— 語法錯 / 名字打錯的話這裡就是 undefined。
    for fn in ("pickInvAmt", "pickInvFee", "pickInvToggle", "pickInvTake"):
        check(pg.evaluate(f"() => typeof (window._finRecon || {{}}).{fn}") == "function",
              f"window._finRecon.{fn} 掛上了")

    print("")
    print("[5] 回得去收支明細")
    pg.evaluate("() => { const m = document.getElementById('finbank-wb-modal');"
                " if (m) m.style.display = 'none'; }")
    pg.click("#cash-recon-back")
    pg.wait_for_timeout(3000)
    check(not shown(pg, "#cash-recon-panel"), "面板收起來了")
    check(shown(pg, "#cash-list-panel"), "收支清單回來了")
    check(pg.locator("#cash-list-body .crm-row").count() > 0, "清單有列",
          pg.locator("#cash-list-body .crm-row").count())
    check(not errs, "整輪都沒有 JS 例外", errs[:3])
    b.close()

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
