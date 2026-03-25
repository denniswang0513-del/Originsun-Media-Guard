import os
import sys
import socket
import asyncio
import threading
from fastapi import APIRouter, BackgroundTasks, Request  # type: ignore
from fastapi.responses import JSONResponse, FileResponse  # type: ignore

import core.state as state  # type: ignore
from core.socket_mgr import sio  # type: ignore
from config import load_settings, save_settings  # type: ignore
from core.schemas import ListDirRequest, DownloadModelRequest, OpenFileRequest, ValidatePathsRequest  # type: ignore

router = APIRouter()


def _check_admin(request: Request):
    """Check admin permission. No-op if auth module not available."""
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass  # auth module not available, skip check


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
        p = await asyncio.create_subprocess_exec(sys.executable, downloader, size, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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

@router.post("/api/v1/control/update")
async def update_agent(request: Request):
    """Trigger OTA update: download new code, then restart uvicorn.
    Entire flow runs in a detached Python process that survives os._exit."""
    _check_admin(request)
    import subprocess, json, sys
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        monitor_path = os.path.join(base_dir, "update_monitor.py")
        status_file = os.path.join(base_dir, "update_status.json")
        py = sys.executable

        # Write initial status
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump({"step": 0, "pct": 2, "msg": "Starting update..."}, f, ensure_ascii=False)

        # Start monitor on port 8001 (DETACHED, survives os._exit)
        subprocess.Popen(
            [py, monitor_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )

        master = load_settings().get("master_server", "http://192.168.1.11:8000")

        # Build a self-contained restart script in TEMP
        # This script: 1) waits for old server to die 2) runs updater 3) starts new server
        import tempfile as _tf
        restart_script = os.path.join(_tf.gettempdir(), "originsun_restart.py")
        with open(restart_script, 'w', encoding='utf-8') as f:
            f.write(f'''#!/usr/bin/env python3
"""One-shot OTA restart script. Runs detached from the main server process."""
import subprocess, sys, os, time, json

BASE = r"{base_dir}"
PY = r"{py}"
MASTER = r"{master}"
STATUS = os.path.join(BASE, "update_status.json")

def write_status(step, pct, msg):
    try:
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump({{"step": step, "pct": pct, "msg": msg}}, f, ensure_ascii=False)
    except: pass

def kill_port_8000():
    """Kill all processes listening on port 8000."""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if ":8000" in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
    except: pass

os.chdir(BASE)

# Wait for old server to release port
write_status(1, 5, "Waiting for old server to stop...")
time.sleep(3)
kill_port_8000()
time.sleep(2)

# Phase 1: Run OTA updater (if exists)
updater = os.path.join(BASE, "update_agent.py")
if os.path.exists(updater):
    write_status(1, 10, "Running OTA updater...")
    try:
        subprocess.run([PY, updater, MASTER], timeout=600, cwd=BASE)
    except Exception as e:
        write_status(1, 10, f"Updater error: {{e}}")

# Phase 2: Ensure port 8000 is free
write_status(2, 80, "Preparing to start server...")
kill_port_8000()
time.sleep(1)

# Phase 3: Start uvicorn directly (DETACHED, no VBS, no BAT)
write_status(3, 90, "Starting server...")
env = os.environ.copy()
env["PYTHONPATH"] = BASE
subprocess.Popen(
    [PY, "-m", "uvicorn", "main:io_app", "--host", "0.0.0.0", "--port", "8000"],
    cwd=BASE,
    env=env,
    creationflags=0x00000008 | 0x00000200,  # DETACHED + CREATE_NEW_PROCESS_GROUP
)

write_status(3, 100, "Server started. Waiting for connection...")
''')
        # Launch restart script as DETACHED process
        subprocess.Popen(
            [py, restart_script],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )

        # Kill ourselves after 1 second (gives time for HTTP response)
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return {"status": "updating"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
    import json, urllib.request
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

@router.post("/api/v1/utils/open_file")
async def open_local_file(req: OpenFileRequest):
    try:
        import webbrowser as _wb
        safe_path = req.path.replace("\\", "/")
        if not safe_path.startswith("file://"): safe_path = f"file:///{safe_path}"
        _wb.open(safe_path)
        return {"status": "ok", "opened": safe_path}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/v1/utils/open_folder")
async def open_local_folder(req: OpenFileRequest):
    try:
        folder_path = os.path.dirname(req.path)
        if os.path.exists(folder_path):
            import subprocess
            subprocess.Popen(f'explorer /select,"{req.path}"')
            return {"status": "ok", "opened": folder_path}
        return {"status": "error", "message": f"資料夾不存在: {folder_path}"}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.get("/api/v1/utils/pick_folder")
async def api_pick_folder(title: str = "選擇資料夾"):
    def _pick():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()
        path = filedialog.askdirectory(title=title)
        root.destroy()
        return path
    selected = await asyncio.to_thread(_pick)  # type: ignore
    return {"path": selected}

@router.get("/api/v1/utils/pick_file")
async def api_pick_file(title: str = "選擇檔案"):
    def _pick():
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()
        path = filedialog.askopenfilename(title=title)
        root.destroy()
        return path
    selected = await asyncio.to_thread(_pick)  # type: ignore
    return {"path": selected}

@router.post("/api/v1/utils/create_shortcut")
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
        sys_enc = locale.getpreferredencoding()
        with open(vbs_path, "w", encoding=sys_enc, errors="replace") as f:
            f.write(vbs_code.strip())
        
        subprocess.run(["cscript.exe", "//nologo", vbs_path], check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        return {"status": "success", "message": "桌面捷徑已成功建立！\n請查看桌面上的「Originsun Media Guard Web」。"}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"腳本執行失敗 (cscript 錯誤): {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"建立失敗: {str(e)}"}

@router.post("/api/v1/validate_paths")
async def validate_paths(req: ValidatePathsRequest):
    """檢查每個路徑的磁碟機是否存在、路徑本身是否存在（供遠端主機派發前驗證）"""
    results = {}
    for p in req.paths:
        drive = os.path.splitdrive(p)[0]  # e.g. "S:"
        drive_exists = os.path.exists(drive + os.sep) if drive else True
        path_exists = os.path.exists(p)
        results[p] = {
            "drive": drive,
            "drive_exists": drive_exists,
            "path_exists": path_exists,
        }
    return {"status": "ok", "results": results}

@router.get("/api/v1/utils/resolve_drop")
async def api_resolve_drop(name: str):
    def _find():
        import re
        import subprocess
        
        clean_name = name.strip()
        
        match = re.search(r'\(([a-zA-Z]):\)', clean_name)
        if match: return f"{match.group(1).upper()}:\\"
        match = re.search(r'^([a-zA-Z]):$', clean_name)
        if match: return f"{match.group(1).upper()}:\\"
        match = re.search(r'^([a-zA-Z])_drive$', clean_name, re.IGNORECASE)
        if match: return f"{match.group(1).upper()}:\\"

        drives = [f"{chr(d)}:\\" for d in range(65, 91) if os.path.exists(f"{chr(d)}:\\")]

        for drive in drives:
            try:
                out = subprocess.check_output(f'vol {drive[0]}:', text=True, shell=True, stderr=subprocess.DEVNULL)
                if clean_name.lower() in out.lower(): return drive
            except Exception: pass

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
                if p.startswith("FOLDER:"): open_folders.append(p.removeprefix("FOLDER:"))
                else: selected_items.append(p)

            exact_matches = [p for p in selected_items if os.path.basename(p).lower() == lower_name]
            if exact_matches: return max(exact_matches, key=len)
                    
            folder_candidates = []
            for folder in open_folders:
                candidate = os.path.join(folder, clean_name)
                if os.path.exists(candidate): folder_candidates.append(candidate)
            if folder_candidates: return max(folder_candidates, key=len)
        except Exception: pass

        user_profile = os.environ.get('USERPROFILE', '')
        known_system_dirs = ["Desktop", "Downloads", "Documents", "Videos", "Pictures", "Music"]
        if user_profile:
            for sysdir in known_system_dirs:
                if clean_name.lower() == sysdir.lower():
                    full = os.path.join(user_profile, sysdir)
                    if os.path.exists(full): return full
                    onedrive = os.environ.get('OneDriveConsumer', os.environ.get('OneDrive', ''))
                    if onedrive:
                        od_path = os.path.join(onedrive, sysdir)
                        if os.path.exists(od_path): return od_path

        if user_profile:
            for subdir in known_system_dirs:
                candidate = os.path.join(user_profile, subdir, clean_name)
                if os.path.exists(candidate): return candidate

        if len(clean_name) >= 2:
            for drive in drives:
                candidate = os.path.join(drive, clean_name)
                if os.path.exists(candidate): return candidate
                    
            for drive in drives:
                try:
                    for sub in os.scandir(drive):
                        if sub.is_dir():
                            candidate = os.path.join(sub.path, clean_name)
                            if os.path.exists(candidate): return candidate
                except Exception: continue

        return ""
        
    path = await asyncio.to_thread(_find)  # type: ignore
    return {"path": path}

@router.post("/api/admin/restart")
async def admin_restart(request: Request):
    _check_admin(request)
    import subprocess
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vbs = os.path.join(base_dir, "start_hidden.vbs")

    async def _restart():
        await asyncio.sleep(1.5)
        try:
            if os.path.exists(vbs):
                subprocess.Popen(["wscript.exe", vbs], shell=False)
            else:
                bat = os.path.join(base_dir, "update_agent.bat")
                subprocess.Popen(bat, shell=True, creationflags=0x00000008)
        except Exception: pass
        asyncio.get_running_loop().call_later(0.5, os._exit, 0)

    asyncio.create_task(_restart())
    return {"status": "restarting", "message": "Agent 正在重新啟動，請稍後重新整理頁面..."}

@router.get("/download_agent")
async def download_agent():
    file_path = "Originsun_Agent.zip"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Originsun_Agent.zip")
    return {"error": "系統尚未打包 Originsun_Agent.zip，請聯絡管理員放置此檔案於伺服器根目錄。"}

@router.post("/api/v1/publish")
async def publish_version(request: Request):
    """Trigger version publish from the web UI. Requires admin."""
    _check_admin(request)
    import subprocess, re
    body = await request.json()
    version = body.get("version", "").strip()
    notes = body.get("notes", "").strip()

    if not version:
        return JSONResponse({"status": "error", "message": "Version is required"}, 400)
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        return JSONResponse({"status": "error", "message": f"Invalid version format: {version} (need X.Y.Z)"}, 400)
    if not notes:
        notes = "Update"

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    publish_script = os.path.join(base_dir, "publish_update.py")
    if not os.path.exists(publish_script):
        return JSONResponse({"status": "error", "message": "publish_update.py not found"}, 500)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, publish_script, "--version", version, "--notes", notes],
            capture_output=True, text=True, timeout=600, cwd=base_dir,
        )
        log_output = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            return {"status": "ok", "message": f"v{version} published", "log": log_output}
        else:
            return JSONResponse({"status": "error", "message": f"Publish failed (exit {result.returncode})", "log": log_output}, 500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"status": "error", "message": "Publish timed out (600s)"}, 500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 500)


@router.get("/download_update")
async def download_update(background_tasks: BackgroundTasks):
    """Serve a lightweight code-only ZIP for OTA updates (~10-30MB).
    Excludes python_embed, ffmpeg, ffprobe, Originsun_Agent.zip, etc."""
    import zipfile, tempfile
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Use ota_manifest as single source of truth
    try:
        from ota_manifest import AGENT_FILES, AGENT_DIRS, EXCLUDE_DIRS
        files_to_include = list(AGENT_FILES)
        dirs_to_include = list(AGENT_DIRS)
    except ImportError:
        # Fallback if ota_manifest not available
        files_to_include = [
            "main.py", "config.py", "core_engine.py", "report_generator.py",
            "notifier.py", "drive_sync.py", "transcriber.py", "tts_engine.py",
            "taiwan_dict.json", "download_model.py", "version.json", "logo.ico",
            "update_agent.bat", "update_agent.py", "update_monitor.py",
            "preflight.py", "ota_manifest.py", "start_hidden.vbs",
            "requirements_agent.txt", "bootstrap.py", "server.py",
        ]
        EXCLUDE_DIRS = {'.venv', 'venv', 'node_modules', '.git', '.claude', 'tests',
                        'models', 'voice', 'credentials', '__pycache__', 'e2e',
                        'python_embed', '_rollback'}
        dirs_to_include = ["frontend", "templates", "core", "routers", "utils", "db"]

    # Auto-discover additional Python packages
    for entry in os.listdir(base_dir):
        if entry.startswith('.') or entry.startswith('_') or entry in EXCLUDE_DIRS:
            continue
        full = os.path.join(base_dir, entry)
        if os.path.isdir(full) and entry not in dirs_to_include:
            if any(f.endswith('.py') for f in os.listdir(full)):
                dirs_to_include.append(entry)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="originsun_update_")
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in files_to_include:
                full = os.path.join(base_dir, f)
                if os.path.exists(full):
                    zf.write(full, f)
            for d in dirs_to_include:
                dpath = os.path.join(base_dir, d)
                if not os.path.isdir(dpath):
                    continue
                for root, _, fnames in os.walk(dpath):
                    if "__pycache__" in root:
                        continue
                    for fn in fnames:
                        fp = os.path.join(root, fn)
                        arcname = os.path.relpath(fp, base_dir)
                        zf.write(fp, arcname)
        tmp.close()
        background_tasks.add_task(os.unlink, tmp.name)
        return FileResponse(tmp.name, filename="Originsun_Update.zip",
                            media_type="application/zip")
    except Exception as e:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/download_updater")
