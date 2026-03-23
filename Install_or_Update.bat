@echo off
title Originsun Agent Setup
echo.
echo ============================================================
echo   Originsun Media Guard Pro - Install / Update
echo ============================================================
echo.

set "SERVER=http://192.168.1.11:8000"
set "FOUND="

for /d %%d in ("%USERPROFILE%\Desktop\Originsun*") do @if exist "%%~d\main.py" set "FOUND=%%~d"
for /d %%d in ("%USERPROFILE%\Downloads\Originsun*") do @if exist "%%~d\main.py" set "FOUND=%%~d"
for /d %%d in ("C:\Originsun*") do @if exist "%%~d\main.py" set "FOUND=%%~d"
for /d %%d in ("D:\Originsun*") do @if exist "%%~d\main.py" set "FOUND=%%~d"
for /d %%d in ("C:\Users\%USERNAME%\Originsun*") do @if exist "%%~d\main.py" set "FOUND=%%~d"
if exist "%USERPROFILE%\Desktop\Originsun_Agent\main.py" set "FOUND=%USERPROFILE%\Desktop\Originsun_Agent"

if not defined FOUND goto :fresh_install

echo [OK] Found Agent: %FOUND%
echo.
cd /d "%FOUND%"

if exist .venv\Scripts\pip.exe (
    echo [1/3] Installing packages...
    .venv\Scripts\pip.exe install sqlalchemy[asyncio] asyncpg --quiet 2>nul
    echo [2/3] Updating...
    .venv\Scripts\python.exe bootstrap.py --update %SERVER%
) else if exist python_embed\python.exe (
    echo [1/3] Installing packages...
    python_embed\python.exe -m pip install sqlalchemy[asyncio] asyncpg --quiet 2>nul
    echo [2/3] Updating...
    python_embed\python.exe bootstrap.py --update %SERVER%
) else (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo [3/3] Starting...
if exist start_hidden.vbs wscript.exe start_hidden.vbs
echo.
echo   Update complete! Press Ctrl+Shift+R in browser to refresh.
echo.
pause
exit /b 0

:fresh_install
echo [INFO] No existing Agent found. Fresh install...
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
