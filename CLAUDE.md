# Originsun Media Guard Pro — Claude Code 完整交接文件

> **版本**: v2.4.404（2026-09-09）<!-- publish_update.py 自動維護，勿手改 -->
> **目標讀者**: 接手開發的 AI 協作者（Claude Code）
> **開發環境**: Windows 11、Python 3.11、Vanilla JS (ES Modules)
> **啟動方式**: `e:\Dev\Originsun-Media-Guard\.venv\Scripts\python.exe main.py`
> **✅ Phase M 完整版 A 部署完成**: 對外官方網站（`originsun-studio.com`）
> • 對外服務 24/7 在 NAS（192.168.1.132），master Windows 可隨時關機/重啟對外不掉
> • **NAS 容器**：`Website_Nginx`（port 8090→80，serve dist/ + proxy /api/website/*）
>   + `website-api`（8001 內部，FastAPI 跑 routers/website/）— 共用既有 `postgres_default` bridge
> • **既有 5 容器不動**：cloudflared / FileReport_Nginx / originsun_postgres / MCP / n8n
> • **cloudflared**：在 CF Zero Trust 儀表板把 `test.originsun-studio.com` 路由到
>   `192.168.1.132:8090`（不再指 master:4321）
> • **編輯流程**（2026-07-08 同源化）：admin Tab 同源打 serve 本頁的 agent
>   （master 8000 / dev 8001，master 跑同一套 routers/website）→ 寫 NAS Postgres
>   → mark_dirty 入 DB singleton row → 60s debounce → master 跑 npm build
>   → master scp dist/ 到 NAS → 對外網站立即更新
> • **舊跨域路已停用**：07-03 test hostname 換 www 後，www 的 Cloudflare bot 對抗層
>   會間歇擋帶 Authorization 的 admin fetch（preflight 過、GET 消失）。且 admin UI 由
>   master serve，master 關機時頁面本來就開不起來——「跨域不依賴 master」假設不成立。
>   NAS website-api 容器仍在：serve 對外站 runtime API（聯絡表單等）+ rebuild debounce。
> • **/publish 自動 sync**：master /publish 流程末段 scp `routers/services/core/db/main_website.py/config.py`
>   到 NAS code/ + `docker restart website-api`，container 拿到最新 endpoint 邏輯
> • **JWT 共用**：master + NAS website-api 都讀同一個 jwt_secret（NAS 容器透過 env，
>   master 從 settings.json 讀）— admin token 跨機可驗
> • 分支 `feature/website-m`，完整規劃見 [`docs/WEBSITE_ARCHITECTURE.md`](docs/WEBSITE_ARCHITECTURE.md)，
>   實際部署細節見 [`docker/INDEX.md`](docker/INDEX.md)

---

## 0. 新 Session 自動簡報

每次開啟新對話時，**第一件事**執行 `/briefing`，讓使用者快速掌握專案現況、ROADMAP 進度、未提交變更、開發規則。

**例外**：如果使用者的第一句話是緊急指令（如 "fix..."、"快修..."、"緊急..."），則跳過簡報直接處理任務。

---

## 0.6 公布欄 = Claude 交辦收件匣（協定，每個 session 都要懂）

後台「📌 公布欄」Tab（master API `/api/v1/bulletin`，存 mediaguard）是**團隊 + Claude 共用的任務清單**。
使用者會把要交辦的事丟進去，欄位：`assignee`(me/claude)、`status`(todo/doing/done)、`priority`、
`pinned`、`note`、`activity`(Claude 執行進度 log)、`conversation`(「問 Claude」對話)。

**當使用者說「做公布欄」/「處理公布欄的 X」/「清一清公布欄」時：**
1. `GET /api/v1/bulletin`（需 admin token 或 bulletin 模組；本機同源打 8000/8001）→ 撈項目。
2. 只挑 **`assignee == "claude"` 且 `status != "done"`** 的（那些才是交給你的；`assignee=="me"` 是
   使用者自己的待辦，**別動**）。
3. 逐項執行 —— 正常工具 + 使用者在場審核（**不是自主亂跑**；破壞性/部署照常先確認）。
4. 做完：`PUT /api/v1/bulletin/{id}` 設 `status="done"`，並把「你做了什麼」append 進 `activity`。
5. 做不完/需使用者決定：設 `status="doing"` + 在 `activity` 記卡在哪、需要什麼。

> 取 token：`.venv/Scripts/python.exe -c "from core.auth import create_token; print(create_token({'sub':'admin','username':'admin','access_level':3,'modules':['bulletin']}))"`。

**「問 Claude」（`POST /bulletin/{id}/ask`）是另一條路**：後端跑 `claude --print --permission-mode plan`
（唯讀諮詢、只回建議不執行），由 `routers/api_bulletin._run_ask` 處理，session 內的你不用管它。

---

## 1. 專案背景與定位

這是一個**內部媒體管理 SaaS 工具**，部署在製作公司的本機伺服器（`192.168.1.107:8000`），提供給同事用網頁瀏覽器存取。核心工作流程是：

1. 攝影師從記憶卡（Card）將素材**備份**到本機與 NAS
2. 備份同時可選擇性觸發**轉檔**（Proxy）、**串接**（Reel）、**視覺報表**
3. 剪輯師用**比對驗證**確認素材完整性
4. **語音辨識（Whisper）** 可將影片音軌轉為逐字稿（SRT/TXT）
5. **TTS** 功能可用 Edge-TTS 生成語音，或用 F5-TTS 進行零樣本聲音複製
6. **台灣正音引擎** 可將大陸用語自動轉為台灣用語 + 發音校正（熱更新 JSON 字典）

系統分為**主控端（伺服器）**和**代理端（同事電腦）**：
- 主控端：`main.py` 啟動的 FastAPI 服務，提供 Web UI + API
- 代理端：同事電腦上的本機 Agent（同樣一份程式碼，只是以不同 port 啟動），執行在本機的轉檔任務並回報進度

---

## 2. 快速啟動

> ⚠️ **本機（`e:\Dev` 開發 checkout）與生產主 agent 共存，務必先讀第 2.1 節共存守則。**
> 簡言之：**dev 一律用 8001 啟動、絕不在本機 `/publish`**。生產主 agent
> （`C:\OriginsunAgent`，v1.10.131）開機自啟、佔用 **8000**，別去碰它。

```powershell
# ✅ 開發模式（熱重載，dev 專用 8001）— 本機唯一正確的啟動方式
e:\Dev\Originsun-Media-Guard\dev_start.ps1
# 等同：.venv\Scripts\uvicorn.exe main:io_app --host 0.0.0.0 --port 8001 --reload
# 注意：hot reload 只能用於 FastAPI 路由，不含 tts_engine.py 頂部的 patch 副作用
```

```powershell
# ❌ 危險：以下指令會綁 8000，在本機 = 撞生產主 agent，禁止使用
#   .venv\Scripts\python.exe main.py            ← main.py 預設 port=8000
#   .venv\Scripts\uvicorn.exe ... --port 8000   ← 同上
#   wscript.exe start_hidden.vbs                ← start_hidden.vbs→main.py→8000
# 這三條只適用於「乾淨的生產機」，不適用於跑著 agent 的本開發機。
```

dev 服務啟動後在 `http://localhost:8001`；生產 agent 在 `http://localhost:8000`。

### 2.1 開發 / 生產共存守則（本機 `e:\Dev` 必讀）

本開發機同時存在兩套實例，**資料層已隔離、但操作層有兩個地雷**：

| 實例 | 路徑 | Port | DB | 啟動方式 |
|------|------|------|----|---------|
| **dev checkout** | `e:\Dev\Originsun-Media-Guard` | 8001 | `mediaguard_dev` | 手動 `dev_start.ps1` |
| **生產主 agent** | `C:\OriginsunAgent` | 8000 | `mediaguard`（生產） | 開機自啟（Startup 捷徑） |

- ✅ **DB 已分離**：dev→`mediaguard_dev`、生產→`mediaguard`（同 NAS Postgres 不同庫）。
  dev 測試的任務歷史 / CRM / 使用者 / 報表索引 / 排程都進 `_dev`，**不污染生產、排程不重複觸發**。
- 🔴 **地雷 1：Port 8000**。`python main.py` 預設 8000，會撞生產 agent。**dev 只用 8001**。
- 🔴 **地雷 2：`/publish`**。dev 的 `agents` 清單＝真實生產機隊（192.168.1.120/.107）。
  在本機 `/publish` 或 OTA 會把 dev 程式碼推到整個生產機隊。**本機不發版**，要發版去乾淨環境或明確確認。
- 🟡 NAS 檔案夾（reports/voice）與 JWT secret 為 dev/生產共用，影響小（索引已 DB 分離）。
- ⚠️ autostart 腳本（`create_startup_shortcut.ps1` 等）**不可**改指 `e:\Dev`——
  會讓 dev 開機自啟在 8000，每次開機撞生產 agent。維持指向 `C:\OriginsunAgent` 或棄用。

---

## 3. 完整目錄結構

