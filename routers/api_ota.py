"""OTA update, publish, download, and restart endpoints.

Split from api_system.py — all endpoint paths remain unchanged.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import asyncio
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Query, Request  # type: ignore
from fastapi.responses import JSONResponse, FileResponse  # type: ignore

router = APIRouter()


def _check_admin(request: Request):
    """Check admin permission. No-op if auth module not available."""
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass  # auth module not available, skip check


# ── Module-level state ──────────────────────────────────────────────────
_publish_lock = asyncio.Lock()
_publish_status: dict = {}  # {"job_id": {"status": "running"/"done"/"error", "log": "...", "version": "..."}}
_PUBLISH_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "publish_history.json"
)


def _append_publish_history(version: str, notes: str, success: bool, user: str = "admin"):
    """Append a publish record to publish_history.json."""
    history = []
    if os.path.exists(_PUBLISH_HISTORY_FILE):
        try:
            with open(_PUBLISH_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
    history.append({
        "version": version,
        "notes": notes,
        "published_by": user,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "success": success,
    })
    # Keep last 50 entries
    history = history[-50:]
    with open(_PUBLISH_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ── Shared restart logic ────────────────────────────────────────────────

async def _do_update_restart():
    """OTA update + restart, used by /control/update and /internal/restart.

    Spawns a detached helper (see core.process_spawn) that waits for us
    to exit, then runs update_agent.py (download + extract + pip + preflight)
    and finally spawns a new uvicorn. Helper writes update_status.json
    along the way (read by master after restart to surface OTA result).
    """
    from core.process_spawn import trigger_detached_restart
    trigger_detached_restart(run_ota=True)
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return {"status": "updating"}


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/api/v1/control/update")
async def update_agent(request: Request):
    """Trigger OTA update + restart. Runs Install_or_Update logic as a detached BAT.

    Flow: write BAT to TEMP -> launch detached -> exit server -> BAT runs update_agent.py -> starts server.
    Progress written to update_status.json (same format as update_agent.py).

    No JWT required -- but restricted to localhost only.
    Remote callers use /internal/restart with X-Internal-Key instead.
    """
    client_ip = request.client.host if request.client else ''
    if client_ip not in ('127.0.0.1', '::1', 'localhost'):
        return JSONResponse({"detail": "Only localhost can trigger update"}, 403)
    return await _do_update_restart()


@router.get("/api/v1/update_status")
async def get_update_status():
    """Return last OTA update result from update_status.json.

    Called by master after agent restarts to check if the update succeeded or failed.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    status_file = os.path.join(base_dir, "update_status.json")
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"step": 0, "pct": 0, "msg": "no update history"}


@router.post("/api/v1/internal/restart")
async def internal_restart(request: Request):
    """Internal restart endpoint -- called by master server to trigger OTA update + restart.
    Uses X-Internal-Key header for simple auth (no JWT needed).
    Same logic as control/update but without JWT requirement.
    """
    key = request.headers.get("X-Internal-Key", "")
    if key != "originsun-internal-restart":
        return JSONResponse({"detail": "Invalid internal key"}, 401)
    return await _do_update_restart()


@router.post("/api/admin/restart")
async def admin_restart(request: Request):
    _check_admin(request)
    from core.process_spawn import trigger_detached_restart
    trigger_detached_restart(run_ota=False)
    asyncio.get_running_loop().call_later(1.5, os._exit, 0)
    return {"status": "restarting", "message": "Agent 正在重新啟動，請稍後重新整理頁面..."}


@router.get("/download_agent")
async def download_agent():
    file_path = "Originsun_Agent.zip"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Originsun_Agent.zip")
    return {"error": "系統尚未打包 Originsun_Agent.zip，請聯絡管理員放置此檔案於伺服器根目錄。"}


@router.get("/download_update")
async def download_update(background_tasks: BackgroundTasks):
    """Serve a lightweight code-only ZIP for OTA updates.
    ZIP is built in a thread to avoid blocking the event loop."""
    import zipfile, tempfile, asyncio
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _build_zip() -> str:
        """Synchronous ZIP builder -- runs in thread pool."""
        from ota_manifest import AGENT_FILES, AGENT_DIRS, EXCLUDE_DIRS
        files_to_include = list(AGENT_FILES)
        dirs_to_include = list(AGENT_DIRS)

        for entry in os.listdir(base_dir):
            if entry.startswith('.') or entry.startswith('_') or entry in EXCLUDE_DIRS:
                continue
            full = os.path.join(base_dir, entry)
            if os.path.isdir(full) and entry not in dirs_to_include:
                if any(f.endswith('.py') for f in os.listdir(full)):
                    dirs_to_include.append(entry)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="originsun_update_")
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
        return tmp.name

    try:
        zip_path = await asyncio.to_thread(_build_zip)
        background_tasks.add_task(os.unlink, zip_path)
        return FileResponse(zip_path, filename="Originsun_Update.zip",
                            media_type="application/zip")
    except Exception as e:
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
echo [OK] 升級完成！正在處理自動啟動設定...
echo.

