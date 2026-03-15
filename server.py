# ============================================================
# DEPRECATED - Please use main.py instead
# ============================================================
# This file is the old monolithic backend from v1.4.1 and earlier.
# It has been replaced by the modular architecture:
#   main.py       <- FastAPI entry point (start this instead)
#   core/         <- shared modules
#   routers/      <- per-tab routers
#
# Run: uvicorn main:app --host 0.0.0.0 --port 8000
# ============================================================

import sys
print()
print('=' * 60)
print('server.py is DEPRECATED - please run main.py instead')
print('Command: uvicorn main:app --host 0.0.0.0 --port 8000')
print('=' * 60)
print()
sys.exit(1)

# ---- Legacy code below - kept for reference only ----

import os
import sys
import asyncio
import socketio  # type: ignore
import uvicorn  # type: ignore
import threading
import socket
import webbrowser
import uuid
import json as _json
from fastapi import FastAPI, BackgroundTasks  # type: ignore
from pydantic import BaseModel  # type: ignore
from typing import List, Tuple, Any
from core_engine import MediaGuardEngine, ReportManifest, FileRecord  # type: ignore
from queue import Queue
import shutil
import filecmp
from fastapi.staticfiles import StaticFiles  # type: ignore
import tkinter as tk
from tkinter import filedialog
from fastapi.responses import FileResponse  # type: ignore

from fastapi.middleware.cors import CORSMiddleware  # type: ignore

app = FastAPI(title="Originsun Media Guard Web API")

from starlette.middleware.base import BaseHTTPMiddleware # type: ignore
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method == "GET":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
io_app = socketio.ASGIApp(sio, app)

# 主 event loop 的引用，從同步執行緒發送到 async Socket.IO 用
_main_loop: asyncio.AbstractEventLoop | None = None

def _emit_sync(event: str, data: dict) -> None:
    """Thread-safe way to emit a Socket.IO event from a sync context."""
    global _status_log_buffer
    if event == "log" and "msg" in data:
        _write_log_to_file(data["msg"])
        # 同步寫入 HTTP polling buffer，供心跳監控抓取
        _status_log_buffer.append(data["msg"])
        if len(_status_log_buffer) > _STATUS_LOG_MAX:
            _status_log_buffer = _status_log_buffer[-_STATUS_LOG_MAX:]
    if _main_loop:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data), _main_loop)

# 用來處理 WebSocket 的同步阻塞 (Server 端)
conflict_events = {} # session_id
current_conflict_action = "copy"
conflict_event = threading.Event()
global_conflict_action: str | None = None  # 全部覆蓋/全部略過時設定

# 全域的當前任務 Log 寫入目標檔案
_current_task_log_file: str | None = None

# HTTP polling 用的 in-memory log buffer（供 /api/v1/status 回傳給 heartbeat 監控）
_STATUS_LOG_MAX = 2000
_status_log_buffer: list = []

def _write_log_to_file(msg: str):
    """將訊息加上 timestamp 附加到當前任務指定的 log 檔案中."""
    if not _current_task_log_file:
        return
    try:
        from datetime import datetime as _dt
        import os
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(_current_task_log_file), exist_ok=True)
        with open(_current_task_log_file, "a", encoding="utf-8-sig") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception as e:
        print(f"Failed to write log to file: {e}")

@sio.on('resolve_conflict')
async def handle_conflict_resolution(sid, data):
    global current_conflict_action
    current_conflict_action = data.get('action', 'copy')
    conflict_event.set()

@sio.on('set_global_conflict')
async def handle_set_global_conflict(sid, data):
    global global_conflict_action, current_conflict_action
    action = data.get('action')  # 'overwrite' | 'skip' | None (reset)
    global_conflict_action = action
    # 也解鎖目前等待中的衝突
    current_conflict_action = action or 'skip'
    conflict_event.set()

# =================================================================
# 任務佇列與背景執行
# =================================================================
task_queue = Queue()
# 為了避免 GPU 過載或磁碟擁擠，我們使用單一背景 Worker
worker_busy = False
_current_progress = None

class BackupRequest(BaseModel):
    task_type: str = "backup"
    project_name: str
    local_root: str
    nas_root: str
    proxy_root: str
    cards: List[Tuple[str, str]] # List of [CardName, SourcePath]
    do_hash: bool = True
    do_transcode: bool = True
    do_concat: bool = True
    do_report: bool = False  # 小 FLAG：是否產生 HTML 視覺報表

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
    pairs: List[Tuple[str, str]] # List of (source_path, dest_path)
    mode: str = "quick" # "quick" or "xxh64"

class ReportJobRequest(BaseModel):
    task_type: str = "report"
    source_dir: str
    output_dir: str
    nas_root: str = ""
    report_name: str = ""  # 用戶自定義報表名稱，空白則自動用日期
    do_filmstrip: bool = True
    do_techspec: bool = True
    do_hash: bool = False
    do_gdrive: bool = False
    do_gchat: bool = False
    do_line: bool = False
    exclude_dirs: list = []  # 路徑前置詞列表，扫描時跳過這些目錄（用於排除 proxy 資料夾）

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

class DownloadModelRequest(BaseModel):
    model_size: str

# ─── System Settings API ─────────────────────────────────────────────────────

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_DEFAULT_SETTINGS: dict = {
    "notifications": {
        "line_notify_token": "",
        "google_chat_webhook": "",
        "custom_webhook_url": "",
    },
    "message_templates": {
        "backup_success":    "✅ 【備份成功】專案 {project_name} 已完成！\n📂 檔案數：{file_count} | 💾 容量：{total_size}",
        "report_success":    "📊 【報表生成】{project_name} 視覺報表已出爐！\n🔗 點此查看：{report_url}",
        "transcode_success": "🎬 【轉檔完成】{project_name} 已轉檔完成！\n📂 輸出至：{dest_dir} | 共 {file_count} 個檔案",
        "concat_success":    "🔗 【串接完成】{output_file} 已輸出！\n📁 儲存位置：{dest_dir}",
        "verify_success":    "🔍 【比對完成】{project_name}\n✅ 通過：{pass_count} | ❌ 失敗：{fail_count} | 共 {total_count} 個",
    },
    "notification_channels": {
        "backup":    {"gchat": True, "line": False},
        "report":    {"gchat": True, "line": False},
        "transcode": {"gchat": True, "line": False},
        "concat":    {"gchat": True, "line": False},
        "verify":    {"gchat": True, "line": False},
    },
    "compute_hosts": [],
}

def _save_settings(data: dict) -> None:
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=4)

def _init_settings() -> None:
    """Ensure settings.json exists with defaults on boot."""
    if not os.path.exists(_SETTINGS_FILE):
        try:
            _save_settings(_DEFAULT_SETTINGS)
        except Exception as e:
            print(f"Failed to auto-create settings.json: {e}")

_init_settings()

def _load_settings() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f)
                # Deep-merge dicts with defaults, otherwise fallback or accept list/value
                merged: dict = {}
                for section, defaults in _DEFAULT_SETTINGS.items():
                    if isinstance(defaults, dict):
                        merged[section] = {**defaults, **data.get(section, {})}
                    else:
                        merged[section] = data.get(section, defaults)
                return merged
        except Exception:
            pass
    return {s: (dict(v) if isinstance(v, dict) else v) for s, v in _DEFAULT_SETTINGS.items()}

# Settings payload uses Dict so it accepts the nested structure freely
from fastapi import Request as _Request  # type: ignore

@app.get("/api/v1/health")
async def health_check():
    """Health check — used by remote hosts to confirm reachability before job dispatch."""
    return {"status": "ok", "service": "OriginsunTranscode", "busy": worker_busy}

@app.get("/api/settings/load")
async def load_settings_api():
    return _load_settings()

@app.post("/api/settings/save")
async def save_settings_api(request: _Request):
    try:
        new_settings = await request.json()
        _save_settings(new_settings)
        return {"status": "success", "message": "設定已儲存"}
    except Exception as e:
        from fastapi.responses import JSONResponse  # type: ignore
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