```
Originsun-Media-Guard/
│
│  ── 【進入點與核心邏輯】
├── main.py                  # FastAPI 應用程式進入點
│                            #   - 建立 FastAPI app 並掛載 NoCacheMiddleware + CORS
│                            #   - 建立 socketio.ASGIApp(sio, app) → io_app
│                            #   - 掛載所有 router（api_backup, api_verify, api_proxy...）
│                            #   - 最後將 frontend/ 目錄 mount 為靜態檔案（html=True）
│                            #   - _periodic_version_check()：背景每 10 分鐘檢查主控端版號
│                            #     有更新時透過 Socket.IO 推播 'update_available' 給前端
│                            #   - _read_local_version()：讀取本機 version.json 版號
│                            #   - _is_newer(remote, local)：語意版本比較（支援 v 前綴）
│
├── core_engine.py           # 核心引擎類別 MediaGuardEngine（🔴 破千行，編輯前先量）
│                            #   - run_backup_job()：備份邏輯（本機 + NAS 雙寫、衝突處理）
│                            #   - run_transcode_job()：呼叫 ffmpeg.exe 進行 Proxy 轉檔
│                            #   - run_concat_job()：合併多個影片成一個 Reel
│                            #   - run_verify_job()：對比本機/NAS 檔案的 xxHash64
│                            #   - get_video_metadata()：用 ffprobe.exe 抽取影片 meta
│                            #   - generate_film_strip()：用 ffmpeg 截取縮圖條（報表用）
│                            #   - get_xxh64()：計算 xxHash64（比 SHA256 快 10 倍）
│                            #   - 包含 ReportManifest、FileRecord dataclass
│                            #   - _pause_event / _stop_event：threading.Event，用於暫停/停止任務
│
├── transcriber.py           # Whisper 語音辨識
│                            #   - 使用 faster-whisper（比原版快 4~8 倍）
│                            #   - run_transcribe_job()：掃描影片 → ffmpeg 抽音頻 → Whisper → 輸出 SRT/TXT
│                            #   - 支援 individual_mode（每個影片單獨一個檔）或合併模式
│                            #   - 支援 generate_proxy（順帶轉 Proxy 檔）
│
├── tts_engine.py            # TTS 引擎（支援 Edge-TTS + F5-TTS）
│                            #   - 頂部有 torchcodec/torchaudio Windows 修補（見第 5 節）
│                            #   - run_edge_tts()：async，透過 subprocess 呼叫 edge-tts CLI
│                            #   - run_f5_tts_clone()：async，使用 F5-TTS 進行零樣本聲音複製
│                            #   - f5_tts_is_available()：檢查 f5-tts 套件是否已安裝
│                            #   - _get_f5_tts()：lazy-load F5-TTS 模型（全局快取 _f5_instance）
│                            #   - _transcribe_ref_audio()：用 faster-whisper 轉錄參考音訊
│                            #   - _prepare_gen_text()：生成文字前處理（數字轉中文、句邊界注入、句末標點）
│                            #   - _pad_text_for_inference()：偵測短尾塊並補齊虛擬文字避免截斷
│                            #   - _convert_numbers_in_text()：阿拉伯數字→中文讀法（百分比、年份、序數等）
│                            #   - F5_MODEL_DIR = ./models/f5_tts
│
├── taiwan_dict.json         # 台灣正音字典（熱更新，不需重啟）
│                            #   - vocab_mapping：大陸→台灣用語（14 筆，如 視頻→影片）
│                            #   - pronunciation_hacks：發音校正（10 筆，如 垃圾→勒色）
│
├── utils/
│   ├── __init__.py
│   └── taiwan_normalizer.py # 台灣正音引擎
│                            #   - normalize_for_taiwan_tts(text)：依序套用 vocab + pronunciation
│                            #   - _load_dict()：每次呼叫時讀取 taiwan_dict.json（支援熱更新）
│                            #   - 最長優先替換（避免子字串衝突）
│
├── notifier.py              # 任務完成通知器
│                            #   - send_google_chat()：發送到 Google Chat Incoming Webhook
│                            #   - send_line_notify()：發送 LINE Notify
│                            #   - notify_all()：同時發送所有啟用的管道
│                            #   - notify_tab()：通用函式，根據 template_key + settings.json 決定要發給哪個管道
│                            #   - 訊息模板支援 {project_name}、{file_count}、{total_size} 等佔位符
│
├── config.py                # 設定讀寫
│                            #   - init_settings()：啟動時確保 settings.json 存在（自動建立預設值）
│                            #   - load_settings()：讀取並 merge 預設值（深度 merge）
│                            #   - save_settings()：將 dict 寫回 settings.json
│                            #   - 預設值含 master_server（主控端 URL，用於 HTTP OTA 更新）
│
├── report_generator.py      # HTML 報表產生器
│                            #   - save_report()：把 ReportManifest 渲染成 HTML（Jinja2 模板）
│                            #   - generate_pdf_from_html()：用 playwright/weasyprint 轉 PDF
│
├── bootstrap.py             # 雙模式腳本
│                            #   - `python bootstrap.py`：初始安裝（venv、pip、ffmpeg）
│                            #   - `python bootstrap.py --update [URL]`：OTA 更新（下載 ZIP → 備份 → 解壓 → 重啟）
│                            #   - 備份+雙層回滾：解壓失敗自動還原 + 啟動失敗 health check 15 秒回滾
│                            #   - 回滾時彈出 Windows MsgBox 通知使用者
│                            #   - _kill_port(port)：統一的 port 占用清除
│                            #   - _restart_agent(base_dir)：統一的重啟邏輯
│                            #   - 僅使用 stdlib（urllib、zipfile、subprocess），無外部依賴
│
├── version.json             # {"version": "1.8.1", "build_date": "...", "notes": "..."}
│                            #   前端定期輪詢此檔來比對 NAS 版本號 → OTA 更新觸發點
│
├── settings.json            # 執行時設定（不進 git，由 config.py 自動初始化）
│
│  ── 【core/ — 後端共用模組】
├── core/
│   ├── socket_mgr.py        # sio 單例（socketio.AsyncServer）
│   │                        #   - cors_allowed_origins='*'
│   │                        #   - 監聽 'resolve_conflict' 和 'set_global_conflict' 事件
│   │
│   ├── state.py             # 全局可變狀態（模組層級的全局變數）
│   │                        #   - task_queue: Queue           → 任務佇列（FIFO）
│   │                        #   - worker_busy: bool           → 是否有任務在執行
│   │                        #   - _current_progress: dict     → 最新進度字典（供 /status 輪詢）
│   │                        #   - conflict_event: Event       → 衝突等待的 threading.Event
│   │                        #   - current_conflict_action     → 'copy'/'skip'/'overwrite'
│   │                        #   - global_conflict_action      → 全局衝突策略（None 表示逐一詢問）
│   │                        #   - _current_task_log_file      → 目前任務的 .txt log 路徑
│   │                        #   - _status_log_buffer: list    → 最新 2000 條 log（供 /status 返回）
│   │                        #   - _current_report_task        → 未完成的報表 asyncio.Task
│   │                        #   - _report_pause_event: Event  → 報表暫停控制
│   │                        #   - _main_loop: asyncio loop    → set_main_loop() 由 startup 事件設定
│   │
│   ├── engine_inst.py       # MediaGuardEngine 全局單例
│   │                        #   - engine = MediaGuardEngine(logger_cb=..., error_cb=...)
│   │                        #   - logger_cb 將引擎的 print 轉成 Socket.IO emit
│   │
│   ├── logger.py            # 同步/非同步 log 橋接器
│   │                        #   - _emit_sync(event, data)：在工作執行緒中安全 emit（用 run_coroutine_threadsafe）
│   │                        #   - _engine_log_cb(msg)：過濾並分類訊息為 info/system/verbose
│   │                        #   - _write_log_to_file(msg)：將 log 寫入 state._current_task_log_file
│   │                        #   - log 類型：'info'（灰色）、'system'（黃色加粗）、'error'（紅色）
│   │
│   ├── schemas.py           # 所有 Pydantic 請求模型（見第 7.8 節完整清單）
│   │
│   ├── worker.py            # 後台任務執行器
│   │                        #   - _background_worker()：async 函式，消費 task_queue
│   │                        #   - 根據任務類型分派到 engine.run_*_job()
│   │                        #   - _on_progress(data)：呼叫 _emit_sync('progress', data)
│   │                        #   - _on_conflict(data)：發 'file_conflict' event，等待 conflict_event，回傳動作
│   │                        #   - 任務完成後 emit 'task_status' {'status': 'done'/'error'}
│   │                        #   - BackupRequest 完成後若 do_report=True，啟動 _run_report_job()
│   │
│   ├── report_job.py        # 視覺報表非同步任務
│   │                        #   - _run_report_job(req: ReportJobRequest)：完整報表流程
│   │                        #   - Phase 1 Scan → Phase 2 Meta/Hash → Phase 3 Film Strip
│   │                        #   - → Phase 4 Render HTML + PDF → Phase 5 Publish to NAS Web Server
│   │                        #   - 每個 phase 透過 'report_progress' Socket 事件回報進度
│   │                        #   - 可暫停：每次迭代前呼叫 _check_pause()
│   │                        #   - 最終產出放在 {output_dir}/Originsun_Reports/ 下
│   │                        #   - 更新 reports_index.json（本機 + NAS 各一份）
│   │
│   └── scheduler.py         # 任務排程器（croniter cron + run_at 單次排程，每 60 秒掃描）
│
│  ── 【routers/ — FastAPI 路由模組；以 main.py `_ROUTER_MODULES` 為準，勿信此數】
├── routers/
│   ├── __init__.py
│   ├── api_auth.py          # 認證端點（登入/登出/Google OAuth/使用者 CRUD/角色 RBAC，見 7.14）
│   ├── api_backup.py        # 備份端點（見 7.1）
│   ├── api_verify.py        # 驗證端點（見 7.2）
│   ├── api_proxy.py         # Proxy 轉檔 + 分散式計算端點（見 7.3）
│   ├── api_concat.py        # 串接端點（見 7.4）
│   ├── api_report.py        # 報表端點（見 7.5）
│   ├── api_transcribe.py    # 語音辨識端點（見 7.6）
│   ├── api_system.py        # 系統工具端點（見 7.7）
│   ├── api_tts.py           # TTS 端點（見 7.8）
│   ├── api_queue.py         # 佇列管理端點（見 7.9）
│   ├── api_job_history.py   # 任務歷史端點（篩選 + log 查看）
│   ├── api_agents.py        # NAS 共享機器管理 API (GET/POST/DELETE /api/v1/agents)（見 7.11）
│   ├── api_bookmarks.py     # 書籤 CRUD API
│   ├── api_schedules.py     # 排程管理 API
│   │  ── Work OS Phase N 新增（2026-07-08，v1.10.218-222）──
│   ├── api_timesheets.py    # 工時 Sheet 同步 + burn 檢核（N2 階段0，見 docs/appsscript/）
│   ├── api_cashflow.py      # B3 現金流預測+付款節點 + F1 月結鎖帳（守衛 crm_invoices）
│   ├── api_locations.py     # P-a 場景庫（preprod_locations + 照片 + 使用履歷）
│   ├── api_proposals.py     # P-b 提案庫（一鍵成案轉專案 + win/loss + 轉換率）
│   ├── api_intel.py         # P-c 產業情報 runner 端點（sources/items/轉提案）
│   ├── api_portal.py        # B1 看片審批門戶（內部 CRUD + 公開 token 頁 /review.html）
│   ├── api_equipment.py     # B4 器材庫（領用歸還/折舊/稼動率）
│   ├── api_footage.py       # B5 素材庫（掃描建索引 + 逐字稿 pg_trgm 全文檢索）
│   ├── api_crm.py           # CRM 薄殼 — re-export routers/crm/ 的 router（見 7.15）
│   └── crm/                 # CRM 領域套件：_shared + clients/projects/quotes/staff/costs/finance/showcase
│  ── 【services/ 根層 runner（跨 website/ 之外）】
│  services/intel_runner.py  # P-c 第四個 AI runner（RSS 抓取 stdlib、claude 摘要、預設關）
│  services/footage_indexer.py # B5 素材掃描（ffprobe + 逐字稿抽取，thread 收集/async upsert）
│
│  ── 【認證與權限】
├── core/
│   ├── auth.py              # JWT 認證 + 權限守衛（check_admin=Lv3 / check_admin_or_module=Lv3 或指定模組；RBAC v2 無角色層）
│   └── google_auth.py       # Google ID Token 驗證（GIS credential 模式，不需 client_secret）
├── db/
│   ├── models.py            # SQLAlchemy ORM（User + Client + CRM 表；RBAC v2 已無 Role 表，User 含 modules/access_level/google_id/email/avatar_url）
│   └── session.py           # DB 連線 + init_db()（RBAC v2：無角色 seed，權限直接綁帳號）
│
│  ── 【frontend/ — 靜態前端（SPA）】
├── frontend/
│   ├── index.html           # 主殼層
│   │                        #   - 頁籤切換邏輯（點擊 tab 按鈕 → 顯示對應 section）
│   │                        #   - 各頁籤的 HTML 結構都在這個檔案內
│   │                        #   - 底部載入 app.js 作為 type="module"
│   │
│   ├── app.js               # SPA 殼層（2026-09-03 拆到 1,100 行左右；分散式派發在 js/app/remote-dispatch.js、
│   │                        #   進度條／完成摘要／錯誤面板在 js/app/progress.js，兩邊都只透過 window.* 互相呼叫）
│   │                        #   - Socket.IO 連線管理（io()、'connect'、'disconnect'）
│   │                        #   - 所有 Socket 事件的監聽（見第 4.1 節完整事件清單）
│   │                        #   - 分散式轉檔派發（dispatchRemoteTranscode）
│   │                        #   - OTA 更新檢查（定時輪詢 /api/v1/version 和 /api/v1/nas_version）
│   │                        #   - 初始化時從 /api/v1/status 恢復 log 歷史
│   │
│   ├── style.css            # 全域樣式（深色主題）
│   │                        #   主色 #1a1a1a，強調色 #3b82f6（藍）/ #d48a04（橘）/ #228b22（綠）
│   │
│   ├── js/shared/
│   │   └── utils.js         # 前端共用工具（ES Module）
│   │                        #   - resolveDropPath(e, file, index)：4 種策略解析拖放路徑
│   │                        #   - appendLog(msg, type)：寫入 terminal
│   │                        #   - pickPath(inputId, type)：呼叫後端 pick_folder/pick_file
│   │                        #   - setupInputDrop(inputId)：讓單一 input 支援拖放路徑
│   │                        #   - setupDragAndDrop(containerId, addRowFunc)：讓列表支援拖放多個路徑
│   │                        #   - addStandaloneSource(listId)：動態新增來源列
│   │                        #   - resetProgress()：重置所有進度條與狀態
│   │                        #   - 所有函式也掛到 window.* 供非 module 程式碼呼叫
│   │
│   └── tabs/                # 各功能頁籤目錄（各含 .html + .js）
│                            #   隔離規則（2026-08-31 校正——原本寫「嚴格隔離」與事實不符）：
│                            #   **頁面邏輯**零跨頁籤依賴；但有 16 個「住在頁籤資料夾裡的
│                            #   共用庫」被跨頁籤 import（crm-utils×7 頁籤、website-utils×7、
│                            #   proposals 的 prop-* 家族、concat↔drone_meta 互嵌編輯器）。
│                            #   白名單與「為什麼不搬進 js/shared/」記在
│                            #   tests/unit/test_tab_import_boundary.py——要加跨頁籤 import
│                            #   先讀它檔頭的三個選項，白名單外的會直接紅。
│       ├── backup/          # 備份頁籤
│       ├── verify/          # 檔案比對驗證頁籤
│       ├── transcode/       # 轉 Proxy 頁籤（高級整理模式，含分散式派發）
│       ├── concat/          # 影片串接頁籤
│       ├── report/          # 視覺報表頁籤
│       ├── transcribe/      # Whisper 語音辨識頁籤
│       ├── tts/             # TTS 頁籤（含 3 個子頁）
│       │                    #   子頁 1：📢 標準 TTS（Edge-TTS）
│       │                    #   子頁 2：🎙️ 聲音複製（F5-TTS）
│       │                    #   子頁 3：📖 正音字典編輯器
│       ├── crm/             # CRM 模組（13 個前端檔案）
│       │                    #   crm.html/js（客戶）、crm-projects（專案）、crm-quotes（報價）
│       │                    #   crm-staff（人力）、crm-invoices（發票）、crm-payments（請款）
│       │                    #   crm-cashbook（收支）、crm-payables（應付）、crm-receivables（應收）
│       │                    #   crm-utils.js（共用）
│       └── projects/        # 專案總覽頁籤
│                            #   機器狀態、佇列管理、Agent 管理
│
│  ── 【模型與資料目錄】
├── models/
│   └── f5_tts/              # F5-TTS 模型快取目錄
│                            #   模型自動從 HuggingFace 下載至 ~/.cache/huggingface/
│                            #   F5TTS_v1_Base (~1.2GB, model_1250000.safetensors)
│
├── voice/                   # 本機聲音角色快取（執行時自動建立）
├── credentials/             # Google API OAuth 憑證（.gitignore，不可提交）
│
│  ── 【工具執行檔】
├── ffmpeg.exe               # 隨附 FFmpeg 7.x，所有影片處理都呼叫它
├── ffprobe.exe              # 隨附 FFprobe，用來讀取影片 metadata
│
│  ── 【部署與發布腳本】
├── publish_update.py        # 互動式版本發布腳本（問版本號 → 打包）
├── build_agent_zip.py       # 非互動式打包腳本（publish_update.py 會呼叫它）
├── start_hidden.vbs         # 無黑色視窗地啟動 python main.py
├── update_agent.bat         # OTA 更新腳本（從主控端 HTTP 下載更新 ZIP，重啟服務）
├── Install_Originsun_Agent.bat  # 新同事安裝精靈（從伺服器下載 Agent.zip，解壓到桌面）
├── Maintainer_Guide.md      # 維護人員操作手冊
│
│  ── 【測試套件】
├── pytest.ini               # pytest 設定（asyncio_mode=auto, testpaths=tests）
├── tests/                   # 🔴 檔數與測試數長得很快，**不要相信任何寫死的數字**
│   ├── conftest.py          # 共用 fixtures（tmp_settings, mock_engine, async_client, real_server）
│   ├── test_smoke.py        # Smoke tests（health / version / settings / frontend / socketio）
│   ├── unit/                # 單元測試 —— 主力，跑得快、不需要 DB
│   │   └── _srcscan.py      # 🔴 掃原始碼型測試的共用工具（repo_src / func_body /
│   │                        #    js_func_body / code_only / js_code_only / call_args /
│   │                        #    py_callers）。要寫「每個做 X 的地方都要呼叫 Y」這種
│   │                        #    不變式，用它，不要自己 read_text + split 手切字串。
│   ├── integration/         # 整合測試（需要跑起來的 app）
│   └── e2e/                 # Playwright 端對端

    要知道現在有幾支測試就跑（別問文件）：
        .venv/Scripts/python.exe -m pytest tests/unit -q --collect-only | tail -1
│
│  ── 【測試與開發文件】
├── TEST_INSTRUCTIONS.md     # 測試套件建置指令（17 個指令）
├── TEST_REPORT.md           # 功能測試報告
└── FINISHING.md             # 收尾流程範本
```

