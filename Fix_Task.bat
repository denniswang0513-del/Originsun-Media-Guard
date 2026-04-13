@echo off
setlocal
title Originsun Agent - Fix Session 0 Issue

REM Self-elevate via UAC if not already admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    > "%TEMP%\_fix_task_elevate.vbs" echo Set o = CreateObject("Shell.Application")
    >>"%TEMP%\_fix_task_elevate.vbs" echo o.ShellExecute "cmd.exe", "/c """ ^& "%~f0" ^& """", "", "runas", 1
    cscript //nologo "%TEMP%\_fix_task_elevate.vbs"
    del "%TEMP%\_fix_task_elevate.vbs" >nul 2>&1
    exit /b 0
)

echo.
echo ============================================================
echo   Originsun Agent - Scheduled Task Session Fix
echo ============================================================
echo.
echo Admin privileges confirmed. Fixing scheduled task...
echo.

set "APP_DIR=C:\OriginsunAgent"
if not exist "%APP_DIR%\start_hidden.vbs" (
    set /p APP_DIR="Agent directory (default: C:\OriginsunAgent): "
    if "%APP_DIR%"=="" set "APP_DIR=C:\OriginsunAgent"
)
if not exist "%APP_DIR%\start_hidden.vbs" (
    echo [ERROR] start_hidden.vbs not found in %APP_DIR%
    pause
    exit /b 1
)

echo [1/4] Stopping current Agent...
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8000 " ^| findstr LISTENING 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
)

echo [2/4] Deleting old scheduled task...
schtasks /delete /tn "OriginsunAgent" /f >nul 2>&1

echo [3/4] Re-registering task (no /rl highest, keeps Session 1)...
schtasks /create /tn "OriginsunAgent" /tr "wscript.exe \"%APP_DIR%\start_hidden.vbs\"" /sc onlogon /f >nul
if %errorlevel% neq 0 (
    echo [ERROR] schtasks /create failed
    pause
    exit /b 1
)

echo [4/4] Starting Agent in user session...
schtasks /run /tn "OriginsunAgent"

echo.
echo Done. Open http://localhost:8000 and test the folder picker.
echo.
pause