:: Migrate to Startup-folder shortcut (Session 1 guaranteed) + delete legacy schtasks
> "%TEMP%\\_originsun_boot_lnk.vbs" echo Set WshShell = CreateObject("WScript.Shell")
>>"%TEMP%\\_originsun_boot_lnk.vbs" echo Set lnk = WshShell.CreateShortcut(WshShell.SpecialFolders("Startup") ^& "\\Originsun Master.lnk")
>>"%TEMP%\\_originsun_boot_lnk.vbs" echo lnk.TargetPath = "wscript.exe"
>>"%TEMP%\\_originsun_boot_lnk.vbs" echo lnk.Arguments  = "%INSTALL_DIR%\\start_hidden.vbs"
>>"%TEMP%\\_originsun_boot_lnk.vbs" echo lnk.WorkingDirectory = "%INSTALL_DIR%"
>>"%TEMP%\\_originsun_boot_lnk.vbs" echo lnk.WindowStyle = 7
>>"%TEMP%\\_originsun_boot_lnk.vbs" echo lnk.Save
cscript //nologo "%TEMP%\\_originsun_boot_lnk.vbs" >nul 2>&1
del "%TEMP%\\_originsun_boot_lnk.vbs" >nul 2>&1
schtasks /delete /tn "OriginsunBoot" /f >nul 2>&1
schtasks /delete /tn "OriginsunAgent" /f >nul 2>&1

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

# 5. Ensure Startup-folder shortcut exists (replaces legacy schtasks).
#    Startup folder shortcut is triggered by user's Session 1 explorer on
#    every logon, guaranteeing master always lands in interactive session
#    where tkinter/WinForms pickers can render.
Write-Host '[System] Ensuring Startup folder shortcut...'
try {{
    $startupFolder = [System.Environment]::GetFolderPath('Startup')
    $lnkPath = Join-Path $startupFolder 'Originsun Master.lnk'
    $WshShell = New-Object -ComObject WScript.Shell
    $lnk = $WshShell.CreateShortcut($lnkPath)
    $lnk.TargetPath = 'wscript.exe'
    $lnk.Arguments  = '"' + (Join-Path $installDir 'start_hidden.vbs') + '"'
    $lnk.WorkingDirectory = $installDir
    $lnk.WindowStyle = 7
    $lnk.Save()
    # Cleanup legacy schtasks paths (Session 0 silent-fail risk)
    schtasks /delete /tn 'OriginsunBoot' /f *>$null
    schtasks /delete /tn 'OriginsunAgent' /f *>$null
}} catch {{}}

# 6. Restart agent
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


@router.post("/api/v1/publish")
async def publish_version(request: Request):
    """Trigger async version publish. Returns immediately with job_id; poll /publish/status for progress."""
    _check_admin(request)

    if _publish_lock.locked():
        return JSONResponse({"status": "error", "message": "另一次發布正在進行中，請稍後再試"}, 409)

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

    # Extract username before launching background task
    pub_user = "admin"
    try:
        from core.auth import _extract_token
        payload = _extract_token(request)
        if payload:
            pub_user = payload.get("sub", "admin")
    except Exception:
        pass

    import uuid as _uuid
    job_id = _uuid.uuid4().hex[:8]
    _publish_status[job_id] = {"status": "running", "log": "", "version": version}

    async def _run():
        async with _publish_lock:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, publish_script, "--version", version, "--notes", notes],
                    capture_output=True, text=True, timeout=600, cwd=base_dir,
                )
                log_output = (result.stdout or "") + (result.stderr or "")
                success = result.returncode == 0
                _append_publish_history(version, notes, success, pub_user)
                _publish_status[job_id] = {
                    "status": "done" if success else "error",
                    "log": log_output,
                    "version": version,
                    "message": f"v{version} published" if success else f"Publish failed (exit {result.returncode})",
                }
            except subprocess.TimeoutExpired:
                _append_publish_history(version, notes, False, "system")
                _publish_status[job_id] = {"status": "error", "log": "", "version": version, "message": "Publish timed out (600s)"}
            except Exception as e:
                _append_publish_history(version, notes, False, "system")
                _publish_status[job_id] = {"status": "error", "log": "", "version": version, "message": str(e)}

    asyncio.create_task(_run())
    return {"status": "ok", "job_id": job_id, "message": "發布已啟動，背景執行中..."}


@router.get("/api/v1/publish/status")
async def publish_status(job_id: str = Query("")):
    """Poll publish job status."""
    if not job_id or job_id not in _publish_status:
        return {"status": "unknown", "message": "找不到此發布任務"}
    return _publish_status[job_id]


@router.get("/api/v1/publish/history")
async def get_publish_history():
    """Return recent publish history."""
    if os.path.exists(_PUBLISH_HISTORY_FILE):
        try:
            with open(_PUBLISH_HISTORY_FILE, "r", encoding="utf-8") as f:
                return {"history": json.load(f)}
        except Exception:
            pass
    return {"history": []}


@router.post("/api/v1/publish/rollback")
async def publish_rollback(request: Request):
    """Rollback to previous version by restoring backed-up version.json + ZIP."""
    _check_admin(request)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rollback_dir = os.path.join(base_dir, "_publish_rollback")

    bak_version = os.path.join(rollback_dir, "version.json")
    bak_zip = os.path.join(rollback_dir, "Originsun_Agent.zip")

    if not os.path.exists(bak_version):
        return JSONResponse({"status": "error", "message": "No rollback data available"}, 404)

    try:
        shutil.copy2(bak_version, os.path.join(base_dir, "version.json"))
        if os.path.exists(bak_zip):
            shutil.copy2(bak_zip, os.path.join(base_dir, "Originsun_Agent.zip"))
        with open(os.path.join(base_dir, "version.json"), "r", encoding="utf-8") as f:
            ver = json.load(f).get("version", "?")
        rb_user = "admin"
        try:
            from core.auth import _extract_token
            payload = _extract_token(request)
            if payload:
                rb_user = payload.get("sub", "admin")
        except Exception:
            pass
        _append_publish_history(ver, "ROLLBACK", True, rb_user)
        return {"status": "ok", "message": f"Rolled back to v{ver}"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 500)