---

## 4. 系統架構詳解

### 4.1 整體資料流與 Socket.IO 事件

```
瀏覽器（前端 SPA）
│
├── HTTP REST (FastAPI)
│   ├── POST /api/v1/jobs              → BackupRequest    → task_queue
│   ├── POST /api/v1/jobs/transcode    → TranscodeRequest → task_queue
│   ├── POST /api/v1/jobs/concat       → ConcatRequest    → task_queue
│   ├── POST /api/v1/jobs/verify       → VerifyRequest    → task_queue
│   ├── POST /api/v1/jobs/transcribe   → TranscribeRequest → task_queue
│   ├── POST /api/v1/report_jobs       → 直接 asyncio.create_task()（不走 queue）
│   └── POST /api/v1/tts_jobs          → 直接執行（尚未接入 queue）
│
└── Socket.IO (python-socketio AsyncServer)
    ├── 瀏覽器 → 伺服器
    │   ├── resolve_conflict          → 使用者解決單一檔案衝突
    │   └── set_global_conflict       → 使用者設定全局衝突策略
    │
    └── 伺服器 → 瀏覽器（共 12 個事件）
        ├── log                       → 一般日誌訊息 {msg, type}
        ├── progress                  → 任務進度 {phase, file_pct, total_pct, done_files, total_files, current_file, speed_mbps, eta_sec}
        ├── task_status               → 任務結束 {status: 'done'|'error'}
        ├── file_conflict             → 檔案衝突通知（觸發前端對話框）
        ├── transcribe_progress       → Whisper 辨識進度 {pct, msg}
        ├── transcribe_done           → Whisper 辨識完成
        ├── transcribe_error          → Whisper 辨識錯誤
        ├── model_download_done       → Whisper 模型下載完成
        ├── model_download_error      → Whisper 模型下載失敗
        ├── report_progress           → 報表生成進度 {phase, pct, msg, type}
        ├── report_job_done           → 報表完成 {report_name, local_path, pdf_path, public_url, drive_url}
        └── update_available          → 後端偵測到新版本 {latest_version, current_version}
```

### 4.2 任務佇列機制（重要）

```
HTTP POST 任務 API
    ↓
state.task_queue.put(req)      # 放入 Queue（執行緒安全）
    ↓
if not state.worker_busy:
    bg_tasks.add_task(_background_worker)   # FastAPI BackgroundTasks
    ↓
_background_worker():          # 在背景執行緒消費 queue（async）
    while not queue.empty():
        task = queue.get()
        await asyncio.to_thread(engine.run_*_job, ...)
        await sio.emit('task_status', {'status': 'done'})
    state.worker_busy = False
```

**關鍵**：任務是嚴格串行執行的（不並行）。多個任務依序排隊，一個完成才執行下一個。**只有視覺報表**（`_run_report_job`）是例外——它用 `asyncio.create_task()` 平行在 `await` 點之間執行。

### 4.3 衝突解決流程

備份時若目的地已有同名檔案，引擎會呼叫 `_on_conflict()` callback：

```
_on_conflict(data):
    1. 若 global_conflict_action 已設定（「全部跳過」/「全部覆蓋」）→ 直接返回
    2. 否則 _emit_sync('file_conflict', data)
    3. 等待 state.conflict_event（最多 60 秒 timeout，超時 → 'skip'）
    4. 前端收到 'file_conflict' → 顯示對話框 → 使用者選擇
    5. 前端 emit 'resolve_conflict'/{action} 或 'set_global_conflict'/{action}
    6. socket_mgr.py 收到 → 設定 state.current_conflict_action → conflict_event.set()
    7. _on_conflict() 返回動作字串
```

### 4.4 暫停/停止機制

- **停止**：`engine.request_stop()` → 設定 `engine._stop_event`，引擎在每個檔案之間 check 並退出
- **暫停**：`engine.request_pause()` + `state._report_pause_event.clear()`，引擎和報表 job 都會 check 並等待
- **恢復**：`engine.request_resume()` + `state._report_pause_event.set()`

---

## 5. 重大技術注意事項（務必閱讀）

### 5.1 torchcodec + torchaudio Windows 修補（`tts_engine.py` 頂部）

**⚠️ 歷史變遷**：v1.5.0 之前使用 Coqui XTTS v2，需要在 `main.py` 頂部做 PyTorch `weights_only` patch 和 torchaudio backend patch。升級為 F5-TTS 後，已從 `main.py` 移除這些 patch，改在 `tts_engine.py` 頂部處理新的相容性問題。

**問題 1 — torchcodec 崩潰**：torchcodec 在 Windows 上呼叫 `os.add_dll_directory('.')` 時觸發 `[WinError 87]`。

**修復**：
```python
_orig_add_dll_directory = os.add_dll_directory
def _safe_add_dll_directory(path):
    try:
        return _orig_add_dll_directory(path)
    except OSError:
        # Return a no-op context manager
        ...
os.add_dll_directory = _safe_add_dll_directory
```

**問題 2 — torchaudio 2.10+ 移除 backend 系統**：torchaudio 2.10 直接使用 torchcodec 作為唯一後端，舊的 `_backend.utils` 路徑不存在。

**修復**：直接 patch `torchaudio.load` 改用 soundfile：
```python
import soundfile as _sf
def _patched_ta_load(uri, ...):
    data, sr = _sf.read(str(uri), dtype="float32", ...)
    return torch.from_numpy(data).unsqueeze(0), sr
torchaudio.load = _patched_ta_load
```

**問題 3 — F5-TTS 內建 ASR 觸發 torchcodec**：F5-TTS 的 `preprocess_ref_audio_text()` 使用 transformers pipeline 自動轉錄參考音訊，pipeline 內部 `import torchcodec` 又觸發同樣的崩潰。

**修復**：`_transcribe_ref_audio()` 函式使用 faster-whisper（已安裝用於 Whisper 頁籤）自行轉錄，再將結果傳入 `ref_text` 參數。

**千萬不要移動**：這些 patch 必須在 `tts_engine.py` 的最頂部（docstring 之後立即執行），因為 `main.py` import `api_tts` 時會間接觸發 `tts_engine.py` 的 import。

### 5.2 Windows 非 ASCII 路徑問題

**問題**：F5-TTS 底層無法開啟路徑含中文/非 ASCII 字元的檔案。

**解法**（`tts_engine.py` 的 `_f5_clone_sync()` 函式）：
```python
ext = os.path.splitext(reference_audio)[1]
tmp_fd, tmp_ref = tempfile.mkstemp(suffix=ext, prefix="f5_ref_")
os.close(tmp_fd)
shutil.copy2(reference_audio, tmp_ref)  # 複製到 ASCII 路徑
# ... 用 tmp_ref 做 TTS ...
os.remove(tmp_ref)  # finally block 中清理
```

### 5.3 Edge-TTS 透過子程序呼叫

Edge-TTS 的 Python API 在 Windows 上直接 `await communicate.stream()` 時，中文文字若包含全形字元有時會產生亂碼。改用 subprocess + temp file 的方式：

```python
with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
    tf.write(text)
cmd = [sys.executable, "-m", "edge_tts", "--file", tf.name, "--voice", voice, ...]
env = os.environ.copy()  # 必須繼承環境（空環境下 asyncio 在 Windows 失敗）
result = subprocess.run(cmd, capture_output=True, text=True, env=env)
```

### 5.4 `_emit_sync` — 從工作執行緒 emit Socket.IO

FastAPI 的 BackgroundTasks 和 `asyncio.to_thread()` 執行的函式在工作執行緒中運行，無法直接 `await sio.emit()`。正確方式：

```python
# core/logger.py
def _emit_sync(event: str, data: dict) -> None:
    loop = state.get_main_loop()  # 取得主 asyncio event loop
    if loop:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data), loop)
```

`state._main_loop` 由 `@app.on_event("startup")` 設定。如果在 worker 中 emit 事件前端收不到，檢查 `state._main_loop` 是否為 None。

### 5.6 RBAC + Google OAuth 認證系統

- **雙寫架構**：使用者資料同時存 `users.json`（JSON 檔）和 PostgreSQL（`db/models/` 套件，NAS 上的 mediaguard 庫）。
  每次修改必須呼叫 `_persist_user(user_data)` 統一寫入，不可分開呼叫 `sync_user_to_json` + `_save_user_to_db`。
- **Google OAuth 使用 GIS Credential 模式**：不需 client_secret，不需 redirect URI。
  前端載入 `accounts.google.com/gsi/client`，Google 返回 ID Token JWT，後端用 `google-auth` 庫驗證。
- **DB Migration**：`main.py` startup 用 `ALTER TABLE users ADD COLUMN IF NOT EXISTS` 加欄位，
  🔴 2026-09 起分兩處：`db/migrations.py` 的清單（CRM_INDEXES 等，只有資料）＋ `main.py` startup 的 `_crm_cols`
  （ADD COLUMN IF NOT EXISTS 清單，本週新欄位都加在這裡）。加欄位時在對應清單末端補一行，兩邊都要看。
- **`_find_user_by(column, value)`**：統一的使用者查找函式，支援 DB 和 JSON fallback。
  不要再新增 `_find_user_by_xxx` 單獨函式。
- **前端登入成功統一用 `_onLoginSuccess(d)`**，不要在密碼和 Google 兩條路徑各寫一次。
- **`_createFormModal(opts)`**：`app.js` 中的通用表單 Modal 建構函式，
  支援 text/password/select/checkboxes，新增表單彈窗時直接複用。

---

## 6. 前端架構詳解 (SPA & WebSockets)

### 6.1 頁籤「點到才載」(`app.js` → `loadTabs()` / `switchTab()`)

2026-09-03 起開頁**只載落地那一頁**（原本 30+ 分頁全載、首屏 ~80 支 API），其餘分頁第一次
`switchTab` 到它時才 `_loadTab(sectionId)`：`fetch('./tabs/<name>/<name>.html')` 填進 section →
`await import('./tabs/<name>/<name>.js')` → `initTab()` → 補套 auth 與機隊勾選面板。載過的分頁記在
`_loadedTabs`，之後切換只是 show/hide，**不會卸掉**。

- 跨分頁要用到別頁模組時走 `window._ensureTabLoaded(sectionId, { embed })`（同時兩處要同一頁只載一次；
  `embed` 給沒有那頁權限但要用它模組的人，如專案頁嵌報價子頁）。回傳「這次真的載了嗎」。
