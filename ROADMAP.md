# Originsun Media Guard Pro — 技術路線圖

> **目標**：從媒體管理工具 → 公司內部 CRM 系統（人資、財務、專案資源），整合 Notion 資料庫 + OpenClaw AI Agent

---

## 現況 (v1.8.4) 基準線

- ✅ 6 個完整工作流程（備份、比對、轉 Proxy、串帶、報表、AI 逐字稿）
- ✅ 模組化後端：`main.py` + `core/` + `routers/`
- ✅ 模組化前端：`frontend/tabs/` 各分頁獨立
- ✅ OTA 純 HTTP 熱更新 + 遠端多機派工
- ✅ 語音生成 Tab — Edge-TTS + F5-TTS 聲音複製 + NAS 聲音庫 + 台灣正音引擎
- ✅ 專案總覽 — 單行緊湊任務卡片 + 歷史分頁 + 佇列管理

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

### 尚需完成（併入 Phase H-remaining，視需求插入）

- [ ] 接入任務佇列（目前 TTS 繞過 task_queue，未排隊）
- [ ] Socket.IO 即時進度事件（目前前端用模擬進度條）
- [ ] 完成通知整合（`notifier.py`）
- [ ] SRT 批次合成功能

---

## Phase I：備份可靠性強化

**優先等級**：🔴 最高 — 直接影響日常備份的穩定性與效率

### 要建置的東西

- [ ] **磁碟空間預檢**：備份前掃描來源總大小，比對目的地可用空間（`shutil.disk_usage()`），不足時警告並終止
- [ ] **中斷點繼續備份**：備份過程中寫入 `.originsun_checkpoint.json` 記錄已完成檔案，下次可跳過已完成的部分
- [ ] **任務排程**：支援 cron 式定時備份（如每天凌晨），或 watchdog 檔案監控自動觸發

### 做完後你看到的改變

- 備份前就知道空間夠不夠，不會跑到一半才失敗
- 斷電或意外中斷後，重新提交同專案備份會自動從上次進度繼續
- 可設定自動排程，減少人工操作

---

## Phase B：PostgreSQL 集中資料庫

**優先等級**：🔴 最高 — 一切後續功能的基礎，改一處全部生效

### 要建置的東西

- [ ] 安裝 PostgreSQL（主控端或 NAS）
- [ ] 遷移 `job_history.json` → `jobs` 表（任務歷史永久保存）
- [ ] 遷移 `settings.json` → `settings` 表（全局設定集中管理）
- [ ] 聲音庫索引 → DB（取代 NAS 檔案掃描）
- [ ] 報表索引 → DB（取代 `reports_index.json`）
- [ ] 所有 Agent 透過主控端 API 讀寫集中資料
- [ ] 任務狀態查詢 API：`GET /api/v1/jobs/{job_id}`
- [ ] 歷史查詢 API：`GET /api/v1/jobs?limit=50`

### 做完後你看到的改變

- 在任何一台機器改設定，所有機器立即生效
- 任務歷史不再有 200 筆上限，支援複雜查詢
- 重啟伺服器後所有資料完整保留

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

---

## 完整時序

```
現在 (v1.8.4)
    │
    ▼ Phase H: 語音生成 (✅ 基本完成)
    │   → Edge-TTS + F5-TTS + 台灣正音引擎
    │
    ▼ Phase I: 備份可靠性強化
    │   → 磁碟預檢 → 中斷點續傳 → 任務排程
    │
    ▼ Phase B: PostgreSQL 集中資料庫
    │   → 全部資料集中，改一處全部生效
    │
    ▼ Phase G: 多機多專案
    │   → DB-backed 佇列 → 負載分派 → 容錯重派
    │
    ▼ Phase F-lite: 基礎監控
    │   → Agent 健康度 → 失敗率 → 告警
    │
    ▼ Phase A: Auth + 角色權限
    │   → JWT → 角色控管 → CRM 的前提
    │
    ▼ Phase J: CRM 模組
    │   → 人資 → 財務 → 專案資源 → Notion 同步
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

**下一步**：Phase I 備份可靠性強化（磁碟預檢 → 中斷點續傳 → 任務排程）。
