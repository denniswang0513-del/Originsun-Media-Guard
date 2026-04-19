# services/website/

Phase M 官網模組業務邏輯層。介於 `routers/website/`（API）與 `db/models/`（資料）之間。

**為什麼有這一層**：routers 只做 HTTP I/O，業務邏輯抽出來方便測試、複用、AI 讀取。

**AI 友善規則**：每檔 ≤ 400 行、單一職責、完整 type hints。

| 檔案 | 職責 | 預估行數 |
|---|---|---|
| `__init__.py` | 模組初始化 | ~10 |
| `project_service.py` | 作品查詢、排序、公開切換邏輯 | ~250 |
| `category_service.py` | 分類 CRUD 邏輯 + 多對多關聯維護 | ~150 |
| `inquiry_service.py` | 聯絡表單接收 + 狀態管理 + 轉 CRM client | ~200 |
| `notify_service.py` | 4 通道通知組合（Email + LINE + GChat + 自動回覆） | ~150 |
| `rebuild_service.py` | 觸發 Astro build + Notion 同步 + build 狀態查詢 | ~100 |

**呼叫者**：只能被 `routers/website/*.py` 呼叫，禁止跨層呼叫（AI 友善）。
