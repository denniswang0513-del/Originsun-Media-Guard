@echo off
chcp 65001 >nul
title Originsun SaaS Agent Launcher
color 0B

:: ==== Self-relaunch from TEMP to survive file overwrite ====
if "%~1"=="tempcopy" goto :run_agent
:: Copy self to TEMP then relaunch hidden (so ZIP extraction won't break running script)
set "TEMP_BAT=%TEMP%\originsun_agent_launcher.bat"
copy /y "%~f0" "%TEMP_BAT%" >nul
set "ORIG_DIR=%~dp0"
if "%ORIG_DIR:~-1%"=="\" set "ORIG_DIR=%ORIG_DIR:~0,-1%"
> "%TEMP%\hide_agent.vbs" echo CreateObject("Wscript.Shell").Run """%TEMP_BAT%"" tempcopy ""%ORIG_DIR%""", 0, False
wscript "%TEMP%\hide_agent.vbs"
del "%TEMP%\hide_agent.vbs"
exit /b

:run_agent
:: =============================

:: Use passed dir (from TEMP relaunch) or fallback to own dir
if not "%~2"=="" (
    set "INSTALL_DIR=%~2"
) else (
    set "INSTALL_DIR=%~dp0"
)
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

:: Master server URL (passed as 3rd arg from api_system.py, or default)
if not "%~3"=="" (
    set "MASTER_URL=%~3"
) else (
    set "MASTER_URL=http://192.168.1.11:8000"
)

:: ---- Guard: must run from local disk, not NAS ----
echo %INSTALL_DIR% | findstr /i "^\\\\" >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo [ERROR] 請勿直接從 NAS 執行此程式！
    echo         請先用桌面上的「安裝 Originsun Agent」捷徑完成安裝，
    echo         安裝完成後從桌面捷徑啟動。
    echo.
    pause
    exit /b 1
)

echo ===================================================
echo   Originsun Media Guard Pro - Local Agent
echo ===================================================
echo.

set "STATUS_FILE=%INSTALL_DIR%\update_status.json"
set "UPDATE_ZIP=%TEMP%\originsun_update.zip"

:: ---- Detect best Python executable ----
set "EMBED_PY="
if exist "%INSTALL_DIR%\python_embed\python.exe" (
    set "EMBED_PY=%INSTALL_DIR%\python_embed\python.exe"
    goto :found_py
)
if exist "%INSTALL_DIR%\.venv\Scripts\python.exe" (
    set "EMBED_PY=%INSTALL_DIR%\.venv\Scripts\python.exe"
    goto :found_py
)
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%P in ('where python') do (
        set "EMBED_PY=%%P"
        goto :found_py
    )
)
echo [Error] 找不到 Python！請執行安裝精靈。
pause
exit /b 1

:found_py
echo [System] Using Python: %EMBED_PY%
set "REQ_FILE=%INSTALL_DIR%\0225_requirements.txt"
set "REQ_BACKUP=%TEMP%\originsun_req_backup.txt"
set "NEED_PIP=1"

echo [System] Checking for updates from %MASTER_URL% ...

:: ---- Backup requirements before update ----
if exist "%REQ_FILE%" copy /y "%REQ_FILE%" "%REQ_BACKUP%" >nul

:: ---- Step 1: Download update from master server via HTTP ----
echo {"step":1,"pct":5,"msg":"正在從伺服器下載最新版本..."} > "%STATUS_FILE%"
echo [System] Downloading update from %MASTER_URL%/download_update ...

:: ---- Start update monitor on port 8001 ----
if exist "%INSTALL_DIR%\update_monitor.py" (
    start /b "" "%EMBED_PY%" "%INSTALL_DIR%\update_monitor.py"
    ping 127.0.0.1 -n 2 > nul
)

powershell -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%MASTER_URL%/download_update' -OutFile '%UPDATE_ZIP%' -TimeoutSec 300 -UseBasicParsing } catch { exit 1 }"
if %errorlevel% neq 0 (
    echo {"step":1,"pct":5,"msg":"下載失敗，使用目前版本啟動伺服器..."} > "%STATUS_FILE%"
    echo [System] Download failed. Starting with current version.
    set "NEED_PIP=0"
    goto :check_pip
)

echo {"step":1,"pct":20,"msg":"正在解壓更新檔案..."} > "%STATUS_FILE%"
echo [System] Extracting update...
powershell -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%UPDATE_ZIP%' -DestinationPath '%INSTALL_DIR%' -Force"
del /f /q "%UPDATE_ZIP%" >nul 2>&1

echo {"step":1,"pct":28,"msg":"更新完成，正在檢查套件..."} > "%STATUS_FILE%"
echo [System] Update extracted.

:: ---- Check if requirements changed (use && to avoid delayed expansion issue) ----
if exist "%REQ_BACKUP%" (
    fc /b "%REQ_FILE%" "%REQ_BACKUP%" >nul 2>&1 && set "NEED_PIP=0"
    del "%REQ_BACKUP%" >nul 2>&1
)

:check_pip
:: ---- Step 2: Install packages (only if requirements changed) ----
if "%NEED_PIP%"=="0" (
    echo {"step":2,"pct":82,"msg":"套件無需更新，跳過安裝。"} > "%STATUS_FILE%"
    echo [System] Requirements unchanged, skipping pip install.
    goto :start_server
)

echo {"step":2,"pct":30,"msg":"正在安裝/更新 Python 套件（首次可能需要 10-20 分鐘）..."} > "%STATUS_FILE%"
echo [System] Requirements changed. Installing/updating Python packages...
echo [System] First-time install may take 10-20 minutes.
echo ---------------------------------------------------
"%EMBED_PY%" -m pip install -r "%INSTALL_DIR%\0225_requirements.txt" --no-warn-script-location
echo ---------------------------------------------------
echo {"step":2,"pct":82,"msg":"套件安裝完成！"} > "%STATUS_FILE%"
echo [System] Package install complete.

:start_server
:: ---- Step 3: Start server ----
echo {"step":3,"pct":85,"msg":"正在重新啟動伺服器..."} > "%STATUS_FILE%"
echo.
echo [System] Starting Originsun Local Agent...
echo [Hint] Do not close this window. Minimize it and use the web interface.
echo ---------------------------------------------------

:: ---- Kill processes bound to port 8000 (without killing ourselves or update_monitor) ----
echo [System] Freeing port 8000...
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":8000.*LISTENING" 2^>nul') do (
    echo [System] Killing PID %%P on port 8000...
    taskkill /F /PID %%P >nul 2>&1
)
ping 127.0.0.1 -n 3 >nul

netsh advfirewall firewall show rule name="Originsun Agent Port 8000" >nul 2>&1
if %errorlevel% neq 0 (
    echo [System] Requesting Administrator privileges to add Firewall rule...
    powershell -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Originsun Agent Port 8000\" dir=in action=allow protocol=TCP localport=8000' -Verb RunAs -WindowStyle Hidden"
)

:: ---- Change to install directory before starting uvicorn ----
cd /d "%INSTALL_DIR%"
if %errorlevel% neq 0 (
    echo [Error] Cannot cd to %INSTALL_DIR%
    pause
    exit /b 1
)

:: ---- Start uvicorn (log to file since window is hidden) ----
echo [System] Launching uvicorn on port 8000...
set "PYTHONPATH=%INSTALL_DIR%"
echo [%date% %time%] Starting uvicorn... >> "%INSTALL_DIR%\agent_server.log"
"%EMBED_PY%" -m uvicorn main:io_app --host 0.0.0.0 --port 8000 >> "%INSTALL_DIR%\agent_server.log" 2>&1
