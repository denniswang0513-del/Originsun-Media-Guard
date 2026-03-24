@echo off
title Originsun Agent Setup
echo.
echo ============================================================
echo   Originsun Media Guard Pro - Install / Update
echo ============================================================
echo.

set "SERVER=http://192.168.1.11:8000"

REM Kill old process on port 8000
echo [0/3] Stopping old Agent...
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>nul
)
timeout /t 2 /nobreak >nul

REM Search for existing Agent
set "FOUND="
for /d %%d in ("%USERPROFILE%\Desktop\Originsun*" "%USERPROFILE%\Downloads\Originsun*" "C:\Originsun*" "D:\Originsun*" "C:\Users\%USERNAME%\Originsun*") do @if exist "%%~d\main.py" set "FOUND=%%~d"

if not defined FOUND goto :fresh_install

echo [OK] Found Agent: %FOUND%
echo.

REM Force download latest update (bypass old bootstrap)
echo [1/3] Downloading latest update...
cd /d "%FOUND%"
powershell -Command "Invoke-WebRequest -Uri '%SERVER%/download_update' -OutFile '%TEMP%\originsun_update.zip'" 2>nul
if not exist "%TEMP%\originsun_update.zip" (
    echo [WARN] Update download failed, trying full install...
    goto :fresh_install
)

echo [2/3] Extracting update...
powershell -Command "Expand-Archive -Path '%TEMP%\originsun_update.zip' -DestinationPath '%FOUND%' -Force"
del "%TEMP%\originsun_update.zip" 2>nul

echo [2.5/3] Installing packages...
if exist .venv\Scripts\pip.exe (
    .venv\Scripts\pip.exe install sqlalchemy[asyncio] asyncpg --quiet 2>nul
) else if exist python_embed\python.exe (
    python_embed\python.exe -m pip install sqlalchemy[asyncio] asyncpg --quiet 2>nul
)

echo [3/3] Starting...
if exist start_hidden.vbs wscript.exe start_hidden.vbs
echo.
echo   Update complete! Press Ctrl+Shift+R in browser to refresh.
echo.
pause
exit /b 0

:fresh_install
echo [INFO] No existing Agent found. Fresh install to Desktop...
echo.
echo Downloading full package from server (~1GB, please wait)...
cd /d "%USERPROFILE%\Desktop"
powershell -Command "Invoke-WebRequest -Uri '%SERVER%/download_agent' -OutFile 'Originsun_Agent.zip'"
if not exist Originsun_Agent.zip (
    echo [ERROR] Download failed! Check if server %SERVER% is online.
    pause
    exit /b 1
)
echo Extracting...
if not exist Originsun_Agent mkdir Originsun_Agent
powershell -Command "Expand-Archive -Path 'Originsun_Agent.zip' -DestinationPath 'Originsun_Agent' -Force"
del Originsun_Agent.zip 2>nul
if exist Originsun_Agent\main.py (
    cd Originsun_Agent
    echo Installing packages...
    if exist python_embed\python.exe python_embed\python.exe -m pip install sqlalchemy[asyncio] asyncpg --quiet 2>nul
    echo Starting...
    if exist start_hidden.vbs wscript.exe start_hidden.vbs
    echo.
    echo   Install complete! Open http://localhost:8000 in browser.
) else (
    echo [ERROR] Extract failed!
)
echo.
pause
