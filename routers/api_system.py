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
    # No job_id: stop ALL running/paused jobs (backward compat)
    for j in state.get_all_jobs().values():
        if j.status in (state.JobStatus.RUNNING, state.JobStatus.PAUSED):
            if j.engine:
                j.engine.request_stop()
            if j.asyncio_task and not j.asyncio_task.done():
                j.asyncio_task.cancel()
            j.status = state.JobStatus.CANCELLED
    return {"status": "stopped"}

@router.post("/api/v1/control/update")
async def update_agent():
    import subprocess, json, sys
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bat_path = os.path.join(base_dir, "update_agent.bat")
        monitor_path = os.path.join(base_dir, "update_monitor.py")
        status_file = os.path.join(base_dir, "update_status.json")

        # Write initial status so monitor has something to serve immediately
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump({"step": 0, "pct": 2, "msg": "正在啟動更新程序..."}, f, ensure_ascii=False)

        # Start monitor on port 8001 — DETACHED so it survives os._exit
        subprocess.Popen(
            [sys.executable, monitor_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )

        # Copy bat to TEMP so NAS xcopy won't break it mid-execution
        import shutil, tempfile
        temp_bat = os.path.join(tempfile.gettempdir(), "originsun_agent_launcher.bat")
        shutil.copy2(bat_path, temp_bat)

        # Start update bat from TEMP — hidden window
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        subprocess.Popen(
            ["cmd", "/c", temp_bat, "tempcopy", base_dir],
            startupinfo=si,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            shell=False
        )
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
    import json
    from config import load_settings
    ota_dir = load_settings().get("nas_paths", {}).get("ota_dir", "")
    if not ota_dir:
        return {"version": "unknown", "error": "nas_paths.ota_dir not configured"}
    v_file = os.path.join(ota_dir, "version.json")
    try:
        if os.path.exists(v_file):
            with open(v_file, "r", encoding="utf-8") as f: return json.load(f)
        return {"version": "unknown", "error": "NAS version.json not found"}
    except Exception as e: return {"version": "unknown", "error": str(e)}

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
async def admin_restart():
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

@router.get("/download_installer")
async def download_installer():
    file_path = "Install_Originsun_Agent.bat"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Install_Originsun_Agent.bat")
    return {"error": "找不到自動安裝腳本，請確認伺服器根目錄下有 Install_Originsun_Agent.bat。"}
