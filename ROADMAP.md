# Originsun Media Guard Pro — 技術路線圖

> **目標**：從媒體管理工具 → 公司內部 CRM 系統（人資、財務、專案資源），整合 Notion 資料庫 + OpenClaw AI Agent

---

## 現況 (v1.9.9) 基準線

- ✅ 6 個完整工作流程（備份、比對、轉 Proxy、串帶、報表、AI 逐字稿）
- ✅ 模組化後端：`main.py` + `core/` + `routers/`
- ✅ 模組化前端：`frontend/tabs/` 各分頁獨立
- ✅ OTA 無痛更新（6 層防護：busy 檢查 → 下載防中斷 → 備份回滾 → 自動 pip install → 全 router import 測試 → health check）
- ✅ 語音生成 Tab — Edge-TTS + F5-TTS 聲音複製 + NAS 聲音庫 + 台灣正音引擎 + 佇列整合 + GPU 推理
- ✅ 專案總覽 — 單行緊湊任務卡片 + 歷史搜尋篩選 + Log 查看器 + 佇列管理 + 排程
- ✅ 處理主機 UI — 本機自動偵測整合 + 狀態燈（綠/紅）+ IP 去 port + 多選/單選分離
- ✅ 機器狀態即時監控 — 綠燈脈衝（在線）/ 紅燈（離線）/ 橘燈（慢）+ CPU 顯示
- ✅ 進度條優化 — GB 容量顯示 + 完成摘要保留 + 預計完成時間點 + 動態段數
- ✅ 統一動作列 — 暫停/中止/開始新佇列，每個 TAB 一致
- ✅ 日誌分欄 — 左欄任務摘要（system+error）+ 右欄全部日誌（含遠端主機 log）
- ✅ 報表手機版 RWD — 純圖流 + 技術規格 toggle + 膠卷圖放大 + 水平滑動
- ✅ 書籤 + 排程系統 — 儲存常用設定、cron 定時排程
- ✅ 開機自動啟動 — Windows Startup 捷徑 + `start_hidden.vbs`
- ✅ PostgreSQL 集中資料庫 — QNAP NAS Docker + SQLAlchemy async + DB 優先 JSON fallback

---

## Phase H：語音生成 + 聲音複製

**狀態**：✅ 基本功能完成（v1.6.0）

### 已建置

- `tts_engine.py` — Edge-TTS 標準 TTS（台灣口音，免費）
- `tts_engine.py` — F5-TTS 聲音複製（Flow Matching 零樣本複製）
- `tts_engine.py` — 智能文字前處理（數字轉中文、句邊界注入、短塊補齊）
- `routers/api_tts.py` — 9 個 API 端點（標準 TTS / F5-TTS 複製 / 聲音庫 CRUD / 正音字典）
- `frontend/tabs/tts/` — 三子頁 UI（標準 TTS / 聲音複製 / 正音字典）
- `utils/taiwan_normalizer.py` — 台灣正音引擎（vocab_mapping + pronunciation_hacks）
- NAS 聲音庫：統一管理，全辦公室共享

### v1.9.8 完成

- [x] 接入任務佇列（TtsRequest + TtsCloneRequest schema，enqueue_job async）
- [x] Socket.IO 即時進度事件（progress + task_status，和其他 TAB 統一）
- [x] 完成通知整合（`notifier.py` — `tts_success` template）
- [x] GPU 推理修正（update_agent.bat .venv 優先於 python_embed）
- [x] F5-TTS device 自動重建（CPU→CUDA 檢測不一致時重新載入）
- [x] 獨立 concurrency slot（`CONCURRENCY_LIMITS['tts'] = 1`）

### 狀態：✅ 全部完成

---

## Phase I：備份可靠性 + 工作流效率

**狀態**：✅ 全部完成

### 要建置的東西

- [x] **磁碟空間預檢**：`core_engine.py:_check_disk_space()` — 備份前檢查本機+NAS 可用空間（含 5% 安全餘量），不足時終止
- [x] **中斷點繼續備份**：`core_engine.py:_load/_save/_remove_checkpoint()` — 原子寫入 `.originsun_checkpoint.json`，每 50 檔批次存檔，大小驗證，成功後自動清除
- [x] **任務排程**：支援 cron 式定時排程（`core/scheduler.py` + `routers/api_schedules.py`）
- [x] **工作流預設組合**：書籤系統（`routers/api_bookmarks.py`），儲存常用設定，排程時一鍵載入
- [x] **歷史進階搜尋**：專案名模糊搜尋 + 任務類型 / 狀態下拉篩選，整合於完成區工具列
- [x] **任務 Log 查看器**：歷史項目「Log」按鈕 → modal 顯示完整 log（512KB 上限截尾）
- [x] **Log 集中存放 NAS**：`nas_paths.logs_dir`，所有機器 log 統一存到 NAS，任意機器可查看

