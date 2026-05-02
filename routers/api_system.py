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

@router.get("/api/settings/load")
async def load_settings_api():
    return load_settings()

@router.post("/api/settings/save")
async def save_settings_api(req: Request):
    _check_admin(req)
    try:
        new_settings = await req.json()
        save_settings(new_settings)
        return {"status": "success", "message": "設定已儲存"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@router.get("/api/v1/settings")
async def get_settings_compat():
    s = load_settings()
    n = s.get("notifications", {})
    t = s.get("message_templates", {})
    return {
        "line_token": n.get("line_notify_token", ""),
        "gchat_webhook": n.get("google_chat_webhook", ""),
        "custom_webhook": n.get("custom_webhook_url", ""),
        "tpl_backup_success": t.get("backup_success", ""),
        "tpl_report_success": t.get("report_success", ""),
    }


# ── Directory listing ───────────────────────────────────────────────────

@router.post("/api/v1/list_dir")
async def list_dir_api(req: ListDirRequest):
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
        p = await asyncio.create_subprocess_exec(sys.executable, downloader, size, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def read_stream(stream):
            while True:
                line = await stream.readline()
                if not line: break
                text = line.decode('utf-8', errors='replace').strip()
                if text and ("MB/" in text or "%" in text or "Downloading" in text or text.startswith("[")):
                    await sio.emit('log', {'type': 'system', 'msg': text})
        await asyncio.gather(read_stream(p.stdout), read_stream(p.stderr))
        await p.wait()
        if p.returncode == 0:
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


# ── Restart endpoint (delegates schtasks plumbing to api_ota) ──────

@router.post("/api/v1/system/restart")
async def system_restart(request: Request):
    """Restart endpoint mirroring /api/v1/internal/restart with the
    `OriginsunRestart` task name (so it can co-exist if both endpoints
    are ever pinged in sequence)."""
    key = request.headers.get("X-Internal-Key", "")
    if key != "originsun-internal-restart":
        client_ip = request.client.host if request.client else ""
        if client_ip not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"detail": "Unauthorized"}, 403)

    import tempfile
    from routers.api_ota import _launch_in_user_session
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_py = os.path.join(base_dir, ".venv", "Scripts", "python.exe")
    embed_py = os.path.join(base_dir, "python_embed", "python.exe")
    py = venv_py if os.path.exists(venv_py) else (embed_py if os.path.exists(embed_py) else sys.executable)

    bat_path = os.path.join(tempfile.gettempdir(), "originsun_sys_restart.bat")
    updater = os.path.join(base_dir, "update_agent.py")
    out_log = os.path.join(base_dir, "uvicorn_out.log")
    err_log = os.path.join(base_dir, "uvicorn_err.log")
    out_bak = out_log + ".bak"
    err_bak = err_log + ".bak"
    lines = [
        "@echo off",
        f'cd /d "{base_dir}"',
        "del update_status.json >nul 2>nul",
        'for /f "tokens=5" %%p in (\'netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"\') do taskkill /PID %%p /F >nul 2>nul',
        "timeout /t 3 /nobreak >nul",
    ]
    if os.path.exists(updater):
        lines.append(f'"{py}" "{updater}"')
    lines += [
        f'del "{out_bak}" >nul 2>nul',
        f'move /Y "{out_log}" "{out_bak}" >nul 2>nul',
        f'del "{err_bak}" >nul 2>nul',
        f'move /Y "{err_log}" "{err_bak}" >nul 2>nul',
        # Direct `start "" /B uvicorn` — vbs's WshShell.Run silently
        # fails for the final uvicorn launch from schtasks /run /it.
        f'start "" /B "{py}" -m uvicorn main:io_app --host 0.0.0.0 --port 8000 > "{out_log}" 2> "{err_log}"',
    ]
    with open(bat_path, "w", encoding="ascii", errors="replace") as f:
        f.write("\r\n".join(lines))

    # Schtasks runs in a thread + os._exit is scheduled afterwards so the
    # HTTP response returns immediately rather than blocking up to 20s.
    async def _spawn_and_exit():
        await asyncio.to_thread(_launch_in_user_session, bat_path, "OriginsunRestart")
        await asyncio.sleep(1.0)
        os._exit(0)

    asyncio.create_task(_spawn_and_exit())
    return {"status": "updating"}
