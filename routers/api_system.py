"""System core endpoints: health, settings, status, jobs, models, control, version.

OTA/publish/download endpoints moved to api_ota.py.
Utility endpoints moved to api_utils.py.
All endpoint paths remain unchanged.
"""
import json
import os
import sys
import socket
import asyncio
import urllib.request
from fastapi import APIRouter, BackgroundTasks, Request  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore

import core.state as state  # type: ignore
from core.socket_mgr import sio  # type: ignore
from config import load_settings, save_settings  # type: ignore
from core.schemas import ListDirRequest, DownloadModelRequest  # type: ignore

router = APIRouter()


def _check_admin(request: Request):
    """Check admin permission. No-op if auth module not available."""
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass  # auth module not available, skip check


# ── Health & Settings ───────────────────────────────────────────────────

@router.get("/api/v1/health")
async def health_check():
    jobs = state.get_all_jobs()
    running = [j for j in jobs.values() if j.status == state.JobStatus.RUNNING]
    busy = len(running) > 0

    cpu_pct = 0.0
    mem_pct = 0.0
    try:
        import psutil  # type: ignore
        cpu_pct = psutil.cpu_percent(interval=None)
        mem_pct = psutil.virtual_memory().percent
    except Exception:
        pass

    ver = ""
    try:
        import json as _j
        with open("version.json", "r", encoding="utf-8") as f:
            ver = _j.load(f).get("version", "")
    except Exception:
        pass

    current_tasks = [
        {"job_id": j.job_id, "project_name": j.project_name, "task_type": j.task_type}
        for j in running
    ]

    return {
        "status": "ok",
        "hostname": socket.gethostname(),
        "cpu_percent": cpu_pct,
        "memory_percent": mem_pct,
        "worker_busy": busy,
        "active_job_count": len(running),
        "current_tasks": current_tasks,
        "version": ver,
    }

# ⚠️ settings.json 內含機密：`jwt_secret` 能簽出 access_level=3 的 admin token（master 與
# NAS website-api 共用同一把，internal endpoint 的 X-Internal-Key 也是它），`database_url`
# 帶 Postgres 密碼。而 `/api/settings/load` 是**未認證**端點（前端 4 處在無 token 狀態下讀
# 它，不能改成要求認證），且 master 8000 經 Cloudflare tunnel 對外 —— 2026-07-10 實測
# `https://foundry.originsun-studio.com/api/settings/load` 從公網可讀到這兩個值。
# 因此輸出前一律抹除。新增機密欄位時務必同步加進這裡。
_SECRET_KEYS = ("jwt_secret", "database_url")
_SECRET_SUBKEYS = {"google_oauth": ("client_secret",)}


def _redact_settings(s: dict) -> dict:
    """回傳去機密的淺拷貝（不改動原 dict —— 它是 load_settings 的快取內容）。"""
    out = {k: v for k, v in s.items() if k not in _SECRET_KEYS}
    for parent, subs in _SECRET_SUBKEYS.items():
        if isinstance(out.get(parent), dict):
            out[parent] = {k: v for k, v in out[parent].items() if k not in subs}
    return out


@router.get("/api/settings/load")
async def load_settings_api():
    return _redact_settings(load_settings())

