@echo off
chcp 65001 >nul
title Originsun Media Guard Pro - Local Agent
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
    echo [ERROR] Do not run from NAS! Use the desktop shortcut.
    echo.
    pause
    exit /b 1
)

echo ===================================================
echo   Originsun Media Guard Pro - Local Agent
echo ===================================================
echo.

:: ---- Detect best Python executable ----
set "EMBED_PY="
if exist "%INSTALL_DIR%\.venv\Scripts\python.exe" (
    set "EMBED_PY=%INSTALL_DIR%\.venv\Scripts\python.exe"
    goto :found_py
)
if exist "%INSTALL_DIR%\python_embed\python.exe" (
    set "EMBED_PY=%INSTALL_DIR%\python_embed\python.exe"
    goto :found_py
)
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%P in ('where python') do (
        set "EMBED_PY=%%P"
        goto :found_py
    )
)
echo [Error] Python not found! Please run the installer.
pause
exit /b 1

:found_py
echo [System] Using Python: %EMBED_PY%

:: ---- Start update monitor on port 8001 ----
if exist "%INSTALL_DIR%\update_monitor.py" (
    start /b "" "%EMBED_PY%" "%INSTALL_DIR%\update_monitor.py"
    ping 127.0.0.1 -n 2 > nul
)

:: ---- Run Python OTA updater (backup → download → pip → preflight → rollback) ----
echo [System] Running OTA updater...
"%EMBED_PY%" "%INSTALL_DIR%\update_agent.py" "%MASTER_URL%"
if %errorlevel% neq 0 (
    echo [System] Update failed, rolled back. Starting with previous version.
) else (
    echo [System] Update check complete.
)

:: ---- Kill processes bound to port 8000 ----
echo [System] Freeing port 8000...
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":8000.*LISTENING" 2^>nul') do (
    echo [System] Killing PID %%P on port 8000...
    taskkill /F /PID %%P >nul 2>&1
)
ping 127.0.0.1 -n 3 >nul

:: ---- Firewall rule ----
netsh advfirewall firewall show rule name="Originsun Agent Port 8000" >nul 2>&1
if %errorlevel% neq 0 (
    echo [System] Requesting Administrator privileges for Firewall rule...
    powershell -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Originsun Agent Port 8000\" dir=in action=allow protocol=TCP localport=8000' -Verb RunAs -WindowStyle Hidden"
)

:: ---- Start server ----
cd /d "%INSTALL_DIR%"
if %errorlevel% neq 0 (
    echo [Error] Cannot cd to %INSTALL_DIR%
    pause
    exit /b 1
)

:: Restore full environment from registry
for /f "delims=" %%P in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"') do set "PATH=%%P"

echo [System] Launching uvicorn on port 8000...
set "PYTHONPATH=%INSTALL_DIR%"
echo [%date% %time%] Starting uvicorn... >> "%INSTALL_DIR%\agent_server.log"
"%EMBED_PY%" -m uvicorn main:io_app --host 0.0.0.0 --port 8000 >> "%INSTALL_DIR%\agent_server.log" 2>&1
