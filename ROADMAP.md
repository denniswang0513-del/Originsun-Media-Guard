# Originsun Media Guard Pro — 技術路線圖

> **目標**：從媒體管理工具 → 公司內部 CRM 系統（人資、財務、專案資源），整合 Notion 資料庫 + OpenClaw AI Agent

---

## 現況 (v1.10.209) 基準線<!-- publish_update.py 自動維護版號，勿手改 -->

- ✅ 7 個完整工作流程（備份、比對、轉 Proxy、串帶、報表、AI 逐字稿、空拍寫入）
- ✅ 模組化後端：`main.py` + `core/` + `routers/`（router 容錯載入，缺模組跳過不 crash）
- ✅ 模組化前端：`frontend/tabs/` 各分頁獨立
- ✅ OTA 安全更新（4 層套件防線 + 7 階段更新流程 + 解壓前殺 port + `/publish` skill 自動驗證）
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
- ✅ Auth + RBAC — JWT 登入 + Google OAuth + 模組級守衛（v2 已移除角色層，權限直接綁帳號 modules+access_level）
- ✅ 版本發布工具 — `publish.html` Web UI + 並發鎖 + 發布歷史 + 一鍵回滾
- ✅ CORS Private Network Access — 跨 LAN 機器的 preflight 支援
- ✅ 版本 badge 本機更新 — 點擊版號即觸發 OTA（移除 JWT 限制 + 步驟動畫 Modal）
- ✅ OTA 簡化 — 移除 port 8001 monitor，改用 `update_status.json` + health 輪詢
- ✅ Phase 0 程式碼重構 — app.js 拆 10 模組 + projects.js 拆 3 + api_system.py 拆 3 router
- ✅ 分散式補轉 3 層 fallback — 失敗換遠端主機 → 本機轉 → 再試一次（保證不漏檔）
- ✅ 進度條錯誤面板 — 任務報錯時自動展開錯誤摘要，可查看完整 Log
- ✅ PDF 報表分頁優化 — header/summary 壓縮 + 每檔 tbody 綁定 break-inside:avoid + 膠卷手機版滑動修復
- ✅ Cloudflare 外網存取支援 — 移除 forceInstallModal + 外網自動偵測 + 狀態燈修正
- ✅ NAS 瀏覽器 — 外網使用者用 Web Modal 取代 Windows 對話框，自動偵測磁碟根目錄 + 白名單安全限制
- ✅ API 目錄瀏覽端點 — `GET /api/v1/browse` 支援 `browse_roots` 白名單 + 自動偵測磁碟
- ✅ CRM 客戶管理 Tab — `routers/api_crm.py` 7 端點 + `frontend/tabs/crm/` + CSV 匯入（Notion/Google Sheets）
- ✅ 空拍排程監控（v1.10.79/80）— 每日指定時間掃來源根目錄 → 自動轉檔 + 串帶到目的；完成通知 Google Chat/LINE；取消全部任務按鈕；「檢視轉檔設定」popover（快照顯示 + dirty 項高亮與主面板比對）；立即執行讀 UI 不需先存
- ✅ 製作串帶對齊空拍寫入（v1.10.80）— Payload 補齊 tint/影調/curves/xfade（silent bug）；主面板縮圖網格（複用 clip_card.js）；新增來源三鈕（多選檔案/資料夾/空白列）；Modal 依可見 tab 自動切資料源

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

## Phase K：遠端 Agent 管理 + OTA 更新

**狀態**：✅ 全部完成

### 已建置

#### OTA 7 階段更新流程（`update_agent.py`）
- [x] Phase 1 CHECK → Phase 2 COMPARE → Phase 3 BACKUP → Phase 4 DOWNLOAD+EXTRACT → Phase 5 PIP → Phase 5b MANIFEST SAFETY NET → Phase 6 PREFLIGHT → Phase 7 CLEANUP
- [x] 任何階段失敗 → 自動回滾到 `_rollback/` 備份

#### 4 層套件完整性防線
- [x] **Publish 自動更新 requirements**：`publish_update.py` 掃描所有 import → 自動 append 到 `requirements_agent.txt`
- [x] **Server-side Preflight 卡控**：打包前在 server 跑 `preflight.py`，失敗則不發布
- [x] **Manifest Safety Net**：`update_agent.py` Phase 5b 讀 `update_manifest.json` 補裝遺漏套件
- [x] **動態 Preflight**：`preflight.py` 掃描所有 .py 的 import 逐一驗證，不靠硬編碼清單

#### 版本發布工具（`publish.html` + `/publish` skill）
- [x] NAS 上的 Web UI，Admin 登入後可一鍵發布新版
- [x] `/publish` Claude Code skill：一鍵 commit → 發布 → 驗證 OTA → 推送全部代理 → 驗證結果
- [x] 並發鎖（`asyncio.Lock`，同一時間只能有一個 publish）
- [x] 發布歷史紀錄（`publish_history.json`，最近 50 筆）
- [x] 一鍵回滾（`POST /api/v1/publish/rollback`，還原上一版 version.json + ZIP）
- [x] Agent 狀態總覽（publish 前顯示所有機器版本 + busy 狀態）
- [x] 版本降級保護（前端 + 後端雙重驗證）
- [x] 發布後自動驗證 OTA ZIP 大小（> 10MB 阻止發布）+ 自動重啟主控端

#### 共享常數模組（`ota_manifest.py`）
- [x] `STDLIB`、`LOCAL_MODULES`、`EXCLUDE_DIRS`、`IMPORT_TO_PIP`、`scan_imports()` 統一定義
- [x] `publish_update.py`、`preflight.py` 共用，消除重複

#### 遠端管理
- [x] 遠端一鍵更新：主控端 proxy → Agent `/api/v1/internal/restart`
- [x] `system/restart` fallback：當 `api_ota.py` 載入失敗時，`api_system.py` 提供備用重啟端點
- [x] 更新進度監控：輪詢 `update_status.json` + health fallback（移除 port 8001 monitor 依賴）
- [x] 批次更新：[全部更新 N 台過舊] 按鈕
- [x] 版本狀態顯示 + 「有新版」橘色標記
- [x] `Install_or_Update.bat`：自動找 Agent 目錄 + `pip install -r requirements_agent.txt` + 無硬編碼套件
- [x] 版本 badge 本機更新：`control/update` 無需 JWT（localhost 限定）+ 前端步驟動畫 Modal
- [x] OTA 簡化：移除 `update_monitor.py` port 8001 依賴，改用 detached BAT + `update_status.json`
- [x] 解壓前殺 port 8000：避免 Windows 檔案鎖定導致 .py 覆蓋失敗
- [x] OTA ZIP 瘦身：`AGENT_DIRS` 移除 `windows_helper`（381MB→567KB）

### 做完後你看到的改變

- `publish.html` 一鍵發布，壞版本出不了門（preflight 卡控）
- 套件不會再掉（4 層防線自動偵測 + 自動安裝）
- 遠端一鍵更新，不需到同事電腦前操作
- 發布失敗可一鍵回滾

---

## Phase 0：程式碼重構（進入 CRM 前必做）

**狀態**：✅ 全部完成
**基準數據**：全專案 32,044 行 / 115 個檔案 → 重構後最大活躍檔案從 3499 行降到 1951 行

### 問題分析

| 檔案 | 行數 | 問題 |
|------|------|------|
| `frontend/app.js` | 3499 | Auth + 登入 + OAuth + 使用者管理 + 角色管理 + API Key + OTA + 版本 + 進度條 + 設定 + dropdown 全混在一起 |
| `frontend/tabs/projects/projects.js` | 2180 | 專案總覽 + Agent 卡片 + 遠端更新 + 批次更新 + 歷史篩選 |
| `routers/api_system.py` | 1028 | 健康 + 狀態 + 設定 + OTA + publish + 下載 + 工具端點 |
| `frontend/tabs/projects/projects.css` | 1318 | 專案總覽全部樣式 |

### 優先級 1：拆分 `app.js`（3499 行 → 10 個模組）

```
frontend/js/
├── auth/
│   ├── auth-state.js      ← 狀態 + 登入/登出 + dropdown (~250行)
│   ├── google-oauth.js    ← GIS 整合 (~80行)
│   └── login-modal.js     ← 登入 Modal (~100行)
├── admin/
│   ├── user-mgmt.js       ← 使用者管理 Tab Modal (~400行)
│   ├── role-mgmt.js       ← 角色管理 Modal (~200行)
│   └── api-keys.js        ← API Key UI (~250行)
├── update/
│   ├── version-check.js   ← 版本偵測 + badge (~200行)
│   └── update-modal.js    ← 本機更新 Modal (~120行)
├── settings/
│   └── settings-modal.js  ← 通知設定 Modal (~150行)
└── shared/
    ├── utils.js           ← 現有（不動）
    └── modal-styles.js    ← _ensureModalStyles + _createFormModal (~120行)

frontend/app.js            ← 進入點：import + Socket.IO + 初始化 (~300行)
```

### 優先級 2：拆分 `projects.js`（2180 行 → 3 個模組）

```
frontend/tabs/projects/
├── projects.js        ← 進入點 + 任務卡片 + 歷史 (~800行)
├── agent-cards.js     ← 機器卡片渲染 + 狀態監控 (~600行)
└── agent-update.js    ← 遠端更新 + 批次更新 (~500行)
```

### 優先級 3：拆分 `api_system.py`（1028 行 → 3 個模組）

```
routers/
├── api_system.py      ← 健康/狀態/設定/控制 (~400行)
├── api_ota.py         ← OTA + publish + rollback + download (~400行)
└── api_utils.py       ← open_file/pick_folder/shortcuts (~200行)
```

### 不動的部分

| 檔案 | 行數 | 理由 |
|------|------|------|
| `core_engine.py` | 1556 | 核心引擎，邏輯內聚，拆了反而難維護 |
| `tts_engine.py` | 676 | TTS 引擎，單一職責 |
| `core/worker.py` | 681 | 任務消費器，內聚 |
| `core/auth.py` | 491 | Auth 核心，剛好 |
| 其餘模組 | <500 | 已經健康 |
| `remove_all_emojis.py` | 31 | 一次性工具 |
| `backup_source.py` | 24 | 一次性工具 |

### 實際完成結果

| 指標 | 重構前 | 重構後 |
|------|--------|--------|
| `app.js` | 3499 行 | **1951 行** + 10 個模組 (72-250行/個) |
| `projects.js` | 2180 行 | **1687 行** + agent-cards (418行) + agent-update (140行) |
| `api_system.py` | 1031 行 | **332 行** + api_ota (523行) + api_utils (239行) |
| 修改 API Key UI 需讀取 | app.js 3499 行 | `api-keys.js` 194 行 |
| 修改 OTA 邏輯需讀取 | api_system.py 1031 行 | `api_ota.py` 523 行 |
| 新增 CRM 模組 | 塞進 app.js | 獨立 `tabs/crm/` + `routers/api_crm.py` |

### 新增的模組檔案

```
frontend/js/
├── shared/modal-styles.js   (160行) — Modal 樣式 + 表單建立器
├── auth/auth-state.js       (174行) — Auth 狀態 + 登出 + RBAC
├── auth/login-modal.js       (88行) — 登入 Modal + fetch 攔截
├── auth/google-oauth.js      (72行) — Google OAuth
├── admin/user-mgmt.js       (250行) — 使用者管理 Tab Modal
├── admin/role-mgmt.js       (157行) — 角色管理
├── admin/api-keys.js        (194行) — API Key CRUD
├── update/version-check.js  (173行) — 版本偵測 + badge
├── update/update-modal.js   (212行) — 更新 Modal + migration
└── settings/settings-modal.js(114行) — 通知設定

frontend/tabs/projects/
├── agent-cards.js           (418行) — Agent 機器卡片 + 狀態輪詢
└── agent-update.js          (140行) — 遠端更新 + 批次更新

routers/
├── api_ota.py               (523行) — OTA + publish + download
└── api_utils.py             (239行) — 檔案工具端點
```

### 做完後你看到的改變

- AI 每次修改只需讀 1-2 個 200-500 行的檔案，不再讀 3000 行找 20 行
- 重複工具函式已消除（`_isNewer`, `_text` 統一 export/import）
- CRM 模組有自己的目錄，不會跟現有功能打架

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
- [x] 7 張表：`job_history`, `agents`, `bookmarks`, `scheduled_jobs`, `reports`, `users`, `roles`
- [x] 遷移 `job_history.json` → DB（移除 200 筆上限）
- [x] 遷移 `agents.json` → DB（`routers/api_agents.py` DB-first + NAS JSON fallback，啟動時自動 NAS→DB 遷移）
- [x] 遷移 `bookmarks.json` → DB（加 machine_id 跨機可見）
- [x] 遷移 `scheduled_jobs.json` → DB（集中管理排程）
- [x] 遷移 `reports_index.json` → DB（消除 local+NAS 雙寫）
- [x] `settings.json` 保留本機 JSON（機器專屬配置）
- [x] DB 優先 + JSON fallback（3 秒 timeout，DB 不可用不阻塞）
- [x] 60 秒 health monitor 自動重連
- [x] `machine_id` 自動填入 hostname
- [x] 降級測試通過（DB 停→JSON fallback→DB 恢復→自動重連）
- [x] 啟動時自動 seed 預設角色 + 遷移舊使用者到 RBAC role_id

### 做完後你看到的改變

- 任務歷史不再有 200 筆上限
- 所有 Agent 共用同一份機器清單、書籤、排程、使用者帳號
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

### 要建置的東西（2026-07-06 健康檢查後分階段：先做第一步，其餘再評估）

- [ ] **第一步（獨立價值，先做）：佇列 DB 持久化** — QUEUED/WAITING 任務落 DB，
      master 重啟不再丟排隊任務（現況 `core/state.py` 純記憶體 `_jobs` dict）。
      這一步不改分派邏輯、與終局架構同向，做完立刻解掉現役穩定性問題。
- [ ] 中央任務佇列（DB-backed 分派，取代記憶體 Queue）
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
**狀態**：🟡 部分完成

### 已完成

- [x] Agent 健康度面板（`publish.html` Connected Agents card：在線/離線/busy + 版本號）
- [x] 主 UI 機器狀態即時監控（綠燈脈衝/紅燈/橘燈 + CPU 顯示）
- [x] **失敗主動告警**（v1.10.209）：任務失敗 / 官網 rebuild 失敗 / 每日備份失敗 /
      DB 斷線恢復 / 部署 smoke 失敗 → Google Chat/LINE 推播（原本通知只在成功時發）

### 尚需完成

- [ ] Agent 離線告警（heartbeat 斷線 → 推播；目前只有 UI 紅燈，沒開網頁看不到）
- [ ] 任務失敗率統計（成功率、平均處理時間）
- [ ] 獨立監控儀表板頁面（整合到主 UI）

### 做完後你看到的改變

- 一個畫面看到所有機器狀態和任務分布
- Agent 掛掉在 1 分鐘內收到通知

---

## Phase A：Auth + 角色權限

**狀態**：✅ 全部完成

### 已建置

- [x] JWT Token 登入端點（`POST /api/v1/auth/login`）
- [x] 六角色 RBAC（admin / producer / editor / cameraman / assistant / viewer）+ 模組級守衛
- [x] FastAPI Middleware：`_check_admin()` 容錯包裝（auth 模組不存在時不 crash）
- [x] 前端：登入頁 + 權限控管（不同角色看到不同頁籤）
- [x] Google OAuth 登入（GIS Credential 模式，自動建立/連結帳號，`core/google_auth.py`）
- [x] 使用者管理 UI（美化 Modal + 角色切換即時更新模組 tags + Google 頭像）
- [x] 角色管理 UI（新增角色 Modal + 權限等級 + 模組 checkbox）
- [x] DB 使用者表擴充（`google_id`, `email`, `avatar_url` 欄位，啟動時自動 migrate）

- [x] API Key 管理機制 — `X-API-Key` header + SHA-256 hash + rate limit + JSON fallback
  - `core/auth.py`：`_extract_token()` 支援 JWT + API Key 雙通道
  - `routers/api_api_keys.py`：CRUD 端點（建立/列出/改名/停用/啟用/刪除）
  - `db/models.py`：`ApiKey` ORM model
  - 前端：Tab 分頁式使用者管理 Modal（使用者 / API Keys 分頁切換）
  - 安全：rate limit（10 次/5 分鐘）、刪除使用者連帶撤銷 keys
- [x] Google OAuth 重複建立帳號 bug 修復
  - `_find_user_by()` DB 查無結果時改為 fallback 到 JSON（不再直接 return None）
  - DB `password_hash` 改為 nullable（Google-only 使用者無密碼）
- [x] UI 優化：Tab 分頁式使用者管理 Modal（使用者 / API Keys 切換）
- [x] 「系統設定」→「通知設定」改名 + 移至下拉選單（無需登入即可存取）
- [x] 「重新啟動 Agent」從設定 Modal 移至下拉選單（與通知設定同層級，無需登入）

### 做完後你看到的改變

- ✅ 沒登入的人打開網頁 → 跳到登入頁面
- ✅ 不同角色看到不同頁籤（模組級控管）
- ✅ 可用 Google 帳號一鍵登入
- ✅ OpenClaw 或外部系統呼叫 API 時帶 `X-API-Key: osk_xxx` 即可認證
- ✅ 使用者管理 Tab 分頁：使用者列表 / API Keys 分開顯示，更整潔
- ✅ 通知設定和重新啟動 Agent 不需登入就能用（下拉選單）

---

## Phase J：CRM + 專案管理 + 帳務系統