- `switchTab` 派的 `tab-changed` 事件帶 `fresh`（＝這次切換才載進來）。「切回來要重抓」的鉤子
  （提案庫、CRM 專案、財務）看到 `fresh` 就不再抓——init 剛抓過。
- 分頁 DOM 開機時不存在：預設值、勾選監聽都住在各分頁自己的 `initTab`；機隊勾選面板由載入器在分頁長出來時補。
- 快取三層（main.py NoCacheMiddleware，不認得任何路徑）：handler 自己宣告的 Cache-Control 優先（index.html、
  影像紀錄縮圖、財務文件／OTA 包用 `core.no_store.no_store_file()`）→ 有 ETag 的靜態檔 no-cache（304）→
  沒驗證器的 JSON 一律 no-store。
- 背景輪詢預算：無限期的狀態輪詢一律 `startVisiblePolling(fn, ms, { sectionId })`（`js/shared/utils.js`；
  分頁在背景或 SPA 分頁被藏起來就不打）；本機代理每 3 秒問 `/api/v1/health`（不是會回整段 log 的 `/status`）、
  版本比對 60 秒一次、機隊燈號 30 秒一輪且畫面上沒燈不打。規則釘在 `tests/unit/test_frontend_request_budget.py`。

### 6.2 智慧路徑解析策略 (`utils.js` → `resolveDropPath`)

在一般的網頁瀏覽器中，出於安全限制，無法直接從拖放（Drag & Drop）取得檔案的**絕對路徑**。為了讓同事能像操作桌面軟體一樣直覺，本機代理實作了四層降級解析法：

1. **RFC 2483 (uri-list)**：解析 `text/uri-list` 中的 `file:///` 協定，還原回 Windows 路徑。
2. **純文字匹配**：檢查 `text/plain` 是否符合 `C:\` 或 `\\` 開頭的路徑格式。
3. **Electron / Chrome 原生路徑**：支援 `file.path`。
4. **PowerShell 深度解析 (`/api/v1/utils/resolve_drop`)**：後端透過 PowerShell 查詢所有 Explorer 視窗中的檔案，比對檔名後回傳最可能的完整絕對路徑。

### 6.3 本機節點偵測與 OTA 更新 UI

- **連線偵測**：`app.js` 每 3 秒執行一次 `pollLocalAgent()`。
  - 若 `http://localhost:8000` 回應 → 切換 Socket.IO 連線至 localhost。
  - 若偵測不到 → 進入 `forceInstallModal`（鎖定畫面，引導安裝）。
- **OTA 版本圖示**：
  - 比對後端 `version.json` 與 NAS 的版號。
  - 若有新版 → 右上角出現閃爍更新按鈕。
  - 點擊後觸發 `updateAgent()`，畫面遮蔽直到連線恢復並自動重整。

### 6.4 分段式進度條設計 (`app.js` → `updateProgress`)

系統採用**三段加一**的視覺化設計：
- 第一段（藍 `#1f538d`）：備份進度
- 第二段（橘 `#d48a04`）：轉檔進度
- 第三段（綠 `#228b22`）：串接進度
- 第四段（紫 `#7c3aed`）：報表生成進度（僅在備份後自動觸發報表時顯示）

前端根據後端發送的 `phase` 動態調整各區段的寬度百分比。

---

## 7. 完整 API 端點參考

### 7.1 備份 (`routers/api_backup.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/jobs` | 建立備份任務 → task_queue |

### 7.2 驗證 (`routers/api_verify.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/jobs/verify` | 建立 Hash 比對任務 → task_queue |

### 7.3 Proxy 轉檔與分散式計算 (`routers/api_proxy.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/jobs/transcode` | 建立轉檔任務 → task_queue |
| POST | `/api/v1/merge_host_outputs` | 合併多節點 `HostDispatch_*` 輸出到主目錄 |
| POST | `/api/v1/verify_proxies` | 驗證專案下預期的 Proxy 檔案是否存在 |
| POST | `/api/v1/verify_standalone_proxies` | 比對來源 stem 與 Proxy stem 是否對應 |
| POST | `/api/v1/compare_source` | 比對來源目錄與 Proxy 輸出目錄的完整性 |

### 7.4 串接 (`routers/api_concat.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/jobs/concat` | 建立串接任務 → task_queue |

### 7.5 報表 (`routers/api_report.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/report_jobs` | 啟動報表任務（asyncio.create_task，不走 queue） |
| GET | `/api/v1/reports/history` | 取得 NAS 上的報表歷史索引 |
| DELETE | `/api/v1/reports/{report_id}` | 刪除報表索引中的指定項目 |

### 7.6 語音辨識 (`routers/api_transcribe.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/jobs/transcribe` | 建立 Whisper 辨識任務 → task_queue |

### 7.7 系統工具 (`routers/api_system.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/health` | 健康檢查（回傳服務狀態與主機資訊） |
| GET | `/api/v1/status` | 取得佇列狀態、進度、log 緩衝 |
| GET | `/api/v1/version` | 取得本機版本號 |
| GET | `/api/v1/nas_version` | 從主控端 HTTP 取得最新版號（用於 OTA 版本比對） |
| GET | `/api/settings/load` | 讀取 settings.json |
| POST | `/api/settings/save` | 寫入 settings.json |
| POST | `/api/settings/company-image/{kind}` | 上傳公司 Logo／印章（kind=logo/seal；存 repo `company_assets/`，直接寫進 settings.company；管理員） |
| GET | `/api/settings/company-image/{kind}` | 取回公司 Logo／印章（帶 token；設定頁預覽用） |
| GET | `/q/{code}` | **免登入**：報價單線上檢視頁（share_token 逐字比對；`main.py` 轉呼 `routers/crm/quotes`） |
| GET | `/q/{code}/pdf` | **免登入**：同上，下載 PDF |
| GET | `/api/v1/settings` | 取得設定（相容格式） |
| POST | `/api/v1/list_dir` | 列出目錄下的影片檔案 |
| GET | `/api/v1/models/status` | 檢查 Whisper 模型下載狀態 |
| POST | `/api/v1/models/download` | 背景下載 Whisper 模型 |
| POST | `/api/v1/control/pause` | 暫停目前任務 |
| POST | `/api/v1/control/resume` | 恢復已暫停的任務 |
| POST | `/api/v1/control/stop` | 停止目前任務 |
| POST | `/api/v1/control/update` | 觸發 OTA 更新 |
| POST | `/api/v1/utils/open_file` | 在瀏覽器開啟檔案 |
| POST | `/api/v1/utils/open_folder` | 在 Explorer 開啟資料夾 |
| GET | `/api/v1/utils/pick_folder` | 彈出系統資料夾選擇器 |
| GET | `/api/v1/utils/pick_file` | 彈出系統檔案選擇器 |
| POST | `/api/v1/utils/create_shortcut` | 建立桌面捷徑 |
| GET | `/api/v1/utils/resolve_drop` | 拖放路徑反向解析 |
| POST | `/api/v1/validate_paths` | 驗證路徑磁碟機與目錄是否存在（遠端派發前驗證） |
| POST | `/api/admin/restart` | 重啟 Agent 服務 |
| GET | `/download_agent` | 下載 Originsun_Agent.zip（完整安裝包 ~1GB） |
| GET | `/download_update` | 下載輕量 OTA 更新 ZIP（僅程式碼 ~200KB） |
| GET | `/download_updater` | 下載獨立遷移用 bat（v1.7→v1.8 首次升級） |
| GET | `/download_installer` | 下載安裝精靈 .bat |
| GET | `/bootstrap.ps1` | 動態 PowerShell 腳本（一行指令完成升級） |

### 7.8 TTS 語音生成（`routers/api_tts.py`）

**現況**：後端 API 和前端 UI 均已完成，但尚未接入任務佇列、Socket.IO 即時進度、完成通知。

#### 標準 TTS（Edge-TTS）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/tts_jobs` | 使用 Edge-TTS 合成語音 |
| GET | `/api/v1/tts/voices` | 列出所有可用的 Edge-TTS 聲音 |
| GET | `/api/v1/tts/preview` | 串流預覽指定聲音 |
| POST | `/api/v1/tts/estimate` | 估算 TTS 時長與字元速率 |

#### 聲音複製（F5-TTS）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/tts_jobs/clone` | 從參考音訊克隆聲音合成（F5-TTS 零樣本） |
| GET | `/api/v1/tts/f5_status` | 檢查 F5-TTS 套件是否已安裝 |

#### 台灣正音字典

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/tts/dictionary` | 讀取 taiwan_dict.json（熱更新） |
| POST | `/api/v1/tts/dictionary` | 更新 taiwan_dict.json（不需重啟） |

#### 聲音角色管理

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/voice_profiles` | 列出 NAS 上所有聲音角色 |
| POST | `/api/v1/voice_profiles` | 新增聲音角色到 NAS |
| DELETE | `/api/v1/voice_profiles/{id}` | 刪除聲音角色 |
| POST | `/api/v1/voice_profiles/{id}/cache` | 將 NAS 角色快取到本機 |


### 7.15 CRM 模組（`routers/crm/` 套件）

> 2026-07-06 起由單檔 api_crm.py（~5,100 行）拆分為領域套件：`_shared.py`（router 單例 +
> 跨領域 helpers）+ `clients` / `projects` / `quotes` / `staff` / `costs` / `finance` / `showcase`。
> `routers/api_crm.py` 只剩薄殼（re-export router + `_mint_showcase_edit_token`），**URL 全部不變**。
> ⚠️ 端點數會持續漂移，**以程式碼為準**（`grep -r '@router\.' routers/crm | wc -l`），不要相信文件裡寫死的數字。

CRM 系統包含 6 個獨立 Tab + 帳務管理的 5 個子視圖：

| Tab | 前端檔案 | DB 表 |
|-----|---------|-------|
| 🤝 客戶管理 | `crm/crm.html` + `crm.js` | `clients` |
| 📁 專案管理 | `crm/crm-projects.html` + `.js` | `crm_projects`, `crm_project_staff`, `crm_project_expenses` |
| 💰 報價管理 | `crm/crm-quotes.html` + `.js` | `crm_quotations`, `crm_quotation_items`, `crm_quotation_templates` |
| 👥 人力資源 | `crm/crm-staff.html` + `.js` | `crm_staff` |
| 🧾 帳務—發票 | `crm/crm-invoices.html` + `.js` | `crm_invoices` |
| 🧾 帳務—請款 | `crm/crm-payments.html` + `.js` | `crm_payment_requests` |
| 🧾 帳務—收支明細 | `crm/crm-cashbook.html` + `.js` | `crm_cash_entries`, `crm_cash_invoice_links` |
| 🧾 帳務—應付帳款 | `crm/crm-payables.html` + `.js` | (視圖，含 batch-month) |
| 🧾 帳務—應收帳款 | `crm/crm-receivables.html` + `.js` | (視圖，查詢 `crm_invoices`) |

**客戶詳情面板**：4 個 Tab — 客戶資訊（inline 編輯）、客戶關係（inline 編輯）、客戶績效（專案統計/狀態分布/年度營收）、專案紀錄（卡片列表+詳情彈窗）

**客戶狀態自動化**：`_auto_update_client_status()` 根據成案專案數量自動判定（0=潛在客戶, 1=新客戶, 2+=舊客戶），在專案建立/刪除時觸發

**應付帳款重構**：按月份分組（已付款按 payment_date 月份、應付款按 planned_month），月份可調整（儲存按鈕確認）、付款狀態可雙向切換。預支款（`is_advance=1`）不納入應付帳款。

**預支款自動結算**（全自動計算，無手動 flag，後端 `GET /payments/advances` 即時算出）：

舉例：公司預支 $40,000 給製片王士源拍「技術展示影片」。

```
步驟 1｜建立預支款
  預支金額 = $40,000
  狀態：未發款、待收款
  → 公司還沒把錢匯出去

步驟 2｜在收支明細記一筆「發款」（支出 $40,000，關聯此預支）
  系統檢查：關聯此預支的「支出」合計 ($40,000) ≥ 預支金額 ($40,000) → ✓
  狀態：已發款、待收款
  → 公司已經把錢匯給製片了

步驟 3｜製片在外面花錢，登記支出明細
  交通 $5,000 + 便當 $3,000 + 場地 $30,000 = 支出合計 $38,000
  餘額 = $40,000 − $38,000 = $2,000（需還款）
  狀態：已發款、需還款 $2,000
  → 製片還有 $2,000 沒花完，要還公司

步驟 4｜製片還款，在收支明細記一筆「收款」（收入 $2,000，關聯此預支）
  系統檢查：關聯此預支的「收入」合計 ($2,000) > 0 → ✓
  系統檢查：餘額 ($40,000 − $38,000 = $2,000)... 但收入補回 → 帳平了嗎？
  餘額 = 預支 $40,000 − 專案支出 $38,000 = $2,000 ≠ 0
  狀態：已發款、已收款、但餘額 ≠ 0 → 未結清
  → 要等支出登記完整才會結清

  （如果製片補登一筆雜支 $2,000，餘額變 0 → 已結清 ✓）
```

