# Originsun Media Guard Pro — 技術路線圖

> **目標**：從內部工具 → 公司級服務 + OpenClaw AI Agent 整合

---

## 現況 (v1.5.0) 基準線

- ✅ 6 個完整工作流程（備份、比對、轉 Proxy、串帶、報表、AI 逐字稿）
- ✅ 模組化後端：`main.py` + `core/` + `routers/`
- ✅ 模組化前端：`frontend/tabs/` 各分頁獨立
- ✅ OTA 熱更新 + 遠端多機派工
- ✅ 🔊 語音生成 Tab — Edge-TTS + F5-TTS 聲音複製 + NAS 聲音庫 + 台灣正音引擎

---

## Phase H：🔊 語音生成 + 聲音複製

**狀態**：✅ 基本功能完成（v1.6.0）

### 已建置

- `tts_engine.py` — Edge-TTS 標準 TTS（台灣口音，免費）
- `tts_engine.py` — F5-TTS 聲音複製（Flow Matching 零樣本複製）
- `tts_engine.py` — 智能文字前處理（數字轉中文、句邊界注入、短塊補齊）
- `routers/api_tts.py` — 9 個 API 端點（標準 TTS / F5-TTS 複製 / 聲音庫 CRUD / 正音字典）
- `frontend/tabs/tts/` — 三子頁 UI（📢 標準 TTS / 🎙️ 聲音複製 / 📖 正音字典）
- `utils/taiwan_normalizer.py` — 台灣正音引擎（vocab_mapping + pronunciation_hacks）
- `taiwan_dict.json` — 正音字典（14 筆用語 + 10 筆發音校正）
- NAS 聲音庫：`\\192.168.1.132\...\voice` 為主資料庫，本機為快取

### 尚需完成

- [ ] 接入任務佇列（目前 TTS 繞過 task_queue，未排隊）
- [ ] Socket.IO 即時進度事件（目前前端用模擬進度條）
- [ ] 完成通知整合（`notifier.py`）
- [ ] SRT 批次合成功能
- [x] F5-TTS 智能文字前處理（數字轉中文、句邊界注入、短塊補齊）
- [x] UI 用語統一（克隆→複製、合成→生成）+ 移除開發中標記
- [x] 完整工作流程測試（75 個自動化測試：unit + integration + smoke + e2e）

### 做完後使用者能看到的功能

- 輸入文字 → 選台灣口音聲音 → 得到 MP3
- 上傳某人的聲音片段 → 克隆該聲音唸任何文字
- 聲音庫統一管理，全辦公室共享

---

## Phase A：身份驗證層 (Auth)

**優先等級**：🔴 最高 — 沒有它就不能對外開放任何功能

### 要建置的東西

- [ ] API Key 管理機制（產生、撤銷）
- [ ] JWT Token 登入端點（`POST /auth/login`）
- [ ] FastAPI Middleware：所有 API 需帶 Token 才能呼叫
- [ ] 前端：登入頁 / Token 自動附帶

### 做完後你看到的改變

- 沒登入的人打開網頁 → 跳到登入頁面
- OpenClaw 或任何外部系統呼叫 API 時，需帶 API Key
- 這是後續所有功能的前提

---

## Phase B：資料庫層

**優先等級**：🟠 高 — 沒有它，任務歷史重啟就消失

### 要建置的東西

- [ ] 導入 SQLite（本機）或 PostgreSQL（生產）
- [ ] 任務紀錄表：`job_id`、`type`、`status`、`result`、`created_at`
- [ ] 任務狀態查詢 API：`GET /api/v1/jobs/{job_id}`
- [ ] 歷史查詢 API：`GET /api/v1/jobs?limit=50`

### 做完後你看到的改變

- 重啟伺服器後，過去所有任務記錄仍然存在
- 前端「歷史紀錄」頁面有真實資料
- OpenClaw 可以查詢「剛剛那個任務跑完了嗎？結果是什麼？」

---

## Phase C：Webhook / 事件通知

**優先等級**：🟠 高 — OpenClaw 整合的核心機制

### 要建置的東西

- [ ] Webhook 訂閱機制：外部系統登記「任務完成後通知我」
- [ ] 任務完成後主動 POST 到 callback URL
- [ ] 事件格式標準化：`{ event, job_id, status, result, output_path }`

### 做完後你看到的改變

- OpenClaw 派工後，不需要輪詢，任務一完成就自動收到通知繼續下一步
- 可以串接任意第三方服務（Slack、Notion、自訂流程）

---