# Backward-compat aliases (flat structure → nested read-back)
@app.get("/api/v1/settings")
async def get_settings_compat():
    s = _load_settings()
    n = s.get("notifications", {})
    t = s.get("message_templates", {})
    return {
        "line_token": n.get("line_notify_token", ""),
        "gchat_webhook": n.get("google_chat_webhook", ""),
        "custom_webhook": n.get("custom_webhook_url", ""),
        "tpl_backup_success": t.get("backup_success", ""),
        "tpl_report_success": t.get("report_success", ""),
    }

# ─── 目錄掃描 API（供多主機分派時掃描備份來源）─────────────────────────────
class ListDirRequest(BaseModel):
    path: str
    exts: List[str] = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"]

@app.post("/api/v1/list_dir")
async def list_dir_api(req: ListDirRequest):
    """列出指定目錄下所有符合副檔名的影片檔，支援判定單一檔案，供前端分派多主機轉檔使用。"""
    exts = {e.lower() for e in req.exts}
    
    if os.path.isfile(req.path):
        if os.path.splitext(req.path)[1].lower() in exts:
            files = [req.path.replace("\\", "/")]
            return {"files": files, "count": 1}
        else:
            return {"files": [], "error": f"檔案不支援轉換: {req.path}"}
            
    if not os.path.isdir(req.path):
        return {"files": [], "error": f"目錄不存在: {req.path}"}
        
    files: List[str] = []
    for root, _, filenames in os.walk(req.path):
        for fname in sorted(filenames):
            if os.path.splitext(fname)[1].lower() in exts:
                files.append(os.path.join(root, fname).replace("\\", "/"))
    return {"files": files, "count": len(files)}

@app.get("/api/v1/health")
async def health_check():
    """簡單的 HTTP 心跳測試，供前端分派時確保主機活著"""
    return {"status": "ok", "host": socket.gethostname() if 'socket' in sys.modules else "unknown"}

class MergeOutputRequest(BaseModel):
    proxy_root: str
    project_name: str