### 做完後你看到的改變

- 備份前就知道空間夠不夠，不會跑到一半才失敗
- 斷電或意外中斷後，重新提交同專案備份會自動從上次進度繼續
- 可設定自動排程，減少人工操作
- 重複性工作不需要每次重新填寫設定，選擇預設組合即可開始
- 在任何一台機器都能搜尋歷史任務、查看完整 log

---

## Phase K：遠端 Agent 管理

**狀態**：✅ 全部完成

### 要建置的東西

- [x] **OTA 回滾機制**：備份+雙層回滾（解壓失敗 + 啟動失敗 health check）+ Windows MsgBox 通知
- [x] **OTA 無痛更新（v1.9.8~v1.9.9）**：
  - `publish_update.py` 自動生成 `update_manifest.json`（掃描 import → 比對 requirements → 列出新套件）
  - `bootstrap.py` 解壓後自動 `pip install` 新套件
  - `bootstrap.py` 更新前 `worker_busy` 檢查（有任務在跑 → 拒絕更新）
  - `bootstrap.py` 下載寫 `.tmp` → 完整後 rename（防下載中斷）
  - `bootstrap.py` 全 12 router import 測試（失敗 → 回滾 + 彈窗）
  - `main.py` router 容錯載入（缺模組跳過該 router，不 crash）
  - `build_agent_zip.py` + `/download_update` 自動掃描含 .py 的資料夾（不再漏包）
  - `start_hidden.vbs` 啟動前 kill 舊進程（防止版本殘留）
  - `Install_or_Update.bat` 一鍵安裝/更新（自動找 Agent 目錄 + pip + bootstrap）
  - health proxy 改用 `asyncio.to_thread`（防阻塞 event loop）
- [x] **遠端一鍵更新（v1.9.10）**：主控端 POST proxy → 遠端 `/api/v1/control/update`，機器卡片 [更新] 按鈕
- [x] **更新進度監控（v1.9.10）**：輪詢 `update_monitor` port 8001 + health fallback（前 15 秒跳過 health 避免誤判）
- [x] **批次更新（v1.9.10）**：[全部更新 N 台過舊] 按鈕，滾動逐台更新（一台完成再下一台）
- [x] **版本狀態顯示**：機器卡片顯示版本號（health proxy 回傳 version 欄位）
- [x] **「有新版」標記（v1.9.10）**：`_isNewer` 語意版本比對，過舊顯示橘色 ⬆ + [更新] 按鈕

### 做完後你看到的改變

- 在專案總覽頁面看到每台機器的版本號
- 版本過舊的機器會顯示更新按鈕
- 點一下就能遠端更新，不需要到同事電腦前操作
- 可以一次更新所有機器

---

## Phase L：行動端適配（RWD）

**優先等級**：🟡 中 — 目前 UI 針對大螢幕優化
**狀態**：🟡 部分完成（報表頁面手機版已完成）

### 已完成

- [x] **報表手機版 RWD**：`@media (max-width: 768px)` 純圖流佈局 + 技術規格 toggle + 膠卷圖放大滑動
- [x] **膠卷圖優化**：格數 15→10（每格更大更清晰）+ 手機版水平滑動

### 尚需完成

- [ ] **主 UI 響應式佈局**：頁籤改為底部導航或漢堡選單
- [ ] **觸控優化**：按鈕尺寸加大、拖放改為檔案選擇器
- [ ] **關鍵頁面適配**：專案總覽、任務進度、歷史查詢（優先讓管理者手機看狀態）

### 做完後你看到的改變

- 管理者用手機就能看到所有機器狀態和任務進度
- 緊急時可用手機操作基本功能（暫停/停止任務）

---

## Phase B：PostgreSQL 集中資料庫

**狀態**：✅ 全部完成

### 已建置

