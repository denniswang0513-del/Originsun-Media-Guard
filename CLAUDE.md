# Originsun Media Guard Pro — Claude Code 完整交接文件

> **版本**: v1.10.98（2026-04-28）
> **目標讀者**: 接手開發的 AI 協作者（Claude Code）
> **開發環境**: Windows 11、Python 3.11、Vanilla JS (ES Modules)
> **啟動方式**: `d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe main.py`
> **🚀 進行中專案**: Phase M — 對外官方網站（`originsun-studio.com`）
> • 開發：Windows 本機（`website/` 目錄、Astro + Tailwind、localhost:4321）
> • 部署：**100% 在 NAS 192.168.1.132**，路徑 `/share/Container/AI_Workspace/Originsun_Web/Website/`
> • 新增容器 2 個：**專用** `Website_Nginx`（port 8081→80）+ `website-api`（8001 內部），在新 bridge `originsun_web`
> • 既有 5 容器全部不動：cloudflared (macvlan) / FileReport_Nginx / originsun_postgres / MCP / n8n
> • cloudflared 複用：在 CF Zero Trust 儀表板加 public hostname `originsun-studio.com → 192.168.1.132:8081`
> • NAS 入口 `main_website.py`（只載 `routers/website/`）；Windows 既有 `main.py` 不動
> • 官網管理 Tab 前端在 Windows、fetch 跨機呼叫 `http://192.168.1.132:8081/api/website/admin/*`（JWT secret 共用）
> • 分支 `feature/website-m`，完整規劃見 [`docs/WEBSITE_ARCHITECTURE.md`](docs/WEBSITE_ARCHITECTURE.md)

---

## 0. 新 Session 自動簡報

每次開啟新對話時，**第一件事**執行 `/briefing`，讓使用者快速掌握專案現況、ROADMAP 進度、未提交變更、開發規則。

**例外**：如果使用者的第一句話是緊急指令（如 "fix..."、"快修..."、"緊急..."），則跳過簡報直接處理任務。

---

## 1. 專案背景與定位

這是一個**內部媒體管理 SaaS 工具**，部署在製作公司的本機伺服器（`192.168.1.11:8000`），提供給同事用網頁瀏覽器存取。核心工作流程是：

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

```powershell
# 開發模式（無熱重載）
d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe main.py

# 開發模式（有熱重載，推薦）
# 注意：hot reload 只能用於 FastAPI 路由，不包含 tts_engine.py 頂部的 patch 副作用
d:\Antigravity\OriginsunTranscode\.venv\Scripts\uvicorn.exe main:io_app --host 0.0.0.0 --port 8000 --reload

# 背景無視窗啟動（正式部署方式）
wscript.exe start_hidden.vbs
```

服務啟動後，前端在 `http://localhost:8000`（或 `http://192.168.1.11:8000`）。

---

## 3. 完整目錄結構

