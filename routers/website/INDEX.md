# routers/website/

Phase M 官網模組 FastAPI 路由。**只掛在 NAS website-api container** (`main_website.py`)，Windows 的 `main.py` 不載入。

**AI 友善規則**：每檔 ≤ 400 行、單一職責。

| 檔案 | 職責 | 認證 | 行數 | Endpoints |
|---|---|---|---|---|
| `__init__.py` | 組合所有 router | — | 29 | — |
| `_common.py` | 共用 auth guard / rate limit / client IP | — | 80 | — |
| `public.py` | 對外 API（works/featured/categories/services/team/meta/contact） | 無（rate limit + Turnstile） | 162 | 8 |
| `admin_works.py` | 作品對外展示編輯、排序、置頂 | check_admin | 74 | 4 |
| `admin_categories.py` | 作品分類 CRUD + 拖曳排序 | check_admin | 78 | 5 |
| `admin_services.py` | 服務項目 CRUD | check_admin | 67 | 4 |
| `admin_inquiries.py` | 聯絡詢問收件箱 + 轉 CRM client | check_admin | 97 | 5 |
| `admin_settings.py` | website_settings 批次 upsert | check_admin | 39 | 2 |
| `admin_rebuild.py` | Rebuild + Notion 同步 + 儀表板統計 | check_admin | 77 | 5 |

**Prefix**：`public.py` 為 `/api/website/`，`admin_*.py` 為 `/api/website/admin/`。

**依賴**：`services/website/` 業務邏輯層、`db/models_website/`、`core/schemas_website`。

**總 endpoint**：33 支（8 public + 25 admin）。