- [x] PostgreSQL 16 on QNAP NAS (192.168.1.132) via Docker Container Station
- [x] SQLAlchemy 2.0 async + asyncpg driver
- [x] 5 張表：`job_history`, `agents`, `bookmarks`, `scheduled_jobs`, `reports`
- [x] 遷移 `job_history.json` → DB（移除 200 筆上限）
- [x] 遷移 `agents.json` → DB（取代 NAS SMB 共享）
- [x] 遷移 `bookmarks.json` → DB（加 machine_id 跨機可見）
- [x] 遷移 `scheduled_jobs.json` → DB（集中管理排程）
- [x] 遷移 `reports_index.json` → DB（消除 local+NAS 雙寫）
- [x] `settings.json` 保留本機 JSON（機器專屬配置）
- [x] DB 優先 + JSON fallback（3 秒 timeout，DB 不可用不阻塞）
- [x] 60 秒 health monitor 自動重連
- [x] `machine_id` 自動填入 hostname
- [x] 降級測試通過（DB 停→JSON fallback→DB 恢復→自動重連）

### 做完後你看到的改變

- 任務歷史不再有 200 筆上限
- 所有 Agent 共用同一份機器清單、書籤、排程
- NAS PostgreSQL 停掉→系統自動降級回 JSON，不中斷服務

---

## Phase G：多機多專案分散式架構

**優先等級**：🔴 高 — 日常工作需要多機並行處理

### 現況 vs 目標

| | 現在 | 目標 |
|---|------|------|
| 架構 | 一台主機 → 一個佇列 → 輪流跑任務 | 多台機器各自接任務 → 多專案並行 |
| 並行 | 同一時間只有一個任務 | 多專案同時在不同機器上執行 |
| 容錯 | 機器掛掉任務就停了 | 任務自動轉移到其他機器 |

### 要建置的東西

- [ ] 中央任務佇列（DB-backed，取代記憶體 Queue）
- [ ] Agent 自動註冊 + 心跳回報
- [ ] 負載分派（根據 GPU/CPU 能力分配任務）
- [ ] 容錯：Agent 斷線 → 任務自動重派
- [ ] 專案命名空間：每個 Project 有自己的 Job Group，互不干擾
- [ ] 儀表板：每台機器狀態 + 當前任務 + 進度

### 做完後你看到的改變

- Project A 在 Machine 1+3 同時跑、Project B 在 Machine 2 跑 — 真正並行
- 任一台機器掛掉，其任務自動轉移到其他機器
- 儀表板顯示：每台機器當前在跑哪個專案、進度多少
- 可以動態新增機器到叢集（擴容不需要停服）

---

## Phase F-lite：基礎監控

**優先等級**：🟠 高 — 多機環境需要可觀測性

### 要建置的東西

- [ ] Agent 健康度面板（在線/離線/負載）
- [ ] 任務失敗率統計（成功率、平均處理時間）
- [ ] 告警通知整合（已有 `notifier.py`，擴充至 Agent 異常告警）

### 做完後你看到的改變

- 一個畫面看到所有機器狀態和任務分布
- Agent 掛掉在 1 分鐘內收到通知

---

## Phase A：Auth + 角色權限

**優先等級**：🟠 高 — CRM 模組的前提（敏感資料保護）

### 要建置的東西

- [ ] JWT Token 登入端點（`POST /auth/login`）
- [ ] 角色權限（admin / manager / user）
- [ ] FastAPI Middleware：全域攔截，依角色控管 API 存取
- [ ] 前端：登入頁 + 權限控管（不同角色看到不同頁籤）
- [ ] API Key 管理機制（供 OpenClaw 等外部系統使用）

### 做完後你看到的改變

- 沒登入的人打開網頁 → 跳到登入頁面
- 人資/財務頁籤只有 admin/manager 看得到
- OpenClaw 或外部系統呼叫 API 時需帶 API Key

---

## Phase J：CRM 模組

**優先等級**：🟡 中 — 公司內部管理系統

### 要建置的東西

- [ ] **人資模組**：員工資料、出勤記錄、假勤管理
- [ ] **財務模組**：專案預算、報銷流程、費用追蹤
- [ ] **專案資源管理**：素材庫總覽、排程、人力配置
- [ ] **Notion 雙向同步**：專案板、任務追蹤與 Notion 資料庫即時同步

### 做完後你看到的改變

- 同事在同一個系統管理專案進度、人事、財務
- Notion 上的專案更新自動同步到系統，反之亦然
- 管理層可以一站式查看公司營運狀態

---

## Phase C：Webhook / 事件通知

**優先等級**：🟡 中 — 自動化串接的核心

### 要建置的東西