```
OriginsunTranscode/
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
├── core_engine.py           # 核心引擎類別 MediaGuardEngine（約 1550 行）
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
├── transcriber.py           # Whisper 語音辨識（約 500 行）
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
│   ├── report_job.py        # 視覺報表非同步任務（約 240 行）
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
│  ── 【routers/ — FastAPI 路由模組（12 個）】
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
│   └── api_crm.py           # CRM 客戶管理 API（見 7.15）— 客戶 CRUD + CSV 匯入 + AM 使用者列表
│
│  ── 【認證與權限】
├── core/
│   ├── auth.py              # JWT 認證 + RBAC 權限守衛（check_admin, get_current_user, find_role_by_name）
│   └── google_auth.py       # Google ID Token 驗證（GIS credential 模式，不需 client_secret）
├── db/
│   ├── models.py            # SQLAlchemy ORM（User + Role + Client 表，User 含 google_id/email/avatar_url）
│   └── session.py           # DB 連線 + init_db() + 預設角色/使用者 seed
│
│  ── 【frontend/ — 靜態前端（SPA）】
├── frontend/
│   ├── index.html           # 主殼層（約 450 行）
│   │                        #   - 頁籤切換邏輯（點擊 tab 按鈕 → 顯示對應 section）
│   │                        #   - 各頁籤的 HTML 結構都在這個檔案內
│   │                        #   - 底部載入 app.js 作為 type="module"
│   │
│   ├── app.js               # 主要應用邏輯（約 2200 行）
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
│   │   └── utils.js         # 前端共用工具（ES Module，約 220 行）
│   │                        #   - resolveDropPath(e, file, index)：4 種策略解析拖放路徑
│   │                        #   - appendLog(msg, type)：寫入 terminal
│   │                        #   - pickPath(inputId, type)：呼叫後端 pick_folder/pick_file
│   │                        #   - setupInputDrop(inputId)：讓單一 input 支援拖放路徑
│   │                        #   - setupDragAndDrop(containerId, addRowFunc)：讓列表支援拖放多個路徑
│   │                        #   - addStandaloneSource(listId)：動態新增來源列
│   │                        #   - resetProgress()：重置所有進度條與狀態
│   │                        #   - 所有函式也掛到 window.* 供非 module 程式碼呼叫
│   │
│   └── tabs/                # 各功能頁籤目錄（每個頁籤嚴格隔離，各含 .html + .js）
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
├── tests/
│   ├── conftest.py          # 共用 fixtures（tmp_settings, mock_engine, async_client, real_server）
│   ├── test_smoke.py        # Smoke tests（5 個：health, version, settings, frontend, socketio）
│   ├── unit/                # 單元測試（20 個）
│   │   ├── test_taiwan_normalizer.py  # 台灣正音引擎（8 個測試）
│   │   ├── test_config.py             # 設定讀寫（6 個測試）
│   │   └── test_core_engine.py        # 核心引擎（6 個測試）
│   ├── integration/         # 整合測試（30 個）
│   │   ├── test_api_system.py         # 系統 API（7 個測試）
│   │   ├── test_api_queue.py          # 佇列管理 API（8 個測試）
│   │   ├── test_api_validate_paths.py # 遠端路徑驗證 API（8 個測試）
│   │   ├── test_task_queue.py         # 任務佇列（3 個測試）
│   │   └── test_conflict_resolution.py # 衝突解決（4 個測試）
│   └── e2e/                 # 端對端測試（20 個）
│       ├── conftest.py      # Playwright fixtures
│       ├── test_ui_basic.py           # 基本 UI（5 個測試）
│       ├── test_task_flow.py          # 任務流程（3 個測試）
│       ├── test_projects_tab.py       # 專案總覽 UI（8 個測試）
│       └── test_validate_remote_paths.py  # 遠端路徑驗證前端（4 個測試）
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

- **雙寫架構**：使用者資料同時存 `users.json`（JSON 檔）和 SQLite DB（`db/models.py`）。
  每次修改必須呼叫 `_persist_user(user_data)` 統一寫入，不可分開呼叫 `sync_user_to_json` + `_save_user_to_db`。
- **Google OAuth 使用 GIS Credential 模式**：不需 client_secret，不需 redirect URI。
  前端載入 `accounts.google.com/gsi/client`，Google 返回 ID Token JWT，後端用 `google-auth` 庫驗證。
- **DB Migration**：`main.py` startup 用 `ALTER TABLE users ADD COLUMN IF NOT EXISTS` 加欄位，
  不另建 migration 檔案。新增欄位時加在同一個迴圈裡。
- **`_find_user_by(column, value)`**：統一的使用者查找函式，支援 DB 和 JSON fallback。
  不要再新增 `_find_user_by_xxx` 單獨函式。
- **前端登入成功統一用 `_onLoginSuccess(d)`**，不要在密碼和 Google 兩條路徑各寫一次。
- **`_createFormModal(opts)`**：`app.js` 中的通用表單 Modal 建構函式，
  支援 text/password/select/checkboxes，新增表單彈窗時直接複用。

---

## 6. 前端架構詳解 (SPA & WebSockets)

### 6.1 頁籤動態載入邏輯 (`app.js` → `loadTabs()`)

為了維持首頁開啟速度並落實模組化，本機代理使用**非同步動態載入**頁籤：

1. `app.js` 啟動後執行 `loadTabs()`。
2. 對每個頁籤執行 `fetch('./tabs/<name>/<name>.html')`。
3. 取得內容後填入 `index.html` 的對應 `section`。
4. 使用 `await import('./tabs/<name>/<name>.js')` 動態載入該頁籤的邏輯。
5. 呼叫 `module.initTab()` 完成初始化。

**載入順序**：backup → verify → transcode → concat → report → transcribe → tts

**容錯設計**：TTS 頁籤的載入包裹在獨立 try-catch 中，即使載入失敗也不影響其他頁籤。

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


### 7.15 CRM 模組（`routers/api_crm.py` — 88 端點）

CRM 系統包含 6 個獨立 Tab + 帳務管理的 5 個子視圖：

| Tab | 前端檔案 | 端點數 | DB 表 |
|-----|---------|--------|-------|
| 🤝 客戶管理 | `crm/crm.html` + `crm.js` | 7 | `clients` |
| 📁 專案管理 | `crm/crm-projects.html` + `.js` | 14 | `crm_projects`, `crm_project_staff`, `crm_project_expenses` |
| 💰 報價管理 | `crm/crm-quotes.html` + `.js` | 13 | `crm_quotations`, `crm_quotation_items`, `crm_quotation_templates` |
| 👥 人力資源 | `crm/crm-staff.html` + `.js` | 11 | `crm_staff` |
| 🧾 帳務—發票 | `crm/crm-invoices.html` + `.js` | 6 | `crm_invoices` |
| 🧾 帳務—請款 | `crm/crm-payments.html` + `.js` | 7 | `crm_payment_requests` |
| 🧾 帳務—收支明細 | `crm/crm-cashbook.html` + `.js` | 5 | `crm_cash_entries` |
| 🧾 帳務—應付帳款 | `crm/crm-payables.html` + `.js` | 3 | (視圖，含 batch-month) |
| 🧾 帳務—應收帳款 | `crm/crm-receivables.html` + `.js` | 2 | (視圖，查詢 `crm_invoices`) |

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
1. DB Model → `db/models.py`（新欄位需在 `main.py` startup 加 `ALTER TABLE ADD COLUMN IF NOT EXISTS`）
2. Schema → `core/schemas.py`
3. API → `routers/api_crm.py`
4. 前端 → `frontend/tabs/crm/` 對應 `.html` + `.js`
5. 帳務子視圖用 lazy-load，import 路徑必須用 `location.origin` 絕對路徑
6. RBAC 4 處：`roles.json`, `db/session.py`, `user-mgmt.js`, `auth-state.js`
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
| POST | `/api/v1/auth/users` | 新增使用者（需 admin） |
| PUT | `/api/v1/auth/users/{username}` | 修改使用者角色/密碼（需 admin） |
| DELETE | `/api/v1/auth/users/{username}` | 刪除使用者（需 admin） |
| GET | `/api/v1/auth/google/config` | 取得 Google OAuth 設定（公開） |
| POST | `/api/v1/auth/google/login` | Google ID Token 登入 |
| GET | `/api/v1/roles` | 列出所有角色 |
| POST | `/api/v1/roles` | 新增角色（需 admin） |
| PUT | `/api/v1/roles/{id}` | 修改角色權限/模組（需 admin） |
| DELETE | `/api/v1/roles/{id}` | 刪除角色（需 admin） |

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
d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe -m py_compile <modified_file>
```
前端 `.js` / `.html` 修改後，必須確認：
- HTML div/tag 平衡（特別是 CRM 巢狀模板字串）
- `window.*` 函式名稱與 `onclick` 引用一致
- ES Module import 路徑正確

