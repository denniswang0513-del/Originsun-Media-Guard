# -*- coding: utf-8 -*-
"""UI：傳錯張的警示要真的出現在畫面上、看得見、切換發票會消失。

後端算出來沒人看得到等於沒做 —— 這個 repo 咬過「按了沒反應」兩次。
自己建 ZZ 測試發票，跑完刪掉（連磁碟上的檔一起）。
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
TOK = create_token({"sub": "admin", "username": "admin",
                    "access_level": 3, "modules": []})
FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "einvoice_full.pdf")
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(m, path, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + "/api/v1" + path, data=d, method=m, headers={
        "Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mk(**kw):
    st, r = call("POST", "/crm/invoices",
                 {"title": "ZZ_ui " + uuid.uuid4().hex[:6],
                  "invoice_date": "2026-08-14", "payment_type": "收款",
                  "entity": "parent", **kw})
    assert st == 200, (st, r)
    return (r.get("invoice") or r)["id"]


# 一張「會對不上」的、一張乾淨的（用來驗切換時警示會消失）
bad_id = mk(company_name="國家表演藝術中心國家兩廳院", tax_id="00973926",
            amount_total=12345, invoice_number="DQ99999999")
other_id = mk(company_name="ZZ 另一張", amount_total=999)
made, files = [bad_id, other_id], []

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1700, "height": 1100})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{TOK}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_crm_invoices')")
        pg.wait_for_timeout(3000)
        # 🔴 財務殼預設不是發票子視圖 —— 不點這顆的話 #finance-invoices-wrap
        #    是 display:none，整個面板 size=0x0，警示條有節點但 is_visible() false。
        pg.locator('[data-inv-view="invoices"]').first.click()
        pg.wait_for_timeout(3000)

        print("[1] 打開那張發票")
        # 🔴 選取函式是 window._invSelect（selectInvoice 沒掛到 window）；
        #    而且 #inv-file-pane 是 HTML 裡的**靜態**元素 —— 拿它的 count 當
        #    「面板有內容」是空的斷言（永遠通過）。要等的是面板裡的檔案 input。
        pg.evaluate(f"window._invSelect('{bad_id}')")
        pg.wait_for_selector("#inv-file-input", state="attached", timeout=15000)
        check(pg.locator("#inv-file-input").count() > 0, "電子發票區已渲染")

        print("\n[2] 上傳一張別張發票的 PDF")
        # 檔案 input 的 id 是 inv-file-input（被 _invFilePick 用 .click() 觸發），
        # 直接對它 set_input_files；它是隱藏的，所以不能用一般的 click 流程。
        pg.set_input_files("#inv-file-input", FIXTURE)
        pg.wait_for_timeout(5000)
        check(not errs, "沒有 JS 例外", errs[:2])

        print("\n[3] 🔴 警示要真的看得見")
        warn = pg.locator(".inv-file-warn")
        check(warn.count() > 0, "警示條出現了", warn.count())
        check(warn.first.is_visible() if warn.count() else False,
              "🔴 看得見（不是有節點但沒樣式）")
        txt = warn.first.inner_text() if warn.count() else ""
        for word in ("傳錯張", "統編", "金額", "抬頭"):
            check(word in txt, f"寫了「{word}」")
        print("     畫面上的文字：")
        for ln in [x for x in txt.split("\n") if x.strip()]:
            print("       " + ln)

        print("\n[4] 檔案照樣存好（警示不是閘門）")
        st, g = call("GET", f"/crm/invoices/{bad_id}")
        inv = g.get("invoice") or g
        files.append(inv.get("file_url"))
        check(bool(inv.get("file_url")), "檔案關聯在", inv.get("file_name"))

        print("\n[5] 🔴 切到別張發票，警示要消失")
        pg.evaluate(f"window._invSelect('{other_id}')")
        pg.wait_for_timeout(2500)
        left = pg.locator(".inv-file-warn")
        check(left.count() == 0 or not left.first.is_visible(),
              "A 的警示沒有掛在 B 上", left.count())
        b.close()
finally:
    print("\n[清理]")
    for i in made:
        call("DELETE", f"/crm/invoices/{i}")
    for f in files:
        if f and os.path.isfile(f):
            try:
                os.remove(f)
            except OSError:
                pass
    check(all(call("GET", f"/crm/invoices/{i}")[0] == 404 for i in made),
          "測試發票已刪")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
