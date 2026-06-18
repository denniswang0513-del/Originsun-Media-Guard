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

# ── Dev → Prod deploy (8001 dev checkout → 8000 production master) ────────
# Same machine (192.168.1.107): copy dev code into the production checkout,
# then restart 8000 so it loads the new code. Fleet stays on its own OTA.
_PROD_DIR = r"C:\OriginsunAgent"
_PROD_RESTART_URL = "http://127.0.0.1:8000/api/v1/internal/restart"
# Runtime-state files that live in AGENT_FILES but must NOT clobber prod:
#   settings.json   → prod DB config (mediaguard, not _dev) + notify tokens
#   taiwan_dict.json→ hot-edited on prod via the 正音字典 UI
#   users.json      → prod accounts (not in AGENT_FILES, excluded defensively)
_DEPLOY_EXCLUDE_FILES = {"settings.json", "taiwan_dict.json", "users.json"}


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

async def _do_update_restart(run_ota: bool = True):
    """OTA update + restart, used by /control/update and /internal/restart.

    Spawns a detached helper (see core.process_spawn) that waits for us
    to exit, then runs update_agent.py (download + extract + pip + preflight)
    and finally spawns a new uvicorn. Helper writes update_status.json
    along the way (read by master after restart to surface OTA result).

    run_ota=False → skip the OTA download/extract entirely and just respawn
    uvicorn on the current on-disk code. Used by deploy_to_prod, which already
    copied the new code locally: making the master OTA-download from *itself*
    while it is exiting failed ("無法連線到主控端") and left a uvicorn spawned
    in the wrong working dir → `No module named 'db'` crash (2026-06-18).
    The run_ota=False path goes straight to spawn_uvicorn_detached (cwd=base).
    """
    from core.process_spawn import trigger_detached_restart
    trigger_detached_restart(run_ota=run_ota)
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return {"status": "updating" if run_ota else "restarting"}


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
    # ?ota=0 → clean restart, no OTA (deploy_to_prod already copied the code).
    run_ota = request.query_params.get("ota", "1") not in ("0", "false", "no")
    return await _do_update_restart(run_ota=run_ota)


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
    server_url = f"http://{request.headers.get('host', '192.168.1.107:8000')}"
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
    server_url = f"http://{request.headers.get('host', '192.168.1.107:8000')}"
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


