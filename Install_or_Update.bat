@echo off
setlocal EnableExtensions
title Originsun Agent Installer
echo.
echo ============================================================
echo   Originsun Media Guard Pro - Install / Update
echo ============================================================
echo.
echo Path: %~f0
echo.

set "SERVER=http://192.168.1.11:8000"
set "INSTALL_DIR=C:\OriginsunAgent"
set "SELF=%~f0"
set "LOCAL_COPY=%TEMP%\Originsun_Install_Update.bat"

REM Step 1: if UNC, copy to local and relaunch
if "%SELF:~0,2%"=="\\" goto :unc_copy
goto :check_admin

:unc_copy
echo [Step 1] Running from NAS, copying to local temp...
copy /Y "%SELF%" "%LOCAL_COPY%" >nul
if not exist "%LOCAL_COPY%" (
    echo [ERROR] Copy failed to %LOCAL_COPY%
    pause
    exit /b 1
)
echo [Step 1] Copy OK. Launching local copy...
start "" cmd /k "%LOCAL_COPY%"
exit /b 0

:check_admin
net session >nul 2>&1
if %errorlevel% equ 0 goto :run_install

echo [Step 2] Requesting administrator privileges...
echo          Click Yes on UAC prompt.
REM Write a tiny elevation helper to avoid nested-quote hell
> "%TEMP%\_originsun_elevate.vbs" echo Set objShell = CreateObject("Shell.Application")
>>"%TEMP%\_originsun_elevate.vbs" echo objShell.ShellExecute "cmd.exe", "/k """ ^& "%SELF%" ^& """", "", "runas", 1
cscript //nologo "%TEMP%\_originsun_elevate.vbs"
del "%TEMP%\_originsun_elevate.vbs" >nul 2>&1
exit /b 0

:run_install
echo [Step 3] Admin confirmed. Starting install...
echo.

REM Kill old process on port 8000
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>nul
)
taskkill /F /IM python.exe >nul 2>nul
timeout /t 2 /nobreak >nul

REM Find existing install
set "FOUND="
if exist "C:\OriginsunAgent\main.py"                       set "FOUND=C:\OriginsunAgent"
if exist "%LOCALAPPDATA%\OriginsunAgent\main.py"           set "FOUND=%LOCALAPPDATA%\OriginsunAgent"
if exist "%USERPROFILE%\Desktop\OriginsunAgent\main.py"    set "FOUND=%USERPROFILE%\Desktop\OriginsunAgent"
if exist "%USERPROFILE%\Desktop\Originsun_Agent\main.py"   set "FOUND=%USERPROFILE%\Desktop\Originsun_Agent"

if defined FOUND goto :do_update
goto :do_fresh

:do_update
echo [OK] Found: %FOUND%
echo [Update] Downloading update zip...
cd /d "%FOUND%"
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%SERVER%/download_update','%TEMP%\originsun_update.zip')"
if not exist "%TEMP%\originsun_update.zip" (
    echo [WARN] Download failed. Trying full install...
    goto :do_fresh
)
echo [Update] Extracting...
powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\originsun_update.zip' -DestinationPath '%FOUND%' -Force"
del "%TEMP%\originsun_update.zip" >nul 2>&1
echo [Update] Installing packages...
if exist "%FOUND%\python_embed\python.exe" "%FOUND%\python_embed\python.exe" -m pip install sqlalchemy[asyncio] asyncpg --quiet >nul 2>&1
call :autostart "%FOUND%"
echo [Update] Starting...
if exist "%FOUND%\start_hidden.vbs" start "" /D "%FOUND%" wscript.exe "%FOUND%\start_hidden.vbs"
echo.
echo ============================================================
echo   Update complete! Refresh browser with Ctrl+Shift+R
echo ============================================================
pause
exit /b 0

:do_fresh
echo [Fresh Install] Target: %INSTALL_DIR%
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%" 2>nul
if not exist "%INSTALL_DIR%" (
    echo [ERROR] Cannot create %INSTALL_DIR%. Admin required.
    pause
    exit /b 1
)

echo [Fresh 1/3] Downloading full package, about 1GB...
powershell -NoProfile -Command "(New-Object Net.WebClient).DownloadFile('%SERVER%/download_agent','%TEMP%\Originsun_Agent.zip')"
if not exist "%TEMP%\Originsun_Agent.zip" (
    echo [ERROR] Download failed. Check server %SERVER%
    pause
    exit /b 1
)

echo [Fresh 2/3] Extracting to %INSTALL_DIR%...
powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\Originsun_Agent.zip' -DestinationPath '%INSTALL_DIR%' -Force"
del "%TEMP%\Originsun_Agent.zip" >nul 2>&1
if not exist "%INSTALL_DIR%\main.py" (
    echo [ERROR] Extract failed, main.py missing
    pause
    exit /b 1
)

cd /d "%INSTALL_DIR%"
echo [Fresh 3/3] Installing packages...
if exist "%INSTALL_DIR%\python_embed\python.exe" "%INSTALL_DIR%\python_embed\python.exe" -m pip install sqlalchemy[asyncio] asyncpg --quiet >nul 2>&1

netsh advfirewall firewall show rule name="Originsun Agent Port 8000" >nul 2>&1
if %errorlevel% neq 0 netsh advfirewall firewall add rule name="Originsun Agent Port 8000" dir=in action=allow protocol=TCP localport=8000 >nul 2>&1

call :autostart "%INSTALL_DIR%"

echo [Fresh] Starting agent...
if exist "%INSTALL_DIR%\start_hidden.vbs" start "" /D "%INSTALL_DIR%" wscript.exe "%INSTALL_DIR%\start_hidden.vbs"

echo.
echo ============================================================
echo   Install complete!
echo   Open http://localhost:8000 in browser
echo   Agent will auto-start on boot
echo ============================================================
pause
exit /b 0

:autostart
REM %1 = APP_DIR (with quotes)
set "APP_DIR=%~1"
echo [AutoStart] Creating shortcuts and scheduled task...

REM Desktop shortcut via temp VBS (no nested quote hell)
> "%TEMP%\_originsun_mklnk.vbs" echo Set WshShell = CreateObject("WScript.Shell")
>>"%TEMP%\_originsun_mklnk.vbs" echo Set lnk = WshShell.CreateShortcut(WshShell.SpecialFolders("Desktop") ^& "\Originsun Agent.lnk")
>>"%TEMP%\_originsun_mklnk.vbs" echo lnk.TargetPath = "wscript.exe"
>>"%TEMP%\_originsun_mklnk.vbs" echo lnk.Arguments  = """%APP_DIR%\start_hidden.vbs"""
>>"%TEMP%\_originsun_mklnk.vbs" echo lnk.WorkingDirectory = "%APP_DIR%"
>>"%TEMP%\_originsun_mklnk.vbs" echo lnk.Save
cscript //nologo "%TEMP%\_originsun_mklnk.vbs" >nul 2>&1

REM Scheduled task onlogon
schtasks /query /tn "OriginsunAgent" >nul 2>&1
if %errorlevel% equ 0 schtasks /delete /tn "OriginsunAgent" /f >nul 2>&1
schtasks /create /tn "OriginsunAgent" /tr "wscript.exe \"%APP_DIR%\start_hidden.vbs\"" /sc onlogon /rl highest /f >nul 2>&1

del "%TEMP%\_originsun_mklnk.vbs" >nul 2>&1
exit /b 0
