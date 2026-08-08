"""core/public_assets.py — NAS 對外容器要自己 serve 的前端檔（**單一清單**）。

master 關機時，這些頁仍要開得了：影像紀錄的收照連結、客戶手上的提案共編
連結。對外站的 `dist/` 是 Astro build 產物，沒有這些檔，所以由 website-api
容器自己 serve。

這份清單原本散在三個地方、且互不知道對方存在：
  1. `publish_update.NAS_SYNC_ASSETS`   要 scp 哪些檔過去
  2. `main_website.py`                   容器怎麼 serve 它們
  3. `docker/nginx/originsun.conf`       nginx 反代哪些路徑
少改一處就是「master 上正常、NAS 上靜默壞掉」，而那條路只有客戶會走到 ——
最不容易被自己人發現。所以三邊都從這裡取值，並由
`tests/unit/test_public_assets.py` 釘住：
  - 每個頁面的 ES module **相依閉包**都落在 MODULE_DIRS 裡
    （這條會擋掉「順手加一個 ../crm/… 的 import」那類改動）
  - nginx 對每個項目都有對應的 location
"""
from __future__ import annotations

import os
import re

# 對外 serve 的 HTML 頁（相對 frontend/）。網址就是 "/" + 檔名。
# reference.html：提案公開頁的「研究頁 ↗」連過去的那頁（同一組 ?t= token），
# 不放進來的話 master 關機時客戶點那個連結會 404。
PAGES = ("media-log.html", "proposal-plan.html", "reference.html")

# 上面那些頁 import 得到的模組目錄（相對 frontend/）。網址就是 "/" + 目錄名。
# ⚠️ 只開這幾個子目錄，不是整個 frontend/ —— 那底下是內部 SPA 的全部原始碼。
MODULE_DIRS = ("tabs/proposals", "js/shared")

# publish_update 要同步的路徑（相對 repo 根）。img/ 是頁面裡的 logo。
SYNC_PATHS = (["frontend/img"]
              + [f"frontend/{p}" for p in PAGES]
              + [f"frontend/{d}" for d in MODULE_DIRS])

# `from "x"` / `import("x")` / `importRetry("x")` 三種形式都要抓 —— 公開頁的元件
# 是動態載入的，而 importRetry（utils.js 的動態 import 包裝，新程式一律用它）
# 從語法上看只是個函式呼叫。漏掉它 = 那條相依對這份守衛完全隱形。
_IMPORT_RE = re.compile(
    r"""from\s+["']([^"']+)["']|\bimport(?:Retry)?\(\s*["']([^"']+)["']""")

# 豁免名單：**(誰 import 的, import 什麼)** 一組一組寫。
# 只放「動態 import + .catch()，載不到只是功能降級」的個案 ——
# 🔴 不可以只寫被 import 的路徑：那樣別的檔案**靜態** import 同一個模組
#    （會讓整頁載不起來）也會被一起放行，守衛就形同虛設。
# 加東西進來前先確認「NAS 上載不到時，使用者看得到的行為是什麼」。
SOFT_DEPS = frozenset({
    # prop-fetch 只為了 surfaceWarning 這個 toast 動態拉 crm-utils，
    # 失敗時 .catch 退成 console.warn（見該檔註解）
    ("tabs/proposals/prop-fetch.js", "tabs/crm/crm-utils.js"),
})


def _resolve(base_rel: str, spec: str) -> str:
    """import 字串 → 相對 frontend/ 的路徑（外部網址回 ""）。"""
    if spec.startswith(("http://", "https://", "//")):
        return ""
    if spec.startswith("/"):
        return spec.lstrip("/")
    joined = os.path.join(os.path.dirname(base_rel), spec)
    return os.path.normpath(joined).replace("\\", "/")


def iter_imports(frontend_dir: str, rel: str):
    """產出 `rel` 這個檔直接 import 的相對路徑。"""
    try:
        with open(os.path.join(frontend_dir, rel), encoding="utf-8") as fp:
            src = fp.read()
    except OSError:
        return
    for m in _IMPORT_RE.finditer(src):
        got = _resolve(rel, m.group(1) or m.group(2) or "")
        if got:
            yield got


def import_closure(frontend_dir: str, entry: str) -> set:
    """`entry` 這個頁面（遞迴）用到的所有 **(importer, dep)** 組合。

    回 pair 不是單純的 dep 集合 —— 豁免要能分辨「誰」import 的，見 SOFT_DEPS。
    """
    out, seen, stack = set(), set(), [entry]
    while stack:
        cur = stack.pop()
        for dep in iter_imports(frontend_dir, cur):
            out.add((cur, dep))
            if dep in seen:
                continue
            seen.add(dep)
            if os.path.isfile(os.path.join(frontend_dir, dep)):
                stack.append(dep)
    return out


def is_served(rel: str) -> bool:
    return any(rel.startswith(d + "/") for d in MODULE_DIRS)