@router.post("/api/settings/save")
async def save_settings_api(req: Request):
    _check_admin(req)
    try:
        new_settings = await req.json()
        save_settings(new_settings)
        return {"status": "success", "message": "設定已儲存"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# ── 磁碟代號 ↔ UNC 對應（core/drive_map；備份進件翻譯用）──────

@router.get("/api/v1/drive_map")
async def get_drive_map():
    """有效對應（程式預設 + settings 覆寫）+ 預設表（前端標示哪些被改過）。"""
    from core.drive_map import DEFAULT_DRIVE_MAP, effective_map
    return {"map": effective_map(), "defaults": DEFAULT_DRIVE_MAP}


@router.post("/api/v1/drive_map")
async def save_drive_map(req: Request):
    """存完整期望表 {字母: UNC}（admin — 全軟體層級設定，入口在右上角選單）。
    與程式預設的差異存進 settings.json 的 drive_map（預設有但期望表沒有的
    字母 → 存空字串墓碑 = 停用），使有效對應恆等於期望表。"""
    _check_admin(req)
    from core.drive_map import DEFAULT_DRIVE_MAP
    body = await req.json()
    desired = body.get("drive_map")
    if not isinstance(desired, dict):
        return JSONResponse(status_code=400, content={"detail": "drive_map 需為 {字母: UNC} 物件"})
    clean: dict = {}
    for k, v in desired.items():
        letter = str(k).strip().rstrip(":").upper()
        unc = str(v or "").strip().rstrip("\\/")
        if not (len(letter) == 1 and letter.isalpha()):
            return JSONResponse(status_code=400, content={"detail": f"磁碟代號需為單一英文字母：{k!r}"})
        if not unc.startswith("\\\\"):
            return JSONResponse(status_code=400, content={"detail": f"{letter}: 的對應需為 \\\\ 開頭的 UNC 路徑"})
        clean[letter] = unc
    # save_settings 是深度合併（merge-on-save）— 單純寫新表清不掉舊 override 的字母。
    # 墓碑要涵蓋「程式預設 ∪ 既有 override」中所有不在期望表的字母，有效表才恆等於期望表。
    current = load_settings().get("drive_map") or {}
    known = set(DEFAULT_DRIVE_MAP)
    for k in current:
        letter = str(k).strip().rstrip(":").upper()
        if len(letter) == 1 and letter.isalpha():
            known.add(letter)
    override = dict(clean)
    for letter in known:
        if letter not in clean:
            override[letter] = ""   # 墓碑：停用該字母
    save_settings({"drive_map": override})
    return {"status": "success", "map": clean}

@router.get("/api/v1/settings")
async def get_settings_compat():
    s = load_settings()
    n = s.get("notifications", {})
    t = s.get("message_templates", {})
    return {
        "line_token": n.get("line_notify_token", ""),
        "gchat_webhook": n.get("google_chat_webhook", ""),
        "alert_webhook": n.get("alert_webhook", ""),
        "custom_webhook": n.get("custom_webhook_url", ""),
        "tpl_backup_success": t.get("backup_success", ""),
        "tpl_report_success": t.get("report_success", ""),
    }


@router.post("/api/v1/internal/alert_email")
async def internal_alert_email(request: Request):
    """把 🔴 級告警寄成 email。呼叫者＝任一節點的 `notifier._relay_alert_email`。

    認證：`X-Internal-Key` = JWT secret（與 /internal/seo/run 同一套）。
    只有 master 會被打（notifier 送往 settings.master_server）——SMTP 帳密與收件人存在
    `website_settings`（NAS Postgres），機隊 7 台因此不需要持有寄信憑證。
    收件人：`notify.alert_email_to` 優先，沒設才退 `notify.email_to`（聯絡表單那個信箱）。
    """
    from core.auth import _get_secret
    expected = _get_secret()
    if not expected or request.headers.get("X-Internal-Key", "") != expected:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "invalid json"})
    subject = (body.get("subject") or "Originsun 系統告警").strip()
    text = (body.get("body") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"detail": "empty body"})

    # lazy import：機隊精簡 agent 沒裝 sqlalchemy，這支端點在它們身上不會被呼叫到
    from db.session import get_session_factory
    from services.website.notify_service import _parse_recipients, _smtp_send
    from services.website.settings_service import get_all_settings

    factory = get_session_factory()
    if factory is None:
        return JSONResponse(status_code=503, content={"detail": "db not ready"})
    async with factory() as session:
        settings = await get_all_settings(session)

    raw = settings.get("notify.alert_email_to") or settings.get("notify.email_to") or ""
    to_list = _parse_recipients(raw)
    if not to_list:
        return {"sent": 0, "detail": "no recipients"}
    try:
        await asyncio.to_thread(_smtp_send, settings, to_list, subject, text)
    except Exception as e:  # SMTP 失敗不該把呼叫端的告警路徑一起拖垮
        return JSONResponse(status_code=502, content={"sent": 0, "error": str(e)[:200]})
    return {"sent": len(to_list)}


# ── Directory listing ───────────────────────────────────────────────────

@router.post("/api/v1/list_dir")
async def list_dir_api(req: ListDirRequest):
    # 磁碟代號→UNC：掃描來源常填 T:\ 等網路磁碟代號，本機不一定有掛
    from core.drive_map import make_translator
    req.path = make_translator()(req.path)
    exts = {e.lower() for e in req.exts}
    if os.path.isfile(req.path):
        if os.path.splitext(req.path)[1].lower() in exts:
            return {"files": [req.path.replace("\\", "/")], "count": 1}
        return {"files": [], "error": f"檔案不支援: {req.path}"}
    if not os.path.isdir(req.path): return {"files": [], "error": f"目錄不存在: {req.path}"}
    files = []
    for root, _, fnames in os.walk(req.path):
        for fname in sorted(fnames):
            if os.path.splitext(fname)[1].lower() in exts:
                files.append(os.path.join(root, fname).replace("\\", "/"))
    return {"files": files, "count": len(files)}


