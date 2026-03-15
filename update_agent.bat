@echo off
chcp 65001 >nul
title Originsun SaaS Agent Launcher
color 0B

:: ==== Auto-Hide Mechanism ====
if "%~1"=="hidden" goto :run_agent
echo CreateObject("Wscript.Shell").Run """%~f0"" hidden", 0, False > "%TEMP%\hide_agent.vbs"
wscript "%TEMP%\hide_agent.vbs"
del "%TEMP%\hide_agent.vbs"
exit /b

:run_agent
:: =============================

echo ===================================================
echo   Originsun Media Guard Pro - Local Agent
echo ===================================================
echo.

set "INSTALL_DIR=%~dp0"
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

set "NAS_LATEST=\\192.168.1.132\Container\AI_Workspace\agents\Originsun Media Guard Pro"
set "EMBED_PY=%INSTALL_DIR%\python_embed\python.exe"

echo [System] Checking for updates, please wait...
ping 127.0.0.1 -n 4 > nul

if exist "%NAS_LATEST%" (
    echo [System] NAS connected. Syncing latest version...
    xcopy "%NAS_LATEST%\*" "%INSTALL_DIR%\" /Y /D /E /C /I >nul
    echo [System] Sync complete.
    echo [System] Installing/updating Python packages...
    "%EMBED_PY%" -m pip install -r "%INSTALL_DIR%\0225_requirements.txt" --quiet --no-warn-script-location >nul 2>&1
    echo [System] Package check complete.
) else (
    echo [System] NAS unavailable. Starting with current version.
)

echo.
echo [System] Starting Originsun Local Agent...
echo [Hint] Do not close this window. Minimize it and use the web interface.
echo ---------------------------------------------------

netsh advfirewall firewall show rule name="Originsun Agent Port 8000" >nul 2>&1
if %errorlevel% neq 0 (
    echo [System] Requesting Administrator privileges to add Firewall rule...
    powershell -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Originsun Agent Port 8000\" dir=in action=allow protocol=TCP localport=8000' -Verb RunAs -WindowStyle Hidden"
)

"%EMBED_PY%" -m uvicorn main:io_app --host 0.0.0.0 --port 8000

pause