@app.post("/api/v1/merge_host_outputs")
async def merge_host_outputs_api(req: MergeOutputRequest):
    """將各台主機的 HostDispatch_* 目錄內的檔案，合併到專案資料夾統一目錄，並刪除暫存資料夾"""
    proj_dir = os.path.join(req.proxy_root, req.project_name)
    if not os.path.exists(proj_dir):
        return {"status": "error", "message": f"Proxy 專案目錄 {proj_dir} 不存在"}
    
    moved_count: int = 0
    try:
        for idx, item in enumerate(os.listdir(proj_dir)):
            sub_path = os.path.join(proj_dir, item)
            if os.path.isdir(sub_path) and item.startswith("HostDispatch_"):
                # 把裡面的檔案全搬到 proj_dir，保留相對路徑結構 (例如卡名)
                for root, _, files in os.walk(sub_path):
                    for f in files:
                        src_f = os.path.join(root, f)
                        rel_path = os.path.relpath(src_f, sub_path)
                        dst_f = os.path.join(proj_dir, rel_path)
                        
                        # 確保目標子目錄存在
                        os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                        
                        # 若已有同名檔，安全覆蓋
                        if os.path.exists(dst_f):
                            dst_dir = os.path.dirname(dst_f)
                            dst_f = os.path.join(dst_dir, f"_dup_{item}_{f}")
                        shutil.move(src_f, dst_f)
                        moved_count = int(moved_count) + 1  # type: ignore[arg-type]
                # 搬完後刪除暫存目錄
                shutil.rmtree(sub_path)
        return {"status": "ok", "merged": moved_count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ─── 合併主機輸出 API ─────────────────────────────────────────────────────────
class MergeHostOutputsRequest(BaseModel):
    proxy_root: str          # e.g. Z:/ANENT_Backup/20260305/Proxy
    project_name: str        # e.g. 20260305

@app.post("/api/v1/merge_host_outputs")
async def merge_host_outputs(req: MergeHostOutputsRequest):
    """將各主機的 HostDispatch_* 子目錄合併到統一的 Proxy 資料夾。"""
    base = os.path.join(req.proxy_root, req.project_name)
    if not os.path.isdir(base):
        return {"status": "error", "message": f"目錄不存在: {base}"}

    merged: int = 0
    errors = []
    for subdir in os.listdir(base):
        if not subdir.startswith("HostDispatch_"):
            continue
        src_dir = os.path.join(base, subdir)
        if not os.path.isdir(src_dir):
            continue
            
        # 遞迴走訪 HostDispatch 目錄，保留子目錄結構 (例如 cardName)
        import shutil
        for root, dirs, files in os.walk(src_dir):
            for fname in files:
                src_file = os.path.join(root, fname)
                # 計算相對路徑
                rel_path = os.path.relpath(src_file, src_dir)
                dst_file = os.path.join(base, rel_path)
                
                # 確保目標子目錄存在
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                
                try:
                    if os.path.exists(dst_file):
                        dst_dir = os.path.dirname(dst_file)
                        dst_file = os.path.join(dst_dir, f"_dup_{subdir}_{fname}")
                    shutil.move(src_file, dst_file)
                    merged = int(merged) + 1  # type: ignore[arg-type]
                except Exception as e:
                    errors.append(f"{rel_path}: {e}")
                    
        # 清除暫存目錄
        try:
            shutil.rmtree(src_dir)
        except Exception:
            pass

    return {"status": "ok", "merged": merged, "errors": errors}


# ─── 驗證 Proxy 轉檔完整性 ───────────────────────────────────────────────────
class VerifyProxiesRequest(BaseModel):
    proxy_root: str
    project_name: str
    expected_files: dict  # { "card_name": ["rel/path/1.mov", ...] }

@app.post("/api/v1/verify_proxies")
async def verify_proxies(req: VerifyProxiesRequest):
    """檢查預期的 Proxy 檔案是否都存在，回傳缺失的檔案清單。"""
    missing = []
    base_dir = os.path.join(req.proxy_root, req.project_name)
    
    if not os.path.isdir(base_dir):
        # 如果整個 base_dir 都不存在，那所有的檔案一定都 missing
        for card_name, files in req.expected_files.items():
            for f in files:
                missing.append({"card_name": card_name, "file": f})
        return {"status": "ok", "missing_files": missing}
        
    for card_name, files in req.expected_files.items():
        card_dir = os.path.join(base_dir, card_name)
        for f in files:
            expected_path = os.path.join(card_dir, f)
            if not os.path.exists(expected_path):
                missing.append({"card_name": card_name, "file": f})
                
    return {"status": "ok", "missing_files": missing}


# ─── 獨立 Proxy 驗證機制 (Standalone Proxy Verification) ─────────

class VerifyStandaloneProxiesRequest(BaseModel):
    sources: List[str]  # 來源列表（包含檔案或資料夾）
    dest_dir: str       # 輸出的 Proxy 根目錄

@app.post("/api/v1/verify_standalone_proxies")
async def verify_standalone_proxies(req: VerifyStandaloneProxiesRequest):
    """檢查獨立轉檔 (Standalone Proxy) 產出的檔案是否完整。
    使用純檔名 (stem) 比對，忽略來源的目錄深度。"""
    missing_sources = []
    video_exts = {".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"}
    proxy_exts = {".mov", ".mp4"}

    # 1. 掃描所有來源，建立 stem -> original_path 的對應表
    source_stems: dict = {}
    for token in req.sources:
        if not token: continue

        if os.path.isfile(token) and token.lower().endswith(tuple(video_exts)):
            stem = os.path.splitext(os.path.basename(token))[0].lower()
            if stem not in source_stems:
                source_stems[stem] = token

        elif os.path.isdir(token):
            for root, _, fnames in os.walk(token):
                for fname in fnames:
                    if fname.lower().endswith(tuple(video_exts)):
                        stem = os.path.splitext(fname)[0].lower()
                        if stem not in source_stems:
                            source_stems[stem] = os.path.join(root, fname).replace("\\", "/")

    # 2. 掃描目的地，收集所有已產生的 proxy stems
    proxy_stems: set = set()
    if os.path.isdir(req.dest_dir):
        for root, _, fnames in os.walk(req.dest_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() in proxy_exts:
                    stem = os.path.splitext(fname)[0].lower()
                    if stem.endswith("_proxy"):
                        stem = stem[:-6]  # type: ignore
                    proxy_stems.add(stem)

    # 3. 找出 missing 的來源檔案
    for stem, original_path in source_stems.items():
        if stem not in proxy_stems:
            missing_sources.append(original_path)

    return {"status": "ok", "missing_sources": missing_sources}


# ─── 比對來源與輸出 API ───────────────────────────────────────────────────────
class CompareSourceRequest(BaseModel):
    source_dir: str          # 備份來源目錄（用來算「應該有幾個」）
    output_dir: str          # 合併後的 Proxy 輸出目錄
    video_exts: List[str] = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"]
    proxy_exts: List[str] = [".mov", ".mp4"]  # Proxy 輸出格式
    flat_proxy: bool = False  # True: proxy 輸出為攤平結構，忽略路徑只比 stem

@app.post("/api/v1/compare_source")
async def compare_source(req: CompareSourceRequest):
    """比對來源目錄與 Proxy 輸出目錄，回傳缺漏的原始檔案路徑清單。
    flat_proxy=True 時，proxy 以攤平方式輸出（不含子目錄），比對只用純 stem。"""
    if not os.path.isdir(req.source_dir):
        return {"status": "error", "message": f"來源目錄不存在: {req.source_dir}"}

    src_exts = {e.lower() for e in req.video_exts}
    proxy_exts = {e.lower() for e in req.proxy_exts}

    # 掃描來源
    source_stems: dict = {}
    for root, _, fnames in os.walk(req.source_dir):
        for f in fnames:
            if os.path.splitext(f)[1].lower() in src_exts:
                stem = os.path.splitext(f)[0].lower()
                if req.flat_proxy:
                    # 攤平模式：只用純 stem，忽略子目錄路徑
                    key = stem
                else:
                    rel_dir = os.path.relpath(root, req.source_dir)
                    if rel_dir == ".": rel_dir = ""
                    key = os.path.join(rel_dir, stem).replace("\\", "/").lower()
                # 有同 stem 衝突時保留第一個（攤平模式下不可避免）
                if key not in source_stems:
                    source_stems[key] = os.path.join(root, f).replace("\\", "/")

    # 掃描輸出（哪些 stem 已有 proxy）
    proxy_stems: set = set()
    if os.path.isdir(req.output_dir):
        for root, _, fnames in os.walk(req.output_dir):
            for f in fnames:
                if os.path.splitext(f)[1].lower() in proxy_exts:
                    stem = os.path.splitext(f)[0].lower()
                    if stem.endswith("_proxy"):
                        stem = str(stem)[:-6]  # type: ignore[index]
                    if req.flat_proxy:
                        key = stem
                    else:
                        rel_dir = os.path.relpath(root, req.output_dir)
                        if rel_dir == ".": rel_dir = ""
                        key = os.path.join(rel_dir, stem).replace("\\", "/").lower()
                    proxy_stems.add(key)

    # 缺漏 = 來源有但 Proxy 沒有
    missing = [
        path for key, path in source_stems.items()
        if key not in proxy_stems
    ]

    return {
        "status": "ok",
        "source_count": len(source_stems),
        "proxy_count": len(proxy_stems),
        "missing_count": len(missing),
        "missing": missing,
    }


# ─── Standalone Report Job Endpoints ───────────────────────────────────────

def _fmt_size_py(size: int) -> str:
    for u in ("B","KB","MB","GB","TB"):
        if size < 1024: return f"{size:.1f} {u}"
        size //= 1024  # type: ignore
    return f"{size} TB"

# Tracks the currently running report asyncio task (for cancellation)
_current_report_task: "asyncio.Task | None" = None
# Pause gate — set = running, cleared = paused
_report_pause_event: asyncio.Event = asyncio.Event()
_report_pause_event.set()  # default: not paused

@app.post("/api/v1/report_jobs")
async def start_report_job(req: ReportJobRequest):
    """Start a standalone visual-report generation job in the background."""
    global _current_report_task
    _report_pause_event.set()  # ensure not stuck in paused state
    _current_report_task = asyncio.create_task(_run_report_job(req))
    return {"status": "queued"}

async def _run_report_job(req: ReportJobRequest):
    """Background coroutine: scan → meta → filmstrip → HTML render → (Drive) → notify."""
    from datetime import datetime as _dt
    from core_engine import SUPPORTED_EXTS  # type: ignore
    from report_generator import save_report, generate_pdf_from_html  # type: ignore

    # ──────────────────────────────────────────────────────────────────────
    # Helper: emit progress update (async, runs in the event loop directly)
    # ──────────────────────────────────────────────────────────────────────
    async def _emit_rpt(phase: str, pct: float, msg: str, typ: str = "info"):
        _write_log_to_file(msg)
        await sio.emit("report_progress", {"phase": phase, "pct": pct, "msg": msg, "type": typ})

    async def _emit_log(typ: str, msg: str):
        _write_log_to_file(msg)
        await sio.emit("report_progress", {"phase": "", "pct": 0, "msg": msg, "type": typ})

    async def _check_pause():
        """Yield control, and if paused, wait until resumed."""
        if not _report_pause_event.is_set():
            await _emit_log("system", "⏸ 報表暫停中，等待繼續...")
            await _report_pause_event.wait()  # blocks until resume
        await asyncio.sleep(0)  # yield to event loop

    global _current_task_log_file
    _dt_str = _dt.now().strftime("%Y%m%d_%H%M%S")
    
    # 建立本機報表根目錄
    reports_dir = os.path.join(str(req.output_dir), "Originsun_Reports")
    os.makedirs(reports_dir, exist_ok=True)

    _target_log_dir = req.output_dir if req.output_dir else req.source_dir
    _current_task_log_file = os.path.join(_target_log_dir, f"{req.report_name or 'Report'}_Log_{_dt_str}.txt")
    _write_log_to_file(f"=== Originsun Media Guard Pro 視覺報表任務開始 ===")

    try:
        # ── Phase 1: Scan ──────────────────────────────────────────────────
        await _emit_rpt("scan", 0, "🔍 掃描影片中...")
        all_files: list = []
        _exclude_abs = [os.path.abspath(ex) for ex in (req.exclude_dirs or []) if ex]
        for root, dirs, fnames in os.walk(req.source_dir):
            # Prune any sub-directories that sit inside an excluded path
            if _exclude_abs:
                _valid_dirs: List[str] = [
                    d for d in dirs
                    if not any(
                        os.path.abspath(os.path.join(str(root), str(d))).startswith(ex)
                        for ex in _exclude_abs
                    )
                ]
                dirs[:] = _valid_dirs  # type: ignore
            for fn in fnames:
                if fn.lower().endswith(SUPPORTED_EXTS):
                    all_files.append(os.path.join(root, fn))
        # 強制依據完整絕對路徑之字母排序，以確保畫面報表進度與產出的 HTML 陣列順序穩定
        all_files.sort()
        total = len(all_files)
        await _emit_rpt("scan", 100, f"✅ 掃描完成：找到 {total} 個影片", "ok")

        if total == 0:
            await _emit_log("error", "未找到任何支援的影片檔！")
            return

        # ── Phase 2: Meta / Hash ───────────────────────────────────────────
        manifest = ReportManifest(
            project_name=os.path.basename(req.source_dir),
            local_root=req.source_dir,
            nas_root=req.nas_root,
            start_time=_dt.now()
        )
        for i, fp in enumerate(all_files):
            await _check_pause()
            pct = ((i + 1) / total) * 100
            fname = os.path.basename(fp)
            await _emit_rpt("meta", pct, f"[{i+1}/{total}] 讀取 Meta: {fname}")

            rec = FileRecord(filename=fname, src_path=fp, size_bytes=os.path.getsize(fp))

            if req.do_techspec:
                meta = await asyncio.to_thread(MediaGuardEngine.get_video_metadata, fp)
                rec.fps = meta.get("fps", 0.0)
                rec.resolution = meta.get("resolution", "")
                rec.codec = meta.get("codec", "")
                rec.duration = meta.get("duration", 0.0)

            if req.do_hash:
                engine_inst = MediaGuardEngine()
                xxh = await asyncio.to_thread(engine_inst.get_xxh64, fp)  # type: ignore
                rec.xxh64 = xxh or ""

            manifest.files.append(rec)

        manifest.total_files = total
        manifest.total_bytes = sum(f.size_bytes for f in manifest.files)
        await _emit_rpt("meta", 100, "✅ Meta 讀取完成", "ok")

        # ── Phase 3: Film Strip ────────────────────────────────────────────
        if req.do_filmstrip:
            for i, rec in enumerate(manifest.files):
                await _check_pause()
                pct = ((i + 1) / total) * 100
                await _emit_rpt("strip", pct, f"[{i+1}/{total}] 生成膠卷: {rec.filename}")
                try:
                    rec.film_strip_b64 = await asyncio.to_thread(
                        MediaGuardEngine.generate_film_strip, rec.src_path
                    )
                except Exception as _se:
                    await _emit_log("info", f"膠卷略過 {rec.filename}: {_se}")
        await _emit_rpt("strip", 100, "✅ 膠卷生成完成", "ok")

        # ── Phase 4: Render HTML ───────────────────────────────────────────
        await _emit_rpt("render", 20, "📝 正在渲染 HTML 報表...")
        manifest.end_time = _dt.now()

        _rpt_name = req.report_name.strip() if req.report_name.strip() else \
            _dt.now().strftime("%Y%m%d") + "_Report"
        local_html = await asyncio.to_thread(save_report, manifest, reports_dir, _rpt_name)

        await _emit_rpt("render", 50, "📄 正在轉換 PDF 文件...")
        local_pdf = local_html.replace(".html", ".pdf")
        pdf_success = await generate_pdf_from_html(local_html, local_pdf)
        if pdf_success:
            await _emit_log("system", f"📄 PDF 建立成功: {local_pdf}")
        else:
            local_pdf = ""

        # ── Phase 5: Publish Public URL ────────────────────────────────────
        await _emit_rpt("render", 60, "🌐 發佈至公開 Web 伺服器...")
        public_url = ""
        nas_web_dir = r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport"
        try:
            os.makedirs(nas_web_dir, exist_ok=True)
            nas_html = os.path.join(nas_web_dir, os.path.basename(local_html))
            shutil.copy2(local_html, nas_html)
            
            if local_pdf:
                nas_pdf = os.path.join(nas_web_dir, os.path.basename(local_pdf))
                shutil.copy2(local_pdf, nas_pdf)
                
            public_url = f"http://filereport.originsun-studio.com/{os.path.basename(local_html)}"
            await _emit_log("system", f"🌐 專案已發佈至 Web Server: {public_url}")
        except Exception as _we:
            await _emit_log("info", f"Web Server 發佈與備份失敗: {_we}")

        # Update reports_index.json
        index_path = os.path.join(reports_dir, "reports_index.json")
        report_id = str(uuid.uuid4()).replace("-", "")[0:8]  # type: ignore
        entry = {
            "id": report_id,
            "name": os.path.basename(local_html),
            "local_path": local_html,
            "pdf_path": local_pdf,
            "public_url": public_url,
            "drive_url": "",
            "created_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
            "file_count": total,
            "total_size_str": _fmt_size_py(manifest.total_bytes),
        }
        nas_index_path = os.path.join(nas_web_dir, "reports_index.json")
        try:
            # 1. Update Local Index
            history: dict = {"reports": []}
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as _f:
                    history = _json.load(_f)
            history["reports"].insert(0, entry)
            with open(index_path, "w", encoding="utf-8") as _f:
                _json.dump(history, _f, ensure_ascii=False, indent=2)

            # 2. Update NAS Index (if paths differ)
            if index_path != nas_index_path:
                nas_history: dict = {"reports": []}
                if os.path.exists(nas_index_path):
                    with open(nas_index_path, "r", encoding="utf-8") as _nf:
                        nas_history = _json.load(_nf)
                nas_history["reports"].insert(0, entry)
                with open(nas_index_path, "w", encoding="utf-8") as _nf:
                    _json.dump(nas_history, _nf, ensure_ascii=False, indent=2)
        except Exception as _ie:
            await _emit_log("info", f"索引更新失敗: {_ie}")

        # Optional Google Drive upload
        drive_url = ""
        if req.do_gdrive:
            await _emit_rpt("render", 70, "☁️ 上傳至 Google Drive...")
            try:
                _settings: dict = {}
                _sp = os.path.join(os.getcwd(), "settings.json")
                if os.path.exists(_sp):
                    with open(_sp, "r", encoding="utf-8") as _sf:
                        _settings = _json.load(_sf)
                from drive_sync import upload_to_drive  # type: ignore
                drive_url = await asyncio.to_thread(
                    upload_to_drive, local_html, _settings.get("gdrive_folder_id") or None
                )
                entry["drive_url"] = drive_url
                
                # Helper to update drive_url in a specific index file
                def _update_drive_url_in_index(path: str):
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as _f:
                            _hist = _json.load(_f)
                        for r in _hist.get("reports", []):
                            if r.get("id") == report_id:
                                r["drive_url"] = drive_url
                        with open(path, "w", encoding="utf-8") as _f:
                            _json.dump(_hist, _f, ensure_ascii=False, indent=2)

                _update_drive_url_in_index(index_path)
                if index_path != nas_index_path:
                    _update_drive_url_in_index(nas_index_path)

                await _emit_log("system", f"[OK] Drive: {drive_url}")
            except Exception as _de:
                await _emit_log("info", f"[!] Google Drive 略過: {_de}")

        # Notifications
        await _emit_rpt("render", 85, "📱 發送通知...")
        try:
            from notifier import notify_tab  # type: ignore
            # generate absolute URL to the report based on local network IP if possible
            # for now, placeholder string since we don't know the explicit domain
            _rpt_url = req.report_name or "report"
            if drive_url:
                _rpt_url = drive_url
            
            await asyncio.to_thread(notify_tab, "report_success",
                project_name=manifest.project_name, 
                file_count=manifest.total_files, 
                total_size=manifest.total_bytes,
                report_url=_rpt_url
            )
        except Exception as _ne:
            await _emit_log("info", f"通知發送失敗: {_ne}")

        await _emit_rpt("render", 100, "🎉 報表完成！", "ok")
        await sio.emit("report_job_done", {
            "report_name": os.path.basename(local_html),
            "local_path":  local_html,
            "pdf_path": local_pdf,
            "public_url": public_url,
            "drive_url":   drive_url,
        })

    except asyncio.CancelledError:
        await sio.emit("report_progress", {
            "phase": "", "pct": 0, "msg": "🛑 報表工作已被強制中止", "type": "error"
        })
        raise  # let asyncio know the task was cancelled cleanly

    except Exception as _err:
        await sio.emit("report_progress", {
            "phase": "", "pct": 0, "msg": f"❌ 報表任務失敗: {_err}", "type": "error"
        })

    finally:
        global _current_report_task
        _current_report_task = None
        _current_task_log_file = None
        # Switch UI back to backup mode
        await sio.emit("report_progress", {"phase": "__done__", "pct": 0, "msg": "", "type": "system"})


@app.get("/api/v1/reports/history")
async def get_reports_history(output_dir: str = ""):
    """Return the contents of reports_index.json from the central NAS directory."""
    index_path = r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport\reports_index.json"
    if not os.path.exists(index_path):
        return {"reports": []}
    try:
        import json as _j
        with open(index_path, "r", encoding="utf-8") as f:
            return _j.load(f)
    except Exception as e:
        return {"reports": [], "error": str(e)}

@app.delete("/api/v1/reports/{report_id}")
async def delete_report_entry(report_id: str, output_dir: str = ""):
    """Remove a report entry from the central NAS reports_index.json (does NOT delete the HTML file)."""
    import json as _j
    index_path = r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport\reports_index.json"
    
    if not os.path.exists(index_path):
        return {"status": "error", "message": "Global NAS index not found"}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = _j.load(f)
        data["reports"] = [r for r in data.get("reports", []) if r.get("id") != report_id]
        with open(index_path, "w", encoding="utf-8") as f:
            _j.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _engine_log_cb(msg: str):
    # Determine if it's a high-level system/stage log (Left pane) or detailed verbose log (Right pane)
    
    # Exclude verbose file-level operations that happen to have [OK] or 完成
    is_verbose = (
        msg.startswith("[OK] 本機寫入") or 
        msg.startswith("[OK] NAS 寫入") or
        msg.startswith("[OK] 完成:") or 
        msg.startswith("[OK] 串帶完成:") or
        "請求已送出" in msg
    )
    
    if is_verbose:
        _emit_sync('log', {'type': 'info', 'msg': msg})
        return

    sys_prefixes = ('[OK]', '[X]', '[!]', '[Engine]', '===', '---', '系統')
    if any(msg.startswith(p) for p in sys_prefixes) or '開始' in msg or '完成' in msg or '失敗' in msg:
        _emit_sync('log', {'type': 'system', 'msg': msg})
    else:
        _emit_sync('log', {'type': 'info', 'msg': msg})

engine = MediaGuardEngine(
    logger_cb=_engine_log_cb,
    error_cb=lambda msg: _emit_sync('log', {'type': 'error', 'msg': msg})
)

@app.on_event("startup")
async def _capture_event_loop():
    global _main_loop
    _main_loop = asyncio.get_running_loop()

async def _background_worker():
    global worker_busy
    while not task_queue.empty():
        worker_busy = True
        task: Any = task_queue.get()
        
        task_name = getattr(task, "project_name", getattr(task, "task_type", "Unknown"))  # type: ignore
        engine.clear_logs()
        global _status_log_buffer
        _status_log_buffer = []  # 每個新任務開始時清空 polling buffer
        await sio.emit('log', {'type': 'system', 'msg': f'開始執行新任務: {task_name}'})
        
        log_dir = ""
        if isinstance(task, BackupRequest):
            # 存放在總專案資料夾底下
            log_dir = os.path.join(task.local_root, task.project_name)
        elif isinstance(task, TranscodeRequest) or isinstance(task, ConcatRequest):
            # 必須寫入作業系統暫存區，嚴禁寫入 dest_dir，否則會觸發 NAS 資料夾鎖定導致 FFmpeg Permission Denied
            import tempfile
            log_dir = tempfile.gettempdir()
        elif isinstance(task, VerifyRequest) and task.pairs:
            log_dir = task.pairs[0][1]
        
        # 每次新任務開始時，重置全域衝突模式
        global global_conflict_action, _current_task_log_file
        global_conflict_action = None
        
        from datetime import datetime as _dt
        _dt_str = _dt.now().strftime("%Y%m%d_%H%M%S")
        if log_dir:
            from core_engine import MediaGuardEngine # type: ignore
            # 統一檔名，避免遇到 H.264 等含有 . 號的檔名變成副檔名
            safe_task_name = str(task_name).replace(".", "_").replace(" ", "_")
            _current_task_log_file = os.path.join(log_dir, f"{safe_task_name}_Log_{_dt_str}.txt")
            _write_log_to_file(f"=== Originsun Media Guard Pro {getattr(task, 'task_type', 'Job')} 任務開始 ===")
        else:
            _current_task_log_file = None

        # 定義 WebSocket 進度回報回呼
        def _on_progress(data: dict):
            global _current_progress  # type: ignore
            _current_progress = data  # type: ignore[assignment]
            _emit_sync('progress', data)
            
        # 定義 WebSocket 衝突回報回呼 (因為跑在 thread 中，可以用同步 wait)
        def _on_conflict(data: dict) -> str:
            # 若已設定全部覆蓋/略過模式，直接回傳不彈對話框
            if global_conflict_action:
                return global_conflict_action
            conflict_event.clear()
            # 從同步執行緒發送衝突對話框至前端
            _emit_sync('file_conflict', data)
            # 阻塞等待前端回傳，最多等 60 秒 (如果在 60 秒內前端沒有回覆，則視為主動略過，避免後台卡死)
            if not conflict_event.wait(timeout=60):
                return 'skip'
            return current_conflict_action

        try:
            if isinstance(task, BackupRequest):
                manifest = ReportManifest(
                    project_name=task.project_name,
                    local_root=task.local_root,
                    nas_root=task.nas_root,
                    proxy_root=task.proxy_root,
                ) if getattr(task, 'do_report', False) else None

                await asyncio.to_thread(
                    engine.run_backup_job,
                    sources=task.cards,
                    local_root=task.local_root,
                    nas_root=task.nas_root,
                    project_name=task.project_name,
                    do_hash=task.do_hash,
                    on_progress=_on_progress,
                    on_conflict=_on_conflict,
                    manifest=manifest,
                    do_report=getattr(task, 'do_report', False)
                )
                
                # 自動後驗處理 (Proxy轉檔)
                if getattr(task, "do_transcode", False):
                    for card_name, _ in task.cards:
                        if engine._pause_event.is_set() and engine._stop_event.is_set():
                            break
                        src_dir = os.path.join(task.local_root, task.project_name, card_name)
                        dest_dir = os.path.join(task.proxy_root, task.project_name, card_name)
                        if os.path.exists(src_dir):
                            await asyncio.to_thread(
                                engine.run_transcode_job,
                                sources=[src_dir],
                                dest_dir=dest_dir,
                                on_progress=_on_progress
                            )
                            
                # 自動後驗處理 (時間碼串帶)
                if getattr(task, "do_concat", False):
                    for card_name, _ in task.cards:
                        if engine._pause_event.is_set() and engine._stop_event.is_set():
                            break
                        # 優先找 proxy 轉檔的資料夾，如果沒轉檔就找原本備份的資料夾
                        use_proxy = getattr(task, "do_transcode", False)
                        if use_proxy:
                            src_dir = os.path.join(task.proxy_root, task.project_name, card_name)
                        else:
                            src_dir = os.path.join(task.local_root, task.project_name, card_name)
                        dest_dir = os.path.join(task.proxy_root, task.project_name, card_name)
                        if os.path.exists(src_dir):
                            await asyncio.to_thread(
                                engine.run_concat_job,
                                sources=[src_dir],
                                dest_dir=dest_dir,
                                custom_name=f"{task.project_name}_{card_name}_reel",
                                resolution="720P",
                                codec="H.264 (NVENC)",
                                burn_timecode=True,
                                burn_filename=False,
                                on_progress=_on_progress
                            )
                            
            elif isinstance(task, TranscodeRequest):
                await asyncio.to_thread(
                    engine.run_transcode_job,
                    sources=task.sources,
                    dest_dir=task.dest_dir,
                    on_progress=_on_progress
                )
                # ── Post Transcode: notify ──────────────────────
                try:
                    from notifier import notify_tab  # type: ignore
                    _fc = sum(
                        len(list(os.walk(str(src)))) and
                        sum(len(f) for _, _, f in os.walk(str(src)))
                        for src in task.sources if os.path.isdir(str(src))
                    )
                    _proj = os.path.basename(task.dest_dir)
                    await asyncio.to_thread(notify_tab, "transcode_success",
                        project_name=_proj, dest_dir=task.dest_dir, file_count=_fc)
                except Exception:
                    pass
            elif isinstance(task, ConcatRequest):
                await asyncio.to_thread(
                    engine.run_concat_job,
                    sources=task.sources,
                    dest_dir=task.dest_dir,
                    custom_name=task.custom_name,
                    resolution=task.resolution,
                    codec=task.codec,
                    burn_timecode=task.burn_timecode,
                    burn_filename=task.burn_filename,
                    on_progress=_on_progress
                )
                # ── Post Concat: notify ────────────────────────
                try:
                    from notifier import notify_tab  # type: ignore
                    c_upper = task.codec.upper()
                    ext = ".mp4" if ("H264" in c_upper or "H.264" in c_upper or "NVENC" in c_upper or "X264" in c_upper) else ".mov"
                    if task.custom_name:
                        folder_name = task.custom_name
                    else:
                        first_src = task.sources[0] if task.sources else ""
                        if os.path.isdir(first_src):
                            folder_name = os.path.basename(first_src) or "Reel"
                        else:
                            folder_name = "Standalone_Reel"
                    _out = f"{folder_name}{ext}"
                except Exception:
                    pass
            elif isinstance(task, TranscribeRequest):
                try:
                    import transcriber  # type: ignore
                    import importlib
                    importlib.reload(transcriber)
                except ImportError as ie:
                    err_msg = f"無法載入語音辨識模組: {ie}"
                    await sio.emit('log', {'type': 'error', 'msg': err_msg})
                    await sio.emit('transcribe_error', {'msg': err_msg})
                    continue
                safe_dest = task.dest_dir.rstrip("\\/") if task.dest_dir else ""
                _proj = os.path.basename(safe_dest)
                if not _proj or _proj.endswith(":"):
                    _proj = "transcribe"
                
                # Capture current event loop for threadsafe emit
                _loop = asyncio.get_running_loop()
                def _on_prog(pct, msg):
                    if engine._stop_event and engine._stop_event.is_set():
                        raise Exception("使用者強制中止任務")
                        
                    asyncio.run_coroutine_threadsafe(
                        sio.emit('transcribe_progress', {'pct': pct, 'msg': msg}),
                        _loop
                    )
                    
                res = await asyncio.to_thread(
                    transcriber.run_transcribe_job,
                    sources=task.sources,
                    dest_dir=task.dest_dir,
                    project_name=_proj,
                    model_size=task.model_size,
                    output_srt=task.output_srt,
                    output_txt=task.output_txt,
                    output_wav=task.output_wav,
                    generate_proxy=task.generate_proxy,
                    individual_mode=task.individual_mode,
                    progress_callback=_on_prog,
                    check_cancel_cb=engine._stop_event.is_set
                )
                if res.get("success"):
                    await sio.emit('transcribe_done', {'dest_dir': res.get("dest_dir")})
                    # ── Post Transcribe: notify ──────────────────────
                    try:
                        from notifier import notify_tab  # type: ignore
                        await asyncio.to_thread(notify_tab, "transcribe_success",
                            project_name=_proj, dest_dir=task.dest_dir, file_count=res.get("file_count", 0))
                    except Exception:
                        pass
                else:
                    err_msg = res.get("error", "未知錯誤")
                    await sio.emit('log', {'type': 'error', 'msg': f'逐字稿發生錯誤: {err_msg}'})
                    # 發送專門針對逐字稿的失敗狀態，供前端解鎖按鈕，避免干擾全域任務狀態
                    await asyncio.sleep(0.1) # debounce
                    await sio.emit('transcribe_error', {'msg': err_msg})
                    continue

            elif isinstance(task, VerifyRequest):
                await asyncio.to_thread(
                    engine.run_verify_job,
                    pairs=task.pairs,
                    mode=task.mode,
                    on_progress=_on_progress
                )
                # ── Post Verify: notify ────────────────────────
                try:
                    from notifier import notify_tab  # type: ignore
                    _total = len(task.pairs)
                    # Try to infer pass/fail from engine log buffer
                    _fail = sum(1 for line in engine._log_buffer if '[FAIL]' in line or 'FAIL' in line.upper())
                    _pass = _total - _fail
                    _proj = os.path.basename(task.pairs[0][0]) if task.pairs else "Unknown"
                    await asyncio.to_thread(notify_tab, "verify_success",
                        project_name=_proj, pass_count=_pass, fail_count=_fail, total_count=_total)
                except Exception:
                    pass
                
            # ─── Post-task: HTML Report & Notification Pipeline ───
            if isinstance(task, BackupRequest) and getattr(task, 'do_report', False):
                try:
                    # Scan the backed-up local directory using the same pipeline
                    # as the standalone Visual Report tab (same defaults)
                    local_dir = os.path.join(task.local_root, task.project_name)

                    await sio.emit('progress', {
                        'phase': 'report', 'status': 'processing',
                        'current_file': '📊 備份完成，正在啟動視覺報表掃描...', 'total_pct': 0
                    })

                    report_req = ReportJobRequest(**{
                        "source_dir": local_dir,
                        "output_dir": task.local_root,    # saves to local_root/Originsun_Reports/
                        "nas_root": task.nas_root,
                        "report_name": task.project_name,
                        "do_filmstrip": True,   # same default as VR tab
                        "do_techspec": True,   # same default as VR tab
                        "do_hash": task.do_hash,
                        "do_gdrive": False,
                        "do_gchat": False,
                        "do_line": False,
                        # 排除 proxy 目錄，確保報表只掃描本機備份目的地
                        "exclude_dirs": [
                            os.path.join(task.proxy_root, task.project_name)
                        ] if task.proxy_root else [],
                    })
                    # Run full report pipeline (scan → meta → filmstrip → HTML)
                    asyncio.create_task(_run_report_job(report_req))

                    await sio.emit('progress', {
                        'phase': 'report', 'status': 'processing',
                        'current_file': '📊 視覺報表掃描中（請查看「視覺報表」頁籤追蹤進度）', 'total_pct': 50
                    })

                except Exception as ex:
                    _emit_sync('log', {'type': 'error', 'msg': f'任務 {task_name} 執行發生錯誤: {ex}'})

            elif isinstance(task, BackupRequest) and not getattr(task, 'do_report', False):
                # Silent summary — still notify but no report
                try:
                    from notifier import notify_all  # type: ignore
                    
                    file_count: int = 0
                    total_size: float = 0.0
                    local_dir = os.path.join(task.local_root, task.project_name)
                    if os.path.exists(local_dir):
                        for root, _, files in os.walk(local_dir):
                            for f in files:
                                p = os.path.join(str(root), f)
                                if not os.path.islink(p):
                                    file_count += 1  # type: ignore
                                    total_size += os.path.getsize(p)  # type: ignore
                                    
                    await asyncio.to_thread(notify_all, task.project_name, None, file_count, total_size)
                except Exception:
                    pass

            await sio.emit('task_status', {'status': 'done'})
        except Exception as e:
            await sio.emit('log', {'type': 'error', 'msg': f'任務執行失敗: {str(e)}'})
            await sio.emit('task_status', {'status': 'error', 'detail': str(e)})
        finally:
            if log_dir:
                try:
                    await asyncio.to_thread(engine.save_job_log, log_dir, f"{task_name} 工作日誌")
                except Exception as log_err:
                    print(f"Error saving job log: {log_err}")
            
        task_queue.task_done()
    global _current_progress
    worker_busy = False
    _current_progress = None

# =================================================================
# API 端點
# =================================================================

@app.get("/api/v1/models/status")
async def get_models_status():
    # [CRITICAL WINDOWS FIX] Disable HuggingFace symlinks 
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"

    sizes = ["turbo", "large-v3", "medium", "small", "base", "tiny"]
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    os.makedirs(models_dir, exist_ok=True)
    
    status = {}
    try:
        from faster_whisper import download_model  # type: ignore
        for size in sizes:
            try:
                path = download_model(size, cache_dir=models_dir, local_files_only=True)
                status[size] = True if path else False
            except Exception:
                status[size] = False
    except ImportError:
        pass
    return {"status": status}

@app.post("/api/v1/models/download")
async def download_model_endpoint(req: DownloadModelRequest, background_tasks: BackgroundTasks):
    async def _download_task(size: str):
        import subprocess
        await sio.emit('log', {'type': 'system', 'msg': f'⏳ 開始在背景為您下載模型: {size} ... (需時數分鐘，請勿關機)'})
        downloader = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_model.py")
        p = await asyncio.create_subprocess_exec(
            sys.executable, downloader, size,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        async def read_stream(stream):
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if text:
                    # HuggingFace tqdm prints to stderr which contains \r, we stream notable ones
                    if "MB/" in text or "%" in text or "Downloading" in text or text.startswith("["):
                        await sio.emit('log', {'type': 'system', 'msg': text})
        
        await asyncio.gather(
            read_stream(p.stdout),
            read_stream(p.stderr)
        )
        await p.wait()
        
        if p.returncode == 0:
            await sio.emit('log', {'type': 'system', 'msg': f'🎉 模型 {size} 下載並部署完成！'})
            await sio.emit('model_download_done', {'model_size': size})
        else:
            await sio.emit('log', {'type': 'error', 'msg': f'❌ 模型 {size} 下載發生錯誤，這可能是網路異常所致。'})
            await sio.emit('model_download_error', {'model_size': size})

    background_tasks.add_task(_download_task, req.model_size)
    return {"status": "started", "model_size": req.model_size}

@app.post("/api/v1/jobs")
async def create_backup_job(req: BackupRequest, bg_tasks: BackgroundTasks):
    task_queue.put(req)
    if not worker_busy:
        bg_tasks.add_task(_background_worker)
    return {"status": "queued", "position": task_queue.qsize()}

@app.post("/api/v1/jobs/transcode")
async def create_transcode_job(req: TranscodeRequest, bg_tasks: BackgroundTasks):
    task_queue.put(req)
    if not worker_busy:
        bg_tasks.add_task(_background_worker)
    return {"status": "queued", "position": task_queue.qsize()}

@app.post("/api/v1/jobs/concat")
async def create_concat_job(req: ConcatRequest, bg_tasks: BackgroundTasks):
    task_queue.put(req)
    if not worker_busy:
        bg_tasks.add_task(_background_worker)
    return {"status": "queued", "position": task_queue.qsize()}

@app.post("/api/v1/jobs/verify")
async def create_verify_job(req: VerifyRequest, bg_tasks: BackgroundTasks):
    task_queue.put(req)
    if not worker_busy:
        bg_tasks.add_task(_background_worker)
    return {"status": "queued", "position": task_queue.qsize()}

@app.post("/api/v1/jobs/transcribe")
async def create_transcribe_job(req: TranscribeRequest, bg_tasks: BackgroundTasks):
    task_queue.put(req)
    if not worker_busy:
        bg_tasks.add_task(_background_worker)
    return {"status": "queued", "position": task_queue.qsize()}

@app.post("/api/v1/control/pause")
async def pause_job():
    engine.request_pause()
    _report_pause_event.clear()  # pause report job too
    return {"status": "paused"}

@app.post("/api/v1/control/resume")
async def resume_job():
    engine.request_resume()
    _report_pause_event.set()  # resume report job too
    return {"status": "resumed"}

@app.post("/api/v1/control/stop")
async def stop_job():
    global _current_report_task
    engine.request_stop()
    # Also cancel any running report task
    if _current_report_task and not _current_report_task.done():
        _current_report_task.cancel()
    return {"status": "stopped"}

@app.post("/api/v1/control/update")
async def update_agent():
    import subprocess
    import sys
    try:
        # 使用 wscript.exe 在背景執行 start_hidden.vbs，不產生黑畫面
        subprocess.Popen(["wscript.exe", "start_hidden.vbs"], shell=False)
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return {"status": "updating", "message": "系統即將重新啟動並更新"}
    except Exception as e:
        return {"status": "error", "message": f"啟動更新程式失敗: {e}"}

@app.get("/api/v1/status")
async def get_status(log_offset: int = 0):
    logs = list(_status_log_buffer)[log_offset:]  # type: ignore[misc]
    return {
        "status": "online",
        "busy": worker_busy,
        "queue_length": task_queue.qsize(),
        "paused": engine._pause_event.is_set() if engine else False,
        "logs": logs,
        "new_log_offset": log_offset + len(logs),
        "progress": _current_progress
    }

@app.get("/api/v1/version")
async def get_version():
    try:
        if os.path.exists("version.json"):
            import json
            with open("version.json", "r", encoding="utf-8") as f:
                return json.load(f)
        return {"version": "0.0.0", "build_date": "Unknown", "error": "version.json not found"}
    except Exception as e:
        return {"version": "0.0.0", "build_date": "Error", "error": str(e)}

@app.get("/api/v1/nas_version")
async def get_nas_version():
    """直接讀取 NAS 上的 version.json，作為更新版本的權威來源"""
    import json
    NAS_AGENT_PATH = r"\\192.168.1.132\Container\AI_Workspace\agents\Originsun Media Guard Pro"
    nas_version_file = os.path.join(NAS_AGENT_PATH, "version.json")
    try:
        if os.path.exists(nas_version_file):
            with open(nas_version_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"version": "unknown", "error": "NAS version.json not found", "nas_path": nas_version_file}
    except Exception as e:
        return {"version": "unknown", "error": str(e)}

class OpenFileRequest(BaseModel):
    path: str

@app.post("/api/v1/utils/open_file")
async def open_local_file(req: OpenFileRequest):
    """Open a local file (e.g. HTML report) in the server machine's default browser."""
    try:
        import webbrowser as _wb
        safe_path = req.path.replace("\\", "/")
        if not safe_path.startswith("file://"):
            safe_path = f"file:///{safe_path}"
        _wb.open(safe_path)
        return {"status": "ok", "opened": safe_path}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/v1/utils/open_folder")
async def open_local_folder(req: OpenFileRequest):
    """Open the folder containing the local file in Windows Explorer."""
    try:
        import os
        folder_path = os.path.dirname(req.path)
        if os.path.exists(folder_path):
            import subprocess
            subprocess.Popen(f'explorer /select,"{req.path}"')
            return {"status": "ok", "opened": folder_path}
        return {"status": "error", "message": "Folder does not exist"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/download_agent")
async def download_agent():
    file_path = "Originsun_Agent.zip"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Originsun_Agent.zip")
    return {"error": "系統尚未打包 Originsun_Agent.zip，請聯絡管理員放置此檔案於伺服器根目錄。"}

@app.get("/download_installer")
async def download_installer():
    file_path = "Install_Originsun_Agent.bat"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Install_Originsun_Agent.bat")
    return {"error": "找不到自動安裝腳本，請確認伺服器根目錄下有 Install_Originsun_Agent.bat。"}

@app.post("/api/v1/utils/create_shortcut")
async def create_desktop_shortcut():
    import subprocess
    import locale
    try:
        vbs_path = os.path.join(os.getcwd(), "Create_Shortcut.vbs")
        
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            my_ip = s.getsockname()[0]
            s.close()
        except:
            my_ip = "127.0.0.1"
            
        ico_path = os.path.join(os.getcwd(), "logo.ico")
        
        vbs_code = f"""
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
chromePath1 = WshShell.ExpandEnvironmentStrings("%ProgramFiles%") & "\\Google\\Chrome\\Application\\chrome.exe"
chromePath2 = WshShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\\Google\\Chrome\\Application\\chrome.exe"
chromePath3 = WshShell.ExpandEnvironmentStrings("%LocalAppData%") & "\\Google\\Chrome\\Application\\chrome.exe"
chromePath = ""
If fso.FileExists(chromePath1) Then
    chromePath = chromePath1
ElseIf fso.FileExists(chromePath2) Then
    chromePath = chromePath2
ElseIf fso.FileExists(chromePath3) Then
    chromePath = chromePath3
End If
serverUrl = "http://localhost:8000"
icoPath = "{ico_path}"
sLinkFile = WshShell.SpecialFolders("Desktop") & "\\Originsun Media Guard Web.lnk"
Set oLink = WshShell.CreateShortcut(sLinkFile)
If chromePath <> "" Then
    oLink.TargetPath = chromePath
    oLink.Arguments = "--app=" & serverUrl & " --start-fullscreen"
    If fso.FileExists(icoPath) Then
        oLink.IconLocation = icoPath
    Else
        oLink.IconLocation = chromePath & ", 0"
    End If
Else
    oLink.TargetPath = serverUrl
    If fso.FileExists(icoPath) Then
        oLink.IconLocation = icoPath
    End If
End If
oLink.Description = "Originsun Media Guard Pro Web Edition"
oLink.WindowStyle = 1
oLink.Save
MsgBox "桌面捷徑已成功建立！請查看桌面上的「Originsun Media Guard Web」。", vbInformation, "安裝成功"
"""
        # 使用系統預設編碼 (Windows 上通常是 cp950 或 cp1252) 寫入檔案，確保 cscript 看得懂 MsgBox 裡的中文
        sys_enc = locale.getpreferredencoding()
        with open(vbs_path, "w", encoding=sys_enc, errors="replace") as f:
            f.write(vbs_code.strip())
        
        # 執行腳本
        subprocess.run(["cscript.exe", "//nologo", vbs_path], check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        return {"status": "success", "message": "桌面捷徑已成功建立！\n請查看桌面上的「Originsun Media Guard Web」。"}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"腳本執行失敗 (cscript 錯誤): {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"建立失敗: {str(e)}"}

@app.get("/api/v1/utils/pick_folder")
async def api_pick_folder(title: str = "選擇資料夾"):
    """使用 Tkinter 開啟伺服器端的資料夾選擇對話框"""
    def _pick():
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()
        path = filedialog.askdirectory(title=title)
        root.destroy()
        return path
    
    selected = await asyncio.to_thread(_pick)  # type: ignore
    return {"path": selected}

@app.get("/api/v1/utils/pick_file")
async def api_pick_file(title: str = "選擇檔案"):
    """使用 Tkinter 開啟伺服器端的檔案選擇對話框"""
    def _pick():
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()
        path = filedialog.askopenfilename(title=title)
        root.destroy()
        return path
    
    selected = await asyncio.to_thread(_pick)  # type: ignore
    return {"path": selected}

@app.get("/api/v1/utils/resolve_drop")
async def api_resolve_drop(name: str):
    """
    強大的路徑解析器：
    1. 偵測磁碟代號如 "USB 磁碟機 (E:)" -> E:\\
    2. COM Shell.Application 取得正在選取的項目
    3. 掃描本機磁碟與常見目錄尋找同名資料夾
    """
    def _find():
        import re
        import subprocess
        
        clean_name = name.strip()
        
        # 1. 偵測名稱中的磁碟機代號，例如 "USB 磁碟機 (E:)" 或 "FileTransfer (O:)"
        match = re.search(r'\(([a-zA-Z]):\)', clean_name)
        if match:
            return f"{match.group(1).upper()}:\\"
        # 處理純代號如 "E:"
        match = re.search(r'^([a-zA-Z]):$', clean_name)
        if match:
            return f"{match.group(1).upper()}:\\"
        # 處理 Chromium 瀏覽器特有的根目錄拖曳脫逸（E:\ 會被改名為 E_drive）
        match = re.search(r'^([a-zA-Z])_drive$', clean_name, re.IGNORECASE)
        if match:
            return f"{match.group(1).upper()}:\\"

        # 取得所有可用邏輯磁碟
        drives = [f"{chr(d)}:\\" for d in range(65, 91) if os.path.exists(f"{chr(d)}:\\")]

        # 2. 透過系統 VOL 指令比對 Volume Name
        for drive in drives:
            try:
                out = subprocess.check_output(f'vol {drive[0]}:', text=True, shell=True, stderr=subprocess.DEVNULL)
                if clean_name.lower() in out.lower():
                    return drive
            except Exception:
                pass

        # 3. 嘗試透過 PowerShell + Shell.Application 取得正在選取的項目與開啟的資料夾
        try:
            ps_script = """
            [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
            $app = New-Object -ComObject Shell.Application
            foreach ($win in $app.Windows()) {
                try {
                    $sel = $win.Document.SelectedItems()
                    if ($sel -ne $null) {
                        foreach ($item in $sel) { Write-Output $item.Path }
                    }
                    $folderPath = $win.Document.Folder.Self.Path
                    if ($folderPath -ne $null -and $folderPath -ne "") {
                        Write-Output "FOLDER:$folderPath"
                    }
                } catch {}
            }
            """
            paths = subprocess.check_output(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL).splitlines()
            lower_name = clean_name.lower()
            
            selected_items = []
            open_folders = []
            for p in paths:
                p = p.strip()
                if not p: continue
                if p.startswith("FOLDER:"):
                    open_folders.append(p.removeprefix("FOLDER:"))
                else:
                    selected_items.append(p)

            # 3a. 先檢查是否有剛好被反白的選取檔案/資料夾（精確 basename 比對）
            exact_matches = [p for p in selected_items if os.path.basename(p).lower() == lower_name]
            if exact_matches:
                # 優先回傳最深（最長）的路徑，這樣當有多個同名資料夾時能更精確
                return max(exact_matches, key=len)
                    
            # 3b. 檢查目前所有開啟的檔案總管資料夾內，是否包含此資料夾
            # 優先回傳最深（最長）的 folder 中找到的結果，避免與根目錄同名的資料夾被誤判
            folder_candidates = []
            for folder in open_folders:
                candidate = os.path.join(folder, clean_name)
                if os.path.exists(candidate):
                    folder_candidates.append(candidate)
            if folder_candidates:
                return max(folder_candidates, key=len)
        except Exception:
            pass

        # 4. 常見目錄 — 先特判：若名稱本身就是系統資料夾名稱，直接回傳完整路徑
        user_profile = os.environ.get('USERPROFILE', '')
        known_system_dirs = ["Desktop", "Downloads", "Documents", "Videos", "Pictures", "Music"]
        if user_profile:
            for sysdir in known_system_dirs:
                if clean_name.lower() == sysdir.lower():
                    full = os.path.join(user_profile, sysdir)
                    if os.path.exists(full):
                        return full
                    # OneDrive 版本的桌面
                    onedrive = os.environ.get('OneDriveConsumer', os.environ.get('OneDrive', ''))
                    if onedrive:
                        od_path = os.path.join(onedrive, sysdir)
                        if os.path.exists(od_path):
                            return od_path

        # 4b. 資料夾在系統資料夾下（子資料夾）
        if user_profile:
            for subdir in known_system_dirs:
                candidate = os.path.join(user_profile, subdir, clean_name)
                if os.path.exists(candidate):
                    return candidate

        # 5. 掃描磁碟根目錄與第一層子目錄
        # 安全保護：若名稱太短（單一字母），避免找到不相關的 C:\A 這類巧合目錄
        if len(clean_name) >= 2:
            for drive in drives:
                candidate = os.path.join(drive, clean_name)
                if os.path.exists(candidate):
                    return candidate
                    
            for drive in drives:
                try:
                    for sub in os.scandir(drive):
                        if sub.is_dir():
                            candidate = os.path.join(sub.path, clean_name)
                            if os.path.exists(candidate):
                                return candidate
                except Exception:
                    continue

        # 無法確定正確路徑，回傳空字串，前端會顯示警告
        return ""
        
    path = await asyncio.to_thread(_find)  # type: ignore
    return {"path": path}


# ─── Admin: Remote Restart ───────────────────────────────────────────────────
@app.post("/api/admin/restart")
async def admin_restart():
    """Restart the agent process via update_agent.bat (pulls latest update from NAS then relaunches)."""
    import subprocess
    base_dir = os.path.dirname(os.path.abspath(__file__))
    vbs = os.path.join(base_dir, "start_hidden.vbs")

    async def _restart():
        await asyncio.sleep(1.5)   # give the HTTP response time to arrive
        try:
            if os.path.exists(vbs):
                # Use the same hidden-window VBS pattern already proven to work
                subprocess.Popen(
                    ["wscript.exe", vbs],
                    shell=False
                )
            else:
                # Fallback: launch update_agent.bat directly
                bat = os.path.join(base_dir, "update_agent.bat")
                subprocess.Popen(
                    bat,
                    shell=True,
                    creationflags=0x00000008  # DETACHED_PROCESS
                )
        except Exception:
            pass
        asyncio.get_running_loop().call_later(0.5, os._exit, 0)

    asyncio.create_task(_restart())
    return {"status": "restarting", "message": "Agent 正在重新啟動，請稍後重新整理頁面..."}


# =================================================================
# 初始化靜態前端
# =================================================================
if os.path.exists("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
else:
    os.makedirs("frontend", exist_ok=True)
    with open("frontend/index.html", "w", encoding="utf-8") as f:
        f.write("<h1>Originsun Media Guard Web (Frontend Pending)</h1>")
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    port = 8000
    print(f"[Server] 啟動 FastAPI 服務於 port {port}")
    import webbrowser
    # 延遲 1 秒後開啟瀏覽器
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(io_app, host="0.0.0.0", port=port, log_level="error")
