"""routers/crm — CRM API 拆分包（原 routers/api_crm.py 單檔 144 端點）。

各領域模組 import 時即透過 decorator 把 route 註冊到 _shared.router。
⚠️ import 順序 = 原檔 section 出現順序 = FastAPI 路由註冊順序（先註冊先贏），
不可重排 —— 例如 /projects/closing 必須先於 /projects/{project_id} 註冊。
"""
from . import clients    # noqa: F401  客戶管理 + CSV + users
from . import projects   # noqa: F401  專案管理 + 結案看板 + CSV
from . import project_links  # noqa: F401  母帳↔私帳：換帳本／推送／對應表（依賴 projects，須在其後）
from . import quotes     # noqa: F401  報價 + 報價範本
from . import staff      # noqa: F401  人力 + 履歷 + 派工
from . import costs      # noqa: F401  雜支 + 收據 + 成本估算 + 子表
from . import finance    # noqa: F401  共用 helper + 發票（cash/payments/taxonomy 都 import 它）
from . import cash       # noqa: F401  收支明細 + 應付/應收 + 收款↔發票分配
from . import cash_splits  # noqa: F401  收支拆項（帳目一筆、內容拆裂；依賴 cash/finance）
from . import payments   # noqa: F401  請款單 + 預支款 + 批次付款
from . import taxonomy   # noqa: F401  私帳收支分類樹（/cash-taxonomy）
from . import work_stages  # noqa: F401  工作階段（/work-stages；路徑獨立，順序無關）
from . import invoice_files  # noqa: F401  電子發票檔（上傳/下載/分享連結）＋發票根目錄設定
from . import showcase   # noqa: F401  Showcase + token 編輯 + Site API
from . import works      # noqa: F401  1:N 作品子端點（依賴 projects/showcase，須在其後）
from . import media_log  # noqa: F401  影像紀錄（路徑獨立 /media-log，順序無關）
from . import proposal_assets  # noqa: F401  提案資產夾（路徑獨立 /proposal-assets）
from . import archive       # noqa: F401  結案歸檔清單 + 專案回顧（依賴 proposal_assets 的資產夾）
from . import flow          # noqa: F401  專案工作流（階段 × 五軌進度；路徑 /projects/{id}/flow）
from . import brief_templates  # noqa: F401  企劃範本庫（_範本 夾；依賴 proposal_assets 的 root）
from . import briefs         # noqa: F401  提案的企劃書（多版；路徑獨立 /briefs）
from . import proposal_quotes  # noqa: F401  提案的報價單分頁（上傳多版 + AI 分析；依賴 proposal_assets）
from . import proposal_meetings  # noqa: F401  提案的會議記錄分頁（純人寫，無 AI）
from . import payouts       # noqa: F401  匯款通知（路徑獨立 /payouts、/public/payout）
from . import petty          # noqa: F401  零用金請款（路徑獨立 /petty，順序無關）
from . import benefits       # noqa: F401  福利池（路徑獨立 /benefits，順序無關）

from fastapi import APIRouter  # noqa: E402

from ._shared import CRM_PREFIX, public_router, token_router  # noqa: F401,E402
from ._shared import router as guarded_router  # noqa: E402

# composition root：master 掛這一個物件就有全部端點，URL 與拆分前完全相同；
# NAS 對外容器只掛 public_router。
#
# 🔴 對外白名單**不能**用 `guarded_router.include_router(public_router)` 收進去：
# `include_router` 會把父 router 的 `dependencies` 併給每一條被收編的路由，於是
# `_crm_read_guard`（要登入）套到了 token 端點上 —— 那批的憑證**是 token**，
# 本來就給沒有帳號的人用。實際後果：影像紀錄與雜支登記的公開頁在 master 上
# 一律 401（在 NAS 那側才活著，因為那邊直接掛 public_router，所以沒人發現）。
# 平行掛在一個沒有守衛的外殼上，兩邊行為才一致。
router = APIRouter()
router.include_router(public_router, prefix=CRM_PREFIX)
# token_router：同樣匿名（token 自驗），但只在 master —— NAS 對外容器不掛它，
# 曝露面不用為「只有 master 在 serve 的編輯器」變大。見 _shared.py 的說明。
router.include_router(token_router, prefix=CRM_PREFIX)
router.include_router(guarded_router)
