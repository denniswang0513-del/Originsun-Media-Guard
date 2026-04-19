# routers/website/

Phase M 官網模組 FastAPI 路由。拆按「使用者類型」分檔。

**部署**：這個目錄的路由**只掛載在 NAS website-api container** (`main_website.py`)，Windows 主機的 `main.py` 不掛載這些。

**AI 友善規則**：每檔 ≤ 400 行、單一職責。

| 檔案 | 職責 | 認證 | 預估行數 |
|---|---|---|---|
| `__init__.py` | 組合 public_router + admin_router | — | ~20 |
| `public.py` | 網站前端呼叫的公開 API（works、featured、contact） | 無（rate limit + Turnstile） | ~300 |
| `admin_works.py` | 作品排序、置頂、公開切換 | website_admin | ~200 |
| `admin_categories.py` | 作品分類 CRUD | website_admin | ~150 |
| `admin_services.py` | 服務項目 CRUD | website_admin | ~150 |
| `admin_inquiries.py` | 聯絡詢問收件箱 | website_admin | ~200 |
| `admin_settings.py` | 網站設定 key-value | website_admin | ~100 |
| `admin_rebuild.py` | 觸發 Astro rebuild + Notion 同步 | website_admin | ~150 |

**Prefix**：`public.py` 為 `/api/website/`，`admin_*.py` 為 `/api/website/admin/`。

**依賴**：`services/website/`（業務邏輯）、`db/models/`（資料表）、`core/schemas/website.py`（Pydantic）。