**優先等級**：🔴 高 — 整合散落在 Notion / Google Sheets 的客戶、專案、財務資料
**狀態**：✅ 核心功能完成（64 API / 11 DB 表 / 6 Tab + 5 子視圖）

### J-1：客戶管理 Tab ✅
- [x] Client CRUD + CSV 匯入 + AM 篩選

### J-2：專案管理 Tab ✅
- [x] Project CRUD + CSV 匯入 + 狀態（洽談中/報價中/進行中/已結案）
- [x] 財務系統：合約金額/帳務追蹤/雜支明細/財務摘要（SQL 聚合）
- [x] 專案派工：從人員庫選人 + 天數 × 日費 = 內部成本

### J-2+：報價管理 Tab ✅
- [x] 報價 CRUD + 項目明細（群組分類） + 折扣 + 稅率 + 最終報價
- [x] 報價範本（CRUD + 一鍵套用）
- [x] 統計儀表板（本月總額/待簽核/簽約率）
- [x] 內部成本欄 + 利潤率計算
- [x] 專案→報價雙向整合 + 報價簽核啟動專案

### J-2++：人力資源 Tab ✅
- [x] 人員庫 CRUD + CSV 匯入（姓名/職能/日費/銀行/身分證/住址/作品集）
- [x] 專案派工 + 人員專案紀錄
- [x] 收款人↔人員庫聯動（請款/收支自動帶入身分證）

### J-2+++：帳務管理 Tab ✅
- [x] **發票**：CRUD + CSV 匯入（對應 Google Sheets 格式）
- [x] **請款**：三區塊 Modal（請款內容/付款資訊/補充資訊）+ 動態必填驗證
  - 專案外包/雜支 → 專案必填
  - 發票代開 → 代開發票必填 + 發票號碼自動帶入
  - 付款狀態：未付款/應付款（預計付款月）/已付款
- [x] **收支明細**：現金流日記帳 + CSV 匯入
- [x] **應付帳款**：按月份分組 + 銀行帳號帶入 + 單筆/批次付款 + 月份調整 + 已付款↔應付款切換 + 複製匯款資訊
- [x] **應收帳款**：按客戶分組（已開立發票）+ 未收款/已收款篩選 + 單筆/批次收款 + 複製客戶資訊 + 天數逾期警示
- [x] **手機版 RWD**：`/expense.html`（雜支登記 + 拍照收據）+ `/invoice.html`（發票登記）
- [x] **全模組 Inline 編輯**：詳情面板分 Tab 編輯（`crm-utils.js` 共用 + 請款專用三區塊版）
- [x] **雜支明細**：專案財務 sub-tab 內嵌 + 收據上傳 + inline 新增/編輯/刪除
- [x] **客戶狀態自動化**：根據成案專案數量自動判定（0=潛在客戶, 1=新客戶, 2+=舊客戶）
- [x] **客戶績效 Tab**：四格指標（專案總數/合約總額/平均單價/收款率）+ 狀態分布 + 年度營收
- [x] **客戶↔專案聯動**：客戶詳情「專案紀錄」Tab + 專案詳情彈窗（4 Tab 完整資訊）
- [x] **客戶列表擴充**：專案數量 + 合約總額欄位（SQL subquery 聚合）
- [x] **CRM 效能優化**：Tab 並行載入 + GET 請求去重 + SQL JOIN/聚合取代全表掃描 + DB 索引

### J-5：成本子表 ✅
- [x] 專案帳目支援多張成本子表（拍攝日 / 子專案）
- [x] 每張子表獨立管理：預算 / 成本估算 / 雜支
- [x] DB 新表 `crm_project_cost_groups` + startup migration
- [x] 10 個新/改 API（CRUD + 跨組彙總 + `/public/cost-groups/*` × 4）
- [x] 前端模組 `crm-projects-cost-groups.js`（切換卡 + 4 Modal + state-aware 按鈕）
- [x] 子表儀表板整併到 chip（刪除獨立 strip 避免資訊重複）
- [x] 行政雜支列 inline edit（類別/細項/金額/請款人 + 新 PATCH 端點 + 並發 save）
- [x] 獨立公開雜支頁 `/group-expense.html`（每子表一個唯一 URL + 手機 RWD + 拍照/相簿選擇）
- [x] `/expense.html` 加子表下拉 + 支援 preset_project/preset_group URL 參數

### J-3：備份 Tab 整合（⬜ 下一步）

- [ ] 備份頁籤新增「選擇現有專案」下拉選項
- [ ] 選擇專案後自動帶入 project_name、local_root、nas_root、proxy_root

### J-4：進階功能（⬜ 未來）

- [ ] **報價 PDF 輸出**：Jinja2 模板 + playwright 轉 PDF
- [ ] **排班日曆**：人力資源視覺化排程
- [ ] **Notion 雙向同步**

### 做完後你看到的改變

- 客戶/專案/報價/人員/發票/請款/收支/應收 全部從 Google Sheets 集中到一個系統
- 報價自動計算利潤率，一鍵套用範本
- 請款→應付帳款自動彙整，複製匯款資訊直接貼到網銀
- 手機可在拍攝現場即時登記雜支 + 拍收據
- 所有詳情面板支援 inline 編輯，不需開 Modal

---

## Phase M：對外官方網站 + 官網管理 Tab

**優先等級**：🔴 高 — 6/1 NAS Staging / 7/1 正式切換
**時程**：2026-04-20 ~ 2026-07-01（10 週）
**網域**：`originsun-studio.com`（沿用舊站、DNS 轉 Cloudflare）
**部署目標**：**所有網站功能 100% 在 NAS**（穩定 24/7）
**分支**：`feature/website-m`
**完整文件**：[`docs/WEBSITE_ARCHITECTURE.md`](docs/WEBSITE_ARCHITECTURE.md)

### 部署架構

```
開發階段 (M-A ~ M-E，Week 1-6)
  Windows 本機 (你電腦)
  └── npm run dev → localhost:4321
      └── 連到 localhost:8000 (現有 FastAPI)

部署階段 (M-F ~ M-H，Week 7-10) — 全部在 NAS 192.168.1.132
  NAS QNAP Container Station（5 既有 + 2 新 = 7 容器）

  既有容器（不動）：
    cloudflared    macvlan 192.168.1.137  (Token 模式，CF 儀表板管 routing)
    FileReport_Nginx bridge A 10.0.3.2    (port 8080→80，專用)
    originsun_postgres bridge B 172.29.20.2  (port 5432)
    MCP、n8n 等其他服務

  新增容器（新 bridge network `originsun_web`）：
    Website_Nginx  port 8081→80   serve Astro dist + /uploads + 反代 /api/website
    website-api    port 8001 (內部)  入口 main_website.py，連 postgres 走 host

  CF Zero Trust 儀表板（Web UI 操作，不改容器設定）：
    originsun-studio.com         → HTTP://192.168.1.132:8081
    preview.originsun-studio.com → HTTP://192.168.1.132:8081

  /share/Container/AI_Workspace/Originsun_Web/
  ├── FileReport/、Agents/、Logs/  (既有)
  └── Website/                     🆕
      ├── repo/      (git clone)
      ├── dist/      (Astro build 產物)
      ├── uploads/   (使用者上傳圖片)
      └── docker/    (docker-compose.yml + Dockerfile + nginx.conf)

Windows 192.168.1.107 (員工內網、既有系統)
  └── FastAPI main (既有：CRM / Transcode / Backup…)
      └── 官網管理 Tab 前端
          └── fetch('http://<NAS_IP>:8001/api/website/admin/...')
              → 跨機呼叫 NAS FastAPI（共用 JWT secret + CORS）
```

**關鍵設計**：
- **網站穩定性 100% 靠 NAS**：Windows 關機、重開都不影響網站
- **程式碼單一 repo**，兩個 entry point：`main.py` (Windows 既有) / `main_website.py` (NAS 新)
- **JWT secret 共用**：Windows + NAS 用同一把 secret，管理 Tab 跨機呼叫免重新登入
- **大部分 API 在 build time 就執行**（Astro SSG → 靜態 HTML），Runtime API 只服務聯絡表單與 admin Tab

### 執行階段（3 大 Stage 共 8 個子階段）

#### Stage 1：本機開發（Week 1-6，零風險）

**M-A 本機專案骨架**（Week 1）
- [ ] 建立 `feature/website-m` 分支
- [ ] Astro 4 + Tailwind 本機初始化（`website/` 目錄）
- [ ] 空目錄骨架 + 每個目錄一份 `INDEX.md`
- [ ] AI 友善檔案結構（每檔 ≤ 400 行）

**M-B 資料層**（Week 2）
- [ ] `crm_projects` 擴充 11 對外欄位（public、slug、youtube_id…）
- [ ] 新表 `website_categories`（作品分類 CRUD、多對多）
- [ ] 新表 `website_project_categories`（關聯表）
- [ ] 新表 `website_settings`（key-value 全站設定）
- [ ] 新表 `website_services`（服務項目）
- [ ] 新表 `website_contact_inquiries`（聯絡表單收件）
- [ ] `crm_staff` 擴充 `show_on_website`
- [ ] Seed data（舊站 4 分類 + 4 服務項目 + 14 筆 settings）

**M-C 後端 API**（Week 3）
- [ ] `routers/website/public.py` 對外 API（含 Turnstile 驗證）
- [ ] `routers/website/admin_*.py` 6 支管理端點
- [ ] `services/website/` 業務邏輯層 5 檔
- [ ] Rate limit + CORS 白名單（dev: localhost、prod: originsun-studio.com）

**M-D 官網管理 Tab**（Week 4）
- [ ] RBAC 4 處更新（`website_admin` 模組）
- [ ] 9 子視圖：儀表板 / 首頁 / 作品集 / 分類 / 服務 / 關於 / 詢問 / 部落格 / 設定
- [ ] CRM 專案 Tab 加「🌐 對外展示」子區塊

**M-E 前端 Astro 網站**（Week 5-6）
- [ ] Base Layout + Header + Footer + 繁中/英文雙語切換
- [ ] 首頁（YouTube Hero 背景 + 精選作品 6 宮格）
- [ ] 作品集列表 + 詳情（lite-youtube-embed）
- [ ] 關於我們（含 `crm_staff` 團隊成員）+ 服務項目
- [ ] 聯絡頁（Cloudflare Turnstile + 4 通道通知）
- [ ] 部落格（Notion as CMS）
- [ ] 🚀 **本機 `localhost:4321` 完整可看**（~5/31）
- [ ] 臨時網址 `cloudflared tunnel --url localhost:4321` 供遠端 review

#### Stage 2：NAS 部署（Week 7-8，不碰 DNS）

**M-F NAS 容器部署**（Week 7）— 2 新容器（Website_Nginx + website-api）
- [ ] NAS SSH：到 `/share/Container/AI_Workspace/Originsun_Web/`，建 `Website/` 子目錄
- [ ] `git clone` repo 到 `Website/repo/`
- [ ] 新增 `main_website.py`（~30 行，只載入 `routers/website/`）
- [ ] 新增 `docker/Dockerfile.website`（python:3.11-slim + FastAPI 最小依賴）
- [ ] 新增 `docker/docker-compose.yml`：
  - 定義新 bridge network `originsun_web`
  - `Website_Nginx`（nginx:alpine, port 8081→80, 掛 dist + uploads + conf）
  - `website-api`（build from Dockerfile.website, expose 8001, 連 postgres 走 host）
- [ ] 新增 `docker/nginx/originsun.conf`：
  - `/`               → `/usr/share/nginx/html/`（Astro dist）
  - `/uploads/*`      → `/usr/share/nginx/html/uploads/`
  - `/api/website/*`  → `proxy_pass http://website-api:8001`
- [ ] 在 NAS build Astro：`cd repo/website && npm ci && npm run build` → 產出到 `Website/dist/`
- [ ] `docker-compose up -d` 起 2 個新容器
- [ ] **Cloudflare Zero Trust 儀表板**（Web UI）：到既有 tunnel → Public Hostnames
  - 加 `originsun-studio.com` → Service: `HTTP://192.168.1.132:8081`
  - 加 `preview.originsun-studio.com` → 同上
- [ ] website-api 環境變數：`DATABASE_URL`（走 host `192.168.1.132:5432`，跟 Windows 相同）、`JWT_SECRET`（共用）
- [ ] website-api CORS：`192.168.1.107:8000`（Windows Tab）、`originsun-studio.com`、localhost
- [ ] 驗證既有 5 容器（cloudflared / FileReport_Nginx / postgres / MCP / n8n）全部仍正常

**M-G NAS Staging 完整驗證**（Week 8）
- [ ] 完整 build + deploy 流程可重複執行（pull → build → nginx reload）
- [ ] `POST /api/website/contact` 從 nginx 進入 NAS FastAPI 成功（4 通道通知發送）
- [ ] 官網管理 Tab 從 Windows 跨機呼叫 NAS admin API 成功
- [ ] PostgreSQL 連線正常（NAS 本機）
- [ ] uploads 上傳流程測試（Admin Tab 上傳 → NAS FastAPI → 寫入 NAS 檔案系統 → nginx serve）
- [ ] 所有靜態資源 cache header 正確
- [ ] Lighthouse ≥ 95（Performance / Accessibility / Best Practices / SEO）
- [ ] SEO 結構化資料 + Schema.org VideoObject + sitemap
- [ ] Google Search Console + GA4（接手舊站歷史資料）
- [ ] **穩定性測試**：Windows 關機，確認網站 + 表單仍正常運作

#### Stage 3：DNS 切換上線（Week 9-10）

**M-H DNS 轉移 + 正式切換**（Week 9-10）
- [ ] 遠振後台 DNS 稽核（使用者執行，填 A/MX/TXT/CNAME 清單）
- [ ] Cloudflare 新帳號 add site + 匯入 DNS + 設 DNS only
- [ ] 遠振改 NS 指向 Cloudflare，等 24-48 小時 propagation
- [ ] 舊站 `www.` + root 持續運作驗證
- [ ] 新增 `preview.originsun-studio.com` A record → NAS Tunnel（robots noindex）
- [ ] 6/30：DNS TTL 預降至 300 秒
- [ ] **7/1**：root + `www.` 切換指向 NAS Tunnel、拔 robots、送 GSC sitemap
- [ ] 7/1 ~ 7/7：舊站內容保留為 fallback

### 做完後你看到的改變

- 公司有現代化 SEO 友善官方網站，**100% 部署在 NAS 上 24/7 穩定運作**
- **Windows 主機關機、重開、維護都不影響網站**（連聯絡表單都正常）
- 所有網站持久資料都在 NAS：DB、上傳檔案、build 產物
- 作品自動從 CRM 已結案 + 公開的專案拉出，不需雙寫維護
- 聯絡表單直接進 CRM 變潛在客戶（`website_contact_inquiries` → `clients`）
- 非工程師（行銷/PM）可在 Notion 寫部落格、在官網管理 Tab 調作品排序
- 所有檔案 ≤ 400 行，未來 AI 協作更順

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

## Phase E：master 去單點化 + 生產環境部署

**優先等級**：🟠 中高（2026-07-06 健康檢查重定義）— 系統早已是生產
（機隊 10 台 + 對外官網 + CRM 管公司正式帳），真正的風險不是缺 HTTPS，
而是 master 是一台身兼 dev 的 Windows 桌機、掛了沒有重建程序。

### 要建置的東西（依風險排序）

- [ ] **master 重建 SOP**：新機 → 裝依賴（requirements_server.txt）→ 還原
      settings/credentials/SSH key → 接回 NAS 與機隊 的完整照抄式文件
- [ ] **NAS 災難重建程序**：NAS 整台死掉時，postgres / nginx / cloudflared /
      website-api 在他處重建 + 從 Google Drive 備份還原的 runbook
      （現有 BACKUP_RESTORE.md 全部假設 NAS 還活著）
- [ ] dev / 生產分機評估（或至少把 master 的五重身分
      —— build/relay/claude runner/OTA 來源/備份調度 —— 文件化）
- [ ] Nginx 反向代理 + SSL（HTTPS）
- [ ] 自動重啟服務強化

### 做完後你看到的改變

- master 硬碟掛掉 → 照 SOP 半天內重建，而不是災難
- NAS 掛掉 → 官網與 DB 有明確的異地重生路徑
- （後段）服務網址 HTTPS 化、外網可安全存取

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
已完成
    │
    ▼ Phase H: 語音生成 (✅)
    │   → Edge-TTS + F5-TTS + 台灣正音引擎 + 佇列整合 + GPU 推理
    │
    ▼ Phase I: 備份可靠性 + 工作流效率 (✅)
    │   → 磁碟預檢 + 中斷點續傳 + 排程 + 書籤 + 歷史搜尋 + Log 查看 + NAS Log
    │
    ▼ Phase B: PostgreSQL 集中資料庫 (✅)
    │   → QNAP Docker + 7 張表 + DB 優先 JSON fallback + 自動重連 + RBAC seed
    │
    ▼ Phase K: 遠端 Agent 管理 + OTA (✅)
    │   → 7 階段 OTA + 4 層套件防線 + publish.html + 遠端更新 + 回滾
    │
    ▼ Phase A: Auth + 角色權限 (✅)
    │   → JWT + Google OAuth + RBAC v2（權限直接綁帳號）+ 使用者管理 UI + API Key 管理
    │
    ▼ Phase 0: 程式碼重構 (✅)
    │   → app.js 3499→1951+10模組 / projects.js 2180→1687+2模組 / api_system.py 1031→332+2router
    │
    ▼ Phase J: CRM + 專案管理 + 帳務 (✅ 核心完成)
    │   → 140+ API（持續增長，以程式碼為準）/ 11+ DB 表 / 6 Tab + 5 子視圖 + 手機版 RWD + Inline 編輯
    │