三個狀態的判定來源：

| 狀態 | 怎麼算 | 資料來源 |
|------|--------|---------|
| 已發款 | 收支明細「支出」欄關聯此預支的合計 ≥ 預支金額 | `cash_entries.expense` |
| 已收款 | 收支明細「收入」欄關聯此預支的合計 > 0 | `cash_entries.deposit` |
| 已結清 | 已發款 ✓ + 已收款 ✓ + 餘額(預支金額 − 專案支出 − 已收回款) = 0 | 三者皆滿足 |

- 已結清的預支不再出現在收支明細的預支選單中，避免重複關聯。
- 前端不做任何狀態寫入，全部由後端查詢時即時計算。

**效能優化**：CRM Tab 並行載入（`Promise.all`）、`crmFetch` GET 請求去重、`quotations/stats` SQL 聚合、`payables/receivables` JOIN 取代全表掃描、5 個新 DB 索引

**共用模組**：`crm/crm-utils.js`（crmFetch + GET 請求去重, esc, fmtNum, renderAvatar, populateUserSelect, populateClientSelect, setupResizeHandle, enableInlineEdit, addEditButton）

**手機版 RWD**：`/expense.html`（雜支登記）、`/invoice.html`（發票登記）

**RBAC 模組**：`crm_clients`, `crm_projects`, `crm_quotes`, `crm_staff`, `crm_invoices`

**新增 CRM 功能 checklist**：
1. DB Model → `db/models/`（`_crm.py`／`_workos.py`／`_system.py`；新欄位需在 `main.py` startup 的 `_crm_cols` 加 `ALTER TABLE ADD COLUMN IF NOT EXISTS`）
2. Schema → `core/schemas.py`
3. API → `routers/crm/<領域>.py`（共用 helper 進 `_shared.py`；純錢流判定進 `core/crm_logic.py` 並加單元測試）
4. 前端 → `frontend/tabs/crm/` 對應 `.html` + `.js`
5. 帳務子視圖用 lazy-load，import 路徑必須用 `location.origin` 絕對路徑
6. 新模組權限 3 處同步（RBAC v2 已無 roles.json/角色 seed）：`core/auth.py` 的 `ALL_MODULES`（source of truth）、`frontend/js/shared/tab-config.js`（TAB_MAP/TAB_LOADERS/TAB_GROUPS）、`frontend/js/admin/user-mgmt.js` 的 `MODULE_LABELS`
7. HTML div 平衡檢查
8. 發布前確認 `version.json` 版號（用戶可能從瀏覽器端發布過）

### 7.9 佇列管理 (`routers/api_queue.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/queue` | 取得所有排隊中和執行中的任務 |
| POST | `/api/v1/queue/reorder` | 重新排序佇列 |
| POST | `/api/v1/queue/{job_id}/urgent` | 將任務設為緊急（排到最前） |
| DELETE | `/api/v1/queue/{job_id}/urgent` | 取消任務的緊急狀態 |

### 7.11 機器管理 (`routers/api_agents.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/agents` | 列出所有機器（從 NAS agents.json 讀取） |
| POST | `/api/v1/agents` | 新增機器 |
| DELETE | `/api/v1/agents/{id}` | 移除機器 |

### 7.13 任務歷史 (`routers/api_job_history.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/job_history` | 查詢歷史（支援 date/task_type/status/q 篩選） |
| POST | `/api/v1/job_history` | 新增歷史紀錄（前端呼叫，自動去重） |
| GET | `/api/v1/job_history/{job_id}/log` | 取得任務 Log 內容（512KB 上限） |
| DELETE | `/api/v1/job_history` | 清除歷史（可指定日期） |

### 7.14 認證與授權 (`routers/api_auth.py`)

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/auth/login` | 密碼登入（返回 JWT） |
| GET | `/api/v1/auth/me` | 取得當前登入使用者資訊 |
| GET | `/api/v1/auth/users` | 列出所有使用者（需 admin） |
| POST | `/api/v1/auth/users` | 新增使用者：`{username, password, modules[], access_level}`（需 admin） |
| PUT | `/api/v1/auth/users/{username}` | 修改使用者：`{password?, modules?, access_level?}`（需 admin） |
| DELETE | `/api/v1/auth/users/{username}` | 刪除使用者（需 admin） |
| GET | `/api/v1/auth/google/config` | 取得 Google OAuth 設定（公開） |
| POST | `/api/v1/auth/google/login` | Google ID Token 登入 |

> **RBAC v2（角色層已移除）**：權限直接綁帳號 —— 每個 User 有 `modules`（可用模組）
> + `access_level`（3=管理員 / 1=一般，唯一硬閘門是 Lv3）。沒有角色表、沒有
> `/api/v1/roles` 端點、沒有「角色管理」UI。前端「使用者管理」直接編每個帳號的
> 4 群模組勾選 + 管理員開關。官網寫入守衛（`routers/website/_common.py`）用
> `core.auth.check_admin_or_module(request, 'website_admin')`：管理員 OR 擁有官網模組
> 即可寫入；全域 `check_admin` 仍只認 Lv3。

### 7.12 Pydantic Schema 完整清單 (`core/schemas.py`)

```python
class BackupRequest(BaseModel):
    task_type: str = "backup"
    project_name: str
    local_root: str
    nas_root: str
    proxy_root: str
    cards: List[Tuple[str, str]]
    do_hash: bool = True
    do_transcode: bool = True
    do_concat: bool = True
    do_report: bool = False
    # Concat settings
    concat_resolution: str = "720P"
    concat_codec: str = "H.264 (NVENC)"
    concat_burn_tc: bool = True
    concat_burn_fn: bool = False
    # Report settings
    report_name: str = ""
    report_output: str = ""
    report_filmstrip: bool = True
    report_techspec: bool = True
    report_hash: bool = False

class TranscodeRequest(BaseModel):
    task_type: str = "transcode"
    sources: List[str]
    dest_dir: str

class ConcatRequest(BaseModel):
    task_type: str = "concat"
    sources: List[str]
    dest_dir: str
    custom_name: str = ""
    resolution: str = "1080P"
    codec: str = "ProRes"
    burn_timecode: bool = True
    burn_filename: bool = False

class VerifyRequest(BaseModel):
    task_type: str = "verify"
    pairs: List[Tuple[str, str]]
    mode: str = "quick"

class ReportJobRequest(BaseModel):
    task_type: str = "report"
    source_dir: str
    output_dir: str
    nas_root: str = ""
    report_name: str = ""
    do_filmstrip: bool = True
    do_techspec: bool = True
    do_hash: bool = False
    do_gdrive: bool = False
    do_gchat: bool = False
    do_line: bool = False
    exclude_dirs: list = []

class TranscribeRequest(BaseModel):
    task_type: str = "transcribe"
    sources: List[str]
    dest_dir: str
    model_size: str = "turbo"
    output_srt: bool = True
    output_txt: bool = True
    output_wav: bool = False
    generate_proxy: bool = False
    individual_mode: bool = False

class ListDirRequest(BaseModel):
    path: str
    exts: List[str] = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"]

class OpenFileRequest(BaseModel):
    path: str

class DownloadModelRequest(BaseModel):
    model_size: str

class MergeHostOutputsRequest(BaseModel):
    proxy_root: str
    project_name: str

class MergeOutputRequest(BaseModel):
    proxy_root: str
    project_name: str

class VerifyProxiesRequest(BaseModel):
    proxy_root: str
    project_name: str
    expected_files: dict

class VerifyStandaloneProxiesRequest(BaseModel):
    sources: List[str]
    dest_dir: str

class CompareSourceRequest(BaseModel):
    source_dir: str
    output_dir: str
    video_exts: List[str] = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"]
    proxy_exts: List[str] = [".mov", ".mp4"]
    flat_proxy: bool = False

class ValidatePathsRequest(BaseModel):
    paths: List[str]
```

---

## 8. 常見問題與除錯指南 (Troubleshooting)

### Q1: 前端一直顯示「未偵測到本機代理」
1. 檢查是否已執行 `python main.py` 或 `start_hidden.vbs`。
2. 檢查 Port 8000 是否被佔用。
3. 如果是在遠端 IP 存取，確保同事電腦也有安裝並運行 Agent（瀏覽器會嘗試 fetch `localhost:8000`）。

### Q2: 語音克隆 (F5-TTS) 無法運作
1. 確認 `pip install f5-tts` 是否已執行。
2. 檢查 `GET /api/v1/tts/f5_status` 回傳 `{"available":true}`。
3. 首次執行會自動從 HuggingFace 下載模型 (~1.2GB)，需等待。
4. CPU 推論速度很慢（約 19 分鐘/句），建議使用 CUDA GPU。
5. 若出現 `[WinError 87]`，確認 `tts_engine.py` 頂部的 torchcodec patch 存在。

### Q3: 影片轉檔失敗 (FFmpeg Error)
1. 檢查 `ffmpeg.exe` 是否存在於專案根目錄。
2. 查看 `terminal_verbose` 中的完整 FFmpeg 命令行，手動複製到 CMD 執行測試。
3. 檢查來源影片檔案路徑是否過長（Windows 255 字元限制）。

### Q4: Worker 中 emit 事件但前端收不到
1. 檢查 `state._main_loop` 是否為 None（應在 startup 事件中設定）。
2. 確認使用的是 `_emit_sync()` 而非直接 `await sio.emit()`（工作執行緒不能用 await）。

---

## 9. 開發者維護建議 (Best Practices)

1. **編輯 `core_engine.py` 時**：請務必注意 `_pause_event` 與 `_stop_event` 的檢查頻率。若迴圈太重會導致使用者點擊「強制中止」後要等很久才反應。
2. **新增 API 時**：
   - 務必在 `core/schemas.py` 定義 Pydantic Model。
   - 在 `routers/` 下建立新的 `.py` 檔案並在 `main.py` include。
   - 更新本文件第 7 節的端點表格。
3. **前端修改時**：
   - 儘量使用 `utils.js` 的 `appendLog` 紀錄關鍵訊息。
   - 避免在 `app.js` 寫入過多與頁籤無關的可變狀態。
   - 新頁籤應建立獨立的 `tabs/<name>/` 目錄，包含 `.html` + `.js`。
4. **OTA 清單同步**：新增根目錄 `.py` 檔案時，務必更新 `ota_manifest.py` 的 `AGENT_FILES`。
5. **版本發布**：使用 `/publish` skill 或手動執行 `python publish_update.py --version X.Y.Z --notes "..."`。
   - **禁止**直接手動修改 `version.json` — 會跳過依賴掃描、preflight、OTA 驗證、自動重啟。
   - 發布流程會自動：掃描 import → 更新 requirements_agent.txt → preflight → 打包 → 驗證 OTA < 10MB → 重啟主控端。
   - `ota_manifest.py` 的 `AGENT_DIRS` **禁止**包含二進制目錄（`windows_helper`、`python_embed`），否則 OTA ZIP 會膨脹到數百 MB。
6. **Worker 迴圈錯誤隔離**：`worker.py` 中多卡迭代的 `try-except` 必須在迴圈**內部**，不可包住整個 `for`。一張卡失敗不能阻斷其他卡。

---

## 9.5 AI 協作者行為準則（強制執行）

> 以下規則適用於所有 AI 協作者（Claude Code / Copilot 等），違反任何一條視為任務未完成。

### 規則 0：發版前必看 agent 是否在忙（OTA 安全）

長時間任務（drone_meta 一個資料夾 100+ 檔案常跑數小時）很常碰到 OTA。
**v1.10.94 起 `/api/v1/agents/{id}/update` 預設會做 busy check**：agent 的
`/api/v1/status` 顯示 `busy=true` 或 `queue_length>0` 或有 `active_jobs` →
回 HTTP 409 拒絕推送（除非加 `?force=true`）。

執行 `/publish` skill 時若有 agent 回 409，**先確認可以中斷該任務再強推**。
被砍掉的 in-flight job：drone_meta 有 manifest 機制（`_drone_meta_manifest.json`
寫在每個 dest 子資料夾），watcher 下次掃描會自動跳過已處理 (stem, ext)、
只補處理沒做完的，所以強推風險已降低（但串帶 reel 會少一次，需要重跑）。

### 規則 A：強制驗證，不准說「Done」就跑

檔案寫入磁碟 ≠ 任務完成。每次修改 `.py` 檔案後，必須執行：
```bash
# Python 語法檢查
e:\Dev\Originsun-Media-Guard\.venv\Scripts\python.exe -m py_compile <modified_file>
```
前端 `.js` / `.html` 修改後，必須確認：
- HTML div/tag 平衡（特別是 CRM 巢狀模板字串）
- `window.*` 函式名稱與 `onclick` 引用一致
- ES Module import 路徑正確

**全部通過才能回報完成。測試失敗就說失敗，不要粉飾。沒有跑過驗證就不要暗示通過了。**

### 規則 B：大檔案必須分段讀取

每次讀檔有 2,000 行的硬上限，超過的部分會被截斷——AI 不會告訴你它沒看完。

**不要相信任何文件裡寫死的行數**（2026-07-07 盤點：前一天更新的清單隔天就漂了）。
🔴 **這包含本文件**：CLAUDE.md 裡的目錄樹只描述「這個檔案是幹嘛的」，
不描述它多大。2026-08-30 體檢時，這裡曾經寫死的五個數字全部過期
（端點 140+ 實際 297、測試 75 實際 1,820、`api_crm.py` 2900+ 行實際 20）。
編輯任何檔案前先量它：

```powershell
(Get-Content <path> -Encoding UTF8).Count
```

🔴 **這兩個參數都不能省**（2026-08-30 體檢實測，這裡原本寫的
`(Get-Content <path> | Measure-Object -Line).Lines` 系統性低估 18–35%）：

| 檔案 | 舊寫法說 | 實際 | 誤差 |
|------|---------|------|------|
| `db/models.py` | 1,389 | **2,121** | −35% |
| `routers/api_finance.py` | 1,503 | 1,843 | −18% |
| `core/finance_logic/_core.py` | 1,067 | 1,307 | −18% |

兩個獨立的錯誤疊在一起：`Measure-Object -Line` **會把空行丟掉**（占 15–20%）；
`Get-Content` **不指定編碼時在含中文的檔上會少切行**（`db/models.py` 少 443 行、
`api_finance.py` 少 169 行）。用位元組 `\n` 數交叉驗證過，`-Encoding UTF8` 那個是對的。

低估的方向正好是危險的那一邊：`db/models.py` 回報 1,389 看起來安全，於是整檔讀
下去 —— 而它 2,121 行，會被靜默截掉四分之一，而且它是全 repo 第二常改的檔。
**Bash 那側用 `wc -l` 即可**（它數 `\n`，沒有這兩個問題）。

需要全貌時產生即時清單（破千行檔案，2026-08-30 用對的量法重數是 30 個；
2026-09-03 app.js 已拆到 ~1,000 行、db/models.py 已拆成套件；剩下仍超過 2,000 硬上限的：core_engine.py、
finance/subviews/recon.js、crm-cashbook.js —— 以 `tests/unit/test_files_stay_readable.py` 的現值為準）：

```powershell
Get-ChildItem -Recurse -Include *.py,*.js -Exclude node_modules |
  Where-Object { $_.FullName -notmatch '\\(\.venv|node_modules|python_embed|__pycache__|dist)\\' } |
  ForEach-Object { [PSCustomObject]@{ Lines=(Get-Content $_.FullName -Encoding UTF8).Count; File=$_.FullName } } |
  Where-Object Lines -gt 1000 | Sort-Object Lines -Descending
