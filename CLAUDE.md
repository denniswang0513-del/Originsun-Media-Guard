# Originsun Media Guard Pro — Claude Code 完整交接文件

> **版本**: v1.5.0（2026-03-16）
> **目標讀者**: 接手開發的 AI 協作者（Claude Code）
> **開發環境**: Windows 11、Python 3.11、Vanilla JS (ES Modules)
> **啟動方式**: `d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe main.py`

---

## 1. 專案背景與定位

這是一個**內部媒體管理 SaaS 工具**，部署在製作公司的本機伺服器（`192.168.1.11:8000`），提供給同事用網頁瀏覽器存取。核心工作流程是：

1. 攝影師從記憶卡（Card）將素材**備份**到本機與 NAS
2. 備份同時可選擇性觸發**轉檔**（Proxy）、**串接**（Reel）、**視覺報表**
3. 剪輯師用**比對驗證**確認素材完整性
4. **語音辨識（Whisper）** 可將影片音軌轉為逐字稿（SRT/TXT）
5. **TTS** 功能（🚧 開發中）可用 Edge-TTS 合成語音，或用 XTTS v2 克隆聲音

系統分為**主控端（伺服器）**和**代理端（同事電腦）**：
- 主控端：`main.py` 啟動的 FastAPI 服務，提供 Web UI + API
- 代理端：同事電腦上的本機 Agent（同樣一份程式碼，只是以不同 port 啟動），執行在本機的轉檔任務並回報進度

---

## 2. 快速啟動

```powershell
# 開發模式（無熱重載）
d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe main.py

# 開發模式（有熱重載，推薦）
# 注意：hot reload 只能用於 FastAPI 路由，不包含 PyTorch monkey-patch 的副作用
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
│                            #   - 最頂部有 PyTorch + torchaudio 的 monkey-patch（見第 5 節）
│                            #   - 建立 FastAPI app 並掛載 NoCacheMiddleware + CORS
│                            #   - 建立 socketio.ASGIApp(sio, app) → io_app
│                            #   - 掛載所有 router（api_backup, api_verify, api_proxy...）
│                            #   - 最後將 frontend/ 目錄 mount 為靜態檔案（html=True）
│
├── server.py                # ⚠️ 遺留的舊版單一大檔案伺服器（v1.4 以前）
│                            #   保留作為歷史參考，不再使用，不可刪除（build_agent_zip.py 會打包它）
│
├── core_engine.py           # 核心引擎類別 MediaGuardEngine（約 1200 行）
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
├── tts_engine.py            # TTS 引擎（🚧 開發中，支援 Edge-TTS + Coqui XTTS v2）
│                            #   - 頂部有 torch + torchaudio monkey-patch（與 main.py 相同）
│                            #   - run_edge_tts()：async，透過 subprocess 呼叫 edge-tts CLI
│                            #   - run_voice_clone()：async，載入 XTTS v2 模型進行聲音克隆
│                            #   - xtts_is_ready()：檢查模型目錄是否存在
│                            #   - download_xtts_model()：觸發模型下載（blocking，應在 executor 中呼叫）
│                            #   - XTTS_MODEL_PATH = ./models/xtts_v2
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
│
├── report_generator.py      # HTML 報表產生器
│                            #   - save_report()：把 ReportManifest 渲染成 HTML（Jinja2 模板）
│                            #   - generate_pdf_from_html()：用 playwright/weasyprint 轉 PDF
│
├── version.json             # {"version": "1.5.0", "build_date": "...", "notes": "..."}
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
│   └── report_job.py        # 視覺報表非同步任務（約 240 行）
│                            #   - _run_report_job(req: ReportJobRequest)：完整報表流程
│                            #   - Phase 1 Scan → Phase 2 Meta/Hash → Phase 3 Film Strip
│                            #   - → Phase 4 Render HTML + PDF → Phase 5 Publish to NAS Web Server
│                            #   - 每個 phase 透過 'report_progress' Socket 事件回報進度
│                            #   - 可暫停：每次迭代前呼叫 _check_pause()
│                            #   - 最終產出放在 {output_dir}/Originsun_Reports/ 下
│                            #   - 更新 reports_index.json（本機 + NAS 各一份）
│
│  ── 【routers/ — FastAPI 路由模組（8 個）】
├── routers/
│   ├── __init__.py
│   ├── api_backup.py        # 備份端點（見 7.1）
│   ├── api_verify.py        # 驗證端點（見 7.2）
│   ├── api_proxy.py         # Proxy 轉檔 + 分散式計算端點（見 7.3）
│   ├── api_concat.py        # 串接端點（見 7.4）
│   ├── api_report.py        # 報表端點（見 7.5）
│   ├── api_transcribe.py    # 語音辨識端點（見 7.6）
│   ├── api_system.py        # 系統工具端點（見 7.7）
│   └── api_tts.py           # TTS 端點（🚧 開發中，見 7.8）
│
│  ── 【frontend/ — 靜態前端（SPA）】
├── frontend/
│   ├── index.html           # 主殼層（約 450 行）
│   │                        #   - 頁籤切換邏輯（點擊 tab 按鈕 → 顯示對應 section）
│   │                        #   - 各頁籤的 HTML 結構都在這個檔案內
│   │                        #   - 底部載入 app.js 作為 type="module"
│   │
│   ├── app.js               # 主要應用邏輯（約 1800 行）
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
│       └── tts/             # TTS 頁籤（🚧 開發中）
│
│  ── 【模型與資料目錄】
├── models/
│   └── xtts_v2/             # XTTS v2 本機模型（約 2GB，需手動下載）
│                            #   子路徑：tts/tts_models--multilingual--multi-dataset--xtts_v2/
│                            #   xtts_is_ready() 只檢查這個路徑下的 config.json 是否存在
│
├── voice/                   # 本機聲音角色快取（執行時自動建立）
├── credentials/             # Google API OAuth 憑證（.gitignore，不可提交）
│
│  ── 【工具執行檔】
├── ffmpeg.exe               # 隨附 FFmpeg 7.x，所有影片處理都呼叫它
├── ffprobe.exe              # 隨附 FFprobe，用來讀取影片 metadata
│
│  ── 【部署與發布腳本】
├── publish_update.py        # 互動式版本發布腳本（問版本號 → 打包 → 推送 NAS）
├── build_agent_zip.py       # 非互動式打包腳本（publish_update.py 會呼叫它）
├── start_hidden.vbs         # 無黑色視窗地啟動 python main.py
├── update_agent.bat         # OTA 更新腳本（從 NAS 複製新程式碼，重啟服務）
├── Install_Originsun_Agent.bat  # 新同事安裝精靈（從伺服器下載 Agent.zip，解壓到桌面）
└── Maintainer_Guide.md      # 維護人員操作手冊
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
│   └── POST /api/v1/tts_jobs          → 直接執行（🚧 尚未接入 queue）
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
        └── report_job_done           → 報表完成 {report_name, local_path, pdf_path, public_url, drive_url}
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

### 5.1 PyTorch 2.6+ monkey-patch（`main.py` 第 1-18 行）

```python
import torch as _torch
_orig_load = _torch.load
def _safe_load(*a, **kw):
    kw.setdefault("weights_only", False)
    return _orig_load(*a, **kw)