@router.get("/api/v1/publish/suggest_notes")
async def suggest_release_notes(since_version: str = Query("")):
    """Suggest release notes from git commit subjects since a baseline version.

    Baseline = ``since_version`` if given (e.g. prod's version when deploying
    8001→8000), else this checkout's current version.json. Finds the
    release/bump commit that introduced that version and lists the commit
    subjects after it, filtering version-bump / doc-bump noise. Falls back to
    the most recent commits when the base can't be located or git is absent.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _git(args, timeout=8):
        try:
            r = subprocess.run(["git"] + args, cwd=base_dir, capture_output=True,
                               text=True, timeout=timeout, encoding="utf-8", errors="replace")
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    baseline = (since_version or "").strip()
    if not baseline:
        vpath = os.path.join(base_dir, "version.json")
        if os.path.exists(vpath):
            try:
                with open(vpath, "r", encoding="utf-8") as f:
                    baseline = json.load(f).get("version", "")
            except Exception:
                baseline = ""

    base_sha = _git(["log", "-n", "1", "--format=%H", "--grep", re.escape(baseline)]) if baseline else ""
    if base_sha:
        raw = _git(["log", f"{base_sha}..HEAD", "--no-merges", "--format=%s"])
    else:
        raw = _git(["log", "-n", "20", "--no-merges", "--format=%s"])

    if not raw:
        return {"notes": "", "baseline_version": baseline, "base": base_sha, "count": 0, "total": 0}

    # Drop version-bump / release / doc-bump commits — keep real changes.
    noise = re.compile(r"^(chore\(release\)|chore\(publish\)|docs\(release\)|chore\(docs\).*bump)", re.I)
    subjects, seen = [], set()
    for s in (ln.strip() for ln in raw.splitlines()):
        if not s or noise.search(s) or s in seen:
            continue
        seen.add(s)
        subjects.append(s)

    total = len(subjects)
    CAP = 12
    notes = "; ".join(subjects[:CAP])
    if total > CAP:
        notes += f"; …等共 {total} 項變更"
    return {"notes": notes, "baseline_version": baseline, "base": base_sha,
            "count": min(total, CAP), "total": total}


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


# ── Dev → Prod deploy ─────────────────────────────────────────────────────

def _deploy_to_prod_sync(version: str, notes: str) -> dict:
    """Copy this (dev) checkout's code into the production checkout (_PROD_DIR),
    bumping version.json and skipping runtime-state files. Runs in a thread.

    Returns {"ok": bool, "log": str}. Does NOT restart 8000 (caller does).
    """
    from ota_manifest import AGENT_FILES, AGENT_DIRS, EXCLUDE_DIRS
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dst = _PROD_DIR
    log: list = []

    # ── Guards ──
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        return {"ok": False, "log": f"來源與目標相同（{src}）— 這不是 dev checkout，拒絕部署。"}
    if not os.path.isdir(dst) or not os.path.exists(os.path.join(dst, "main.py")):
        return {"ok": False, "log": f"生產目錄不存在或不是 agent checkout: {dst}"}

    # ── Preflight on dev code BEFORE touching prod ──
    preflight = os.path.join(src, "preflight.py")
    if os.path.exists(preflight):
        pf = subprocess.run([sys.executable, preflight], capture_output=True, text=True,
                            timeout=60, cwd=src)
        if pf.returncode != 0:
            return {"ok": False, "log": "Preflight 失敗，未動生產：\n" + (pf.stdout or pf.stderr)[:1500]}
        log.append("[OK] Preflight 通過")

    # ── Bump dev version.json (gets copied to prod below) ──
    vpath = os.path.join(src, "version.json")
    vdata = {}
    if os.path.exists(vpath):
        with open(vpath, "r", encoding="utf-8") as f:
            vdata = json.load(f)
    vdata["version"] = version
    vdata["build_date"] = datetime.now().strftime("%Y-%m-%d")
    vdata["notes"] = notes
    tmp = vpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vdata, f, indent=2, ensure_ascii=False)
    os.replace(tmp, vpath)
    log.append(f"[OK] dev version.json → v{version}")

    # ── Copy files ──
    copied = 0
    for fn in AGENT_FILES:
        if fn in _DEPLOY_EXCLUDE_FILES:
            log.append(f"[SKIP] {fn}（生產執行期資料，保留不覆蓋）")
            continue
        sp = os.path.join(src, fn)
        if os.path.exists(sp):
            shutil.copy2(sp, os.path.join(dst, fn))
            copied += 1

    # ── Copy dirs (recursive, skip excluded dirs / __pycache__ / state files) ──
    for d in AGENT_DIRS:
        sdir = os.path.join(src, d)
        if not os.path.isdir(sdir):
            continue
        for root, dirs, files in os.walk(sdir):
            dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS and x != "__pycache__"]
            rel = os.path.relpath(root, src)
            tgt = os.path.join(dst, rel)
            os.makedirs(tgt, exist_ok=True)
            for f in files:
                if f in _DEPLOY_EXCLUDE_FILES:
                    continue
                shutil.copy2(os.path.join(root, f), os.path.join(tgt, f))
                copied += 1
        log.append(f"[OK] 目錄 {d}/ 已複製")

    log.append(f"[OK] 共 {copied} 個檔案 → {dst}")
    return {"ok": True, "log": "\n".join(log)}


@router.get("/api/v1/deploy_to_prod")
async def deploy_to_prod_eligible():
    """Report whether THIS instance may deploy to the production master.

    Used by the publish modal to decide whether to show the deploy button —
    robust across access paths (localhost / LAN / cloudflared tunnel), unlike
    a client-side window.location.port check. Eligible when this checkout is
    not the prod checkout and the prod checkout exists.
    """
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    same = os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(_PROD_DIR))
    prod_ok = os.path.isdir(_PROD_DIR) and os.path.exists(os.path.join(_PROD_DIR, "main.py"))
    prod_version = None
    if prod_ok:
        try:
            with open(os.path.join(_PROD_DIR, "version.json"), "r", encoding="utf-8") as f:
                prod_version = json.load(f).get("version")
        except Exception:
            pass
    reason = ""
    if same:
        reason = "目前就在生產 checkout，無需部署"
    elif not prod_ok:
        reason = f"生產目錄不存在: {_PROD_DIR}"
    return {
        "eligible": (not same) and prod_ok,
        "reason": reason,
        "prod_dir": _PROD_DIR,
        "prod_version": prod_version,
        "this_dir": src,
    }


@router.post("/api/v1/deploy_to_prod")
async def deploy_to_prod(request: Request):
    """Deploy this dev checkout's code into the production master (8000) and
    restart it. Same-machine local copy; fleet agents are NOT touched.

    Returns immediately with job_id; poll /api/v1/publish/status for progress.
    """
    _check_admin(request)

    if _publish_lock.locked():
        return JSONResponse({"status": "error", "message": "另一次發布/部署正在進行中，請稍後再試"}, 409)

    body = await request.json()
    version = body.get("version", "").strip()
    notes = body.get("notes", "").strip()
    if not version or not re.match(r"^\d+\.\d+\.\d+$", version):
        return JSONResponse({"status": "error", "message": f"版本格式需為 X.Y.Z: {version}"}, 400)
    if not notes:
        notes = "Deploy to prod"

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
                result = await asyncio.to_thread(_deploy_to_prod_sync, version, notes)
                deploy_log = result.get("log", "")
                if not result.get("ok"):
                    _append_publish_history(version, f"DEPLOY→8000 FAILED: {notes}", False, pub_user)
                    _publish_status[job_id] = {
                        "status": "error", "log": deploy_log, "version": version,
                        "message": "部署失敗（生產未重啟）",
                    }
                    return

                # ── Restart 8000 so it loads the freshly-copied code ──
                # ?ota=0: code is already copied, so do a clean restart (no OTA).
                # The OTA path made the master download from itself while exiting
                # → spawned uvicorn from the wrong cwd → 'No module named db' crash.
                restart_msg = ""
                def _restart():
                    import urllib.request
                    req = urllib.request.Request(
                        _PROD_RESTART_URL + "?ota=0", method="POST", data=b"{}",
                        headers={"Content-Type": "application/json",
                                 "X-Internal-Key": "originsun-internal-restart"},
                    )
                    urllib.request.urlopen(req, timeout=5)
                try:
                    await asyncio.to_thread(_restart)
                    restart_msg = "已觸發 8000 重啟"
                except Exception as e:
                    restart_msg = f"檔案已部署，但重啟 8000 失敗（請手動重啟）: {e}"

                _append_publish_history(version, f"DEPLOY→8000: {notes}", True, pub_user)
                _publish_status[job_id] = {
                    "status": "done", "log": deploy_log + f"\n[OK] {restart_msg}",
                    "version": version,
                    "message": f"v{version} 已部署到生產 8000；{restart_msg}（約 10 秒後生效）",
                }
            except Exception as e:
                _append_publish_history(version, f"DEPLOY→8000 ERROR: {notes}", False, "system")
                _publish_status[job_id] = {"status": "error", "log": "", "version": version, "message": str(e)}

    asyncio.create_task(_run())
    return {"status": "started", "job_id": job_id}


# ── Deploy website/ frontend (dev 8001 → prod 8000) + trigger rebuild ──────
# 「🌐 發布官網前端」按鈕用：把 website/ 原始碼（不含 node_modules）同步到
# C:\OriginsunAgent\website，再叫 master 8000 重建（npm build → scp dist 到 NAS）。
# website/ 不在 OTA AGENT_DIRS（含 node_modules 太大），所以 deploy_to_prod 不帶它。
_WEBSITE_EXCLUDE_DIRS = {"node_modules", "dist", ".astro", "__pycache__", ".git"}


def _deploy_website_sync() -> dict:
    """把 dev checkout 的 website/ 原始碼複製到 prod website/（排除 node_modules/
    dist/.astro）。在 thread 跑。回 {ok, log}。"""
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(src_root, "website")
    dst = os.path.join(_PROD_DIR, "website")
    if os.path.normcase(os.path.abspath(src_root)) == os.path.normcase(os.path.abspath(_PROD_DIR)):
        return {"ok": False, "log": "來源與目標相同 — 這不是 dev checkout，拒絕。"}
    if not os.path.isdir(src):
        return {"ok": False, "log": f"dev website/ 不存在：{src}"}
    if not os.path.isdir(os.path.join(dst, "node_modules")):
        return {"ok": False, "log": f"生產 website/ 缺 node_modules（{dst}）— 需先把完整 website/（含 node_modules）放到 8000 一次。"}
    copied = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in _WEBSITE_EXCLUDE_DIRS]
        rel = os.path.relpath(root, src)
        tgt = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(tgt, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(tgt, f))
            copied += 1
    return {"ok": True, "log": f"[OK] website/ 原始碼已同步（{copied} 檔，排除 node_modules/dist/.astro）→ {dst}"}


def _trigger_master_rebuild_blocking(log: list) -> bool:
    """POST master 8000 /internal/rebuild，再輪詢 /internal/rebuild/status 直到完成。
    X-Internal-Key = JWT secret。回 True=build 成功。在 thread 跑。"""
    import urllib.request
    import time as _time
    from core.auth import _get_secret
    base = "http://127.0.0.1:8000"
    key = _get_secret()

    def _post(path):
        req = urllib.request.Request(base + path, method="POST", data=b"{}",
            headers={"Content-Type": "application/json", "X-Internal-Key": key})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def _get(path):
        req = urllib.request.Request(base + path, headers={"X-Internal-Key": key})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    try:
        trig = _post("/api/website/admin/internal/rebuild")
    except Exception as e:
        log.append(f"[ERROR] 觸發 master 重建失敗：{e}")
        return False
    if trig.get("reason") and not trig.get("queued"):
        log.append(f"[ERROR] master 拒絕重建：{trig.get('reason')}")
        return False
    log.append(f"[OK] master 重建已觸發（state={trig.get('state')}）")
    for _ in range(60):  # ~3 分鐘（60 × 3s）
        _time.sleep(3)
        try:
            st = _get("/api/website/admin/internal/rebuild/status")
        except Exception:
            continue
        s = st.get("state")
        if s == "success":
            log.append("[OK] 官網重建完成、dist 已推到 NAS")
            return True
        if s == "error":
            log.append(f"[ERROR] 重建失敗：{st.get('error')}\n{(st.get('output_tail') or '')[-400:]}")
            return False
    log.append("[ERROR] 重建逾時（3 分鐘）")
    return False


@router.get("/api/v1/deploy_website")
async def deploy_website_eligible():
    """是否可發布官網前端（dev checkout + 生產已有 website/node_modules）。"""
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    same = os.path.normcase(os.path.abspath(src_root)) == os.path.normcase(os.path.abspath(_PROD_DIR))
    has_src = os.path.isdir(os.path.join(src_root, "website"))
    prod_ok = os.path.isdir(_PROD_DIR) and os.path.exists(os.path.join(_PROD_DIR, "main.py"))
    prod_has_nm = os.path.isdir(os.path.join(_PROD_DIR, "website", "node_modules"))
    return {"eligible": (not same) and prod_ok and has_src and prod_has_nm}


@router.post("/api/v1/deploy_website")
async def deploy_website(request: Request):
    """同步 website/ 原始碼 → 8000 + 觸發 master 重建 → dist 上 NAS。輪詢 /publish/status。"""
    _check_admin(request)
    if _publish_lock.locked():
        return JSONResponse({"status": "error", "message": "另一次發布/部署正在進行中"}, 409)
    import uuid as _uuid
    job_id = _uuid.uuid4().hex[:8]
    _publish_status[job_id] = {"status": "running", "log": "", "version": "website"}

    async def _run():
        async with _publish_lock:
            log: list = []
            try:
                sync = await asyncio.to_thread(_deploy_website_sync)
                if sync.get("log"):
                    log.append(sync["log"])
                if not sync.get("ok"):
                    _publish_status[job_id] = {"status": "error", "log": "\n".join(log),
                                               "version": "website", "message": "website/ 同步失敗"}
                    return
                ok = await asyncio.to_thread(_trigger_master_rebuild_blocking, log)
                _publish_status[job_id] = {
                    "status": "done" if ok else "error",
                    "log": "\n".join(log), "version": "website",
                    "message": "官網前端已發布、對外站已更新 🌐" if ok else "同步成功，但重建失敗",
                }
                _append_publish_history("website", "發布官網前端 → NAS", ok, "admin")
            except Exception as e:
                _publish_status[job_id] = {"status": "error", "log": "\n".join(log),
                                           "version": "website", "message": str(e)}

    asyncio.create_task(_run())
    return {"status": "started", "job_id": job_id}


# ── Push to the entire production fleet (dev → all agents via master 8000) ──
_MASTER_BASE = "http://127.0.0.1:8000"


def _master_login() -> str:
    """Get an admin token from the production master (8000). Tries admin/admin."""
    import urllib.request
    body = json.dumps({"username": "admin", "password": "admin"}).encode()
    req = urllib.request.Request(_MASTER_BASE + "/api/v1/auth/login", method="POST",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode("utf-8")).get("token", "")


def _master_get(path: str, token: str):
    import urllib.request
    req = urllib.request.Request(_MASTER_BASE + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _push_fleet_sync(dry_run: bool) -> dict:
    """Trigger OTA update on every production agent via the master (8000).

    Pull-model: each agent downloads from the master's /download_update, so the
    master must ALREADY hold the target version (use 部署到生產 8000 first).
    No force → the master's per-agent busy-check (409) skips busy machines.
    dry_run=True only previews health/busy, never triggers an update.
    """
    import urllib.request
    import urllib.error
    try:
        token = _master_login()
    except Exception as e:
        return {"ok": False, "error": f"登入主控端 8000 失敗：{e}（admin/admin 不對請改帳密）"}
    if not token:
        return {"ok": False, "error": "登入主控端 8000 取不到 token"}
    try:
        agents = (_master_get("/api/v1/agents", token).get("agents") or [])
    except Exception as e:
        return {"ok": False, "error": f"取機隊清單失敗：{e}"}

    results = []
    for a in agents:
        aid = a.get("id"); name = a.get("name"); url = (a.get("url") or "").rstrip("/")
        entry = {"id": aid, "name": name, "url": url}
        if dry_run:
            try:
                h = _master_get(f"/api/v1/agents/{aid}/health", token)
                entry["reachable"] = (h.get("status") == "ok")
                entry["version"] = h.get("version")
                entry["busy"] = bool(h.get("worker_busy"))
                entry["result"] = ("busy(會跳過)" if entry["busy"]
                                   else ("would-push" if entry["reachable"] else "離線(會跳過)"))
            except Exception as e:
                entry["reachable"] = False
                entry["result"] = "離線(會跳過)"
                entry["detail"] = str(e)[:100]
            results.append(entry)
            continue
        # real push — no force, master returns 409 to skip busy agents
        try:
            req = urllib.request.Request(
                _MASTER_BASE + f"/api/v1/agents/{aid}/update", method="POST", data=b"{}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                resp = json.loads(r.read().decode("utf-8"))
            entry["result"] = "skipped-busy" if resp.get("status") == "busy" else "triggered"
            entry["detail"] = resp.get("status", "")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                try:
                    bd = json.loads(e.read().decode("utf-8"))
                except Exception:
                    bd = {}
                jobs = [j.get("task_type") for j in bd.get("active_jobs", []) if isinstance(j, dict)]
                entry["result"] = "skipped-busy"
                entry["detail"] = f"queue={bd.get('queue_length', 0)} jobs={jobs}"
            else:
                entry["result"] = "error"; entry["detail"] = f"HTTP {e.code}"
        except Exception as e:
            entry["result"] = "error"; entry["detail"] = str(e)[:100]
        results.append(entry)
    return {"ok": True, "dry_run": dry_run, "count": len(agents), "results": results}


@router.post("/api/v1/push_fleet")
async def push_fleet(request: Request, dry_run: bool = False):
    """Trigger OTA on the entire production fleet via master 8000 (dev-only orchestration).

    dry_run=True previews machines (busy/reachable) WITHOUT pushing.
    Returns immediately with job_id; poll /api/v1/publish/status.
    """
    _check_admin(request)
    if _publish_lock.locked():
        return JSONResponse({"status": "error", "message": "另一次發布/部署/推送正在進行中"}, 409)

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
    _publish_status[job_id] = {"status": "running", "log": "", "version": "fleet"}

    async def _run():
        async with _publish_lock:
            try:
                res = await asyncio.to_thread(_push_fleet_sync, dry_run)
                if not res.get("ok"):
                    _publish_status[job_id] = {"status": "error", "log": "", "version": "fleet",
                                               "message": res.get("error", "推送失敗")}
                    return
                rs = res["results"]
                tag = "[DRY-RUN 預覽] " if dry_run else ""
                lines = [f"{tag}機隊共 {res['count']} 台："]
                for r in rs:
                    lines.append(f"  {r.get('name')} ({r.get('url')}): {r.get('result')} {r.get('detail', '')}".rstrip())
                if dry_run:
                    pushable = sum(1 for r in rs if r.get("result") == "would-push")
                    summary = f"{tag}{res['count']} 台；可推 {pushable}、busy/離線跳過 {res['count'] - pushable}"
                else:
                    trig = sum(1 for r in rs if r.get("result") == "triggered")
                    busy = sum(1 for r in rs if r.get("result") == "skipped-busy")
                    err = sum(1 for r in rs if r.get("result") == "error")
                    summary = f"推送完成：triggered {trig}、busy 跳過 {busy}、error {err}（共 {res['count']} 台）"
                    _append_publish_history("fleet", f"PUSH_FLEET: {summary}", err == 0, pub_user)
                _publish_status[job_id] = {"status": "done", "log": "\n".join(lines), "version": "fleet",
                                           "message": summary, "results": rs, "dry_run": dry_run}
            except Exception as e:
                _publish_status[job_id] = {"status": "error", "log": "", "version": "fleet", "message": str(e)}

    asyncio.create_task(_run())
    return {"status": "started", "job_id": job_id}
