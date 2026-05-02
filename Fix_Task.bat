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
echo   Originsun Agent - Migrate to Startup-Folder autostart
echo ============================================================
echo.
echo Admin privileges confirmed. Migrating from schtasks to Startup folder.
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

echo [2/4] Removing legacy scheduled tasks (OriginsunBoot / OriginsunAgent)...
schtasks /delete /tn "OriginsunBoot" /f >nul 2>&1
schtasks /delete /tn "OriginsunAgent" /f >nul 2>&1

echo [3/4] Creating Startup-folder shortcut (always Session 1)...
> "%TEMP%\_fix_mklnk.vbs" echo Set WshShell = CreateObject("WScript.Shell")
>>"%TEMP%\_fix_mklnk.vbs" echo Set lnk = WshShell.CreateShortcut(WshShell.SpecialFolders("Startup") ^& "\Originsun Master.lnk")
>>"%TEMP%\_fix_mklnk.vbs" echo lnk.TargetPath = "wscript.exe"
>>"%TEMP%\_fix_mklnk.vbs" echo lnk.Arguments  = """%APP_DIR%\start_hidden.vbs"""
>>"%TEMP%\_fix_mklnk.vbs" echo lnk.WorkingDirectory = "%APP_DIR%"
>>"%TEMP%\_fix_mklnk.vbs" echo lnk.WindowStyle = 7
>>"%TEMP%\_fix_mklnk.vbs" echo lnk.Save
cscript //nologo "%TEMP%\_fix_mklnk.vbs" >nul 2>&1
del "%TEMP%\_fix_mklnk.vbs" >nul 2>&1

echo [4/4] Starting Agent in current session...
wscript "%APP_DIR%\start_hidden.vbs"

echo.
echo Done. 以後每次登入 Windows,Startup folder shortcut 會自動由 Session 1
echo explorer 觸發 master,picker 永遠工作。Open http://localhost:8000 verify.
echo.
pause