async def download_updater(request: Request):
    """Generate a one-click updater bat for first-time migration to HTTP OTA."""
    from fastapi.responses import Response
    # Use the request's host so the bat always points back to this server
    server_url = f"http://{request.headers.get('host', '192.168.1.11:8000')}"
    bat_content = f"""@echo off
chcp 65001 >nul
title Originsun Agent - 一鍵升級
color 0B
echo ===================================================
echo   Originsun Media Guard Pro - 一鍵升級工具
echo ===================================================
echo.

set "INSTALL_DIR="
if exist "C:\\OriginsunAgent\\main.py" set "INSTALL_DIR=C:\\OriginsunAgent"
if exist "%~dp0main.py" set "INSTALL_DIR=%~dp0"
if "%INSTALL_DIR%"=="" (
    echo [Error] 找不到安裝目錄！
    echo         請將此檔案放到 Agent 安裝目錄中再執行。
    pause
    exit /b 1
)
if "%INSTALL_DIR:~-1%"=="\\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

echo [System] 安裝目錄: %INSTALL_DIR%
echo [System] 伺服器: {server_url}
echo.

:: Kill running agent
echo [System] 正在停止舊版 Agent...
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":8000.*LISTENING" 2^>nul') do (
    taskkill /F /PID %%P >nul 2>&1
)
ping 127.0.0.1 -n 2 >nul

:: Download update
set "UPDATE_ZIP=%TEMP%\\originsun_update.zip"
echo [System] 正在從伺服器下載最新版本...
powershell -ExecutionPolicy Bypass -Command "try {{ [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '{server_url}/download_update' -OutFile '%UPDATE_ZIP%' -TimeoutSec 300 -UseBasicParsing }} catch {{ Write-Host $_.Exception.Message; exit 1 }}"
if %errorlevel% neq 0 (
    echo [Error] 下載失敗！請確認伺服器是否在線。
    pause
    exit /b 1
)

:: Extract
echo [System] 正在解壓更新檔案...
powershell -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%UPDATE_ZIP%' -DestinationPath '%INSTALL_DIR%' -Force"
del /f /q "%UPDATE_ZIP%" >nul 2>&1

echo.
echo [OK] 升級完成！正在重新啟動 Agent...
echo.

:: Restart agent
cd /d "%INSTALL_DIR%"
if exist "%INSTALL_DIR%\\start_hidden.vbs" (
    wscript "%INSTALL_DIR%\\start_hidden.vbs"
) else (
    start "" "%INSTALL_DIR%\\update_agent.bat"
)

echo [OK] Agent 已啟動，請重新整理網頁。
ping 127.0.0.1 -n 3 >nul
"""
    return Response(
        content=bat_content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=Originsun_Updater.bat"}
    )