```

財務目錄與那 5 個超標檔由 `tests/unit/test_finance_files_stay_readable.py` 守著
（它用 Python 的 `splitlines()`，量得是對的），超過上限會直接紅。

**超過 500 行的檔案，強制使用 `offset` + `limit` 分段讀取。禁止一次讀完後假裝看到了全部內容。**

### 規則 C：長對話中，編輯前必須重讀檔案

當對話累積超過約 10 輪工具呼叫，系統會自動壓縮歷史。之前讀過的檔案內容可能已經被丟掉，但 AI 不知道自己忘了什麼，會用幻覺補上。

**編輯任何檔案前，一律重新讀取目標區段。不准信任記憶中的程式碼。**

特別注意：
- `crm-projects.js` 的 `window.*` 函式定義散布在整個檔案，不讀完不要猜行號
- `routers/crm/*.py` 的端點順序經常變動，不要假設某個函式在「大約第 X 行」
  （`routers/api_crm.py` 本身只剩 20 行的薄殼，早就拆到 `routers/crm/` 了）

### 規則 D：搜尋結果永遠要懷疑

工具結果超過 50,000 字元會被截斷。搜尋結果看起來只有 3 筆，實際上可能有 47 筆。

**結果看起來太少，就要縮小範圍重搜。特別是在重構、重命名、刪除時——漏掉一個引用就是 bug。**

本專案常見陷阱：
- `window._costXxx` 函式在 `.js` 和 `.html`（`onclick`）兩處都有引用
- CRM 的 category 選項在 `.js`（`_buildEditFields`）和 `.html`（`<select>`）各一份
- 新增模組權限要改 3 處：`core/auth.py` `ALL_MODULES`、`frontend/js/shared/tab-config.js`、`frontend/js/admin/user-mgmt.js` `MODULE_LABELS`（RBAC v2 已無 roles.json，別把它創回來）

### 規則 E：重構前先清垃圾

Dead code、unused import、orphaned props，每一行都在吃 token，加速觸發上下文壓縮。

**大型重構前，先開一個 commit 專門清理，再開始真正的工作。** 清理範圍：
- 已移除功能的殘留 CSS class
- 不再使用的 `window.*` 函式
- 被註解掉超過 2 週的程式碼（直接刪除，git 有歷史）
- 空的或只有 `pass` 的函式

### 規則 F：複雜任務拆成子 Agent 並行

單一 Agent 的工作記憶約 167K tokens，超過會觸發壓縮導致幻覺。

**任何涉及超過 5 個獨立檔案的任務，必須拆成子 Agent 分頭進行：**
- 後端 API + DB Model → 一個 Agent
- 前端 HTML + JS + CSS → 一個 Agent
- 測試 → 一個 Agent

不要讓一個 Agent 硬扛到上下文崩潰後再補救。崩潰後的修復成本遠高於拆分成本。

### 規則 G：對外網站（Phase M）新功能必須走 SEO 鐵閘

任何加在 [`website/src/pages/`](website/src/pages/) 的新 `.astro` 頁面，
[`BaseLayout`](website/src/layouts/BaseLayout.astro) 透過 TypeScript 強制：

- `description: string` 必填（30-200 字，每頁獨立寫，不要 fallback 到全站預設）
- `schemaData: SchemaObject[]` 必填（至少 1 個 schema）

**兩種寫法**：

通用頁面 — 用 [`buildBasicPageSeo`](website/src/lib/seo.ts)：
```astro
import { buildBasicPageSeo, breadcrumb2 } from "../lib/seo";
const seo = buildBasicPageSeo(Astro, {
    title: "新功能頁",
    description: "60-160 字 SEO 描述...",
    breadcrumbs: breadcrumb2("新功能頁", "/path"),
});
---
<BaseLayout {...seo} meta={meta}>
```

特殊 schema — 從 `pageSchemas` 工廠挑（12 種）：
`organization / localBusiness / website / webPage / breadcrumb / faqPage /
service / aggregateRating / review / testimonialBundle / videoObject / newsArticle`

build 末段 [`integrations/seo-audit.mjs`](website/integrations/seo-audit.mjs)
還會掃 `dist/*.html` 把關 title / desc 30-200 字 / canonical / JSON-LD /
`<h1>` / `<img alt>` — 缺任一 → build fail。

**admin 可編輯的 SEO 內容**（FAQ / Testimonial / QuickFact / OG image /
llms.txt / indexable / ai_allow 等）8 步流程：

1. DB model → `db/models_website/`（sortable + visible + timestamps 慣例）
2. Migration → `db/migrations_website.py` 加 `CREATE TABLE IF NOT EXISTS`
3. Pydantic schema → `core/schemas_website.py`（Create/Update/Response 三件套）
4. Service → `services/website/seo_service.py` 用 `_create/_update/_delete/_list` 泛型
5. Router → `routers/website/admin_seo.py` 用 `_register_crud(...)` 工廠
6. 公開 endpoint → `routers/website/public.py`（visible_only=True）
7. 前端 admin card → `frontend/tabs/website/subviews/seo.js`（7 cards）
8. Astro page → fetch + 套 `pageSchemas` 工廠

任一寫入觸發 `rebuild_service.mark_dirty()` → 60s debounce → Astro rebuild
→ 對外網站立即更新。

**完整 checklist**：[`docs/NEW_PAGE_CHECKLIST.md`](docs/NEW_PAGE_CHECKLIST.md)

---

## 10. 未來規劃 (Roadmap)

- [x] **TTS 引擎升級**：XTTS v2 (Coqui) → F5-TTS（零樣本聲音複製）
- [x] **台灣正音引擎**：JSON 字典驅動的文字預處理（vocab_mapping + pronunciation_hacks）
- [x] **TTS 前端三子頁**：標準 TTS / 聲音複製 / 正音字典編輯器
- [x] **正音字典 API**：GET/POST `/api/v1/tts/dictionary`，支援熱更新
- [x] **純 HTTP OTA 更新**：移除 NAS SMB 依賴，Agent 從主控端 HTTP 下載輕量更新 ZIP（~200KB）+ 更新防卡死
- [x] **RBAC 權限系統**：v2 已移除角色層 — 權限直接綁帳號（`modules[]` + `access_level`），模組級權限守衛
- [x] **Google OAuth 登入**：GIS Credential 模式，自動建立使用者，支援帳號連結
- [x] **使用者管理 UI**：美化 Modal（取代 prompt()）、逐帳號模組勾選 + 管理員開關、Google 頭像顯示
- [x] **TTS 整合完善**：已接入任務佇列（`enqueue_job`）+ Socket.IO 即時進度 + 完成通知
- [x] **運算主機合併至機器狀態**：NAS 共享 agents.json，各 TAB host checkbox 即時同步
- [x] **機器狀態即時監控**：綠燈脈衝 / 紅燈離線 / 橘燈慢 + CPU 顯示 + 版本號
- [x] **開機自動啟動**：Windows Startup 捷徑 + `start_hidden.vbs`
- [x] **書籤 + 排程系統**：儲存常用設定、cron 定時排程、排程中 badge
- [x] **CRM 客戶管理 Tab**：CRUD + CSV 匯入
- [x] **CRM 專案管理 Tab**：CRUD + 財務系統（合約/帳務/雜支/財務摘要）+ CSV 匯入
- [x] **CRM 報價管理 Tab**：報價 CRUD + 項目明細 + 範本 + 統計儀表板 + 內部成本 + 利潤率
- [x] **CRM 人力資源 Tab**：人員庫 + 專案派工 + CSV 匯入 + 收款人聯動
- [x] **CRM 帳務管理 Tab**：發票/請款/收支明細/應付帳款/應收帳款 五子視圖 + CSV 匯入
- [x] **CRM 雙向整合**：專案↔報價↔人力↔帳務 互通
- [x] **CRM 手機版 RWD**：`/expense.html`（雜支）+ `/invoice.html`（發票）
- [x] **CRM 全模組 Inline 編輯**：詳情面板直接編輯，不需 Modal
- [x] **OTA 更新強化**：ZIP 瘦身（381MB→567KB）+ 解壓前殺 port 8000 + `system/restart` fallback + 發布流程自動驗證
- [x] **串接錯誤隔離**：worker 迴圈獨立 try-except + 大量檔案分批預合併避免 WinError 1455
- [x] **空拍寫入 Tab**：掃描影片 + metadata 寫入 + 修剪/色彩調整 + 排列編輯器
- [x] **空拍排程監控**：每日指定時間掃來源根目錄 → 自動轉檔 + 串帶到目的地；完成推 Google Chat/LINE；取消全部按鈕；「檢視轉檔設定」popover（dirty 項與主面板比對）
- [x] **製作串帶對齊空拍寫入**：Payload 補齊 tint/影調/curves/xfade；主面板縮圖網格（複用 clip_card.js）；新增來源三鈕（多選檔案/資料夾/空白列）；Modal 依可見 tab 自動切換資料源
- [x] **`/publish` skill**：一鍵發布 → 驗證 OTA → 推送全部代理 → 驗證更新結果
- [ ] **報價 PDF 輸出**：Jinja2 模板 + playwright 轉 PDF
- [ ] **J-3 備份 Tab 整合**：選專案自動帶入路徑
- [ ] **行動端適配**：目前 UI 針對大螢幕優化，行動端排版仍需加強
- [x] **Phase M：對外官方網站（✅ 已上線，持續迭代）** — `originsun-studio.com`，2026-04-29 完整版 A 部署完成（NAS 容器 24/7 對外）；後續持續加功能（AI SEO、英文翻譯、301 轉址、結案上架收件匣等）。完整規劃：[`docs/WEBSITE_ARCHITECTURE.md`](docs/WEBSITE_ARCHITECTURE.md)

---

## polish

polish.base: master
polish.test: .venv\Scripts\python.exe -m pytest tests/unit -q

> `tests/integration`／`tests/e2e` 的 fixture 會自己拉起一個真伺服器（`tests/conftest.py` 的 `real_server`），
> 不是純函式測試，所以 /polish 的「全套測試」以單元套件為準（publish gate 也是跑 `tests/unit`）。
> 這條分支相對於 master 的 diff 極大（整條 feature/website-m），跑 /polish 時請用 `focus on <範圍>` 縮小。

---

## 模組職責

> /polish 逐次累積：**這次新增或大改的模組**各一行。不是全 repo 目錄表（那在第 3 節）。

| 模組 | 職責 | 邊界 |
|------|------|------|
| [`core/quotation_pdf.py`](core/quotation_pdf.py) | 報價單 PDF 的**純檢視模型**：分組小結、金額字串、專案優惠倒算、備註組成、有效期預設、檔名 | 無 I/O、不碰 DB／設定檔；金額規則改這裡並補測試 |
| [`services/html_pdf.py`](services/html_pdf.py) | HTML 字串 → A4 PDF 的**唯一**管線（Playwright）＋ Jinja2 渲染＋圖檔轉 data URI | 報價單與人員履歷共用；**不准再有第二處 inline `async_playwright`**（測試釘著） |
| [`templates/quotation_pdf.html`](templates/quotation_pdf.html) | 報價單版面（2026-09-06 owner 定稿） | 視覺正本是 [`frontend/demo/quotation-pdf.html`](frontend/demo/quotation-pdf.html)，**先改示範頁再同步模板** |
| [`frontend/js/shared/quote-file.js`](frontend/js/shared/quote-file.js) | 報價 PDF 檔名規則（`YYYYMMDD_客戶_專案_源日報價單.pdf`） | 零 import 葉節點；桌機 `crm-utils` 與手機 `m/shell.js` 各 re-export 一份，**別再拼第二份檔名** |
| `frontend/m/shell.js` 的 `mdownload` | 手機版帶權限下載（blob；401 導回登入） | 手機分頁只准 `import './shell.js'`，下載一律走它，不要自己 `fetch` |
| [`frontend/js/shared/quote-amounts.js`](frontend/js/shared/quote-amounts.js) | 報價金額試算（小計／折扣／稅無條件捨去／總計）＋付款階段字串互轉 | 零 import 葉節點，桌機與手機共用；**算法逐字對齊 `routers/crm/quotes._calc_quotation`**（畫面數字＝存進去的數字） |
| [`routers/crm/quotes.py`](routers/crm/quotes.py) 的 PDF／分享段 | 報價 PDF 出口（`_quotation_pdf_response`）、寄出即存檔（`archive_quotation_pdf_now` 背景）、線上檢視短碼（`share_quotation`／`/q/{code}`）、報價單資料夾設定 | 渲染只走 `_render_quotation_html`；印 PDF 不帶 `web_pdf_url`（否則「下載 PDF」列會印進去）；草稿 POST /share 回 422 |
| [`routers/crm/work_stages.py`](routers/crm/work_stages.py) | 工作階段（每個分類自己的階段清單）CRUD | 守衛 `_stage_guard`＝管理員／工作追蹤模組／綁定人員且在職（status 空白視同在職）；停用不刪，`used==0` 才准刪 |
| [`routers/api_system.py`](routers/api_system.py) 的公司圖上傳 | Logo／印章上傳＋取回（看檔頭不看副檔名；同 kind 只留一份） | 存 `company_assets/`（gitignore、不掛靜態）；PDF 端讀 `settings.company.<kind>_path` |
| `core.hr_logic` 的草稿列（`PENDING_STATUS`／`row_state`） | 「填了任何一格就存、有時數才進彙整」：pending 列不算工時 | 前端 `ts-sheet.js` 的 content 判定與後端 `normalize_row` 的空白列 422 **要同一組欄位**（專案／做了什麼／備註／階段） |
| `frontend/my.html` 的「我的一週」＋ `ts-sheet.js` 的計畫列 | 個人週規劃（owner 2026-09-08）：一天一欄的板、一張卡＝一列工時（`status=plan`、沒時數，POST 帶 `plan:true`）；當天的卡就是格子裡的藍底「計畫」列；「挪到隔天」＝PUT 只帶 `work_date` | **不判有做沒做**（沒有未執行／自動對上／提醒）；只能排自己的；`row_state(plan=True)` 沒時數也是 plan，`apply_update` 不帶 plan 就沿用列上的狀態，填了時數才變 draft |
| [`core/rbac_templates.py`](core/rbac_templates.py) | 身份範本的**純規則**：合夥／在職／兼職預設鑰匙、`normalize`（丟垃圾鍵、成員鍵收成捆、子鑰匙補總開關、帳務／報價補金額檢視）、`template_for`、`diff` | 無 I/O；存在 settings `rbac.templates`，端點在 `routers/api_auth.py`（`/auth/rbac/templates`）；套用＝寫回帳號自己的 modules，**不是角色層** |
| [`core/public_access.py`](core/public_access.py) | 對外免登入面的登記表 `PUBLIC_SURFACES`（10 面：鍵、誰用、怎麼進、路徑前綴、支援模式）、`normalize`、`surface_for_path`、`current_modes`／`save_modes`、`surface_gate`（async，關閉→404） | **設定正本在共用 Postgres**（`website_settings` 的 `public_access` 列），master 與 NAS 對外容器同一份；模組層 TTL 快取 25 秒、寫入端 `invalidate()`；DB 讀不到 → 退 settings.json → 退預設（**DB 掛掉不擋人**）。守衛掛五個 router 的 `Depends`，`/q/`、`/e/`、`/register` 手動 `await`；新公開面＝登記表加一列＋確認它的 router 有掛 |
| `core/auth.py` 的捆鑰匙與 403 段 | `MODULE_BUNDLES`／`expand_modules`（捆→成員，成員齊補捆）、`MODULE_LABELS`（後端正本）、`denied_detail`／`record_denial`／`recent_denials`（403 帶原因＋300 筆環形緩衝） | 展開只在 `routers/api_auth._enrich_user`（讀帳號咽喉）與存帳號兩處；守衛用成員鍵；「探針」用 `payload_grants(check_logged_in(request), …)` 布林，**不要**拿守衛函式當 if |
| [`frontend/js/admin/user-mgmt.js`](frontend/js/admin/user-mgmt.js) | 使用者管理四個分頁：使用者（權限格＋相依說明＋最近授權不足＋以他的角度看）、API Keys、身份範本、公開區 | 前端 `MODULE_LABELS`／`MODULE_HINTS`／`PERM_PARENT` 是鏡射（鍵集由 test_rbac_module_sync 釘）；帳號的 modules 存捆，畫面只畫 `ALL_MODULES`；`_boundStaffOf` 是「綁定人員→身份」唯一算法 |
| [`core/leave_logic.py`](core/leave_logic.py) | 假勤的**純規則**：工作日／時數換算（8h＝1 天）、credit 餘額與 FIFO 分配、消假模式（free／apply／locked）、提前通知警告、政府行事曆 CSV | 無 I/O；時數一律由起迄／時段算，員工端不收 client 給的 hours（見「不要動的地方」） |
| [`services/leave_service.py`](services/leave_service.py) | 假勤的 I/O：evaluate（送單前的錯誤／警告／餘額）、核准時從時數帳扣、序列化 | 送單與 preview 走**同一支** `hours_from_body`／`evaluate`，兩條路不能各算一次 |
| [`core/milestone_logic.py`](core/milestone_logic.py) ＋ [`services/milestone_service.py`](services/milestone_service.py) | 每週專案里程碑：週的推導（延自上週／過期／延到下週）、彈窗的週 payload、整批 save | `save_week` 是**整批覆寫**：沒帶的欄位不准寫（舊分頁會清掉別人剛填的）；`_hours_by_project` 只算 `hours>0`（計畫列不算） |
| `services/timesheet_self.py` 的合併同案（`merge_day`／`undo_merge`） | 同案同分類同階段的列併成一列＋整列快照可復原 | 規則在 `core.hr_logic.merge_plan`（純函式）；沒有專案的列不併；`_SNAP_COLS` 要涵蓋 `Timesheet` 全部欄位，漏一欄復原就靜默丟資料 |
| [`core/quote_chat.py`](core/quote_chat.py) | 對話式完成報價的**純規則**：組提示（草稿快照／對話歷史／價目／截圖路徑）、把 claude 回的東西正規化成固定形狀、串流中從半截 JSON 撈 reply、模型別名白名單、清圖時把 token 換掉 | 無 I/O；**不決定價格、不算稅**（金額走 `_calc_quotation`）；提示裡不准出現 `internal_cost` |
| [`core/price_book.py`](core/price_book.py) | 報價價目的**純規則**：去重鍵（吃全形／空白差異）、「0 元是待定價不是價」、進提示的那幾行 | 無 I/O；只有**寄出**的報價會收價（草稿還在談，而且自動存每 1.2 秒一發） |
| [`frontend/js/shared/quote-patch.js`](frontend/js/shared/quote-patch.js) | 把 AI 回的 patch 套進草稿（add／update／remove／換大項目＋備註追加） | 零 import 葉節點，桌機手機共用；**編號＝攤平後的順序且跳過沒描述的空白列**，跟後端 `quote_chat.item_lines` 同一套 |
| [`frontend/js/shared/quote-wait.js`](frontend/js/shared/quote-wait.js) | 等 AI 時那句「處理中…」（跳動的點、秒數、排隊中） | 零 import 葉節點；只講**真的**狀態，不做假進度條 |
| [`frontend/js/shared/quote-delete.js`](frontend/js/shared/quote-delete.js) | 刪報價的確認規則（草稿按 OK；已寄送／已簽核要打字輸入案名） | 零 import 葉節點；**三個刪除入口**（報價分頁／專案頁子頁／手機卡片）都要走它 |
| `routers/crm/quotes.py` 的對話／價目段 | `POST`／`GET /quotations/{id}/chat`（背景跑 claude、串流 partial、stage）、`/price-items*`（清單／改／刪／歷史匯入／比對）、寄出時清截圖與收價 | **只算 patch、不直接改項目**（寫入者是前端）；互動式用自己的 `_QUOTE_CHAT_GATE`，不跟夜間 SEO 批次搶 |
| `core/subproc.run_stream` | 逐行交付 stdout 的 subprocess（串流用） | stdin／stderr／stdout 各一條執行緒；**逾時交給 `p.wait()`**，不能只在「收到一行之後」檢查 deadline |
| [`frontend/m/views/quote-chat.js`](frontend/m/views/quote-chat.js) | 手機版報價助理（全螢幕對話、拍／選截圖、串流、用價目補上） | 跟桌機同一組後端與同一支 patch 純函式；**這邊就是寫入者**，PUT 要把後端無條件覆寫的四個欄位原值帶回 |
| [`core/quote_snapshot.py`](core/quote_snapshot.py) | 「生成報價單」的純規則：快照紀錄形狀、過期判定、畫面那句話（尚未生成／內容已修改／已生成 09/10 14:30）、客戶連結網址 | 無 I/O；文案**只在這裡寫一次**（桌機手機都顯示後端給的 `label`）；檔名形狀被 `assets_host._SAFE_NAME` 綁著 |
| `routers/crm/quotes.py` 的生成／對外段 | `generate_quotation_snapshot`（產 PDF ＋ HTML → 歸檔進報價單資料夾 ＋ 寫快照進共用圖床 ＋ 刪舊快照）、`public_quote_html`／`public_quote_pdf`（**送**快照） | **產**只在 master（Playwright 在那），**送**在哪都行 —— 對外那兩支掛 `public_router`，NAS 對外容器也吃得到，master 關機客戶照樣打得開 |


## 不要動的地方

> /polish 逐次累積的地雷。動之前先讀對應那一行。

- **員工工作台「今天與這週」＝總開關＋一顆功能一把**（owner 2026-09-08）：總開關 `me_today_zone`（`core.auth.ME_ZONE_MASTER`）；
  子視圖 `me_worklog`（今天的專案紀錄＝`/me/today`）、`me_week_plan`（我的一週）、`me_team_week`（團隊的一週＋里程碑寫入）、
  `me_project_lookup`（專案查詢＝`/me/projects_burn`）；`/timesheets/mine*` 收 worklog／week_plan 任一（卡＝格子的列）；`me_plan_parttime` 兼職排班獨立。
  後端每支端點都要**總開關＋子鑰匙**（`core.identity.require_zone_staff` 兩道），不是只靠前端不畫按鈕。
  `me_profile` 只開基本資料卡；新註冊與 Google 首登預設只有它。前端 `my.html` 的 `Z1_MASTER`／`Z1_KEYS`／`Z1_VIEW_KEY`、
  權限管理的 `PERM_PARENT`（子鑰匙縮排、總開關沒開子鑰匙灰掉）都是鏡射。
  兩次一次性回填在 `main.py`（settings 旗標 `rbac.me_zone_split_backfilled`、`rbac.me_today_zone_backfilled`），跑過之後 owner 收掉的鑰匙**不會**被補回來。
  要放寬先讀「放寬守衛不能收回原本的鑰匙」那條。
- **`/api/settings/load` 是匿名端點，機密分兩層**（2026-09-08 稽核）：`_SECRET_KEYS`／`_SECRET_SUBKEYS`（簽得出 admin 的：jwt_secret、database_url、
  google secret）連管理員也不回；`_ADMIN_ONLY_SUBKEYS`（工時同步 token、四個 webhook）只回給管理員 token（設定視窗要顯示才能編）。新增機密欄位要進其中一層。
  內部重啟端點（`/internal/restart`、`/system/restart`）的金鑰字串隨 OTA 包公開，安全靠 `core.auth.via_cloudflare` 把公網那條路擋掉——**別**把金鑰換成 `_get_secret()`，機隊各自的 jwt_secret 不共用，master 會推不動 agent。
- **捆鑰匙（2026-09-08 階段 4）**：`postprod`／`preprod`／`hr` 三把捆＝`core.auth.MODULE_BUNDLES` 的成員；帳號與範本存捆、`expand_modules` 在發 token／存帳號／回填時展開成「捆＋成員」。
  守衛請繼續用**成員鍵**（`check_admin_or_module(request,'footage')`），不要拿捆當守衛鍵；新增可勾選模組仍是三處同步（`ALL_MODULES`／`PERMISSION_GROUPS`／`MODULE_LABELS` 前後端），成員鍵不進 `ALL_MODULES`（`test_module_bundles` 釘住）。
- **權限三個正本（2026-09-08 稽核後）**：`core.auth.MODULE_LABELS`（鑰匙中文名，403 detail 用它說「缺哪把」；`test_batch3_one_ruler` 釘鍵集＝ALL_MODULES）、
  `core/rbac_templates.py`（合夥／在職／兼職預設鑰匙；`normalize` 會自動配 `me_today_zone` 總開關與 `money_view`）、`core/public_access.py`（對外免登入面的登記表；設定存共用 DB，NAS 對外容器吃同一份）。
  **母帳寫入一把尺**：發票／請款／收支寫入、匯入、發票影像都是 `require_entity('parent', full)`＝crm_invoices＋money_view，跟讀取相同——不要再用 `_check_finance_auth` 單獨守寫入。
  **routers/crm 還能管理員限定的端點只有白名單那 27 支**（`tests/unit/test_batch3_one_ruler.ADMIN_ONLY_CRM`）：新端點請用分頁鑰匙守衛，真的要管理員就 owner 拍板進清單。
- **兼職排班不走 own-scope 的 `/timesheets/mine/*`**（那邊絕不收 client 給的 staff_id）：另一組 `/timesheets/plan-for/{staff_id}/*`，
  守衛 `_plan_for_ident`＝管理員／工作追蹤整區恆過，否則「綁定＋`me_plan_parttime`＋本人在職／合夥＋對方狀態是兼職」；
  只碰對方的**計畫列**（`_plan_row_of`），時數一律不收（他自己在格子填）；`timesheets.planned_by` 記排的人（在合併快照 `_SNAP_COLS` 裡）。
- **`frontend/tabs/hr_leave/hr_leave.js` 只准用雙斜線註解**：檔頭第 4 行的 API 路徑帶了一個「斜線星號」，
  檔案裡只要再出現一個「星號斜線」（加一段 JSDoc 就會），`tests/unit/_srcscan.js_code_only` 會把中間
  整段當區塊註解剝掉 —— 真的程式碼跟著消失，而測試只會說某個常數不見了。同樣的陷阱在任何「檔頭寫了
  glob 路徑」的 js 都成立。
- **員工自助的請假送單不收 `hours`／`days`**（`MeLeaveCreate`）：收了就能送「五天特休、hours: 0.5」，
  preview 顯示 40 小時、實際只從時數帳扣 0.5。時數一律由起迄／時段算；要手調時數走管理端 `LeaveUpdate`。
- **`merge_plan` 不併沒有專案的列**：鍵會塌成 `("", 分類, 階段)`，不相干的兩件事被併成一列、第二列被刪。
- **報價單無名分類只跟緊鄰的無名列同組**：`_group_items` 的「同名就是同一組」只適用於有名字的組，
  不然舊報價單（整批沒填分類）印出來的順序會跟輸入的不一樣。
- **`frontend/my.html` 的 script 已拆到 `frontend/js/my/`（2026-09-09，2,448→490 行）**：七支**傳統** `<script src>` 依序載入
  （shell → cards → cards-hr → zone1 → week-plan → parttime → team-week），順序＝原本的執行順序，改順序就壞。
  **刻意不是 ES module**：傳統 script 之間共用同一個全域詞法環境，頂層 `const`／`function` 跨檔可見，所以拆檔零改寫；
  改成 module 的話每支變獨立作用域，幾百個跨檔引用都要 import／export。測試讀它們用 `tests/unit/_srcscan.my_page_src()`
  （my.html＋七支串起來，同 `finance_src` 那套慣例），**不要**在測試裡自己 `repo_src("frontend/my.html")` 找函式。
  `frontend/showcase-edit.html` 仍超過單次讀取上限，改它要 offset／limit 分段讀。
- **報價單版面**：owner 逐項拍板過（無公司抬頭區塊、無上下色帶、灰表頭、總額無粗線、備註在結算下方、
  頁尾只留數字）。要調版面先開示範頁比對，別直接改模板。
- **`core.quotation_pdf.PDF_MARGIN` 與模板 `@page` 必須一致**：模板還用它算「單頁時簽章貼底」的
  `.page min-height`（297 − 上 − 下）。改一邊沒改另一邊，簽章框會浮起來。
- **給既有 payload 加新欄位一律 `Optional[...] = None`，寫入端 `if x is not None`**：Cloudflare 給
  `.js` 4 小時瀏覽器快取，舊分頁的 PUT 不帶新欄位，用 `str = ""` 會把別人剛填的值洗掉
  （見 `reference_cloudflare_js_cache`；`crm_quotations.spec` 就是這樣修的）。
- **設定 Modal 的分區只在欄位真的在 DOM 裡才送**：舊 html 配新 js 會把整區寫成空字串
  （`readCompany()` 回 `null` 就整個不送，靠後端 merge-on-save 留住原值）。
- **範本彈窗是從工具列開的**，那時報價彈窗必定關著 —— 別再寫「報價彈窗開著嗎」來分流，那條走不到。
- **Playwright 測手機頁**：塞 token 進 localStorage 後要**換 URL 重載**（同頁只改 hash，殼不會重跑登入閘門）。
- **`update_quotation` 只寫 `req.model_fields_set` 裡有的欄位**（status／quote_date／valid_until／discount）：手機的 PUT
  不帶它們，整包寫回曾把報價日期洗成 NULL、舊折扣洗成 0。桌機刻意送 `null` 清空 valid_until 仍會清，別改成 `is not None`。
- **桌機報價彈窗「正在編哪一筆」看 `_editingQuote`，不准從 `_quotations.find` 查**：那份清單有狀態篩選，
  從專案頁開的草稿查不到就被當成新增（要客戶、折扣歸零）。`quoteDup` 要把 `_editingId`／`_editingQuote` 一起清。
- **放寬守衛不能收回原本的鑰匙**：`/timesheets/project_options` 與 `work-stages` 都曾在「開放給員工」時把
  只有 timesheets／me_finance、沒綁人員的帳號從 200 變成 403／409。改守衛先列出舊守衛放行的每一種帳號再改。
- **請款彈窗 `_updateExtraFields` 重建專案下拉時要帶回目前選的值**：openModal 先選好再呼叫它，重建成空的會把編輯中的專案洗掉。
- **計畫列（`status=plan`）改內容一定要帶著 plan**：格子的 PUT 靠 `tr.dataset.plan → body.plan`、後端靠 `apply_update` 沿用；少一邊，員工改個字那張卡就變「草稿（沒時數）」並被「專案紀錄未完成」提醒抓到（提醒只找 pending，這是刻意的）。
- **`<tr class="ts-mine-row">` 那個 class 字串不能改**（test_ts_shared_components 釘「列 html 只有一份」是找這個字串）：計畫列的藍底走 `data-plan` 屬性選擇器。
- **週記心情選單掛在 body、`position:fixed`**：捲動／resize 要關掉，不然它脫離愛心浮在原地；別再塞回標題列（會被右邊界裁）。
- **報價 patch 的編號＝「攤平後、跳過沒描述的空白列」**：後端 `quote_chat.item_lines` 只看得到 DB 裡的項目（存檔前 `filter(it => it.description)` 過濾過），編輯器裡卻隨時有空白列。`quote-patch.flattenForPatch` 跟著濾掉，兩邊才是同一套；不濾的話 AI 說「改第 3 項」會落在第 2 項、而且改到的是存檔時會被丟掉的那列。送出前一定要先把畫面落地（桌機 `_persistNow`、手機 `save()`），否則順序對不上。
- **`update_quotation` 對 `tax_rate`／`final_price`／`payment_stages`／`terms` 是無條件覆寫**（那幾行不看 `model_fields_set`，跟 status／quote_date／valid_until／discount／spec 不同）：任何 PUT 少帶一個就是靜默清掉它。手機版 `quote-chat.js` 的 `save()` 因此要把載進來的原值原樣帶回。
- **`_autoOn` 只管「打字要不要 debounce」**：編輯既有報價時它是關的（怕靜默改到舊資料）。所有「動過草稿就要立刻落地」的路徑（AI patch／用價目補上／送出前同步）一律走 `_persistNow`，它**無條件 PUT**、不看 `_autoOn`／`_autoDirty` —— 曾經寫過照旗標分岔的版本，在「新增」那條路上完全不存。
- **`frontend/tabs/crm/crm-quotes.html` 的隱藏 `<textarea id="quote-f-terms">` 是相容殼**：CF 給 `.js` 4 小時快取、html 即時，發版後會出現「新 html ＋ 舊 crm-quotes.js」，而舊的 `openModal` 直接讀它 —— 元素不在＝TypeError＝報價彈窗打不開。**發版滿一輪之後**連同 `test_quote_autosave_and_reorder` 那條斷言一起刪。
- **原始碼裡不要出現 `image` 加斜線星號**（HTML 的 `accept` 屬性）：`tests/unit/_srcscan.js_code_only` 會把它當成區塊註解開頭，到下一個「星號斜線」之間的程式碼整段消失，掃原始碼的測試就看不到那些函式（手機版的 `save()` 一度整支不見）。`accept` 改在 JS 裡設。
- **`--model <值>` 會變成 claude CLI 的參數**：前端送什麼就接什麼等於讓瀏覽器往 CLI 塞旗標，一律過 `quote_chat.pick_model()` 的白名單。
- **`fire()` 不是 `background.add_task`**：FastAPI 的 background 串是**串行**的，寄出報價時第一支是 Playwright 產 PDF，它慢或炸掉，後面的清截圖與收價就整串不跑而且靜默。
- **`_load_items()` 回的已經是 dict**（它自己套過 `_item_to_dict`）：再包一次會 AttributeError，包在背景任務裡就是無聲失敗。
- **報價單快照的過期判定比對「來源版本」，不是時間先後**（`quote_snapshot.is_stale`）：產 PDF 要好幾秒，中間有人存了一次的話「生成時間比較晚」永遠成立，會把舊內容判成新鮮的、而且完全靜默。所以 `src` 記的是**開始渲染時讀到的** `updated_at`，回寫時**不准動 `updated_at`**（動了就是生成完當下立刻過期）。判不出來一律當過期。
- **`public_quote_html`／`public_quote_pdf` 在 `public_router` 上＝對外曝露面**：那個 router 是 NAS 對外容器唯一掛的東西。加／改端點要同步兩支白名單測試（`test_media_log_public_router` 的 `EXPECTED`、`test_public_surface` 的 `EXPECTED_API`），而且兩支都要自己 `await surface_gate(request)` —— master 的 `/q/{code}` 有擋一次，但 NAS 那條沒有經過 `main.py`。
- **快照檔名必須是 32 hex ＋ 2-5 碼副檔名**：`core.assets_host._SAFE_NAME` 是圖床唯一的刪除路徑，名字不合它的規矩＝重新生成時舊快照刪不掉，一張一張永遠躺在 NAS 上而且舊網址還通（客戶可能拿到過期版本）。
- **報價單是定稿文件，不是活的頁面**（owner 2026-09-10）：`/q/{code}` 送的是生成好的檔，不即時重算。改回「即時渲染」會同時打掉兩件事 —— 客戶手上那份會隨你改東西靜默變動，而且對外那條路又綁回 master 開機。即時渲染只保留給「還沒生成過的舊連結」（`_live_quote_fallback`），而且它會順手補生成一份。
  **三個入口**（報價分頁／專案頁的報價子頁／手機卡片）都編得動報價，所以三個都要顯示 `pdf_state.label`
  ——漏掉的那個不會報錯，只會讓人改完毫無察覺、客戶繼續拿到舊版（同 `quote-delete.js` 那條「三個入口」的理由）。
