# -*- coding: utf-8 -*-
"""前端判定收斂第一步（2026-09-09，docs/RBAC_PLAN.md §7 第 3 項）。

Cloudflare 給 `.js` 四小時瀏覽器快取，所以分兩版：
  這一版：`crm-utils.js` 只**加** export（`isAdmin`／`canInvoice`／`INVOICE_NEED`），呼叫端一個都不改；
  下一版：等這支進了大家的快取，才把各分頁的 `_isAdmin`／`_canInvoice` 換成 named import 並刪掉本地副本。
這支測試釘住「第一步做了、第二步還沒做」的狀態；下一版做第二步時，把 test_no_caller_switched_yet 換成「本地副本已清零」。
"""
import re

from tests.unit._srcscan import repo_src

UTILS = repo_src("frontend/tabs/crm/crm-utils.js")


def test_exports_exist_and_mirror_the_backend_ruler():
    assert "export const isAdmin = () => (window._accessLevel || 0) >= 3;" in UTILS
    assert "export const canInvoice = () => hasModule('crm_invoices') && canSeeMoney();" in UTILS, \
        "母帳錢流的前端鏡射＝帳務＋金額檢視（後端 require_entity('parent','full')）"
    assert "export const INVOICE_NEED = '財務管理＋金額檢視';" in UTILS
    assert "4 小時" in UTILS.split("export const isAdmin")[0][-900:], "要寫明為什麼呼叫端這一版不換"


def test_no_caller_switched_yet():
    """第二步還沒做：沒有任何檔案 named import 這三個新 export（換了就會在舊快取那輪炸整頁）。"""
    import glob
    for f in glob.glob("frontend/tabs/**/*.js", recursive=True) + glob.glob("frontend/js/**/*.js", recursive=True):
        src = repo_src(f.replace("\\", "/"))
        for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*'[^']*crm-utils\.js'", src):
            named = {n.strip().split(" as ")[0] for n in m.group(1).split(",")}
            assert not (named & {"isAdmin", "canInvoice", "INVOICE_NEED"}), \
                f"{f} 太早改了：要等下一版（docs/RBAC_PLAN.md §7 第 3 項）"
