# Originsun Media Guard Pro — 技術路線圖

> **目標**：從媒體管理工具 → 公司內部 CRM 系統（人資、財務、專案資源），整合 Notion 資料庫 + OpenClaw AI Agent

---

## 現況 (v1.10.82) 基準線

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

Windows 192.168.1.11 (員工內網、既有系統)
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
- [ ] website-api CORS：`192.168.1.11:8000`（Windows Tab）、`originsun-studio.com`、localhost
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
現在 (v1.10.97) ← 你在這裡
    │
    ▼ Phase M: 對外官方網站 (🚀 進行中 2026-04-20 ~ 07-01)
    │   → originsun-studio.com + CF Tunnel + Astro + 9 子視圖管理 Tab
    │   → 🎯 6/1 Staging 初版 / 7/1 正式切換上線
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

**下一步**：Phase M（對外官方網站，2026-04-20 ~ 2026-07-01）。

---

## 版本歷程摘要

| 版本 | 日期 | 重點 |
|------|------|------|
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