- [ ] Webhook 訂閱機制：外部系統登記「任務完成後通知我」
- [ ] 任務完成後主動 POST 到 callback URL
- [ ] 事件格式標準化：`{ event, job_id, status, result, output_path }`

### 做完後你看到的改變

- 任務完成自動通知 Notion / Slack / 自訂流程
- OpenClaw 派工後不需輪詢，任務完成自動觸發下一步

---

## Phase D：OpenAPI 完善 + MCP Server

**優先等級**：🟡 中 — OpenClaw 直接整合的關鍵

### 要建置的東西

- [ ] 補齊 Pydantic Schema，完整 Swagger 文件
- [ ] 建立 `mcp_server.py`：媒體工具 + CRM 工具定義

```yaml
tools:
  # 媒體工具
  - backup_project(source, project_name, dest)
  - transcode_files(sources, dest_dir, resolution)
  - generate_report(source_dir, output_dir)
  - transcribe_audio(sources, model, output_format)
  - concat_reel(sources, output_name, codec)
  - verify_files(pairs, mode)
  # CRM 工具
  - query_project_budget(project_name)
  - list_team_availability(date_range)
  - sync_notion_tasks(project_id)
```

- [ ] 讓 OpenClaw 能自主發現並使用所有工具

### 做完後你看到的改變

- OpenClaw：「幫我整理今天的素材」→ 自動呼叫 `backup_project`
- OpenClaw：「XX 專案預算用了多少？」→ 自動查詢 CRM

---

## Phase E：生產環境部署

**優先等級**：🟢 低 — CRM 上線後需要穩定存取

### 要建置的東西

- [ ] Nginx 反向代理設定
- [ ] Let's Encrypt SSL 憑證（HTTPS）
- [ ] Docker Compose 容器化
- [ ] 自動重啟服務

### 做完後你看到的改變

- 服務網址從 `http://192.168.1.x:8000` → `https://media.yourcompany.com`
- 服務掛掉後自動重啟
- 外網可安全存取

---

## Phase F-full：完整監控

**優先等級**：🟢 低 — 長期穩定必備

### 要建置的東西

- [ ] 集中 Log 管理（Loki / ELK 或簡單的 log rotation）
- [ ] 任務儀表板：成功率、平均處理時間、趨勢圖
- [ ] 營運儀表板：CRM 數據統計、資源使用率
- [ ] 型別標註強化：mypy 配置 + CI 靜態檢查（目前 ~85% 有 type hints，缺正式驗證流程）

---

## 完整時序

```
現在 (v1.9.10)
    │
    ▼ Phase H: 語音生成 (✅ 全部完成)
    │   → Edge-TTS + F5-TTS + 台灣正音引擎 + 佇列整合 + GPU 推理
    │
    ▼ Phase I: 備份可靠性 + 工作流效率 (✅ 全部完成)
    │   → 磁碟預檢 + 中斷點續傳 + 排程 + 書籤 + 歷史搜尋 + Log 查看 + NAS Log
    │
    ▼ Phase B: PostgreSQL 集中資料庫 (✅ 全部完成)
    │   → QNAP Docker + 5 張表 + DB 優先 JSON fallback + 自動重連
    │
    ▼ Phase K: 遠端 Agent 管理 (✅ 全部完成)
    │   → 版本顯示 + OTA 6 層防護 + 一鍵遠端更新 + 進度監控 + 批次更新
    │
    ▼ Phase L: 行動端適配 (🟡 部分完成)
    │   → ✅ 報表手機版 RWD → 待做：主 UI RWD → 觸控優化
    │
    ▼ Phase G: 多機多專案
    │   → DB-backed 佇列 → 負載分派 → 容錯重派
    │
    ▼ Phase A: Auth + 角色權限
    │   → JWT → 角色控管 → CRM 的前提
    │
    ▼ Phase J: CRM / 專案管理模組
    │   → 專案資源 → 人資 → 財務 → Notion 同步
    │
    ▼ Phase F-lite: 基礎監控
    │   → Agent 健康度 → 失敗率 → 告警
    │
    ▼ Phase C: Webhook
    │   → 事件訂閱 → Notion/Slack 通知
    │
    ▼ Phase D: MCP Server
    │   → 媒體 + CRM 工具 → OpenClaw 整合
    │
    ▼ Phase E: 生產部署
    │   → HTTPS + Docker
    │
    ▼ Phase F-full: 完整監控 (持續)
        → 集中 Log + 營運儀表板
```

**下一步**：Phase A（Auth + 角色權限）→ Phase J（專案管理 / 影片前置作業系統）。
