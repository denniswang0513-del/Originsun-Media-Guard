# services/website/

Phase M 官網模組業務邏輯層。介於 `routers/website/`（API）與 `db/models_website/`（資料）之間。

**為什麼有這一層**：routers 只做 HTTP I/O，業務邏輯抽出來方便測試、複用、AI 讀取。

**AI 友善規則**：每檔 ≤ 400 行、單一職責、完整 type hints。

| 檔案 | 職責 | 行數 |
|---|---|---|
| `__init__.py` | 模組初始化 | 7 |
| `project_service.py` | 作品查詢/排序/公開切換 + 分類關聯維護 | 206 |
| `category_service.py` | 分類 CRUD + 公開作品計數 | 119 |
| `service_service.py` | 服務項目 CRUD | 97 |
| `inquiry_service.py` | 聯絡表單 CRUD + 轉 CRM client + 月統計 | 187 |
| `notify_service.py` | 4 通道通知 + Turnstile 驗證（複用 notifier.py） | 120 |
| `rebuild_service.py` | 觸發 Astro build（背景執行）+ Notion 狀態 | 112 |
| `settings_service.py` | website_settings key-value upsert + /meta 組裝 | 68 |

**呼叫者**：只能被 `routers/website/*.py` 呼叫，禁止跨層呼叫（AI 友善）。