**全部通過才能回報完成。測試失敗就說失敗，不要粉飾。沒有跑過驗證就不要暗示通過了。**

### 規則 B：大檔案必須分段讀取

每次讀檔有 2,000 行的硬上限，超過的部分會被截斷——AI 不會告訴你它沒看完。本專案的大檔案清單：

| 檔案 | 約行數 | 必須分段 |
|------|--------|----------|
| `routers/api_crm.py` | 2900+ | ✓ |
| `frontend/app.js` | 2200+ | ✓ |
| `core_engine.py` | 1550+ | ✓ |
| `frontend/tabs/crm/crm-projects-cost.js` | 1000+ | ✓ |

**超過 500 行的檔案，強制使用 `offset` + `limit` 分段讀取。禁止一次讀完後假裝看到了全部內容。**

### 規則 C：長對話中，編輯前必須重讀檔案

當對話累積超過約 10 輪工具呼叫，系統會自動壓縮歷史。之前讀過的檔案內容可能已經被丟掉，但 AI 不知道自己忘了什麼，會用幻覺補上。

**編輯任何檔案前，一律重新讀取目標區段。不准信任記憶中的程式碼。**

特別注意：
- `crm-projects.js` 的 `window.*` 函式定義散布在 1700 行中，不讀完不要猜行號
- `api_crm.py` 的端點順序經常變動，不要假設某個函式在「大約第 X 行」

