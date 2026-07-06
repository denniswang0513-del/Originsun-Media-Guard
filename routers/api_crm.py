"""api_crm.py — CRM API 薄殼（已拆分至 routers/crm/）。

原單檔（~5,500 行、144 端點）依領域拆分為：
  routers/crm/_shared.py   共用 router 單例 + 頂部 imports + 跨領域 helpers
  routers/crm/clients.py   客戶管理 + CSV 匯入 + users
  routers/crm/projects.py  專案管理 + 結案作業看板 + CSV 匯入
  routers/crm/quotes.py    報價管理 + 報價範本
  routers/crm/staff.py     人力資源 + 履歷/作品集 + 派工
  routers/crm/costs.py     雜支 + 收據 + 成本估算 + 成本子表
  routers/crm/finance.py   發票 + 請款/預支 + 收支明細 + 應付/應收
  routers/crm/showcase.py  Showcase / 結案上架 + token 編輯 + Site API

本檔僅保留既有對外 import 介面：
  - main.py 動態載入：`routers.api_crm` 的 `router` 屬性
  - routers/website/admin_works.py：`from routers.api_crm import _mint_showcase_edit_token`
"""
from routers.crm import router  # noqa: F401
from routers.crm.showcase import _mint_showcase_edit_token  # noqa: F401
