# -*- coding: utf-8 -*-
"""/simplify 第 4 輪回歸修正的瀏覽器驗收（/my-ledger.html）。

三個修正，三個真的按下去的斷言：
  1. 逐案清單的搜尋不准跨欄 —— 舊版把 `name + ' ' + client` 併起來比，
     「專案名結尾的字 + 客戶名開頭的字」會命中一個兩邊都不存在的詞。
  2. 改了結案日，那一列要**跟著搬**（清單是照結案日排的）—— 舊版只重畫不重排，
     那列停在舊位置直到重進子視圖。
  3. 發票殼是 lazy-load，載入期間不准是一片空白。

🔴 第 2 點會真的寫一次資料庫：先記下原值、驗完立刻改回去，最後再讀一次確認
   還原成功（沒還原成功就當作 FAIL）。
"""
import sys

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
PID = "c5271c8acabf476c9846bcc63c18a4f5"      # 2026 思沙龍 EP02 / 龍應台文化基金會
STRADDLE = "2 龍"                              # 兩個欄位各自都不含，併起來才含
T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["finance_mine", "money_view"]})
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def names(pg):
    """清單每列的專案名（第 2 欄）。"""
    return pg.eval_on_selector_all(
        "#fpl-list-body .crm-row",
        "els => els.map(e => e.children[1].textContent.trim())")


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1700, "height": 1100})
    # /my-ledger.html 刻意沒有 token 續用路徑（只能真的打帳密登入），所以從主
    # 系統走同一支子視圖 —— projects.js 是同一份，帳本用 window._finEntity 釘。
    ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}');"
                        " window._finEntity = 'mine';")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    pg.evaluate("window.switchTab('tab_crm_invoices')")
    pg.wait_for_selector("#finance-nav [data-subview='projects']", timeout=25000)

    print("[0] 進逐案損益")
    pg.locator("#finance-nav [data-subview='projects']").click()
    pg.wait_for_selector("#fpl-list-body .crm-row", timeout=25000)
    all_names = names(pg)
    check(len(all_names) > 300, "清單載入", len(all_names))

    print("[1] 搜尋不跨欄")
    box = pg.locator("#fpl-q")
    box.fill(STRADDLE)
    pg.wait_for_timeout(700)
    hit = names(pg)
    check(not any("思沙龍 EP02" in n for n in hit),
          f"「{STRADDLE}」不該撈到 2026 思沙龍 EP02", f"{len(hit)} 列：{hit[:3]}")
    box.fill("")
    pg.wait_for_timeout(700)
    check(len(names(pg)) == len(all_names), "清空搜尋回到全部")

    print("[2] 改結案日 → 那一列要搬家")
    pg.locator(f"#fpl-list-body .crm-row[data-id='{PID}']").click()
    pg.wait_for_selector("#fpl-close", timeout=15000)
    orig = pg.input_value("#fpl-close")
    check(bool(orig), "取得原值", orig)
    before = names(pg).index("2026 思沙龍 EP02")
    pg.fill("#fpl-close", "2020-01-01")
    pg.evaluate("window._finProjLedger.save(document.createElement('button'))")
    pg.wait_for_timeout(2500)
    after = names(pg).index("2026 思沙龍 EP02")
    check(after > before, "改成很舊的日期後往下移", f"{before} → {after}")

    print("[2b] 還原，並驗「就地排序 == 重新載入的順序」")
    pg.fill("#fpl-close", orig)
    pg.evaluate("window._finProjLedger.save(document.createElement('button'))")
    pg.wait_for_timeout(2500)
    check(pg.input_value("#fpl-close") == orig, "結案日還原", orig)
    inplace = names(pg)
    # 🔴 這才是真正的不變量：前端就地重排的結果，必須逐列等於現在重新載入
    # 會看到的順序。只驗「回到原本那個 index」是錯的 —— 存檔本來就會動到
    # updated_at（後端排序的次要鍵），原位不見得還是原位。
    pg.locator("#finance-nav [data-subview='dashboard']").click()
    pg.wait_for_timeout(1500)
    pg.locator("#finance-nav [data-subview='projects']").click()
    pg.wait_for_selector("#fpl-list-body .crm-row", timeout=25000)
    fresh = names(pg)
    check(inplace == fresh, "就地排序與重新載入逐列相同",
          next((f"第 {i} 列 {a!r} != {b!r}"
                for i, (a, b) in enumerate(zip(inplace, fresh)) if a != b), "—"))
    check(fresh.index("2026 思沙龍 EP02") < 60, "還原後回到 2026-07 那一段",
          fresh.index("2026 思沙龍 EP02"))

    print("[3] 發票殼載入期間不是空白")
    pg.locator("[data-inv-view='invoices']").first.click()
    pg.wait_for_timeout(120)
    html = pg.eval_on_selector("#finance-invoices-wrap", "e => e.innerHTML.trim()")
    check(len(html) > 0, "點下去馬上就有東西（loading 或內容）", f"{len(html)} 字元")

    check(not errs, "沒有 JS 例外", errs[:2])
    b.close()

print("\n" + ("FAILED: " + "; ".join(fails) if fails else "ALL PASS"))
sys.exit(1 if fails else 0)