# ── Model management ────────────────────────────────────────────────────

@router.get("/api/v1/models/status")
async def get_models_status():
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
    sizes = ["turbo", "large-v3", "medium", "small", "base", "tiny"]
    models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
    os.makedirs(models_dir, exist_ok=True)
    status = {}
    try:
        from faster_whisper import download_model  # type: ignore
        for size in sizes:
            try:
                path = download_model(size, cache_dir=models_dir, local_files_only=True)
                status[size] = True if path else False
            except Exception: status[size] = False
    except ImportError: pass
    return {"status": status}

@router.post("/api/v1/models/download")
async def download_model_endpoint(req: DownloadModelRequest, background_tasks: BackgroundTasks):
    async def _download_task(size: str):
        import subprocess
        await sio.emit('log', {'type': 'system', 'msg': f'⏳ 開始在背景為您下載模型: {size} ... (需時數分鐘，請勿關機)'})
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        downloader = os.path.join(base_dir, "download_model.py")
        loop = asyncio.get_running_loop()
        # Selector-loop-safe: Popen in a worker thread + stream progress lines
        # back onto the loop (asyncio subprocess is unavailable on Windows
        # SelectorEventLoop). stderr merged into stdout so either stream shows.
        def _run_blocking() -> int:
            CNW = 0x08000000 if sys.platform == "win32" else 0
            p = subprocess.Popen(
                [sys.executable, downloader, size],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                creationflags=CNW,
            )
            for line in p.stdout:
                text = line.strip()
                if text and ("MB/" in text or "%" in text or "Downloading" in text or text.startswith("[")):
                    asyncio.run_coroutine_threadsafe(
                        sio.emit('log', {'type': 'system', 'msg': text}), loop)
            p.wait()
            return p.returncode
        rc = await asyncio.to_thread(_run_blocking)
        if rc == 0:
            await sio.emit('log', {'type': 'system', 'msg': f'🎉 模型 {size} 下載並部署完成！'})
            await sio.emit('model_download_done', {'model_size': size})
        else:
            await sio.emit('log', {'type': 'error', 'msg': f'❌ 模型 {size} 下載發生錯誤，這可能是網路異常。'})
            await sio.emit('model_download_error', {'model_size': size})
    background_tasks.add_task(_download_task, req.model_size)
    return {"status": "started", "model_size": req.model_size}


# ── Job control ─────────────────────────────────────────────────────────

@router.post("/api/v1/control/pause")
async def pause_job(job_id: str = ""):
    if job_id:
        job = state.get_job(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})
        if job.engine:
            job.engine.request_pause()
        if job.report_pause_event:
            job.report_pause_event.clear()
        job.status = state.JobStatus.PAUSED
        return {"status": "paused", "job_id": job_id}
    # No job_id: pause ALL running jobs (backward compat)
    for j in state.get_all_jobs().values():
        if j.status == state.JobStatus.RUNNING:
            if j.engine:
                j.engine.request_pause()
            if j.report_pause_event:
                j.report_pause_event.clear()
            j.status = state.JobStatus.PAUSED
    return {"status": "paused"}

@router.post("/api/v1/control/resume")
async def resume_job(job_id: str = ""):
    if job_id:
        job = state.get_job(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})
        if job.engine:
            job.engine.request_resume()
        if job.report_pause_event:
            job.report_pause_event.set()
        job.status = state.JobStatus.RUNNING
        return {"status": "resumed", "job_id": job_id}
    # No job_id: resume ALL paused jobs (backward compat)
    for j in state.get_all_jobs().values():
        if j.status == state.JobStatus.PAUSED:
            if j.engine:
                j.engine.request_resume()
            if j.report_pause_event:
                j.report_pause_event.set()
            j.status = state.JobStatus.RUNNING
    return {"status": "resumed"}

