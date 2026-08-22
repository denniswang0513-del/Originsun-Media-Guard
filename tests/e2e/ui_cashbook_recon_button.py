# -*- coding: utf-8 -*-
"""收支明細的「對帳系統」按鈕：真的按下去要開得起來（owner 2026-08-22）。

owner：「對帳系統我希望移到這裡，一個按鈕就可以開啟」

刻意**不複製**一份對帳系統到收支明細 —— 分類規則有兩個畫面各講各的會很致命。
按鈕直接點側欄的「銀行帳戶」子視圖，走既有的 lazy-load。所以這支要證明的是：
按下去之後，對帳系統的四塊東西真的都在畫面上。
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

    print("[1] 按鈕在收支明細的工具列上")
    # 收支明細是 lazy-load（fetch html + import js），固定 sleep 會撞到還沒載完
    pg.wait_for_selector("#cash-btn-recon", timeout=20000)
    btn = pg.locator("#cash-btn-recon")
    check(btn.count() == 1, "按鈕在", btn.count())
    check(btn.is_visible(), "看得見")
    check("對帳系統" in btn.inner_text(), "文字對", btn.inner_text())

    print("")
    print("[2] 按下去 → 對帳系統開起來")
    btn.click()
    pg.wait_for_timeout(5000)
    check(not errs, "沒有 JS 例外", errs[:2])
    txt = pg.inner_text("body")
    for must in ("對帳系統", "上傳對帳單", "分類規則", "開啟對帳工作台", "核對餘額"):
        check(must in txt, f"「{must}」在畫面上")

    print("")
    print("[3] 側欄的 active 狀態也跟著切過去（不是只有內容變）")
    act = pg.evaluate("""() => {
        const b = document.querySelector('.finance-nav-btn.active');
        return b ? (b.dataset.subview || b.dataset.invView || '') : '';
    }""")
    check(act == "banking", "active 是 banking", act)

    print("")
    print("[4] 分類規則點得開，而且看得到新的『方向』欄")
    pg.click("button:has-text('分類規則')")
    pg.wait_for_timeout(2500)
    t2 = pg.inner_text("body")
    check("關鍵字" in t2 and "方向" in t2, "規則視窗有方向欄")
    check("rule-dir" in pg.content(), "新增表單有方向下拉")
    check(not errs, "整輪都沒有 JS 例外", errs[:3])
    b.close()

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
