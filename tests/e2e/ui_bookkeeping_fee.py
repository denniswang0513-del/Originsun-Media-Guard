# -*- coding: utf-8 -*-
"""記帳費設定卡片真的畫得出來、按得動（owner 2026-08-24 的「按鈕」）。

api_bookkeeping_fee.py 證明了資料那條路；這支證明**畫面上真的有那顆按鈕**：
  ① 卡片在「科目與設定」裡
  ② 顯示目前費率 + 接下來幾期各收多少（2026-09 要是 5,000）
  ③ 存得下去、存完畫面跟著更新
  ④ 舊費率留在「過去的費率」區，沒有被蓋掉

🔴 這支會改 settings.json（不是資料庫）—— 自己備份／還原，而且**不能走
   save_settings**（那是 merge-on-save，刪不掉 key）。
"""
import copy
import io
import json
import os
import sys

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "finance"]})
SETTINGS = os.path.join(r"E:\Dev\Originsun-Media-Guard", "settings.json")
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def read_key():
    d = json.load(io.open(SETTINGS, encoding="utf-8"))
    return copy.deepcopy((d.get("finance") or {}).get("bookkeeping_fee"))


def restore(orig):
    d = json.load(io.open(SETTINGS, encoding="utf-8"))
    fin = d.get("finance") or {}
    if orig is None:
        fin.pop("bookkeeping_fee", None)
    else:
        fin["bookkeeping_fee"] = orig
    d["finance"] = fin
    io.open(SETTINGS, "w", encoding="utf-8").write(
        json.dumps(d, ensure_ascii=False, indent=2))


ORIG = read_key()
print(f"[0] 備份原設定：{'（未設定過）' if ORIG is None else f'{len(ORIG)} 筆'}")

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1600, "height": 1200})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        # 🔴 財務 tab 的 id 是 `crm_invoices`（tab-config 第 57 行：那個 key 載的
        #    就是 finance.html）—— 沒有 `tab_finance` 這個東西
        pg.evaluate("window.switchTab('tab_crm_invoices')")
        pg.wait_for_timeout(4000)
        # 🔴 要收窄到 finance 的導覽鈕 —— 官網 tab 也有一個 data-subview="settings"
        pg.locator("button.finance-nav-btn[data-subview='settings']").click()
        pg.wait_for_selector("#finset-fee-card", timeout=25000)

        print("")
        print("[1] 卡片在，而且講得出目前費率")
        txt = pg.locator("#finset-fee-card").inner_text()
        check("記帳費" in txt, "標題是記帳費")
        check("$2,000" in txt, "顯示目前月費 2,000", txt[:60].replace("\n", " "))
        check("13 個月" in txt or "14 個月" in txt, "顯示一年計幾個月")

        print("")
        print("[2] 接下來幾期各收多少 —— 2026-09 要是 5,000")
        check("2026-09" in txt, "列出了 2026-09 那期")
        check("$5,000" in txt, "2026-09 顯示 5,000（owner 特別交代的那一期）")
        check("$10,000" in txt, "5 月那期是 10,000（多收的月份併在那期）")

        print("")
        print("[3] 三個輸入格 + 儲存鈕都在")
        for sel, what in (("#finset-fee-from", "生效年月"),
                          ("#finset-fee-monthly", "月費"),
                          ("#finset-fee-months", "一年幾個月")):
            check(pg.locator(sel).count() == 1, f"有「{what}」欄")
        check("儲存" in txt, "有儲存鈕")

        print("")
        print("[4] 真的按下去存 —— 換會計就是按這顆")
        pg.fill("#finset-fee-from", "2027-03")
        pg.fill("#finset-fee-monthly", "3200")
        pg.fill("#finset-fee-months", "13")
        pg.evaluate("""() => {
            const b = [...document.querySelectorAll('#finset-fee-card button')]
                .find(x => x.innerText.includes('儲存'));
            if (b) b.click();
        }""")
        pg.wait_for_timeout(3500)
        txt2 = pg.locator("#finset-fee-card").inner_text()
        check("$3,200" in txt2, "畫面更新成新月費", txt2[:70].replace("\n", " "))
        check("$6,400" in txt2, "2027-03 起那幾期變成 6,400（3,200×2）")

        print("")
        print("[5] 🔴 舊費率沒有被蓋掉（歷史期別要用當時的算）")
        check("過去的費率" in txt2, "有「過去的費率」區")
        check("$2,000" in txt2, "一開始那筆還在")
        check("$2,500" in txt2, "2026-09 那筆還在")
        check(not errs, "整輪都沒有 JS 例外", errs[:3])
        b.close()

finally:
    print("")
    print("[清理] 還原設定")
    restore(ORIG)
    now = read_key()
    check(now == ORIG, "settings.json 跟跑之前一模一樣",
          "未設定" if now is None else str(len(now)))

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