@router.post("/api/v1/control/stop")
async def stop_job(job_id: str = ""):
    if job_id:
        job = state.get_job(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})
        if job.engine:
            job.engine.request_stop()
        if job.asyncio_task and not job.asyncio_task.done():
            job.asyncio_task.cancel()
        job.status = state.JobStatus.CANCELLED
        return {"status": "stopped", "job_id": job_id}
    # No job_id: stop ALL jobs (running + paused + queued)
    for j in list(state.get_all_jobs().values()):
        if j.status in (state.JobStatus.RUNNING, state.JobStatus.PAUSED):
            if j.engine:
                j.engine.request_stop()
            if j.asyncio_task and not j.asyncio_task.done():
                j.asyncio_task.cancel()
        if j.status in (state.JobStatus.RUNNING, state.JobStatus.PAUSED,
                         state.JobStatus.QUEUED, state.JobStatus.WAITING):
            j.status = state.JobStatus.CANCELLED
    # 重置所有活躍 slot 計數，防止 slot 被永久佔用
    state._active_counts.clear()
    return {"status": "stopped"}


# ── Status & Jobs ───────────────────────────────────────────────────────

@router.get("/api/v1/status")
async def get_status(log_offset: int = 0):
    jobs = state.get_all_jobs()
    active_statuses = (
        state.JobStatus.QUEUED, state.JobStatus.WAITING,
        state.JobStatus.RUNNING, state.JobStatus.PAUSED,
    )
    active_jobs = {}
    for jid, j in jobs.items():
        if j.status in active_statuses:
            active_jobs[jid] = {
                "job_id": j.job_id,
                "project_name": j.project_name,
                "task_type": j.task_type,
                "status": j.status.value,
                "progress": j.progress,
                "created_at": j.created_at,
                "started_at": j.started_at,
            }

    busy = any(j.status == state.JobStatus.RUNNING for j in jobs.values())
    paused = any(j.status == state.JobStatus.PAUSED for j in jobs.values())
    queue_len = sum(1 for j in jobs.values() if j.status in (state.JobStatus.QUEUED, state.JobStatus.WAITING))

    logs = list(state._global_log_buffer)[log_offset:]
    return {
        "status": "online",
        "busy": busy,
        "queue_length": queue_len,
        "paused": paused,
        "logs": logs,
        "new_log_offset": log_offset + len(logs),
        "progress": None,  # deprecated; use active_jobs[id].progress
        "active_jobs": active_jobs,
    }

@router.get("/api/v1/jobs/{job_id}")
async def get_job_detail(job_id: str):
    job = state.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return {
        "job_id": job.job_id,
        "project_name": job.project_name,
        "task_type": job.task_type,
        "status": job.status.value,
        "progress": job.progress,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_detail": job.error_detail,
    }

@router.get("/api/v1/jobs/{job_id}/logs")
async def get_job_logs(job_id: str, offset: int = 0):
    job = state.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    logs = job.log_buffer[offset:]
    return {"logs": logs, "new_offset": offset + len(logs)}


# ── Version ─────────────────────────────────────────────────────────────

@router.get("/api/v1/version")
async def get_version():
    try:
        import json
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        v_file = os.path.join(base_dir, "version.json")
        if os.path.exists(v_file):
            with open(v_file, "r", encoding="utf-8") as f: return json.load(f)
        return {"version": "0.0.0", "build_date": "Unknown", "error": "version.json not found"}
    except Exception as e: return {"version": "0.0.0", "build_date": "Error", "error": str(e)}

@router.get("/api/v1/nas_version")
async def get_nas_version():
    """Fetch the latest version from the master server over HTTP."""
    master = load_settings().get("master_server", "")
    if not master:
        # 未設定 master_server = 本機就是主控端，直接回傳自己的版號
        return await get_version()
    try:
        url = f"{master.rstrip('/')}/api/v1/version"
        def _fetch():
            req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode())
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        return {"version": "unknown", "error": str(e)}


# ── Restart endpoint (delegates spawn to core.process_spawn) ──────

@router.post("/api/v1/system/restart")
async def system_restart(request: Request):
    """Restart endpoint — delegates to core.process_spawn helper which
    spawns a detached restart sequence (kill port + OTA + rotate + uvicorn)."""
    key = request.headers.get("X-Internal-Key", "")
    if key != "originsun-internal-restart":
        client_ip = request.client.host if request.client else ""
        if client_ip not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"detail": "Unauthorized"}, 403)

    from core.process_spawn import trigger_detached_restart
    trigger_detached_restart(run_ota=True)
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return {"status": "updating"}