## Phase D：OpenAPI 完善 + MCP Server

**優先等級**：🟡 中 — OpenClaw 直接整合的關鍵

### 要建置的東西

- [ ] 補齊 Pydantic Schema，讓 FastAPI 自動生成完整 Swagger 文件
- [ ] 建立 `mcp_server.py`：把 6 個工作流程包裝成 MCP Tool 定義

```yaml
tools:
  - backup_project(source, project_name, dest)
  - transcode_files(sources, dest_dir, resolution)
  - generate_report(source_dir, output_dir)
  - transcribe_audio(sources, model, output_format)
  - concat_reel(sources, output_name, codec)
  - verify_files(pairs, mode)
```

- [ ] 讓 OpenClaw 能「發現」這些工具並自動使用

### 做完後你看到的改變

- 打開 `http://localhost:8000/docs` → 看到完整、乾淨的 API 文件
- 告訴 OpenClaw「你有這個工具」，它就能自主決定何時呼叫它
- 例如：「幫我整理今天收到的素材」→ OpenClaw 自動呼叫 `backup_project`

---

## Phase E：生產環境部署

**優先等級**：🟡 中 — 從區域網路走向公司級對外服務

### 要建置的東西

- [ ] Nginx 反向代理設定
- [ ] Let's Encrypt SSL 憑證（HTTPS）
- [ ] Docker Compose 容器化（可選）
- [ ] systemd 自動重啟服務

### 做完後你看到的改變

- 服務網址從 `http://192.168.1.x:8000` → `https://media.yourcompany.com`
- 服務掛掉後自動重啟，不需要人工干預
- 外網可以安全存取（OpenClaw 從任何地方都能打 API）

---

## Phase G：多機多專案分散式架構

**優先等級**：🟡 中 — 「同時跑多專案」的核心架構

### 現況 vs 目標

| | 現在 | 目標 |
|---|------|------|
| 架構 | 一台主機 → 一個佇列 → 輪流跑任務 | 多台機器各自接任務 → 多專案並行 |
| 並行 | 同一時間只有一個任務 | 多專案同時在不同機器上執行 |
| 容錯 | 機器掛掉任務就停了 | 任務自動轉移到其他機器 |

### 要建置的東西

- [ ] Redis Message Broker：任務存在 Redis 佇列，任何機器都能拉取
- [ ] Celery 分散式 Worker：每台機器裝 Worker Agent，主動去佇列拿任務跑
- [ ] 專案命名空間：每個 Project 有自己的 Job Group，互不干擾
- [ ] 中央調度器：根據機器負載、能力分配任務，避免衝突
- [ ] 衝突偵測：同一批素材不能被兩個任務同時處理

### 做完後你看到的改變

- Project A 在 Machine 1+3 同時跑、Project B 在 Machine 2 跑 — 真正並行
- 任一台機器掛掉，其任務自動轉移到其他機器
- 儀表板顯示：每台機器當前在跑哪個專案、進度多少
- 可以動態新增機器到叢集（擴容不需要停服）

---

## Phase F：監控與告警

**優先等級**：🟢 低 — 長期穩定必備

### 要建置的東西

- [ ] 集中 Log 管理（Loki / ELK 或簡單的 log rotation）
- [ ] 任務儀表板：成功率、平均處理時間、各機器負載
- [ ] 告警機制：服務異常或任務失敗 → 自動發 Google Chat 通知

### 做完後你看到的改變

- 一個畫面看到所有任務狀態、機器健康度
- 服務掛掉在 1 分鐘內收到通知

---

## 完整時序

```
現在 (v1.7.0)
    │
    ▼ Phase H: 語音生成 (✅ 基本完成)
    │   → Edge-TTS + F5-TTS 聲音複製 + 台灣正音引擎 + NAS 聲音庫
    │
    ▼ Phase A: Auth
    │   → 任何人想用都要登入
    │
    ▼ Phase B: 資料庫
    │   → 任務記錄永久保存
    │
    ▼ Phase C: Webhook
    │   → 任務完成主動通知
    │
    ▼ Phase D: MCP Server
    │   → OpenClaw 可以自主呼叫你的服務
    │
    ▼ Phase E: 生產部署
    │   → 對外 HTTPS 服務
    │
    ▼ Phase G: 多機多專案
    │   → 真正並行，多專案同時跑
    │
    ▼ Phase F: 監控 (持續)
        → 長期穩定
```

**下一步**：完成 Phase H 剩餘整合項目（任務佇列、即時進度、通知），再進行 Phase A (Auth)。