現在 (v1.10.209) ← 你在這裡
    │
    ▼ Phase M: 對外官方網站 (✅ 完整版 A 部署完成 2026-04-29)
    │   → NAS Website_Nginx (8090) + website-api 容器 + cloudflared tunnel
    │   → admin Tab 跨機 fetch、master 關機對外不掉、聯絡表單 e2e 通
    │   → /publish 自動 sync code 到 NAS 容器、DB-backed rebuild state
    │
    ▼ Phase J-3: 備份 Tab 整合 (⬜)
    │
    ▼ Phase L: 行動端適配 (🟡 部分完成)
    │   → ✅ 報表手機版 RWD → 待做：主 UI RWD → 觸控優化
    │
    ▼ Phase F-lite: 基礎監控 (🟡 失敗告警已完成 v1.10.209)
    │   → ✅ 失敗推播 → Agent 離線告警 → 失敗率 → 監控頁
    │
    ▼ Phase G: 多機多專案
    │   → 第一步:佇列 DB 持久化 → DB-backed 分派 → 負載分派 → 容錯重派
    │
    ▼ Phase C: Webhook
    │   → 事件訂閱 → Notion/Slack 通知
    │
    ▼ Phase D: MCP Server
    │   → 媒體 + CRM 工具 → OpenClaw 整合
    │
    ▼ Phase E: master 去單點化 + 生產部署
    │   → 重建 SOP → NAS 災備 → HTTPS + Docker
    │
    ▼ Phase F-full: 完整監控 (持續)
        → 集中 Log + 營運儀表板
