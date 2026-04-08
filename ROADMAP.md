# Originsun Media Guard Pro — 技術路線圖

> **目標**：從媒體管理工具 → 公司內部 CRM 系統（人資、財務、專案資源），整合 Notion 資料庫 + OpenClaw AI Agent

---

## 現況 (v1.10.22) 基準線

- ✅ 6 個完整工作流程（備份、比對、轉 Proxy、串帶、報表、AI 逐字稿）
- ✅ 模組化後端：`main.py` + `core/` + `routers/`（router 容錯載入，缺模組跳過不 crash）
- ✅ 模組化前端：`frontend/tabs/` 各分頁獨立
- ✅ OTA 安全更新（4 層套件防線 + 7 階段更新流程 + publish.html 發布工具）
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
- ✅ Auth + RBAC — JWT 登入 + Google OAuth + 六角色權限 + 模組級守衛
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

#### 版本發布工具（`publish.html`）
- [x] NAS 上的 Web UI，Admin 登入後可一鍵發布新版
- [x] 並發鎖（`asyncio.Lock`，同一時間只能有一個 publish）
- [x] 發布歷史紀錄（`publish_history.json`，最近 50 筆）
- [x] 一鍵回滾（`POST /api/v1/publish/rollback`，還原上一版 version.json + ZIP）
- [x] Agent 狀態總覽（publish 前顯示所有機器版本 + busy 狀態）
- [x] 版本降級保護（前端 + 後端雙重驗證）

#### 共享常數模組（`ota_manifest.py`）
- [x] `STDLIB`、`LOCAL_MODULES`、`EXCLUDE_DIRS`、`IMPORT_TO_PIP`、`scan_imports()` 統一定義
- [x] `publish_update.py`、`preflight.py` 共用，消除重複

#### 遠端管理
- [x] 遠端一鍵更新：主控端 proxy → Agent `/api/v1/internal/restart`
- [x] 更新進度監控：輪詢 `update_status.json` + health fallback（移除 port 8001 monitor 依賴）
- [x] 批次更新：[全部更新 N 台過舊] 按鈕
- [x] 版本狀態顯示 + 「有新版」橘色標記
- [x] `Install_or_Update.bat`：自動找 Agent 目錄 + `pip install -r requirements_agent.txt` + 無硬編碼套件
- [x] 版本 badge 本機更新：`control/update` 無需 JWT（localhost 限定）+ 前端步驟動畫 Modal
- [x] OTA 簡化：移除 `update_monitor.py` port 8001 依賴，改用 detached BAT + `update_status.json`

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
| `core_engine.py` | 1452 | 核心引擎，邏輯內聚，拆了反而難維護 |
| `tts_engine.py` | 676 | TTS 引擎，單一職責 |
| `core/worker.py` | 681 | 任務消費器，內聚 |
| `core/auth.py` | 491 | Auth 核心，剛好 |
| `Anent_MediaGuard_Pro.py` | 2559 | 遺留舊版單檔，不動 |
| 其餘 56 個 <500 行檔案 | — | 已經健康 |

### 可清理的遺留檔案

| 檔案 | 行數 | 說明 |
|------|------|------|
| `update_monitor.py` | 60 | 已棄用（port 8001 monitor 已移除） |
| `patch_ui.py` | 298 | 一次性 patch 腳本 |
| `Toolbox_Preview.py` | 68 | 工具預覽腳本 |
| `debug_compare.py` | 35 | 除錯用 |
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
**狀態**：🟡 部分完成

### 已完成

- [x] Agent 健康度面板（`publish.html` Connected Agents card：在線/離線/busy + 版本號）
- [x] 主 UI 機器狀態即時監控（綠燈脈衝/紅燈/橘燈 + CPU 顯示）

### 尚需完成

- [ ] 任務失敗率統計（成功率、平均處理時間）
- [ ] 告警通知整合（已有 `notifier.py`，擴充至 Agent 異常告警）
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
    │   → JWT + Google OAuth + 六角色 RBAC + 使用者/角色管理 UI + API Key 管理
    │
    ▼ Phase 0: 程式碼重構 (✅)
    │   → app.js 3499→1951+10模組 / projects.js 2180→1687+2模組 / api_system.py 1031→332+2router
    │
    ▼ Phase J: CRM + 專案管理 + 帳務 (✅ 核心完成)
    │   → 64 API / 11 DB 表 / 6 Tab + 5 子視圖 + 手機版 RWD + Inline 編輯
    │
現在 (v1.10.64) ← 你在這裡
    │
    ▼ Phase J-3: 備份 Tab 整合 (⬜)
    │
    ▼ Phase L: 行動端適配 (🟡 部分完成)
    │   → ✅ 報表手機版 RWD → 待做：主 UI RWD → 觸控優化
    │
    ▼ Phase G: 多機多專案
    │   → DB-backed 佇列 → 負載分派 → 容錯重派
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

**下一步**：Phase J-3（備份 Tab 整合）。

---

## 版本歷程摘要

| 版本 | 日期 | 重點 |
|------|------|------|
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
