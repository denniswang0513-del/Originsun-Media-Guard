# db/models_website/

Phase M 官網模組的 SQLAlchemy ORM Model，每表一檔（AI 友善）。

**為什麼拆檔 + 改名**：
- 既有 `db/models.py` 已塞滿 CRM / User / Role 等表，本目錄拆新表
- 目錄名加 `_website` 後綴避免與 `db/models.py` 的 Python import 衝突

**AI 友善規則**：每檔 ≤ 200 行、每檔只定義一張表。

| 檔案 | 資料表 |
|---|---|
| `__init__.py` | re-export 所有 Model |
| `category.py` | `website_categories` 作品分類主檔 |
| `project_category.py` | `website_project_categories` 作品↔分類多對多 |
| `setting.py` | `website_settings` key-value 全站設定 |
| `service.py` | `website_services` 服務項目 |
| `inquiry.py` | `website_contact_inquiries` 聯絡表單收件箱 |

**共用 Base**：所有 Model 從 `db.models` import `Base`，與既有表共享 metadata。

**crm_projects / crm_staff 擴充欄位**：不在此目錄，透過 `db/migrations_website.py` 的 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 執行。