@router.get("/bootstrap.ps1")
async def bootstrap_ps1(request: Request):
    """Serve a PowerShell bootstrap script for one-command agent migration."""
    from fastapi.responses import Response
    server_url = f"http://{request.headers.get('host', '192.168.1.11:8000')}"
    ps_content = f"""# Originsun Agent - One-Command Upgrade
# Usage: powershell -c "irm {server_url}/bootstrap.ps1 | iex"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host '==================================================='
Write-Host '  Originsun Media Guard Pro - One-Command Upgrade'
Write-Host '==================================================='
Write-Host ''

# 1. Find install directory
$installDir = $null
$candidates = @(
    'C:\\OriginsunAgent',
    "$env:USERPROFILE\\Desktop\\OriginsunAgent",
    "$env:LOCALAPPDATA\\OriginsunAgent"
)
foreach ($d in $candidates) {{
    if (Test-Path "$d\\main.py") {{ $installDir = $d; break }}
}}
if (-not $installDir) {{
    Write-Host '[ERROR] Cannot find OriginsunAgent install directory!' -ForegroundColor Red
    Write-Host '        Please run this from the Agent directory or install first.'
    pause; exit 1
}}
Write-Host "[System] Install dir: $installDir"
Write-Host "[System] Server: {server_url}"
Write-Host ''

# 2. Kill old agent on port 8000
Write-Host '[System] Stopping old Agent...'
try {{
    Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
        ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}
}} catch {{}}
Start-Sleep -Seconds 2

# 3. Download update ZIP
$zipPath = "$env:TEMP\\originsun_update.zip"
Write-Host '[System] Downloading latest version...'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri '{server_url}/download_update' -OutFile $zipPath -UseBasicParsing -TimeoutSec 300

# 4. Extract (overwrite all)
Write-Host '[System] Extracting update...'
Expand-Archive -Path $zipPath -DestinationPath $installDir -Force
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

# 5. Restart agent
Write-Host ''
Write-Host '[OK] Upgrade complete! Restarting Agent...' -ForegroundColor Green
$vbs = Join-Path $installDir 'start_hidden.vbs'
if (Test-Path $vbs) {{
    Start-Process wscript -ArgumentList "`"$vbs`""
}} else {{
    Start-Process (Join-Path $installDir 'update_agent.bat')
}}
Write-Host '[OK] Agent started. Please refresh the web page.' -ForegroundColor Green
Start-Sleep -Seconds 3
"""
    return Response(
        content=ps_content,
        media_type="text/plain; charset=utf-8",
    )

@router.get("/download_installer")
async def download_installer():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(base, "Install_or_Update.bat")
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Install_or_Update.bat")
    return {"error": "Install_or_Update.bat not found."}