_torch.load = _safe_load
```

**原因**：PyTorch 2.6 把 `weights_only` 預設值改為 `True`，導致 Coqui TTS 的 checkpoint（包含自訂 Python 類別）無法載入。必須在任何模組 `import torch` 之前執行這個 patch。

**千萬不要移動**：這段必須是 `main.py` 的最頂部，因為之後的 `from routers import api_tts` 會間接觸發 `tts_engine.py` 的 import，進而 import PyTorch。

`tts_engine.py` 也有相同的 patch，是為了讓 `tts_engine.py` 能被單獨 import（例如測試時）。

### 5.2 torchaudio 後端強制 soundfile（`main.py` 第 10-17 行）

```python
import torchaudio as _ta
from torchaudio._backend import utils as _ta_utils
from torchaudio._backend.soundfile import SoundfileBackend as _SfB
_ta_utils.get_available_backends.cache_clear()
_ta_utils.get_available_backends = lambda: {"soundfile": _SfB}
```

**原因**：Windows 上 torchaudio 的 torchcodec 後端（新版預設）在 XTTS v2 環境下會崩潰。強制覆蓋成只用 soundfile 後端即可正常。

**注意**：這個 patch 使用了 torchaudio 的私有 API（`_backend.utils`），如果 torchaudio 版本升級，路徑可能改變，需要確認。

### 5.3 Windows 非 ASCII 路徑問題

**問題**：libsndfile（XTTS v2 底層用來讀音訊的 C 函式庫）無法開啟路徑含中文/非 ASCII 字元的檔案。

**解法**（`tts_engine.py` 的 `_clone_sync()` 函式）：
```python
ext = os.path.splitext(reference_audio)[1]
tmp_fd, tmp_ref = tempfile.mkstemp(suffix=ext, prefix="tts_ref_")
os.close(tmp_fd)
shutil.copy2(reference_audio, tmp_ref)  # 複製到 ASCII 路徑
# ... 用 tmp_ref 做 TTS ...
os.remove(tmp_ref)  # finally block 中清理
```

### 5.4 Edge-TTS 透過子程序呼叫

Edge-TTS 的 Python API 在 Windows 上直接 `await communicate.stream()` 時，中文文字若包含全形字元有時會產生亂碼。改用 subprocess + temp file 的方式：

```python
with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
    tf.write(text)
