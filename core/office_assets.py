"""core/office_assets.py — office-api 容器要自己 serve 的前端檔（**單一清單**）。

跟 `core/public_assets.py` 同一個角色，服務對象不同：

    public_assets   對外站（www）上**匿名**可達的頁 —— website-api 容器
    office_assets   內部同仁登入後用的輕頁面 —— office-api 容器

為什麼開第二份而不是加進 public_assets：那份清單的意義是「這真的該給沒有帳號的人
看嗎」，每加一項都強迫想一次。內部面不是那個問題，混在一起會把那個提問稀釋掉。

**這裡只放「不吃 SPA 殼」的頁**（`frontend/index.html` ＋ `app.js` ＋ 30 個分頁那一套
留在 master）。手機殼與 `my.html` 本來就是獨立入口，搬過來是乾淨的切面；把整個 SPA
複製第二份則是兩邊必漂的開始。理由寫在 docs/OFFLINE_MASTER_PLAN.md §4.3。

清單一樣要三處對齊（publish 同步、main_office serve、nginx location），
由 `tests/unit/test_office_surface.py` 釘住 —— 包括「每支被 serve 的 js，它 import 的
東西也要被 serve」（漏一個的症狀是那頁在 NAS 上白畫面，而 master 上一切正常）。
"""
from __future__ import annotations

# 對外 serve 的 HTML 頁（相對 frontend/）。網址就是 "/" + 檔名。
#   my.html      員工工作台（今天與這週、工時、請假、週記）
#   journal.html 週記／上週回顧 —— my.html 用 <iframe src="/journal.html?embed=1"> 整頁內嵌它
#                （owner 2026-07-24「登入後就要看到完整日誌」；正本只有一份，不做鏡像）
#   invoice.html 只是一行轉址到 /m/crm.html#invoice —— 舊書籤與桌面捷徑還指著它
PAGES = ("my.html", "journal.html", "invoice.html")

# 這些頁用得到的模組目錄（相對 frontend/）。網址就是 "/" + 目錄名。
# ⚠️ 只開這幾個，不是整個 frontend/ —— 那底下是內部 SPA 的全部原始碼。
#   m           手機殼＋10 個 view（crm.html 本身也在這裡面，所以不必進 PAGES）
#   js/my       my.html 的七支傳統 script（**不是** ES module，import 掃描看不到它們，
#               所以靠目錄整個開；順序敏感的載入契約見 CLAUDE.md）
#   js/shared   兩邊共用的純函式（報價金額／patch／等待字／刪除確認／工時表…）
#   tabs/petty  手機零用金宿主 import 的 petty-view.js（住在頁籤資料夾裡的共用庫，
#               白名單在 test_tab_import_boundary）
#   img         logo（兩頁都用）
MODULE_DIRS = ("m", "js/my", "js/shared", "tabs/petty", "img")

# 🔴 住在頁籤資料夾裡、但上面那些頁**靜態 import 得到**的共用庫。逐檔列，不是整個目錄開下去：
#   `tabs/crm/` 底下是整套桌機 CRM 的原始碼，`tabs/website/` 是官網後台的 —— 為了兩支共用庫
#   把那兩個目錄整個 serve 出去，等於把內部 SPA 複製一份到對外那台。
#
#   js/shared/ts-projects.js   → tabs/crm/crm-utils.js       （createSortable／sortableTh）
#   js/shared/journal-core.js  → tabs/website/website-utils.js → tabs/crm/crm-utils.js
#   js/shared/paste-image.js   → tabs/website/website-utils.js
#
# 這兩支不補的症狀：master 上一切正常，**只有走 NAS 的人**在「我的一週」與週記那兩塊拿到
# 白畫面（module 載入失敗會整支停掉，不是少一個功能而已）。實測踩到過。
# 白名單與「為什麼不搬進 js/shared/」記在 tests/unit/test_tab_import_boundary.py。
MODULE_FILES = ("tabs/crm/crm-utils.js", "tabs/website/website-utils.js")

# publish 要同步到 NAS 的路徑（相對 repo 根）
SYNC_PATHS = ([f"frontend/{p}" for p in PAGES]
              + [f"frontend/{d}" for d in MODULE_DIRS]
              + [f"frontend/{f}" for f in MODULE_FILES])


def is_served(rel: str) -> bool:
    """`rel`（相對 frontend/）會被 office-api serve 嗎。"""
    return (rel in PAGES or rel in MODULE_FILES
            or any(rel.startswith(d + "/") for d in MODULE_DIRS))
