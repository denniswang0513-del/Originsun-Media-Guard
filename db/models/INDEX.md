# db/models/

Phase M 官網模組的 SQLAlchemy ORM Model，每表一檔（AI 友善）。

**為什麼拆檔**：既有 `db/models.py` 已經塞滿 CRM / User / Role 等表，繼續加會過於龐大。Phase M 新表從此目錄開始拆分。

**AI 友善規則**：每檔 ≤ 200 行、每個檔只定義一張表（或密切關聯的表）。

| 檔案 | 資料表 | 說明 |
|---|---|---|
| `__init__.py` | — | 集中 import 所有 Model 供 main.py / main_website.py 使用 |
| `website_category.py` | `website_categories` | 作品分類主檔（可 CRUD） |
| `website_project_category.py` | `website_project_categories` | 作品↔分類多對多關聯表 |
| `website_setting.py` | `website_settings` | 網站全站設定 key-value |
| `website_service.py` | `website_services` | 服務項目（首頁展示用） |
| `website_inquiry.py` | `website_contact_inquiries` | 聯絡表單收件箱 |

**`crm_projects` 擴充**：11 個對外欄位仍定義在既有 `db/models.py`（不拆），透過 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 在 `main.py` / `main_website.py` startup 執行。

**`crm_staff.show_on_website`**：同上，定義在既有 `db/models.py`。