### 規則 D：搜尋結果永遠要懷疑

工具結果超過 50,000 字元會被截斷。搜尋結果看起來只有 3 筆，實際上可能有 47 筆。

**結果看起來太少，就要縮小範圍重搜。特別是在重構、重命名、刪除時——漏掉一個引用就是 bug。**

本專案常見陷阱：
- `window._costXxx` 函式在 `.js` 和 `.html`（`onclick`）兩處都有引用
- CRM 的 category 選項在 `.js`（`_buildEditFields`）和 `.html`（`<select>`）各一份
- RBAC 權限要改 4 處：`roles.json`、`db/session.py`、`user-mgmt.js`、`auth-state.js`

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

---

## 10. 未來規劃 (Roadmap)

- [x] **TTS 引擎升級**：XTTS v2 (Coqui) → F5-TTS（零樣本聲音複製）
- [x] **台灣正音引擎**：JSON 字典驅動的文字預處理（vocab_mapping + pronunciation_hacks）
- [x] **TTS 前端三子頁**：標準 TTS / 聲音複製 / 正音字典編輯器
- [x] **正音字典 API**：GET/POST `/api/v1/tts/dictionary`，支援熱更新
- [x] **純 HTTP OTA 更新**：移除 NAS SMB 依賴，Agent 從主控端 HTTP 下載輕量更新 ZIP（~200KB）+ 更新防卡死
- [x] **RBAC 權限系統**：角色管理（admin/producer/editor/cameraman/assistant/viewer）+ 模組級權限守衛
- [x] **Google OAuth 登入**：GIS Credential 模式，自動建立使用者，支援帳號連結
- [x] **使用者/角色管理 UI**：美化 Modal（取代 prompt()）、角色切換即時更新模組、Google 頭像顯示
- [ ] **TTS 整合完善**：接入任務佇列、Socket.IO 即時進度、完成通知、SRT 批次合成
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
- [ ] **Phase M：對外官方網站（🚀 進行中）** — `originsun-studio.com` + Cloudflare Tunnel + Astro 4 + 9 子視圖管理 Tab + 從 `crm_projects` 動態撈作品；6/1 Staging 初版 / 7/1 正式切換上線。完整規劃：[`docs/WEBSITE_ARCHITECTURE.md`](docs/WEBSITE_ARCHITECTURE.md)