```

**下一步**：Phase M 已於 2026-04-29 部署上線、持續迭代中（AI SEO / 翻譯 / 轉址 / 結案上架收件匣等）。
排定的下一個獨立 Phase 為 **J-3 備份 Tab 整合**（小工程）與 **F-lite 基礎監控**（失敗告警部分已完成：任務失敗/rebuild 失敗/備份失敗/DB 斷線恢復 主動推播）。

---

## 版本歷程摘要

| 版本 | 日期 | 重點 |
|------|------|------|
| v1.10.136 | 2026-05-21 | **fix(website+agents): 處理主機健康燈走 server proxy + 子視圖載入重試 + 新增作品 skeleton 流程**。(1) 各 TAB「處理主機」狀態燈 [`_checkHostHealth`](frontend/js/shared/utils.js) 從瀏覽器直連 `http://<LAN-IP>/api/v1/health` 改走 master proxy `/api/v1/agents/{id}/health` — 修正從遠端 https 後台（foundry.originsun-studio.com）開時 mixed-content 被擋 + 不在內網兩個原因造成「處理主機」全紅，與「專案總覽」機器卡片一致；`_computeHosts` 兩處建構（app.js / agent-cards.js）補 `id` 欄位。(2) 官網管理子視圖 [`website.js`](frontend/tabs/website/website.js) 動態 import 抽 `_loadSubviewInto`，載入失敗時提供「🔄 重試」按鈕，retry 帶 `?t=<ts>` cache-bust query 繞過 ES module map 對失敗結果的永久快取（發布/重啟造成的瞬間性 subview 載入失敗不再需整頁重整）。(3) 作品集「新增作品」[`works.js`](frontend/tabs/website/subviews/works.js) 跳過小表單，直接 POST 建 skeleton（後端塞 sentinel name「（未命名作品）」、狀態預設「已結案」）開編輯 overlay；overlay 關閉呼叫 `DELETE /works/{id}/if-skeleton`，後端 sanity check name 仍是 sentinel 且各 public_* 全空才連帶刪 crm_projects / crm_project_showcase / website_project_categories，避免開了又關留孤兒；`client_id` 從 None 改空字串避開 NOT NULL 約束。(4) SEO AI runner [`seo_runner.py`](services/website/seo_runner.py) 加 master relay — NAS website-api 容器是 python:3.11-slim 沒 claude CLI，`run_pipeline` 偵測 `MASTER_RELAY_URL` env 時 forward 給 master 的 claude.exe 跑（新增 `/internal/seo/run` internal endpoint，X-Internal-Key 認證）；`_call_claude` 改回傳具體失敗原因、timeout 90→180s。 |
| v1.10.135 | 2026-05-14 | **refactor(website/awards): 獎項紀錄拆獨立子視圖 + 作品圖欄位 + /portfolio 看板尺度重做**。承 v1.10.134 在 SEO Tab 內加的 🏆 獎項紀錄 card → 升格為側邊欄獨立子視圖 [`awards.js`](frontend/tabs/website/subviews/awards.js):註冊 SUBVIEWS、HTML 加按鈕、移除 seo.js 內 `_cardAwards` + 3 個 handler。**cert_url 重新定位為作品圖**:admin UI 標籤「證書/獎杯」→「🖼️ 作品圖 URL」、表格加 64×36 縮圖預覽欄 + cert_url inline 編輯欄、新增表單下方獨立 URL 全寬輸入。**/portfolio 三輪設計演化**:R1 卡片右側 80×80 小縮圖 → R2 編輯雜誌風格(去框、hairline divider、圖左 16:9、editorial typography) → R3 **看板尺度**(容器 max-w-3xl → max-w-5xl 突破、圖 240×135 → 1024×576 滿版約 18 倍面積、年份從 sm 升 5xl light display、space-y-28 純空白分隔、三欄水平 grid 把 year/award/org 拉開像海報底注、Section header 仍保留窄 max-w-3xl 形成 narrow→wide→narrow 編輯節奏)。塞一筆假資料(2024 第 59 屆金鐘獎《父親的後座》)驗證,picsum 圖片穩定載入。 |
| v1.10.134 | 2026-05-13 | **feat(website): /portfolio 加站級獎項紀錄 + 作品集 PDF 下載入口**。新增 `WebsiteAward` model(進 [seo.py](db/models_website/seo.py),「站級四表」)+ migration(`website_awards` + `idx_award_visible_sort` 同 FAQ/Testimonial/QuickFact 模式)+ `AwardLevel = Literal["獲獎", "入圍"]` Pydantic Create/Update/Response + 服務層 4 個 thin wrapper(走 `_crud_base` 泛型)+ admin router `register_crud(awards)` + public `GET /api/website/awards`。Admin UI 加「🏆 獎項紀錄」card 到 [SEO Tab](frontend/tabs/website/subviews/seo.js)(9 欄:年份/名稱/類別/等級/作品/得獎人/頒獎單位/排序/可見,`_levelOptions` helper 收斂兩處 `<option>` 重複)+ SEO 健康分數加 awards 規則。**PDF 下載**:[`works.js`](frontend/tabs/website/subviews/works.js) 作品集 Tab 頂部加 PDF URL 欄位(寫 `website_settings.portfolio.pdf_url`),`settings_service.get_meta` 暴露 `portfolio_pdf_url`,[`/portfolio`](website/src/pages/portfolio.astro) 重寫:頂部「作品集 PDF」按鈕(未設定隱藏,跨網域不帶 download 屬性)、Honors & Awards 單欄直列卡片(年份 + 等級徽章 [獲獎金底/入圍灰底] + 獎項名 · 類別 / 作品 · 得獎人 / 頒獎單位),`hasAwards` + `honorsSubtitleZh/En` 收斂雙語,移除 `@media print`(PDF 改走 admin 上傳)。Astro client 加 `fetchAwards` 走 `_memoizeList`。**4 輪 /simplify 累積 15 項清理**:R1 — `AwardLevel` Literal、recipient 輸入、級別 helper、健康規則、跨網域 download 屬性、TS type;R2 — 模型合進 seo.py、索引收斂、honors subtitle 收斂、模組層變數移除;R3 — 誤導註解、單 loop 計數、stale docstring;R4 — clean。 |
| v1.10.133 | 2026-05-10 | **feat(crm): 全 9 個 list 共用 `createSortable` 排序 helper(點欄頭 toggle asc/desc + localStorage 持久化)+ 完成 Option B mirror**。Phase 1 抽 `createSortable` 通用排序器到 [crm-utils.js](frontend/tabs/crm/crm-utils.js):接受 `storageKey` / `defaultSort` / `panelId` / `getters` config,回傳 `sorted(items)` + `attach()`,點欄頭 toggle asc/desc、localStorage 持久化(per-list 獨立 key)、空值永遠排尾(避免 desc 時遮資料)、`dataset.sortBound` flag 防 idempotent attach 重複 bind。`renderList()` 改 `sorter.sorted(items).map(...)` 取代直接 map,header 用 `<span data-sort-key="X">標題 <span class="crm-sort-ind">↕</span></span>`,CSS 通用 `.crm-list-header [data-sort-key]` cursor:pointer + active 藍色高亮 + 方向箭頭。新增 `enumIndex(arr, val, fallback)` helper(crm-utils.js)— 工作流順序查表 3 行縮成 1 行(`enumIndex(STATUS_ORDER, p.status, '洽談中')`),4 個 list 共用。Phase 2 套用 8 個 list:**客戶總表**(更新日 ↓)、**人力資源**(狀態 ↑:在職→兼職→單位→專案)、**報價總表**(日期 ↓)、**發票**(日期 ↓;狀態:開立中→已開立→作廢)、**請款**(日期 ↓;狀態:應付款→已付款)、**收支明細**(日期 ↓)、**應收帳款**(最久天數 ↓)、**應付帳款**(應付金額 ↓ per-month-group 內排序,month group 順序維持月份序列)。**Option B 補完作品 title/client 鏡射**:之前 v1.10.132 只改了 `_to_public_dict` 給作品集 Tab 跟對外網站 fallback,但 CRM 專案管理用 `_to_project_dict` 沒 fallback,且 PM 在 showcase-edit 存 `public_title` / `public_client` 也沒回寫 `name` / `client_id`,所以從作品集新增的作品 CRM Tab 永遠卡 sentinel「（未命名作品）」+ 空 client。本版新增 [`mirror_public_to_crm`](services/website/project_service.py) `services/website/project_service.py`:寫 `public_title`(strip 後非空)時 → 鏡射到 `name`;`public_client`(strip 後非空)若精確匹配既有 `Client.short_name` → 設 `client_id`(autocomplete dropdown 點選會帶 exact short_name 進 input,精確比對最安全;模糊比對誤判風險高留給人工)。呼叫點 2 處:`update_project_public`(作品集 admin Tab PUT)+ `/public/showcase-edit/{token}` PUT(PM showcase-edit)。**驗證**:smoke 19 + http 10 + ui 3 = 32 個通過,showcase-edit credits 4 個 UI 失敗(`.credit-name-input` resolved to hidden;pre-existing,跟此次工作無關,獨立 /bugfix)。 |
| v1.10.132 | 2026-05-07 | **fix(website): 對外網站精選輪播假性空白 + NAS website-api DB 卡死自我修復 + 作品 title/client source-of-truth 改 CRM**。(1) **對外首頁 hero 顯示「尚無精選作品」placeholder** — 9 件 featured 作品在 DB 裡好好的,API 也回 9 筆,但部署的 dist/index.html 是空 placeholder。**根因鏈條 3 層 silent bug 互相串接**:(a) [`/llms-full.txt`](website/src/pages/llms-full.txt.ts) 呼 `fetchFeatured(30)`,API 限 `le=12` → 422,被 [`_safeGet`](website/src/lib/crm-client.ts) 吞掉成空陣列;(b) `fetchCategories` / `fetchServices` / `fetchTeam` 沒 memoize,Astro build 35 頁 × 多 endpoint 並行 burst 觸發 60/min rate limit → 100+ 個 429,也被 `_safeGet` 全吞;(c) Astro `<Image>` 對 YouTube 舊片(沒上 HD 上傳)的 `maxresdefault.jpg` build 期 fetch → 404 → throw 中斷 build。`_safeGet` 把 (a)+(b) 全吞,build 過了標 success,標 `last_success_at` 成功,部署就把 placeholder dist 推上去了。auto-rebuild 之後沒再被觸發 → stale dist 永久卡住。**修法 7 處**:[`crm-client.ts`](website/src/lib/crm-client.ts):`fetchFeatured` `_safeGet` → `_get`(throw on error 中斷 build,build-critical 不容假性成功);`fetchCategories/Services/Team` 加 `_memoizeList`(35 頁 burst 變 1 call,total request count 從 100+ 降到 ~17)。[`youtube.ts`](website/src/lib/youtube.ts):新增 `resolveThumbnailAsync`,build-time HEAD check `maxresdefault.jpg` 是否存在,404 自動 fallback 到 `hqdefault`,9 張平行 < 200ms 不影響 build。`HomeSlideshow.astro` + `FeaturedWorks.astro` frontmatter 改用 async resolver。[`llms-full.txt.ts`](website/src/pages/llms-full.txt.ts):`fetchFeatured(30)` → `(12)` 配合 API 上限。[`_common.py:rate_limit`](routers/website/_common.py):新增 loopback IP(127.0.0.1 / ::1)bypass,master 上 build 跑 100+ 平行打自己不再被自己 rate limit(對外 abuse 走 cloudflare cf-connecting-ip,本機 build 不會帶這 header)。(2) **NAS website-api DB 卡死無法自我修復** — admin Tab 儀表板顯示「無法載入:資料庫目前不可用」,容器 healthy 32 hours 但所有 DB endpoint 一律 503。根因:[`main_website.py`](main_website.py) 沒有 master `main.py` 那條 60 秒 `_periodic_db_check` async task,容器一旦 startup 抓到 `init_db()` False(postgres 暫時抖動)就永久卡住,要重啟才復活。boot log 顯示前 8 次重啟都 `startup OK (DB online)`,最後 1 次 `startup WITHOUT DB` 後 `state.db_online` 卡 False。**修法**:lifespan 加 `_periodic_db_check` async task(對齊 main.py:711-721),60s 重探 DB,True→False 觸發 `init_db()` 重新初始化、False→True 印「連線恢復」,shutdown 時 cancel。從此 postgres 抖一下會 60 秒內自動恢復。(3) **作品集 Tab 跟 CRM 專案管理顯示不同步** — 從作品集新增作品時 CRM `name` 卡 `「（未命名作品）」`,作品集 Tab `public_client` 沒填的話顯示空白但 CRM client_id 解析得出名字。每作品實際是 ONE row(`crm_projects`)但有兩套 title/client 欄位:`name` / `client_id`(CRM 用)vs `public_title` / `public_client`(作品集 + 對外網站用)且互不同步。**修法**(Option B,CRM 為 source of truth):`_to_public_dict` 改 fallback chain — `title = public_title || name`、`client = public_client || client.short_name || client.full_name`。新增 `_clients_for_projects` batch JOIN(對齊 `_categories_for_projects` pattern)解析 client_id → 顯示名,4 個 caller(`list_public_projects` / `get_public_project_by_slug` / `list_featured_projects` / `list_admin_projects`)同步更新。CRM 改 `name`/`client_id` 立刻反映到作品集 Tab 跟對外網站,PM 還是能用 `public_*` 做 SEO 覆寫(空值 fallback,不為空就 override)。(4) **showcase-edit 作品分類/標籤改手動「儲存所有變更」**(showcase-edit.html):toggle 不再立即 PUT,改更新 in-memory `DATA.selected_category_ids` + chip class/text + `setDirty(true)`,master save 一次 commit 一次 rebuild,跟其他欄位行為一致(不再每 click 一次觸發 60s rebuild debounce reset)。 |
| v1.10.131 | 2026-05-02 | **HOTFIX: api_ota router 整個沒載入(/download_update 404)+ pythonw 避免 WT 視窗**。v1.10.130 上線後 OTA 推不動,所有 agent 都 STUCK_ON_OLD,update_status 顯示「下載失敗 已回滾」。根因:11156db 改 download_updater 動態 BAT 模板時加了 `"""%INSTALL_DIR%\start_hidden.vbs"""` 這行 — Python 把 3 連續雙引號當作外層 `f"""..."""` 結束標記,api_ota.py SyntaxError 整個 module 載入失敗。main.py 的 `try/except` 容錯吞了這個錯,master 啟動時只印 `[WARN] Router api_ota 載入失敗`(被忽略),其他 router 正常服務。結果整個 v1.10.130 期間 api_ota 提供的 13 個 endpoint 全部 404 — `/download_update`、`/api/v1/internal/restart`、`/api/v1/control/update`、`/download_agent`、`/download_installer`、`/api/v1/publish`。Master 主體看似活著但 OTA 整條斷線。**修法**:cmd echo 不需要 triple-quote 跳脫,改 `"%INSTALL_DIR%\start_hidden.vbs"` 單對引號。**順帶搭船**:`start_hidden.vbs` + `core/process_spawn.py:find_python` 改優先用 `pythonw.exe`(Windows GUI 版 Python,無 console)。Windows 11 預設終端機是 Windows Terminal,console exe 啟動時 WT 接管 console host 並彈視窗,即使 vbs `Window=0` (SW_HIDE) 也擋不住。pythonw 根本沒 console 從源頭避開,master 啟動完全靜默。 |
| v1.10.130 | 2026-05-02 | **REFACTOR: 自動啟動從 schtasks 連根拔起,改用 Windows Startup folder shortcut**。今天一整天的 Session 0 / picker 問題追到底,根因是 schtasks `/sc onlogon /it` 任務從 Session 0 觸發時 Windows silent 拒絕(SCHED_S_TASK_HAS_NOT_RUN),而 master 一旦進 Session 0 就再也回不來,tkinter picker 看不到。Win32 `CreateProcessWithTokenW` 可以從 Session 0 admin user 把 cmd / notepad spawn 到 Session 1,但 GUI runtime 的 DLL init(tkinter / WinForms / VBS Shell.BrowseForFolder)在 escape session 都過不去,沒辦法在 Session 0 搶救 picker。連根的解法:**徹底放棄 schtasks,改用 Windows Startup folder shortcut**。Startup folder 的 .lnk 由使用者 Session 1 的 explorer.exe 在 logon 時觸發,blame chain 永遠在 Session 1,master 永遠 Session 1,picker 永遠工作。改動:(1) `Install_or_Update.bat` 安裝末段拿掉 `schtasks /create /tn OriginsunAgent`,改建 Startup folder shortcut + 順手 delete 舊 OriginsunBoot/OriginsunAgent 任務。(2) `Fix_Task.bat` 改成「migrate to Startup folder」工具:UAC 自我提權 → kill 舊 master → 刪 schtasks → 建 Startup shortcut → 啟動 master。(3) `bootstrap.ps1`(master `/bootstrap.ps1` 動態服務)末段加 Startup shortcut 建立 + schtasks 清理。(4) `download_updater`(`/download_updater` 一鍵 .bat)同步。(5) 既有 master 端 `OriginsunBoot` 任務刪除,Startup folder shortcut 建立,從此一勞永逸。**順帶搭船**:`fix(picker): Session 0 detect + fail-fast`(049485a)— 即使將來不知為何 master 又進 Session 0,picker endpoint 0.028 秒回 friendly error,前端 alert 訊息告知使用者「請從桌面雙擊 start_hidden.vbs 重啟」,不會再卡 120 秒 timeout 默默失敗。 |
| v1.10.129 | 2026-05-02 | **REFACTOR: start_hidden.vbs 委派到 core/process_spawn,冷啟動也走乾淨路徑**。v1.10.128 把重啟邏輯統一進 core/process_spawn,但只覆蓋 RESTART endpoint;agent 冷啟動(OriginsunBoot onlogon / bootstrap.ps1 末段 / 桌面捷徑)還是走舊 vbs 的 cmd /c chain。實際踩到的雷:bootstrap.ps1 推到 process-死 agent 報 [OK] Agent started 但 port 8000 沒人接、uvicorn_err.log 不存在 — vbs 那條 cmd /c "...uvicorn..." > log 從來沒實際跑成。常見成因:(1) cmd /c "cd /d 'C:\path\' && ..." 的 \" 在 cmd parser 被當逃脫;(2) `taskkill /F /IM python.exe` 把所有 python 砍光(不只 8000 上的);(3) WshShell.Run cmd, 0, False async 一返回就放手,出錯沒人接。**修法**:vbs 從 47 行壓到 ~30 行,只負責偵測 .venv/python_embed/system Python 路徑,然後 WshShell.Run 直接 CreateProcess 起 `core/process_spawn.py --restart --ota --wait 0`。process_spawn 已經處理 kill_port(只殺 8000) / rotate_log / update_agent.py / Popen + DETACHED_PROCESS 起 uvicorn(完全脫離 console)。3 個 caller 全部受惠:OriginsunBoot 排程 / bootstrap.ps1 末段 / 桌面捷徑。實機驗證(在 process-死 agent 上手動跑 `python -m core.process_spawn --restart --wait 0`):備檔電腦 192.168.1.120 + 配音室黑 192.168.1.107 都救活成功。**順帶搭船**:`scripts/revive_agents.cmd` + `scripts/revive_dead_agents.cmd` + `scripts/revive_dead_agents.ps1` ops scripts(純維運工具,不影響 production code)。 |
| v1.10.128 | 2026-05-02 | **REFACTOR: 重啟系統砍掉重做 — 統一用 `core/process_spawn.py` 直接 detached spawn，不再走 BAT/schtasks/vbs**。v1.10.122-127 連 6 次 hotfix 都在 BAT/schtasks/vbs/cmd /c 鏈裡修補,每修一層踩到下一層暗礁:vbs 在 detached 環境 hang → schtasks /run 從 Session 0 silent fail (267011) → DETACHED_PROCESS 切 console 讓 BAT timeout/move silent fail → start /B 共用 console BAT 結束 console 銷毀 CTRL_CLOSE_EVENT 殺 uvicorn → Intel Fortran runtime forrtl(200) abort。每次補完當下能重啟,但幾分鐘後 master 又會莫名死。**重做思路**:uvicorn 是 server (沒 GUI/stdin/stdout 都進 log),根本不需 console 也不需 user session。直接 `subprocess.Popen` 帶 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` + `DEVNULL` handles,完全跳過 BAT/cmd/schtasks/vbs。**新檔 [`core/process_spawn.py`](core/process_spawn.py)** (~190 行):find_python / kill_port / rotate_log / spawn_uvicorn_detached / trigger_detached_restart + CLI helper (`python -m core.process_spawn --restart [--ota]`)。**4 處重啟程式碼**統一委派:`_do_update_restart` (~80→5 行) / `admin_restart` (~30→5 行) / `system_restart` (~50→5 行) / `_launch_in_user_session` (50 行整個刪)。**Session 取捨**:新 master 繼承呼叫者 session — production 從桌面 vbs/OriginsunBoot 啟動是 Session 1,新 master 也是 Session 1,picker 正常;test 環境 (Claude Code Session 0) spawn 出來也 Session 0,HTTP 服務正常但 picker 不可見 (測試環境固有限制)。`main.py` SelfHeal 那條獨立路徑保留,目的不同 (修救卡 Session 0 的 production agent)。**驗證**(Session 0 環境,production worst case):TEST 1 PID 38384→39288 (8 秒上線, response 58ms 立即回, 30s+ 穩定存活);TEST 2 PID 39288→36892 (8 秒上線, 90s+ 穩定存活)。連續兩次重啟全成功,沒有「幾分鐘後莫名死掉」舊症狀。**順帶搭船**:(1) `publish_update.py` 端點順序對調,先打 `/internal/restart` (正確版) fallback 才 `/system/restart` (legacy duplicate)。(2) `routers/api_agents.py:proxy_agent_health` urllib timeout 4s→1.5s — LAN 健康 agent <100ms 就回,1.5s 對慢 agent 仍有餘裕,但讓死 agent 占用瀏覽器並行連線時間少 2.5s,master 自我 poll 不再排隊延遲被誤標 slow (橘燈)。(3) `frontend/tabs/projects/agent-cards.js:_startPolling` 第一次 poll 加 0-2s 隨機 jitter,避免初始載入時 N 台 agent 同時打導致瀏覽器 6-per-origin 連線上限飽和。 |
| v1.10.127 | 2026-05-02 | **HOTFIX: SelfHeal helper Popen 拿掉 DETACHED_PROCESS**。延續 v1.10.126 — helper 寫了 BAT 但 cmd /c BAT 沒實際拉起 uvicorn。根因：`DETACHED_PROCESS` flag 把 child cmd 從 console 切斷，BAT 內 `timeout` 等指令需要 console handle 才能跑，沒就 silent fail；其餘指令也跟著失常。改用 `CREATE_NO_WINDOW` only + DEVNULL stdin/stdout/stderr，cmd 仍隱形但保留必要 handles。實機驗證：直接從 PowerShell `Start-Process cmd ... -CreateNoWindow` 跑同一個 BAT 能拉起 uvicorn，DETACHED_PROCESS 跑就死。 |
| v1.10.126 | 2026-05-02 | **HOTFIX: /publish 重啟流程 + agent OTA + SelfHeal helper 全部跳過 start_hidden.vbs，BAT 直接 `start "" /B uvicorn`**。診斷收集：v1.10.122-125 publish 重啟測試一致失敗 — vbs 收到 BAT 的 `wscript "vbs"` 後跑完 update_agent.py、log rotation 都成功，但最後 `WshShell.Run cmd, 0, False` for uvicorn 看似 return 0，cmd /c 子程序卻沒實際啟動 uvicorn（process 不在、port 8000 不開、log 0 byte）。手動 `wscript vbs` 在 bash 環境下做同樣事情卻成功，差別在 BAT 是從 `schtasks /run /it` 觸發、無 console 的 detached 環境。改法：(1) `routers/api_system.py:system_restart`（/publish 用）BAT 拿掉 `wscript "{vbs}"` 直接 `start "" /B "{py}" -m uvicorn ... > out.log 2> err.log`；(2) `routers/api_ota.py:_do_update_restart`（agent OTA 用）同樣處理；(3) `routers/api_ota.py:admin_restart` 同樣處理；(4) `main.py:_self_heal_scheduled_task` helper 改寫 temp BAT 檔再 `cmd /c BAT`，避免 `subprocess.list2cmdline` 把路徑 `"D:\..."` 轉成 `\"D:\...\"` 餵給 cmd（cmd 不認 `\"` 反而當字面值使路徑變壞）。BAT 都加 log rotation（rename `.log` → `.log.bak`）保留前次 traceback。副作用：start_hidden.vbs 變成「只在使用者雙擊 / 桌面捷徑啟動」時用，自動化路徑全走 BAT。 |
| v1.10.125 | 2026-05-02 | **HOTFIX(main.py): SelfHeal helper 改直接 spawn uvicorn，跳過 vbs/update_agent.py 整段**。v1.10.123 的 wscript 直 spawn 在實機跑 v1.10.124 publish 時還是失敗：DETACHED_PROCESS + CREATE_NO_WINDOW 環境下 spawn 出來的 wscript 會在 vbs 裡 hang 住（觀察到 30+ 秒 wscript zombie，沒 child process、port 8000 永遠不開）。猜測是無 console 的 detached 環境讓 vbs 的 `WshShell.Run "cmd /c ...", 0, True` 同步等待機制壞掉。修法：helper 改直接 `cmd /c "cd /d ... & python -m uvicorn ... > out.log 2> err.log"`，徹底跳過 vbs 跟 update_agent.py。副作用是 SelfHeal 復活路徑不再走 OTA，但這本來也不該 — OTA 已經在最初的 BAT/vbs chain 跑過了，這裡只是 kill loop 復活。 |
| v1.10.124 | 2026-05-02 | **No-op publish — 驗證 v1.10.123 的 SelfHeal fix 在實際 /publish 重啟流程下能自動復活**（marker idempotency lock + helper 改 wscript 直接 spawn 兩段 fix 是否真的閉環）。 |
| v1.10.123 | 2026-05-02 | **HOTFIX(main.py): SelfHeal helper 用直接 wscript spawn 取代 schtasks /run**。v1.10.122 加了 idempotency lock 但忽略了一件事：`/sc onlogon` 任務即使沒帶 `/it`，Windows 仍把 Logon Mode 標記成「Interactive only」，從 Session 0 用 `schtasks /run /tn OriginsunBoot` 啟不動 — 任務 Last Run 永遠停在 1999/11/30 placeholder、Last Result 267011 (`SCHED_S_TASK_HAS_NOT_RUN`)。**症狀**：v1.10.122 publish 後 master 第一次重啟 SelfHeal 殺自己後，helper 試圖 `schtasks /run` 復活任務，但任務沒真的跑 → master 永遠 stuck。修法：(a) helper 改用 `wscript.exe "{vbs_path}"` 直接 spawn — bypass schtasks 整個 Interactive 守門；(b) 拿掉 v1.10.122 的 `/it` flag（沒解決問題反而把任務鎖在 Interactive only）；(c) doc 補釋為什麼不用 schtasks /run + 用直接 spawn 的副作用（pickers 在 Session 0 仍壞，要等 user logout/login 由 onlogon trigger 拉回 Session 1）。 |
| v1.10.122 | 2026-05-02 | **CRITICAL fix(main.py): SelfHeal kill loop 弄死 master + 全部 9 台 agent**。根因：[main.py:215](main.py#L215) `_self_heal_scheduled_task()` 在模組 import 時無條件跑，當 uvicorn 在 Session 0 啟動（OTA BAT chain / `os._exit` 後 spawn 不一定能拉到 interactive session），SelfHeal 會 (1) 重新註冊 OriginsunBoot 任務（原版**沒帶 `/it`**，降級成非 interactive），(2) 排定 helper 4 秒後 `taskkill /f /pid {pid}` 殺自己，(3) helper 接著 `schtasks /run` 啟新 process。新 process 又進 Session 0 → 又被殺 → 無限迴圈，uvicorn 永遠卡在 `Waiting for application startup.`。觸發場景：v1.10.121 /publish 推送 OTA 給 7 台 agent，全部死光；master 自己也因 `os._exit` chain 重啟落入 Session 0 同樣症狀。修法：(a) 加 5 分鐘 idempotency lock（`%TEMP%\originsun_selfheal.lock`），第二次進 SelfHeal 時若 marker mtime <300s 直接 skip，讓 uvicorn 跑完 startup；(b) `schtasks /create` 補上 `/it` flag 不再降級 Interactive-only 屬性。副作用：第一次重啟後 5 分鐘內若再進 Session 0，Picker 仍是壞的（原本 SelfHeal 想修這個），但比 master 整個死掉好太多；5 分鐘後 marker 過期會再嘗試一次。**順帶搭船**：(1) seo_runner 重構 `_call_claude` 回 `(stdout, error_detail)` tuple — admin toast 直接顯示「不在 PATH / timeout / exit=N stderr=...」而非通用「無回應或失敗」；timeout 90→180s buffer 給網路抖動；偵測 NotImplementedError / OSError 分別回中文診斷訊息。(2) admin_seo 新增 `/internal/seo/run` + `/internal/seo/projects/{id}/run` 兩個 forward endpoint（X-Internal-Key 認證）— NAS website-api container 沒裝 claude.exe，把 admin 請求 relay 給 master 跑。(3) admin_works 新增 `DELETE /works/{id}/if-skeleton` — overlay 關閉時前端呼叫，名字仍是 sentinel `（未命名作品）` 且 public_title/youtube_id/description/credits 全空、showcase row 也沒填內容才刪，避免「開了又關」留垃圾紀錄。(4) `_DEFAULT_WORK_STATUS` 從「進行中」改「已結案」— 作品集上架的本來就是已完成成品。(5) seo_runner 加 `import os`（之前漏的），主控側清掉 deprecated `tests/reports/` 的 `.lock` 殘骸。 |
| v1.10.121 | 2026-05-02 | **作品集對外網站 SEO 大升級 + AI SEO pipeline + 多項 UX 修整 + bugfix**：(1) **作品級 SEO 表 `website_project_seo`**（1:1 對應 crm_projects）— 含 `seo_title / seo_description / keywords / canonical_url / narrative_long / key_facts / faqs / needs_ai_review / ai_review_notes`。Pydantic schemas + 4 admin endpoints（audit / draft / PATCH / approve）+ token-scope 編輯（PM 從 showcase-edit 可改 SEO 覆寫 + 觸發 AI 重生）。注入到 `[slug].astro`（FAQPage schema + canonical override + seo_description fallback chain）+ `[slug].md` 鏡像 + JSON-LD VideoObject canonical。(2) **AI SEO pipeline 排程系統**（`services/website/seo_runner.py` 新檔）— `claude --print` headless subprocess 整合（吃使用者 Max 訂閱額度、無需 API key）；`audit → draft → PATCH` 自動化；croniter 排程 loop（每 60s 檢查 cron，預設 `0 3 * * *` 主機本地時區）；`_run_lock` 序列化避免併發；3 連敗門檻自動寫 `ai_review_notes`；server-side cron 422 驗證；single-work 直接執行（showcase-edit「立即執行此作品」按鈕）。Admin Tab 新「🤖 AI SEO 排程」卡片 + 預覽 dry-run + 立即執行。第一輪實機跑 5 件作品全部生成完整 SEO 內容。(3) **showcase-edit UI 重組**：3 個 SEO `<details>`（301 轉址 + SEO 覆寫 + AI SEO 規劃）合併成單一「SEO」section，移到「網站連結」之下。(4) **作品詳情頁紅字標籤**：客戶名 + 分類合併成單行紅色 `DOCUMENTARY · 客戶名` 在 H1 上方；key_facts/faqs HTML 渲染移除（純給 AI schema/.md 用，視覺端乾淨）。(5) **portfolio 改作品齊左、客戶齊右**（之前是反向）。(6) **三處標題即時同步**：showcase-edit input → 頂部 page-title → admin iframe panel header（postMessage live-sync）。admin works 表格標題欄 bug：`w.public_title || w.name` → 改用 `w.title`（API 已正確投影 `public_title || name`，原本永遠 fallback 到 CRM 內部 name）。admin works 公開/精選/noindex 改唯讀彩色 badge（藍底白勾 / 空框）統一灰階 chip。(7) **`_old_slug_to_url` strip 網域 normalize**（鏡 blog `_normalize_old_url`）— PM 貼整段 `https://www.originsun-studio.com/portfolio/xxx/` 自動 strip 成 `/portfolio/xxx`，DB 兩筆既存污染資料一次性清乾淨。textarea「一行一個」UX 取代原本 chip + add button 介面。(8) **Tags 篩選 UI 升級**（`/works`）：TAGS 列加「全部」chip、預設狀態反白；統一色系（黑底白字 active / 淺灰 inactive，移除 4 階灰度）；切軸時 runtime patcher 重寫 chip href 保留另一軸 query（cross-axis preservation）。(9) **`_run_subprocess` 加 timeout**（build=600s, scp=120s, ssh-clean=30s）— 解決「state 卡 running」黑洞；`asyncio.create_task.add_done_callback` 攔 unhandled exception 寫進 `_REBUILD_STATUS["error"]`。(10) **`canonical_url` URL 驗證**：service 層 `_coerce_canonical_url` regex 檢查 http(s):// + host，非法字串靜默丟掉避免炸 SEO。(11) **credits_mode 預設 'text'**（之前 'block'）— PM 從劇組拿到的就是文字稿，比結構化容易上手。(12) **showcase.html legacy public viewer 完全移除**（被 `originsun-studio.com/works/{slug}` 取代）— 1 個 HTML + `/api/v1/crm/public/showcase/{id}` endpoint + 相關測試 class 清掉。(13) **`start_hidden.vbs` 永久 log redirection** — `uvicorn_out.log` / `uvicorn_err.log` rotate 到 `.bak`，崩潰能抓 traceback。(14) ⭐ **bugfix(default tags resurrect)**：`_SEED_DEFAULT_TAGS` ON CONFLICT DO NOTHING 只擋「row 還在」的 INSERT。Admin DELETE 後 row 不存在 → 沒 conflict → INSERT 復活。修法：加 `WHERE NOT EXISTS (SELECT FROM website_settings WHERE key='defaults.tags_seeded')` 子查詢 + `_MARK_DEFAULT_TAGS_SEEDED` 同 batch commit 標旗標。已 regression test 驗證。(15) **小型 simplify**：`_SHOWCASE_EDIT_PATCHABLE_FIELDS` constant、token endpoint 用 frozenset allowlist、admin runner toast 顯示 first error detail、cron 用主機本地時區（避免 UTC/Local 混用）。 |
| v1.10.120 | 2026-05-01 | **演職員系統 3 層 + showcase autocomplete + 作品 SEO 301 + 1 個 production bug 修復 + /test skill 37 case 全綠**：(1) ⭐ **CRITICAL fix(services/website/credit_service.py): `crm_project_showcase` 表名拼錯（plural ↔ singular）**。SQL 5 處查 `crm_project_showcases`（plural）但實際表叫 `crm_project_showcase`（singular），所有查詢都炸 `relation does not exist` → `except` 靜默 swallow → admin UI 角色 usage_count 永遠 0、`cleanup_staff_id_from_credits` 永遠回 0、`find_projects_by_staff` 永遠空、`_MIGRATE_FLAT_CREDITS_SHOWCASE` 因 `to_regclass` 守門被擋從未執行。同樣修 `db/migrations_website.py` 1 處 + `tests/fixtures/test_data.py` 1 處 + 4 處註解。(2) **演職員管理新模組（3 層）**：`db/models_website/credit_role.py` + `credit_template.py` 兩張新表 — 中英對照公版職位庫 + 預設職位組合模板；`services/website/credit_service.py` CRUD + `_compute_role_usage` 反查 + `cleanup_staff_id_from_credits` cascade；`routers/website/admin_credits.py` admin endpoint；`frontend/tabs/website/subviews/credits.js` 新 admin subview（拖拉排序 + inline 編輯）。(3) **showcase-edit autocomplete combobox**：`frontend/showcase-edit.html` `.credit-name-input` focus → `/staff_search` debounce 200ms → `.credit-name-dropdown` 浮出 staff item 含綠/白點（resume_visible 區分）+ avatar/role/daily_rate；點 item → `_selectStaff` 寫 staff_id + name + resume_url 到 credits[].entries[]。打陌生名字 → 「+ 建立新人員 X」item → `_openQuickAddModal` → `/staff_quick_add` → 自動 `_selectStaff` 把新人連結到 entry。`_searchSeq` race protection 防慢網路舊 fetch 覆蓋新 dropdown。`routers/api_crm.py` 新增 token-scope 兩個 endpoint：`/staff_search`（top-10 + LIKE filter）+ `/staff_quick_add`（status='專案' resume_visible=False created_via='showcase_edit' created_for_project_id=token.sub）。(4) **CRM 人力資源 Tab 擴充**：`frontend/tabs/crm/crm-staff.js` `_loadStaffProjects` 新形狀回 `projects[]` + `credit_only_projects[]` 雙欄；render `.crm-stat-chips` × 2-4：「派工 N 件」/「累計費用 $X」/「⚠ 派工未掛 credit」warn variant / 「外部演員（無派工）」info variant（staff 出現在 showcase.credits 但無派工）。「人員資訊」tab 偵測 `created_via='showcase_edit' + phone+email 空` 顯示 `.crm-info-box` 「📥 由作品編輯時快速建立」hint。(5) **作品 SEO 301 雙保險（鏡 blog 機制）**：`db/models.py:CrmProject` 加 `public_old_slugs JSONB` + `public_cover_url` + `public_noindex`；`services/website/project_service.py` `append_old_slug_if_changed()` 切換 slug 時 detect + dedup + numeric skip + 自循環防護；`list_redirects()` 聚合 public 作品 old_slugs；`/api/website/redirects` merge posts + works；`routers/website/admin_posts.py` 新 `/admin/redirects/sync` 統一 endpoint；Astro `[slug].astro` `sameAs` 從 work.old_urls + `[slug].md.ts` 加「曾用 URL」+ `llms-full.txt.ts` 加「URL 變更紀錄」+ `lib/seo.ts` videoObject/webPage 加 sameAs；`pages/works/index.astro` `?tag=` 第二軸過濾（CategoryFilter `queryKey` prop 必填）。(6) **block credits 結構升級**：[{role_id, name_zh, name_en, entries: [{duty, name, staff_id, resume_url}]}] 取代 flat array；`website/src/lib/credits.ts:normalizeCredits()` 對偶 3 種格式（block list / flat array / dict）；showcase.html 攤平邏輯 `name_zh + duty` → "演員 / 主演" label。(7) **/test skill 完整實作（`.claude/commands/test.md`）**：Phase 2 smoke 19 case（`tests/test_simplify_helpers.py`）+ Phase 3 HTTP 10 case（`tests/test_test_skill_http.py`：CRM staff CRUD / showcase-edit token 5 case / redirects 2 case）+ Phase 4 UI Playwright 8 case（`tests/test_test_skill_ui.py`：CRM staff fresh empty + 外部演員 chip / showcase-edit dropdown 4 case / works-admin hint / showcase-public role label）。**37/37 全綠**。技術重點：admin JWT `add_init_script` 注入 localStorage 跳過 RBAC、`wait_for_function` poll DOM 避開 placeholder、`dispatch_event("click")` 跳過 actionability check。(8) **fix(main.py): `_self_heal_scheduled_task` 加 env var 守門**。原本 Session 0（pytest 不是互動 session）會偵測 schtasks 重註冊 + 4 秒後 taskkill 自己 → 測試 fixture 啟 uvicorn 全部 timeout。新增 `ORIGINSUN_DISABLE_SELFHEAL=1` env var 跳過，`tests/conftest.py` + 兩個新測試 fixture 都帶這個 env var。(9) **website admin 重構**：`services/website/_crud_base.py` 通用 list/create/update/delete + pre_write hook（取代 12 處複製貼上的泛型 CRUD），`seo_service.py` + `credit_service.py` 走 `_crud_base`；`frontend/tabs/website/website-utils.js` `emptyRow(colspan, msg)` + `emptyHint(msg, opts)` 取代 10+ 處散落 inline HTML。 |
| v1.10.119 | 2026-04-30 | **4 個 blog admin Tab 連環修復 + 文章對外寬度調寬**：(1) **fix(seo-audit): redirect 頁不該套內容頁規則**（CRITICAL — 擋住所有 rebuild）。post #11 設了 old_url `/workflow/article-11`，Astro `redirects` config 自動為這個舊 URL 生 `dist/workflow/article-11/index.html` 軟 301 跳轉頁（純 `<meta http-equiv="refresh">` + canonical）。但 [`website/integrations/seo-audit.mjs`](website/integrations/seo-audit.mjs) 對它套了內容頁全套規則 — 要 description / schema.org JSON-LD / `<h1>` — redirect 頁本來就沒這些 → fail → npm build exit=1 → rebuild 永遠卡 error → 「立即發布」按鈕怎麼按都沒用。修法：偵測 HTML 含 `<meta http-equiv="refresh">` 直接跳過（軟 301 redirect 頁是純跳轉、不該套內容頁規則）。(2) **fix(blog): 上傳圖片預覽載入失敗** — upload endpoint 回傳相對路徑 `/uploads/posts/{id}/{name}.webp`，但 admin Tab 從 master Web UI（master:8000）開啟，瀏覽器 `<img src="/uploads/...">` 會解析成 master origin、master 沒這個 static mount → 404。新增 helper `_resolveImageUrl(url)` 把相對路徑 prepend `getApiBase()`（NAS API base），完整 http(s)/data/blob URL 原樣 passthrough。4 處 image render 全部走 helper：image block edit form preview / image block visual preview / 封面圖 thumb（background-image + img）/ 預覽 pane 封面圖。(3) **fix(blog): 新增 block 後畫面沒更新** — `_refreshBlocks()` 找 `#post-blocks` 容器，但空 body 時 `_visualModeView` 不會生這個容器（render 的是「尚無內文」純文字 div）→ 第一次 append 找不到 host → 整段更新被跳過 → header 計數變 1 但畫面卡在「尚無內文」。修法：往父層 `#post-blocks-host`（一定存在）重畫整個 `_visualModeView` 結果，自動處理空↔有兩種狀態切換。(4) **feat(blog): 預覽 pane 預設開啟** — `_previewVisible` 從 false 改 true，編輯既有文章 Modal 一打開預覽就同步顯示在右側（新文章仍走 isNew 守門關閉預覽）。(5) **feat(news): 文章對外寬度從 680px 調寬到 max-w-4xl（896px）** — 之前 `max-w-[680px]` body 對 1920px viewport 只佔 35%，視覺太窄；bump 到 4xl + 把 `WIDTH_CLASS.wide` 同步往上推到 5xl（1024px）保持階梯關係。涵蓋 7 處硬編碼 + WIDTH_CLASS 表。 |
| v1.10.118 | 2026-04-30 | **3 條 critical fix + blog Modal UX 升級**：(1) **fix(transcode): proxy 多軌音訊被砍 + advanced concat mono/stereo 對不上**（使用者場景：0430 備檔 Card_A 的 MXF 多軌單聲道來源 `260422-B-Roll`、`260422-太空人` proxy 後只剩左聲道，連檔失敗）。[`core_engine.py`](core_engine.py) 新增 `_probe_audio_stream_count()` + `_build_proxy_transcode_cmd()` 兩個 staticmethod helper，三條策略（0/1/N audio streams）：0 streams 完全跳過 audio args、1 stream `aresample+aformat=stereo` upsample、N streams `amerge=inputs=N` 合併。全部走 `-c:a aac 192k`（amerge 無法 `-c:a copy`，統一形狀讓 concat 不用特例）。`_run_concat_advanced` 把 `[i:a]anull` 改成 `aresample=48000,aformat=...:channel_layouts=stereo` 跟 standard concat 路徑同 chain。新增 `tests/unit/test_transcode_multi_track_audio.py` 9 case（0/1/N streams + AAC compat + 5 種 container `.mp4 / .mxf / .mov / .mts / .r3d` 鎖死「container 無關」）。(2) **fix(website): register_crud 把 body 誤判為 query → admin 編輯文章存檔 422**（使用者場景：Phase B/C admin Tab 開「編輯文章 #11」改完按 💾 儲存 → 紅色 toast `[{"type":"missing","loc":["query","req"]}]`）。根因：[`routers/website/_common.py`](routers/website/_common.py) 頂部 `from __future__ import annotations` 讓所有型別 annotation 變字串，`register_crud` 寫 `req: create_schema` 是閉包變數不在 module globals → FastAPI `get_type_hints()` resolve 失敗 → fallback 把 req 當 query parameter → 422。修法：拿掉 type annotation 用 `req=Body(...)` 鎖 body 語意，定義後 `__annotations__["req"] = create_schema` 後設實際 class 跳過字串 lookup。涵蓋所有走 register_crud 的 admin endpoint（posts / post_categories / SEO 7 cards / services）。新增 `tests/unit/test_register_crud_body_param.py` 3 case（PUT/POST + Dependant 分類）— revert HEAD 跑同 test 3/3 fail 證明擋得住。**這個 bug 從 v1.10.112 Phase A 引進 register_crud 起就存在**，前 5 版沒人撞到只因 admin Tab 還在用 Notion 同步沒按過儲存；Phase B/C UI 上線後第一次按儲存就炸。(3) **feat(blog): Modal UX 升級** — `frontend/tabs/website/subviews/blog.js` +723/-89，Modal 內 input/textarea/select 散裝樣式之前掉回瀏覽器預設 → 已填值跟 placeholder 顏色幾乎一樣難分辨；scope 到 `#post-modal` 統一深色 + 焦點藍框 + placeholder 灰斜體；新增「純文字模式」開關（`_textMode` flag）讓 admin 可切到純 markdown 編輯（block 編輯器外的 raw 模式）。103/103 unit tests passed。 |
| v1.10.117 | 2026-04-30 | **fix(blog): block 編輯內容存檔遺失 + 新文章 UX 修正**：(1) **CRITICAL BUG FIX** — [`frontend/tabs/website/subviews/blog.js:_readModalForm()`](frontend/tabs/website/subviews/blog.js) 漏了把 `body` 加進 payload。block 編輯器是即時更新 `_editingPost.body[i].field` 的記憶體陣列，不在 DOM form 裡，導致按「💾 儲存」時 body 整個被忽略——使用者排好的 paragraph/heading/image/video/quote/list 全部丟失，只有 metadata 存進去。新增 `body: _editingPost?.body || []` 一行修好（payload 直接帶 PostBlock 陣列）。(2) 拿掉新文章的 `_modalEmptyBlocksHint()` 「先存再編」佔位 — 現在新文章直接顯示 block 編輯器，不需先儲存草稿才能寫內容（一鍵寫完一鍵發）。(3) 新文章 Modal 也顯示 SEO 健康度 widget（之前 `isNew` guard 把它擋掉）— admin 邊寫邊看 SEO 評分而不是發完才知道。 |
| v1.10.116 | 2026-04-30 | **Phase C 完整收尾 + SEO 移轉手冊（Phase A-C 全收官）**：(1) [`frontend/tabs/website/subviews/blog.js`](frontend/tabs/website/subviews/blog.js) 把 Phase B/C 兩段疊代收成單一 1359 行完整版 — block 編輯器 6 種類型（paragraph 含 lead 段勾選 / heading H2-H3 切換 / image 上傳+URL 雙模式含 alt+caption+width 三層 / video YouTube 11-char ID 自動解析+縮圖即時預覽 / quote 引用+作者 / list 有序無序切換含 items 動態增減），每個 block 配 [↑↓✕] + 「+ 在此後插入 ▾」下拉；快速插入工具列 6 種一鍵加結尾；即時預覽 pane 鏡像 Astro 輸出風格、200 字以下行為 trivial 不需 debounce；SEO health widget 即時 10 條檢查（title 長度、desc 長度、og image、字數、內文圖數、img alt 完整性、author E-E-A-T、舊網址轉址、published_at、內鏈密度）彩色分數 + 問題列出；YouTube parser 接受 youtu.be / watch?v= / 11-char ID。image block 在 post 還沒存（沒 ID）時 disable 上傳 UI 防止 404。(2) [`docs/SEO_MIGRATION_GUIDE.md`](docs/SEO_MIGRATION_GUIDE.md) 9KB 跨域文章遷移完整手冊：同域 slug 變動 4 步操作（系統自動處理軟+硬 301）、跨域遷移 5 步（本系統設定 + 舊域 nginx 301 + GSC Change of Address + Bing Webmaster Tools + AI 爬蟲通知）、即時/中期/長期驗收方法（curl / Search Console / 移轉中心 widget）、8 個 FAQ（軟 vs 硬 301 / 舊網域留多久 / archived 文章 SEO / published_at 回填等）、5 個監測工具表、工程細節 path（DB schema → service → API → Astro/nginx）。連回 CLAUDE.md 規則 G + NEW_PAGE_CHECKLIST.md。**Phase A+B+C+docs 部落格 DB 系列規劃全部完成**：admin 在 Web UI 完整建構部落格、Notion 降格 seed/匯入器、SEO 移轉雙保險（軟 301 meta refresh + 硬 301 nginx）+ 完整 admin 操作手冊。 |
| v1.10.115 | 2026-04-30 | **Phase C — 部落格 Block 編輯器 + 即時預覽 + SEO 健康檢查**：[`frontend/tabs/website/subviews/blog.js`](frontend/tabs/website/subviews/blog.js) +549/-18，把 Phase B 的 metadata Modal 從「占位提示」擴成完整 block-based 編輯器。**核心改動**：(1) `openEditPost(id)` 改 async — 列表 API 不回 body 省 payload，編輯時才拉 `/admin/posts/{id}` 完整資料含 body block 陣列。(2) Modal 內加 `_modalBlockEditor()` — block 列表（heading/paragraph/list/image/youtube）支援上下移動 (`moveBlockUp/Down`)、新增、刪除；`uploadBlockImage()` 串 v1.10.114 的 `/posts/{id}/upload-image` endpoint，回傳 `/uploads/...` URL 直接塞 image block src；`parseYoutubeAndStore()` 解析 youtu.be / watch?v= / embed/ 三種格式抽 video_id 存 block。(3) **即時預覽 pane**：右側可摺疊 50% 寬度區塊（Modal width 760px ↔ 1180px 動態切換），`_renderPreview()` 把 block 陣列渲染成接近實際前台樣式的 HTML 給 admin 預覽（`_previewVisible` flag + `togglePreview()` 切換）。`_refreshPreview()` 在 block 變動後同步重渲。(4) **SEO 健康檢查**：`_modalSEOHealth()` 顯示問題列表 — `_seoChecks(p)` 跑 ~10 條規則（title 30-200 字、description 30-200 字、cover_url 必填、slug 格式、og_image fallback、noindex 警告、author 缺失等），按健康度紅/黃/綠標示；`_refreshSEOHealth()` 在 metadata 改動後即時重算。(5) `savePost(overrides = {})` 改成接 overrides，supports「💾 儲存」與「💾 儲存並發布」雙路徑（原本直接改 DOM 是 anti-pattern）。Phase C 完成後 admin 在 Web UI 就能完整建構部落格內容，不需 Notion。 |
| v1.10.114 | 2026-04-30 | **Phase C 前置：blog post 圖片上傳 endpoint（auto WebP）**：[`routers/website/admin_posts.py`](routers/website/admin_posts.py) 新增 `POST /admin/posts/{post_id}/upload-image`，給 Phase C block 編輯器內 image block 用。允許白名單 `.jpg/.jpeg/.png/.webp/.gif/.heic`，10MB 大小上限，存到 `/uploads/posts/{post_id}/<uuid12>.webp`，自動透過 PIL 轉 WebP（quality 82, method 6）— 比 JPG 小 ~30%、比 PNG 小 50-70%；Pillow 失敗時 fallback 寫原 bytes 到 `.bin`（罕見）；不檢查 post 是否 published（draft 也能上傳）；不寫 DB（純檔案），post.body 由 admin 編輯時 PUT 更新。`_save_image_as_webp()` helper 處理 RGBA/LA → RGB 透明圖通道相容、`_UPLOAD_BASE` 用 `os.path.dirname` 三層回到 project root（NAS container `/app/uploads/`、master `/share/.../Website/uploads/` 都通）。回傳 `{"url": "/uploads/posts/{id}/{name}.webp", "filename": ...}` 可直接塞 image block 的 src 欄位。 |
| v1.10.113 | 2026-04-30 | **Phase B — 部落格 admin Tab 完整 UI（DB-as-truth 編輯器）+ nginx include 補洞**：(1) [`frontend/tabs/website/subviews/blog.js`](frontend/tabs/website/subviews/blog.js) 從 258 行單一 Notion 同步面板擴成 826 行 4-tab admin（**+740 / -172**）：📰 文章 list（status/分類/關鍵字篩選 + metadata Modal CRUD + 草稿/發布/下架狀態）、📚 分類（inline table + M2M slug/label/color/sort_order/visible CRUD）、📥 從 Notion（匯入器，Phase A 後 Notion 只是 seed 不再是 truth；含「單純觸發 Rebuild 不撈 Notion」捷徑）、🌐 SEO 移轉（軟+硬 301 計數 + 強制重新同步 + 跨域遷移指引）。串接 v1.10.112 全部新 admin endpoints（`/api/website/admin/posts`、`/post_categories`、`/posts/import-notion`、`/posts/redirects/sync`、`/redirects`）。`renderLoadError` 在 404 時加 hint 提示「NAS website-api 可能跑舊版 → master 跑 /publish」（v1.10.111 自診斷模式延伸到 blog Tab）。Body block 編輯器留給 Phase C，Modal 提示「Phase C 上線後再編內容」。(2) `docker/nginx/originsun.conf` 補上 `include /etc/nginx/snippets/*.conf` directive（v1.10.112 commit body 寫了但檔案沒落實，sed 期間覆蓋）— glob pattern 避免檔案順序耦合，snippet 還沒建時 `nginx -t` 不會失敗；deployed + nginx -t pass + reload OK，硬 301 fallback 鏈完整。 |
| v1.10.112 | 2026-04-30 | **Phase A — 部落格 DB-as-truth 基建（取代 Notion-as-truth posts.json）**：把部落格從「Notion → posts.json → Astro」單向管道，改成「Notion 是匯入器、DB 是真理、admin 編輯永遠勝」雙向架構，為 Phase B/C 的 admin 編輯器、SEO 移轉中心、block 編輯器鋪路。**DB 層 3 張新表**：(1) `website_posts` — 完整 metadata + body JSONB + per-post SEO 覆寫（seo_title/seo_description/og_image_url/canonical_url/noindex/author_name/author_url/ai_allow_override）+ `old_urls JSONB` 給 SEO 301 + status enum(draft/published/archived) + notion_page_id 給 re-import match。(2) `website_post_categories` 文章分類獨立於作品分類（M2M 多選）。(3) `website_post_category_links` junction CASCADE delete。**API 層**：`services/website/post_service.py` 完整 CRUD + Notion upsert + redirects 聚合，DB 為真（`upsert_from_notion(force=False)` 預設跳過已存在），兩階段 batch（一次 SELECT 全 page_ids match + 一次 _resolve_categories 全 distinct slugs + 記憶體 counter）— 100 篇 import 從 O(2N) round-trip 砍到 O(3) total；`list_redirects()` 聚合 published+archived post.old_urls，衝突依 published_at 最新勝；`routers/website/admin_posts.py` 用 `_common.register_crud()` 標準 CRUD + import-notion + redirects/sync 端點；`_common.py` 抽出 `register_crud()` helper 給 admin_seo + admin_posts 共用。公開 endpoints：`/api/website/posts`、`/posts/{slug}`、`/post_categories`、`/redirects`。**Astro 端**：`lib/posts.ts` + `lib/categories.ts` 從 fs.readFile JSON 改 fetch API（走 crm-client._memoizeList → build 期 N 頁共用 1 次 HTTP）；`pageSchemas.newsArticle` 加 dateModified（E-E-A-T 新鮮度）+ per-post Person author；`IPost` 加 SEO 欄位；`pages/news/[slug].md.ts` markdown 鏡像給 AI 爬蟲；`llms-full.txt.ts` 加 posts 區塊（最近 30 篇）。**SEO 301 雙保險**：`astro.config.mjs` redirects field 從 `build-redirects.mjs` 預取（軟 301 meta refresh + canonical）+ docker nginx include redirects.conf（硬 301）；`publish_update.sync_redirects_to_nas()` 拉 API 產 nginx snippet → docker cp + nginx -s reload；任一條失效另一條接手。**Migration tool**：`scripts/migrate_blog_to_db.py` 一次性 posts.json/categories.json → DB（dry-run 預設、`--apply` 真寫、`--auto-publish` 改 published 預設 draft、idempotent slug 跳過、`--force` 清空重灌）。**Simplify pass**：`_post_categories` 改 `_load_category_slugs_for_posts` 避免 link 誤讀；`upsert_from_notion` 130 行抽成 3 個小函式主迴圈 ~25 行；`_next_slug` 加 `_max_slug_int()` base + bulk 用記憶體 counter。 |
| v1.10.111 | 2026-04-30 | **align 套件偵測 + 一鍵安裝 UX**：v1.10.110 升級 align 流程後，發現同事電腦（agent）跑「對齊已有文字稿」會炸 `stable-ts 套件未安裝`——因為 stable-ts + pysrt 是重 ML 依賴（會帶 torch + faster-whisper 約 50MB），歷史上（v1.10.106）刻意沒放進 `requirements_agent.txt` 避免 OTA 膨脹。但這樣每台 agent 第一次用 align 都會撞錯，使用者還要 RDP 進去手動 pip install，UX 太差。新增 (1) `GET /api/v1/jobs/align/check_deps` — 檢查 `stable_whisper` + `pysrt` 能不能 import，回 `{ok, missing: [pkg names]}`。(2) `POST /api/v1/jobs/align/install_deps` — 直接在該 agent 的 venv 跑 `pip install -U stable-ts pysrt`（subprocess + 10 分鐘 timeout），回傳 stdout/stderr 末段給前端顯示。(3) 前端 `submitAlignJob` 加 pre-flight `_ensureAlignDeps()` — 缺套件時跳 modal 給使用者三個選項：① 🔧 一鍵安裝（按下後 disable 按鈕、顯示 pip log、成功後自動繼續送出）② 📋 複製手動指令（`pip install stable-ts pysrt`）③ 取消。modal 的 install/cancel 按鈕在 install 期間都 disabled 避免雙擊；失敗時 install 按鈕變「🔄 重試」。(4) endpoint 不可達時 fallback：log 警告但允許繼續送出（讓使用者看到原始錯誤訊息）。同事不需 RDP，自己在 Web UI 點一下就能裝好。 |
| v1.10.110 | 2026-04-30 | **AI 逐字稿「對齊已有文字稿」UX 升級 + 字幕續顯模式**：(1) **align tab 模型偵測 UI 補齊**：對齊頁的「辨識模型」之前只有純 select，使用者選了沒下載過的模型要等送出才會撞錯。新增 `align_model_status_badge` + `btn_align_download_model`，鏡射「自動辨識」既有 UI；抽出 `_MODEL_UI` const + `_renderModelStatus(mode)` 共用渲染、`_broadcastModelUI()` 內部廣播給所有模式（下載狀態是全域，所有 UI 同步）；新增 `setAllModelDownloadingUI()` / `setAllModelErrorUI()` 兩個語義清楚的 API；`fetchModelStatus()` 同時刷新兩個 UI，`downloadSelectedModel(mode)` 接受 mode 參數但廣播下載中狀態到所有 UI。app.js 的 `model_download_error` handler 從 16 行 hardcoded forEach 簡化成 3 行 `window.setAllModelErrorUI()`，IDs 全部回歸 transcribe.js 的 `_MODEL_UI` 單一真相源。(2) **`hold_until_next` 字幕續顯模式（預設開啟）**：對齊產出的 SRT 之前每條字幕只顯示 alignment 算出的原始時長，停頓 2 秒就消失 2 秒；新增 Netflix 風格「每條 cue.end 延長到下一條 cue.start，最後一條延伸到 audio_duration」模式。`polish_subtitle_timeline` 加 `hold_until_next: bool = True` 參數（與 schema 預設值一致），開啟時跳過既有的 min_duration（最短 1s）+ min_gap（2 影格間隔）邏輯避免互相打架；新增 `hold_extended` stat 給品質報告。`AlignRequest` schema + `run_align_job` + `core/worker.py` 一路傳遞參數。(3) **UX 階層化**：「⏱️ 延長至下一句字幕」變成「✨ 套用打磨」的子選項（縮排 + 左 border），父打磨未勾選時子選項自動 disable + 灰化；父 hint 動態切換顯示當前生效策略（「Frame 對齊 ＋ ✓ 持續顯示模式」/「Frame 對齊 ＋ 最短 1s ＋ 2 影格間隔」/「未啟用」）。對齊完成 summary 面板的「時間優化」欄位 hold 模式時顯示「持續延長 N」取代「延長 / 補間隔」。(4) **測試**：`tests/unit/test_aligner.py` 新增 `test_hold_until_next_extends_to_neighbor` + `test_hold_until_next_off_keeps_legacy_behavior`，3 個依賴舊行為的既有測試明確傳 `hold_until_next=False`。46/46 passed。 |
| v1.10.109 | 2026-04-30 | **過濾 macOS AppleDouble 垃圾檔 + Whisper 模型跨 job 快取**：(1) **AppleDouble 過濾**：客戶從 Mac 拷來的素材每個影片旁都會殘留 `._<filename>` sidecar（macOS Finder 在非 HFS 磁碟存 metadata 的小檔），ffmpeg 開不起來，使用者 backup 任務 transcode 階段每個 `._A084C001_260410NZ.MXF` 都炸 `[!] 轉檔失敗 / Invalid data found when processing input`。新增 module-level helper `core_engine.is_junk_file(path_or_name)` 過濾 `._*` 前綴 + `Thumbs.db` / `.DS_Store` / `desktop.ini`，在 `run_backup_job` / `run_transcode_job` / `run_concat_job` / `run_verify_job` 4 處 + `core/report_job.py:_run_report_job` 1 處掃描迴圈統一用 — 備份不再把垃圾拷到 NAS、轉檔/串帶/報表/驗證不再誤觸。helper 接受 basename 或完整路徑（內部跑 `os.path.basename`），單檔分支 3 個 caller 簡化掉 inline `os.path.basename(token)` 包裝。函式從 `_is_junk_file` 改名 `is_junk_file`（無底線），因為 cross-module 從 `report_job.py` import 不該標記 private。(2) **stable_whisper 模型跨 job 快取**：`aligner.py` 加 module-level `_MODEL_CACHE: Dict[(model_size, device, compute_type), model]`，第一次 load 5-30s，後續同樣參數的對齊 job 直接命中（佇列常一連跑同模型不同影片）；`run_align_job` finally 移除 `del model`，只清 GPU transient state、保留 cache。(3) **配套 worker.py reload 移除**：`core/worker.py` 拿掉 `_run_transcribe` / `_run_align` 的 `importlib.reload(transcriber/aligner)`，reload 會把 `_MODEL_CACHE` 跟 `transcriber` 既有 lazy state 整個清掉，跟 (2) 直接打架。(4) **`.gitignore` 加 `*.rebuild_meta.json`** — Phase M 對外網站 rebuild 流程的工作檔，不該入版。 |
| v1.10.107 | 2026-04-29 | **Phase M 完整版 A 部署 + 高 ROI 6 項技術升級**：(1) **NAS website-api 容器** + Website_Nginx (8090) — admin Tab 跨機 fetch、master 關機對外 24/7 不掉、PM 在家可改作品。(2) **DB-backed website_rebuild_state** — master/NAS 共讀 singleton row，pending_count + last_success_at 不雙寫。NAS 收到 rebuild trigger 經 MASTER_RELAY_URL forward master 跑 npm（容器無 node）。(3) **/publish 自動 sync** — scp routers/services/core/db/main_website.py/config.py 到 NAS code/ + ssh docker restart website-api。(4) **DB 自動備份** — NAS cron 每日 03:30 docker exec pg_dump.gz, 30 天 retention, QNAP 持久化 crontab。(5) **Sitemap.xml + robots.txt** — @astrojs/sitemap plugin、自動 emit sitemap-index.xml + 12 URLs。(6) **Per-work SEO meta** — description 截 155 字、og:site_name、og_image fallback chain（work thumb → site default）。(7) **WebP 自動轉檔** — 4 個 showcase upload endpoint 統一走 _save_image_as_webp，省 ~30-60% 檔案大小。(8) **Pagefind 全文搜尋** — npm build 內建 pagefind-cli，647KB index，/works 加搜尋 widget。(9) **slug fallback 改用 public_number** — 1, 2, 3...第一次 publish 時 auto-assign max+1，永久綁定避免 republish 改編號破壞 SEO 連結；既有 1 件 public 作品 backfill = 1。(10) **Astro stale URL 清理** — scp 前 ssh 清 NAS /works/* /news/* 子目錄，避免改 slug 後舊 URL 殘留。(11) **JWT secret env var 優先** + module cache（避免每次 verify 讀檔）+ 拿掉 hardcoded fallback secret raise instead。(12) **DATABASE_URL env var 優先** — NAS container 透過 .env 注入，不用 mount settings.json。(13) **Dockerfile -200MB image size** — 拿掉 gcc（asyncpg / Pillow 都有 prebuilt wheel）。(14) **nginx config 整理** — `^~ /_astro/` prefix match、`add_header always`、提升 root 到 server 層。 |
| v1.10.106 | 2026-04-28 | **再修 OTA：清掉 requirements_agent.txt 殘留 stale auto-detect**：v1.10.105 推完仍 rollback。讀 agent `/api/v1/update_status` 看到真正錯誤是 `pip install failed: stable_whisper`（PyPI 上不存在的 package）。原因：`requirements_agent.txt` 在 v1.10.104 publish 時被 `publish_update.py` 的 auto-detect 機制看到 WIP `aligner.py`/`worker.py` 的 import，把 `aligner`、`pysrt`、`stable_whisper` 三條塞進去；v1.10.105 只 stash WIP code、沒清 requirements，OTA agent pip install 必然失敗 → rollback。本版手動清掉那 3 行 stale entries（confirm scan_imports 重跑沒任何新 import 會被加回）。教訓：`publish_update.py` auto-append 是**單向**的——一旦寫入就不會自動移除，如果 WIP 改動後又退掉（git stash / revert），requirements_agent.txt 殘留會炸 OTA。發版前要驗 agent `/api/v1/update_status` 確認真正失敗訊息，不要只看 master 端 OK。 |
| v1.10.105 | 2026-04-28 | **重新打包 v1.10.104 修 OTA preflight 失敗**：v1.10.104 推送 9 台 agent 全部 rollback 到 1.10.103。原因：build 時把工作樹未 commit 的 transcribe 對齊 WIP（`core/worker.py` 多了 `import aligner`、`routers/api_transcribe.py` 多了 `from core.schemas import AlignRequest`、新檔 `aligner.py` + `core/whisper_helpers.py`）打進 OTA ZIP，但 `aligner.py` 不在 `ota_manifest.AGENT_FILES` 白名單裡 → 進不了 ZIP → agent 解壓後 preflight 嘗試 `import aligner` 失敗 → rollback。本次先 `git stash` 把 transcribe WIP 隔離，重 publish 確保 OTA ZIP 純淨：v1.10.104 的 admin_works.py / works.js / modal-styles.js 修法照舊保留。教訓：build 機制 = 打包工作樹（不是 git HEAD），所以未 commit 的 WIP 會悄悄混進去；以後 publish 前用 `git status` 嚴格檢查未 commit 的 root-level .py 是否會被 import 但沒進 AGENT_FILES。 |
| v1.10.104 | 2026-04-28 | **官網作品 modal 客戶下拉修：500 + searchable 包裝**：使用者反映「新增作品」modal 客戶下拉沒任何資料。雙層 bug 互相遮蔽：(1) **後端 500** — `routers/website/admin_works.py:lookup_clients` SELECT 引用 `Client.name`，但 `db/models.py` 的 Client 只有 `short_name` + `full_name` 沒 `name`，runtime AttributeError → FastAPI 回 500。改成 `Client.full_name`（SELECT + ORDER BY 兩處）。(2) **前端 silent catch** — `works.js` 的 `try { ... } catch {}` 把 500 吞掉設 `clients = []`，UI 完全沒提示，使用者只看到空 dropdown 沒法判斷哪壞。catch 加 `console.warn` 留痕跡。(3) **順手把 client 下拉變 searchable** — `_createFormModal` 加 `searchable: true` flag，渲染後自動套 `searchableSelect()` 包裝（CSS 已從全域 `crm.css` 載入），`works.js` 的 client_id 欄位啟用。順帶寫 2 個 unit test 釘死 Client 用到的 column 名（`tests/unit/test_admin_works_clients_lookup.py`），下次有人改欄位 CI 直接擋。 |
| v1.10.103 | 2026-04-28 | **CRM 收據資料夾下放到子表 + NAS browser z-index 修復**：(1) **資料模型遷移** — `receipt_path` 從專案層級（`crm_projects.receipt_path`）下放到子表層級（`crm_project_cost_groups.receipt_path`），實務上每次拍攝（每張子表）有自己的收據歸檔資料夾才合理。啟動時三步驟 idempotent migration：ADD `cost_groups.receipt_path` → UPDATE 把舊值搬到該專案 sort_order 最小子表（且子表還沒設值時）→ DROP `crm_projects.receipt_path`。(2) **API 全套同步** — `_to_project_dict` 拔欄位、`_cost_group_to_dict`/create/update/duplicate 加欄位、`_save_receipt` 改讀 `cost_group.receipt_path`（fallback `uploads/receipts/{專案}/{子表}/`）、`list_project_receipts` 改聚合所有子表、新增 `/cost-groups/{gid}/receipts`、`serve_receipt` 安全白名單來源換成 cost_group。(3) **UI 移位** — 移除「補充資訊」📋 收據資料夾那行 inline-edit；「編輯子表」modal 加「收據資料夾」folder picker；「👁 瀏覽收據」按鈕改用當前選中子表的 receipt_path（沒設則跳提示「請先在編輯子表設定」），本機/外部存取分支處理。(4) **NAS browser z-index 修復** — `nas-browser.js` overlay z-index 200 → 2000，過去 CRM folder picker 都從 detail 頁的 inline-edit 觸發（沒在 modal 內），第一次從 1000 z-index modal 內叫 picker 才暴露這個 bug。(5) **首次登入空 tab 修復順帶補進 commit** — `app.js` `loadTabs` 改 idempotent + `_ensureTabsLoaded()` post-login hook、`_onLoginSuccess` async 化呼叫，未登入啟動只載公開 tab、登入後才注入 CRM/admin tab，避免首次登入專案管理永遠空白。 |
| v1.10.102 | 2026-04-28 | **CRM 專案清單 SWR 快取 + 載入中指示**：同事反映「登入後 CRM 沒讀到資料」其實是渲染順序問題 — `initCrmProjectsTab()` 等 5 個 GET 並行完成才有資料，這段空窗 `renderList` 顯示「找不到專案」讓人誤會。兩個修法：(1) **SWR cache** — `loadProjects` 成功後把 unfiltered `state.projects` 寫進 localStorage (`crm_projects_swr_v1`)，下次 tab init 先 `_hydrateProjectsFromCache()` 用 cache 秒渲染、再背景 fetch 新資料覆蓋。fetch 失敗時保留 cache 不洗成空，比之前更穩。(2) **`state.projectsLoaded` 旗標** — 第一次 fetch 完成（成功或失敗）才 flip 成 true，`renderList` 在沒資料且 `!projectsLoaded` 時顯示「載入中…」而非「找不到專案」，使用者馬上知道是還在載而不是真沒資料。logout 時順手清 SWR cache 避免下個登入者看到上一個人的資料。 |
| v1.10.101 | 2026-04-28 | **CRM 專案 AM/PM 加 searchable dropdown（同客戶欄）+ 修 modal reopen 顯示舊值 bug**：AM/PM 套用既有的 `searchableSelect` widget — 點擊就跳搜尋框 + 過濾面板，跟新增專案的「客戶」欄一樣的 UX。三個地方加 wrapping：(1) modal `_populateSelect`（AM）、(2) `_populatePmCheckboxes`（PM）、(3) detail panel cell-by-cell `_projEdit` 的 select 分支。順手修 modal reopen 時搜尋輸入框顯示前一個專案值的 stale-display bug：`searchableSelect` 對已包裝的 select 會 no-op，導致 innerHTML 重建後 visible input 沒同步；3 個 populate 函式都加 `sel._syncSsValue?.()` 強制刷新，並把 currentValue 透過參數傳進 `_populateSelect`（之前 AM 沒傳，每次都從第一個 option 開始）。`_projEdit` 內加 `_committed` 旗標防止「pick → change → commit + 同時 blur → 二次 commit」競爭，順帶為 select 分支加 Escape 取消邏輯。 |
| v1.10.100 | 2026-04-28 | **CRM 專案 PM 改單選下拉（DB 仍是 JSON array）**：原本 PM 是多選 checkbox（detail panel popover + modal checkbox-list），改成單選 dropdown。**DB 不動** — `pm_usernames` 欄位仍是 `JSONB` array，前端塞進 `[name]` (1 元素) 或 `[]` (空)，backend 透明處理、舊資料不丟、不需 migration。實作方法：`_buildEditFields` 加 `listWrap: true` flag 表示「UI 單選但 DB 是 list」，`_projEdit` commit 時包進 `val ? [val] : []`、edit 時取 `rawOrig[0]` 還原成單選；變動偵測用 `JSON.stringify` 比較整個 array。3 處同步：(1) detail panel cell-by-cell（PM cell `data-field="pm_usernames"` + `_projEdit` handler，移除 `_projEditPm` popover 約 50 行）、(2) modal `<div class="checkbox-list">` → `<select>`、(3) `_populatePmCheckboxes` / `_getSelectedPms` 同步切單選邏輯。實測 PUT `["王士源"]` / `[]` 都 200 + 持久化正常。 |
| v1.10.99 | 2026-04-28 | **CRM 專案 AM/PM 改從人力資源拉**：原本 AM 下拉 + PM 多選都 from `state.users`（系統登入帳號 admin/denniswang0513/OriginsunFinance），改成 from `state.staffList`（crm_staff 人力資源表，顯示「姓名 (角色)」）。共 5 處同步：(1) 詳情面板 cell-by-cell 編輯 `_buildEditFields()` 抽 `_staffOptions(includePlaceholder)` helper、(2) 詳情面板 PM 多選 popover (`_projEditPm`)、(3) 新增/編輯 modal AM select (`_populateSelect`)、(4) modal PM checkboxes (`_populatePmCheckboxes`)、(5) 列表頁頂部「全部 AM」filter（共用 `_populateSelect`）。儲存格式：staff.name 字串塞進 `am_username` / `pm_usernames` 欄位（DB column 是泛用 String，名字只是歷史命名）。順手把已不用的 `populateUserSelect` import 從 crm-projects-core.js 拿掉。**轉移期注意**：legacy 資料儲存的是系統 username（denniswang0513 等），新 dropdown 不會 match 既有值 → 顯示成「未指派」直到使用者重選 staff 名字；原欄位內容不丟、純 UI 顯示行為。 |
| v1.10.98 | 2026-04-28 | **CRM 專案資訊編輯三聯修**：(1) `window._projEdit` name collision — `crm-projects.js` 的 modal handler `(id) => openModal(p)` 在 `crm-projects-detail.js` 的 cell handler `(cell) => ...` 之後載入，把 cell handler 蓋掉，所有 cell 點擊靜默走 modal handler 但拿到 DOM element 不是 id，find 不到 → 整個 cell-by-cell 編輯系統實際失效。Rename modal handler 成 `_projOpenForm`，kebab menu 改用新名，cell handler 留原 `_projEdit`。(2) PUT `/projects/{id}` 用 `CrmProjectPayload`（`name+client_id` 必填）→ cell auto-save 只送 dirty field 必 422，static error 但前端 catch 沒 surface，使用者看到「已儲存」實際被拒絕。新增 `CrmProjectPatchPayload`（全 Optional） + 改 endpoint 用 `exclude_unset=True`，partial update 真的能進 DB。client_id 變更時保留原本的客戶存在性檢查。(3) UI 在合約資訊列尾巴加「✎ 編輯」按鈕（`_projOpenForm`），給使用者一個明顯的「打開完整表單」入口，不依賴每 cell 都看出可點擊。順帶把 `_buildEditFields` 的 `description` label 從「說明 / 備註」拆成「說明」+ 補上漏掉的 `notes` 欄位（label「備註」），「+ 加備註」cell 終於能用。 |
| v1.10.97 | 2026-04-28 | **行政雜支類別 10→6 收斂 + 對外網站拿掉假作品 fallback**：(1) v1.10.96 預設 10 類別超出原本 6 個下拉選單範圍，使用者 push back 改回 6（交通/住宿/飲食/提案/器材/其他）。`_EXPENSE_CATEGORY_DEFAULTS` + `EXPENSE_CATEGORIES` + 3 個 public expense form HTML 全部回退；歐姆龍專案的 12 個 v1.10.96 back-fill 出來的 $0 row（場地/車馬/印刷/服裝 × 3 子表）一次清乾淨。(2) Astro 對外網站（works / featured）原本 `crm-client.ts` 在 API 失敗或結果不足 9 筆時 fallback 到 `FAKE_FEATURED`（XYZ 集團/雲山會館/兒童福利聯盟等），使用者刪掉假作品 row 後網站靜態 HTML 還在顯示。改成空陣列 fallback + `index.astro` 拿掉 9 筆補齊邏輯，只渲染真實 published 作品。注意：website/ 不在 OTA AGENT_DIRS，這部分變更**需要在 NAS 端 `npm run build` 重 build** 才會生效。 |
| v1.10.96 | 2026-04-28 | **CRM 行政雜支自動預設 10 個拍片常用類別**：每個新專案/子表建立時自動 seed 10 筆 $0 雜支 row（交通/住宿/飲食/場地/車馬/器材/印刷/服裝/提案/其他），使用者打開直接填金額而不是先按 ➕。三個 hook 點：`POST /projects`（建專案 + 主表）、`POST /projects/{id}/cost-groups`（手動新子表）、`POST /cost-groups/{id}/duplicate`（複製子表）。`POST /projects/{id}/cost-lines/init` 同步擴充：legacy 專案點一次「初始化」就會把 10 個雜支類別補進去，已存在的類別跳過（不覆蓋使用者的金額）。`EXPENSE_CATEGORIES` 從 6 → 10，前後端跟 3 個 public expense form HTML（expense.html、advance-expense.html、group-expense.html）的 `<select>` 同步更新。實測新專案 / 新子表 / 既有專案 init 都 seed 出正確 10 個類別、舊的 飲食 等 row 不被覆蓋。 |
| v1.10.95 | 2026-04-28 | **CRM 成本估算欄位清空後沒儲存修復（exclude_unset）**：`/project-cost-lines/{id}` PUT 端點原本用 `req.model_dump(exclude_none=True)`，把所有 None 值剝光。前端 inline edit 在輸入空字串時送 `{"estimated_quantity": null}`（試圖清空欄位），後端 silently 把 null 扔了 → DB 沒寫入 → 自動儲存指示「已儲存」但實際沒儲存 → 切回專案值還是舊的。改成 `exclude_unset=True`：保留 explicit null（client 真的有送這個欄位），但仍排除 client 沒送的欄位（避免覆蓋整列）。實測 PUT `{"estimated_quantity": null}` 從 silently dropped → 真的清空欄位。加 4 個 unit test (`tests/unit/test_cost_line_payload.py`) 鎖死 explicit-null / explicit-zero / unset / partial-update 四種行為。 |
| v1.10.94 | 2026-04-28 | **OTA 中斷防護 + drone_meta 斷點續傳**：解決「OTA 推送把跑數小時的 drone_meta job 砍掉、reel 沒出」的根本問題。三層防護：(1) **L2 OTA busy check** — `routers/api_agents.py:trigger_agent_update` 推送前先 GET agent 的 `/api/v1/status`，看到 `busy=true` / `queue_length>0` / `active_jobs` 非空 → 回 HTTP 409 + 詳情（agent_name, busy, queue_length, active_jobs 摘要, hint）拒絕，要加 `?force=true` 才強推。urllib 包進 `asyncio.to_thread` 不阻塞 event loop。(2) **L3(a) 斷點續傳 manifest** — worker 在每個 dest 子資料夾寫 `_drone_meta_manifest.json`（atomic .tmp + replace），結構 `{version, stems: {basename: {index, exts:[]}}, next_index}`，每次成功處理一檔就更新。watcher `_scan_candidates` 讀 manifest 對比 source：已記錄的 (stem, ext) + 對應 MAX_NNNN 檔存在 → 跳過；剩下的當「需處理」回傳。沒 manifest 但有 MAX_*.{ext} → 走舊版相容跳過邏輯。worker 端的 `_drone_meta_sync` 也同步讀 manifest hydrate `stem_to_index` + `next_index`，循環裡看到 (stem, ext) 已在 manifest 直接 continue（手動 UI 跑也保護）。(3) **L1 文件化** — CLAUDE.md 9.5 加「規則 0：發版前必看 agent 是否在忙」。Simplify pass 順手清理：抽 `_record_manifest_success` 消去 image/video 兩分支 7 行寫入重複、`import json` 提到模組頂部、watcher scan 用 `os.listdir` 取一次 set 取代 N 次 `os.path.isfile` syscall、修死變數 next_index、簡化不可讀的三元、加 None entry guard。 |
| v1.10.93 | 2026-04-27 | **drone_meta 排程串帶可靠性 + DNG 還原長寬 + RAW+JPG 同名配對**：(1) **排程沒出 reel 修復** — 圖片處理 `_process_image_metadata_sync` 第一步 `shutil.copy2` 沒接 exception，網路磁碟瞬斷/檔案鎖定/權限問題會 raise 炸掉整個 worker 迴圈，串帶（在迴圈後執行）來不及跑。包 try/except 回傳 `_CopyFailed` 假 result，讓現有 returncode 防線接到。順便加「略過串帶」顯式 log（無影片可串、影片全失敗、未設目錄等四種原因都明示）。(2) **DNG 長寬被誤顯示成 960×720 修復** — v1.10.91 強剝 ColorMatrix1/2 + ProfileHueSatMap + ProfileToneCurve + NoiseProfile + OpcodeList → 不完整支援 DNG raw 的 viewer 沒色彩科學欄位無法解碼 → 退回顯示 SubIFD1 embedded preview（960×720）而非 5376×3956 真 raw。改成只清真正暴露 DJI 的 MakerNotes / XMP / 序號 / ProfileName / ProfileCopyright，色彩科學完整保留（Lightroom 用 UniqueCameraModel=XL720 從 Adobe DB 找 profile，不看嵌入 matrix）。檔案損失從 75 KB → 58 KB，所有 viewer 都能正常解碼 5376×3956。(3) **DJI 相機 RAW+JPG 雙檔同名拆散修復** — DJI_0923.DNG + DJI_0923.JPG 原本被當兩個獨立檔，編成 MAX_0001.DNG + MAX_0002.JPG 失去配對。改用 `stem_to_index` 字典 → 同 source basename 共用同一個 MAX_NNNN，輸出 MAX_0001.DNG + MAX_0001.JPG。(4) **XMP 區塊 byte-level 對齊 Autel** — 之前 v1.10.91 的 XMPToolkit 是 `Image::ExifTool 13.42`、缺 CreatorTool/DateCreated、多寫 DateTimeOriginal。改 5 欄位 3 命名空間完全比照 Autel 真品。 |
| v1.10.92 | 2026-04-27 | **CRM 專案內「+ 新增報價」按鈕無作用修復**：在「專案管理 → 選專案 → 報價管理」子 tab 點 + 新增報價沒反應。原因：`crm-quotes.js`（裡面 `initCrmQuotesTab()` 註冊 `window._openQuoteModalForProject/Edit/Duplicate`）只有在使用者點過外層「報價總覽」tab 時才會被 lazy-load。直接從專案進報價子 tab 的人，三個 modal opener 都還是 `undefined`，按鈕的 `if (window._openQuoteModalForXxx)` 防禦檢查 silently 吃掉 click。修法：(1) `crm-projects-quotes.js` 加 `_ensureQuotesModule()` helper（promise-cached、in-flight guard），把 HTML fetch + JS dynamic import + `initCrmQuotesTab()` 三步驟在三個按鈕（`_projAddQuote`/`_pqEdit`/`_pqDuplicate`）按下時 await。(2) `crm-quotes.js` 的 `initCrmQuotesTab()` 加 `_tabInitDone` 旗標 idempotent guard — 兩條 lazy-load 路徑各自的 loaded flag 互不認識，第二次進入會把 search/filter/button/detail-tab 的 addEventListener 全部再綁一次（造成 typing 觸發兩次 fetch、按 add 開兩個 modal 等）。 |
| v1.10.91 | 2026-04-27 | **drone_meta XMP 區塊對齊 Autel 真品（byte-level match）**：v1.10.90 後 XMP 仍有微小破綻 — 寫進去的 XMP block 跟 Autel 不同：(1) `XMP-x:XMPToolkit` 是 exiftool 自加的 `Image::ExifTool 13.42`（Autel 是 `XMP Core 5.5.0`）、(2) 缺 `XMP-xmp:CreatorTool: V2.1.8.27`、(3) 缺 `XMP-photoshop:DateCreated`、(4) 多寫 `XMP-xmp:DateTimeOriginal`（Autel 沒這個）。修法：`-XMP-x:XMPToolkit=XMP Core 5.5.0` 覆寫 exiftool 自加（測試證實可覆蓋，不會被 exiftool 頂掉）+ 加 `-XMP-xmp:CreatorTool=$autel_firmware` + 改用三個正確命名空間 `-XMP-xmp:CreateDate / ModifyDate` + `-XMP-photoshop:DateCreated`，移除 `-XMP:DateTimeOriginal=`。處理後 XMP 區塊跟 Autel 真品 5 欄位、3 個命名空間 byte-level 一致，差別只剩拍攝時間值。`autel_firmware="V2.1.8.27"` 提取為 local 常數避免 EXIF Software 跟 XMP CreatorTool 兩處 drift。實測 DNG/JPG 0 DJI 命中、validate OK。 |
| v1.10.90 | 2026-04-27 | **drone_meta JPG 完美偽裝（修 MakerNoteUnknownText: DJI 漏網之魚）**：v1.10.89 後發現 JPG 處理後 ExifIFD 仍殘留 4 byte `MakerNoteUnknownText: DJI` — 是 exiftool 在 wipe MakerNotes 後從 IFD entry 的 inline 4-byte 值 (`44 4a 49 00`) 推回的「DJI」字串，標準 `-MakerNoteUnknownText=` 改不掉（permanent flag），byte 替換還會反讓 exiftool 重新挖出整段 30+KB DJI 私有 binary。改用 rebuild 法：`-all=` + `-tagsfromfile @ -EXIF:all -GPS:all --MakerNotes --(身分欄位)` 一次清光重建，零 DJI 痕跡。代價：DJI MPF secondary embedded preview (~1MB) 不保留（多數 viewer 不依賴）。實測 grep DJI/Hasselblad/Mavic/L2D/SN 命中數從 1 → 0、validate warning 從 10 → 0、size 17MB → 15.9MB。DNG 路徑不動（rebuild 會破 SubIFD raw decoding，且單 pass 已無漏網）。 |
| v1.10.89 | 2026-04-27 | **drone_meta 支援空拍照片（DJI→Autel 偽裝 Level 1+2 全做）**：影片流程平行擴充到 `.dng/.jpg/.jpeg/.arw/.cr2/.cr3/.nef/.raf/.orf/.rw2/.tif/.tiff` 12 種圖檔副檔名。輸出檔保留原副檔名大寫（`MAX_0001.DNG / MAX_0001.JPG`）。worker 跳過 ffmpeg/DJI 字幕/Autel SRT，圖片走 `shutil.copy2` + exiftool 一條路。**Level 1（識別覆寫）**：Make/Model/UniqueCameraModel/LocalizedCameraModel/LensMake/LensModel/Software/ProfileCopyright + 8 個時間欄位（CreateDate/ModifyDate/DateTimeOriginal/FileCreate/FileModify + XMP 三變體）。**Level 2（DJI 痕跡清光）**：`-XMP:All=`（清掉 GimbalYaw/FlightYaw/ProductName=DJIMavic3 等飛行 metadata）+ `-MakerNotes:All=`（清掉 30+KB DJI 私有 binary）+ `-IFD0:NoiseProfile=`（permanent 旗標需強制 IFD0 prefix）+ ColorMatrix1/2/CalibrationIlluminant1/2/Profile* 7 項/OpcodeList1-3/SerialNumber/CameraSerialNumber/LensSerialNumber/LensInfo/ImageDescription/XPComment/XPKeywords。**保留 sensor-essential**：DNGVersion/BlackLevel/WhiteLevel/AsShotNeutral/CFAPattern/LinearizationTable（不留 raw 仍可開）。watcher `_VIDEO_EXTS` → `_MEDIA_EXTS`、skip 檢查改吃所有 MAX_* 副檔名、`_probe_creation_time` 為圖檔走 exiftool 撈 `DateTimeOriginal/CreateDate`（ffprobe 對 DNG raw 失靈）。concat 自動跳過圖檔、advanced_clips 對齊用 `video_settings` 過濾。實測 DJI_0923.DNG 偽裝後 exiftool `-validate` 通過、JPG 警告 13→10（清掉 DJI bad MakerNotes 反而更乾淨）。 |
| v1.10.88 | 2026-04-27 | **空拍排程時間戳記修復**：drone_watcher 排程觸發時，輸出檔的 8 個時間欄位（`CreateDate / ModifyDate / TrackCreate/Modify / MediaCreate/Modify / FileCreate/Modify` + MOV `creation_time`）被一律寫成「掃描那一刻」的時間（`start_ts = datetime.now()`），而非來源檔的拍攝時間。修法：`core/drone_watcher.py` 加 `_probe_creation_time()` helper，優先 ffprobe 撈 `format_tags:creation_time`（UTC→ 本地 naive ISO），失敗 fallback `os.path.getmtime`，再 fallback 才用 `start_ts`。掃描迴圈每支影片帶 `date_time_override`，全域 `date_time` 改用第一支偵測到的時間。`history` 中的 `ts` 仍為掃描時間（正確，那是 run record）。 |
| v1.10.87 | 2026-04-27 | **api_crm.py 加 `from __future__ import annotations`**：防禦性修復。原本 `routers/api_crm.py:4015` function signature 直接用 `CrmProjectShowcase` 做 type hint，但該 class 在 `try/except ImportError` 內，沒裝 sqlalchemy 的 agent 會 NameError → router 載入失敗 → CRM 全 404。實際在公用-器材 (192.168.1.5) 踩到，同 session 已手動 `pip install sqlalchemy[asyncio] asyncpg` 救了。本版改用 PEP 563 lazy annotation 一勞永逸 — 未來缺 sqlalchemy 的 agent 也能載入 router（runtime 讀 DB 仍由 `_require_db()` 擋下）。 |
| v1.10.86 | 2026-04-27 | **強制重發補拉 v1.10.85**：v1.10.85 OTA 在公用-器材 (192.168.1.5) 解壓時 routers/api_crm.py 被 uvicorn 鎖住沒覆蓋成功（version.json 寫了但 .py 還是舊壞版），導致該機 CRM 仍 404。本版號純 metadata bump 強制 update_agent 重抓一次：解壓前 kill port 8000，process 死透才覆蓋 .py，避免鎖定。其他 7 台代理只是換版號，內容無變。 |
| v1.10.85 | 2026-04-26 | **CRM agent 端 404 hotfix + 專案管理 cell-by-cell auto-save**：(1) **致命 hotfix** — Phase M-W (a7eb47d) 在 `routers/api_crm.py` 加了 module top-level 的 `from services.website.notion_service import _extract_youtube_id`，但 `services/` 不在 OTA AGENT_DIRS（NAS 部署目錄不打包），導致 v1.10.81 ~ v1.10.84 共 4 天所有代理機 import 失敗 → CRM router 沒註冊 → 所有 `/api/v1/crm/*` 全部 404。同事用任何代理機操作 CRM 都讀不到資料。修法：lazy import 移進 `_sync_showcase_to_public` 函式內。(2) **CRM 專案管理 cell-by-cell auto-save** — 拿掉 ✎ 編輯 modal、所有欄位點擊即可編輯、1 秒 debounce auto-save、PM popover、共用 `pickFolderPath` helper、deferred render 避免按鈕被銷毀的 race；snapshot-then-clear pattern 防 in-flight PUT 期間新編輯遺失；`_loadFinancialSummary` 不再清 dirty buffer 防 reload 蓋掉 pending 編輯；`_costCheckUnsaved` PUT 失敗時不執行導航 callback；抽 `_allDirtyCount` / `_clearAllDirty` helper 5 處去重；`searchableSelect` 暴露 `_syncSsValue` 修「→ 複製預估到結算」人員顯示 bug |
| v1.10.84 | 2026-04-25 | **登入流程優化 — first paint ~370ms → ~50ms（7x）**：(1) auth-state.js 樂觀 auth 快取 — localStorage 存 auth_user，模組載入時同步 hydrate window 狀態，`_authReady` 立即 resolve，loadTabs 不再等 /auth/me；100ms 後背景 revalidate，僅在 modules / access_level 真的變動時 location.reload()，網路斷線視為信任 cache（離線可用）。(2) app.js loadTabs 全 14 個 tab 一次 Promise.all（原本 9 串行 + 4 並行 + 1 串行）。(3) `_loadTab` 內部 `fetch(html)` 跟 `import(js)` 並行（兩者沒依賴），每 tab 省 ~1 RTT。(4) `TAB_LOADERS` 移到 tab-config.js 跟 `TAB_MAP` 同檔，sectionId 由 `TAB_MAP[key]` 統一查表。Simplify pass 順帶：修登出 race（revalidate setTimeout 加 token 守衛）+ 抽 `_fetchMe()` / `_adoptUser()` helpers + `STORAGE_KEYS` 常數消 8 處 magic string。Finance 角色補加 backup / drone_meta / website_admin 權限 |
| v1.10.83 | 2026-04-25 | **轉檔/串帶 hotfix + Phase M 持續推進**：(1) DJI Mavic 3+ 轉檔修復 — `-map 0:v` 把 attached_pic mjpeg 縮圖一併選進來，prores_ks 在 mov 容器寫不出 codec tag 導致 8/8 檔失敗，改 `-map 0:v:0`；空拍寫入 worker 同根因同 fix。(2) ffmpeg stderr 黑洞修復 — 抽 `_spawn_with_stderr_tail()` + `_log_stderr_tail()` helper，失敗時自動印最後 12 行錯誤；串帶批次預合併也改用 helper（順帶得益）。(3) `_self_heal_scheduled_task` 認 OriginsunBoot 任務（不只 OriginsunAgent），救援 Session 0 環境讓 picker 對話框不再隱形。(4) Regression test 鎖死 `-map 0:v:0` / `-map 0:a:0?` / `stderr=PIPE` 三條 invariant。(5) Phase M：M-W 官網管理 Tab 作品新增/編輯（與 CRM 完稿共用資料）、M-E-8 Notion-as-CMS 後端骨架、M-D Cloudflare Tunnel 同源修復 |
| v1.10.82 | 2026-04-23 | **Phase J-5 成本子表完整出貨**：每個 CRM 專案可建多張子表（拍攝日/子專案），每張子表獨立管「預算 + 成本估算 + 雜支 + 公開雜支登記連結」。後端 DB 新表 `crm_project_cost_groups` + cost_lines/expenses 加 `cost_group_id` + startup migration；10 個新/改 API 端點（CRUD + 跨組彙總 + 4 個 `/public/cost-groups/*`）。前端新模組 `crm-projects-cost-groups.js`（切換卡 + 新增/編輯/刪除/複製 Modal + state-aware 分享按鈕）+ 子表層級儀表板整併到 chip；行政雜支列支援 inline 編輯（類別/細項/金額/請款人）+ 新 `PATCH /project-expenses/{id}` 端點 + 並發 save；新 `/group-expense.html` 公開頁面（手機 RWD + 拍照/相簿選擇 + 送出後表單重置 + 捲到頂）。20 commits（含 4 輪 /simplify 清理 23 項發現） |
| v1.10.81 | 2026-04-22 | Phase M 階段性出貨：官網管理 Tab 主殼 + 9 子視圖（新增 SEO 子視圖）+ RBAC `website_admin` 模組；DB 擴充 5 張 `website_*` 表 + `crm_projects` 11 個 `public_*` 欄位；後端 services 層 + 對內/對外 API + NAS 專用入口 `main_website.py`；Astro 4 對外網站骨架（首頁/作品集/關於/服務/聯絡/Insight 專欄/portfolio 一頁式）於 `website/`（**不進 OTA**，獨立部署到 NAS） |
| v1.10.80 | 2026-04-19 | 製作串帶功能對齊空拍寫入（payload 補齊 tint/影調/curves/xfade + 主面板縮圖網格 + 檔案多選/資料夾/空白列）+ Watcher 面板強化（立即執行讀 UI 不需先存、取消全部任務按鈕、檢視轉檔設定 popover 含 dirty 標註）+ Worker 自動 makedirs 修空目錄 crash + NVENC probe 影格 64→256 修 false negative + 空拍寫入 tab 未選時 reset 色 |
| v1.10.79 | 2026-04-18 | 空拍轉場預設改 fade（前後交融）+ 排程監控（每日指定時間掃來源根目錄，自動轉檔+串帶，完成通知 Google Chat/LINE）+ 串帶檔名 fallback 修復（project_name 從來源資料夾末段推導，剝除副檔名） |
| v1.10.78 | 2026-04-17 | 空拍編輯 UX：縮圖即時調色(unique SVG filter ID 繞 Chrome cache)、重開保留預覽、scan 清除舊狀態；按鈕列對齊+遠端任務顯示暫停/中止(3秒 debounce 不閃爍) |
| v1.10.77 | 2026-04-16 | NVENC 驅動過舊自動退軟編 (fix SOCA etc. 舊驅動) — proactive probe + reactive fallback |
| v1.10.76 | 2026-04-15 | drone_meta 重構：第一階段純規格化、色彩/trim/xfade 全部延後到串帶；xfade 12 種類型；Trim 拖曳手把+I/O鍵;無音訊素材自動處理；中間檔移除 faststart 砍 50% IO |
| v1.10.75 | 2026-04-14 | 色彩公式對齊預覽/輸出 + 轉檔派發前 path validate + 補轉多輪遠端(扣黑名單) + 串帶優先遠端(退本機) + 進階編輯 UI 精修 |
| v1.10.74 | 2026-04-14 | 空拍進階編輯 UI 重排 + 色溫/色調 (tint) 取代 Gamma + 遠端主機進度面板 + exiftool 併入 OTA 修遠端寫入失敗 |
| v1.10.73 | 2026-04-14 | 修 Session 0 問題：installer 移除 /rl highest + Agent self-heal + Fix_Task.bat 後備腳本（修復 picker 對話框不彈窗） |
| v1.10.72 | 2026-04-13 | 進階編輯器加曲線/影調/即時 SVG 濾鏡預覽 + 套用到全部 + Install_or_Update 修 UNC/UAC + admin 按鈕修復 |
| v1.10.71 | 2026-04-12 | OTA 解壓前殺 port 避免檔案鎖定 + `/publish` skill + system/restart fallback |
| v1.10.70 | 2026-04-12 | 發布流程加 OTA 驗證 + 自動重啟 + 推送全代理驗證 |
| v1.10.68 | 2026-04-11 | OTA ZIP 瘦身（381MB→567KB）— windows_helper 從 AGENT_DIRS 移除 |
| v1.10.67 | 2026-04-11 | 串接迴圈錯誤隔離 + 大量檔案分批預合併避免 WinError 1455 + 空拍寫入 Tab |
| v1.10.65 | 2026-04-02 | CRM 應付帳款按月分組 + 客戶績效 Tab + 專案聯動 + 效能優化 + 客戶狀態自動化 |
| v1.10.64 | 2026-04-01 | CRM 應收帳款子視圖 + 列表置中對齊統一 |
| v1.10.63 | 2026-03-30 | CRM 請款列表置中對齊 + 發票欄 + 欄寬優化 |
| v1.10.52 | 2026-03-30 | CRM 全模組 Inline 編輯 + 請款三區塊版面 |
| v1.10.41 | 2026-03-30 | 帳務管理：應付帳款 + 批次付款 + 複製匯款資訊 + 重新整理按鈕 |
| v1.10.35 | 2026-03-30 | 帳務管理：請款項目完整選單 + 發票代開聯動 + 應付款狀態 |
| v1.10.31 | 2026-03-29 | 帳務管理：收支明細 + 請款 + 發票 + 手機版 RWD |
| v1.10.28 | 2026-03-29 | CRM 人力資源 + 報價內部成本 + 專案派工 + 雙向整合 |
| v1.10.26 | 2026-03-29 | CRM 報價管理 + 項目明細 + 範本 + 統計 + DB migration |
| v1.10.24 | 2026-03-28 | CRM 客戶/專案獨立 Tab + 共用工具模組 + 專案財務系統 |
| v1.10.22 | 2026-03-27 | 分散式補轉 3 層 fallback + 進度條錯誤面板 + PDF 分頁優化 + 膠卷手機版滑動修復 |
| v1.10.22 | 2026-03-27 | Cloudflare 外網支援 + NAS 瀏覽器 + 分散式補轉 3 層 fallback + 進度條錯誤面板 + PDF 分頁優化 |
| v1.10.21 | 2026-03-26 | Phase 0 完成：app.js 拆 10 模組 + projects.js 拆 3 + api_system.py 拆 3 router |
| v1.10.20 | 2026-03-26 | Phase A 完成：API Key CRUD + Tab 分頁式使用者管理 UI + Google OAuth 重複帳號 bug 修復 |
| v1.10.19 | 2026-03-26 | `control/update` 移除 JWT 限制，版本 badge 更新終於能用 |
| v1.10.18 | 2026-03-26 | 本機更新 Modal 步驟動畫，移除 port 8001 monitor 依賴 |
| v1.10.17 | 2026-03-26 | OTA 簡化：detached BAT + `update_status.json` 端點 |
| v1.10.16 | 2026-03-26 | Agent DB 同步 + `trigger_agent_update` 修正 |
| v1.10.12 | 2026-03-26 | OTA 強化：動態 preflight + 自動 requirements + publish 安全 |