cmd = [sys.executable, "-m", "edge_tts", "--file", tf.name, "--voice", voice, ...]
env = os.environ.copy()  # 必須繼承環境（空環境下 asyncio 在 Windows 失敗）
result = subprocess.run(cmd, capture_output=True, text=True, env=env)
```

### 5.5 `_emit_sync` — 從工作執行緒 emit Socket.IO

FastAPI 的 BackgroundTasks 和 `asyncio.to_thread()` 執行的函式在工作執行緒中運行，無法直接 `await sio.emit()`。正確方式：

```python
# core/logger.py
def _emit_sync(event: str, data: dict) -> None:
    loop = state.get_main_loop()  # 取得主 asyncio event loop
    if loop:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data), loop)
```

`state._main_loop` 由 `@app.on_event("startup")` 設定。如果在 worker 中 emit 事件前端收不到，檢查 `state._main_loop` 是否為 None。

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
| GET | `/api/v1/nas_version` | 取得 NAS 版本號 |
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
| POST | `/api/admin/restart` | 重啟 Agent 服務 |
| GET | `/download_agent` | 下載 Originsun_Agent.zip |
| GET | `/download_installer` | 下載安裝精靈 .bat |

### 7.8 TTS 語音合成（🚧 開發中，`routers/api_tts.py`）

**現況**：後端 API 和前端 UI 均已完成，但尚未接入任務佇列、Socket.IO 即時進度、完成通知。

#### 標準 TTS（Edge-TTS）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/tts_jobs` | 使用 Edge-TTS 合成語音 |
| GET | `/api/v1/tts/voices` | 列出所有可用的 Edge-TTS 聲音 |
| GET | `/api/v1/tts/preview` | 串流預覽指定聲音 |
| POST | `/api/v1/tts/estimate` | 估算 TTS 時長與字元速率 |

#### 聲音克隆（XTTS v2）

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/tts_jobs/clone` | 從參考音訊克隆聲音合成 |
| POST | `/api/v1/tts_jobs/profile` | 使用已儲存的聲音角色合成 |

#### 聲音角色管理

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/voice_profiles` | 列出 NAS 上所有聲音角色 |
| POST | `/api/v1/voice_profiles` | 新增聲音角色到 NAS |
| DELETE | `/api/v1/voice_profiles/{id}` | 刪除聲音角色 |
| POST | `/api/v1/voice_profiles/{id}/cache` | 將 NAS 角色快取到本機 |

#### XTTS 模型管理

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/v1/tts/xtts_status` | 檢查 XTTS v2 模型是否已下載 |
| POST | `/api/v1/tts/xtts_download` | 觸發 XTTS v2 模型下載 |

### 7.9 Pydantic Schema 完整清單 (`core/schemas.py`)

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
```

---

## 8. 常見問題與除錯指南 (Troubleshooting)

### Q1: 前端一直顯示「未偵測到本機代理」
1. 檢查是否已執行 `python main.py` 或 `start_hidden.vbs`。
2. 檢查 Port 8000 是否被佔用。
3. 如果是在遠端 IP 存取，確保同事電腦也有安裝並運行 Agent（瀏覽器會嘗試 fetch `localhost:8000`）。

### Q2: 語音克隆 (XTTS) 突然無法運作
1. 確認 `models/xtts_v2` 目錄下是否有內容。
2. 檢查是否有顯存 (VRAM) 溢出。
3. 檢查環境變數 `COQUI_TOS_AGREED` 是否設為 `1`。

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
4. **`server.py` 不可刪除**：雖然已不再使用，但 `build_agent_zip.py` 會打包它。

---

## 10. 未來規劃 (Roadmap)

- [ ] **TTS 整合完善**：接入任務佇列、Socket.IO 即時進度、完成通知、SRT 批次合成
- [ ] **多節點分派最佳化**：目前的 `compute_hosts` 僅能手動指定，未來希望實作基於負載的主動分派
- [ ] **行動端適配**：目前 UI 針對大螢幕優化，行動端排版仍需加強
