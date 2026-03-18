@echo off
title Originsun SaaS Agent Installer
color 0B
chcp 65001 >nul

echo ===================================================
echo   Originsun Media Guard Pro - Auto Installer
echo ===================================================
echo.

set "INSTALL_DIR=C:\OriginsunAgent"
set "SERVER_URL=http://192.168.1.11:8000"
rem NAS_ZIP left empty — install always downloads from server HTTP endpoint
set "NAS_ZIP="
set "ZIP_FILE=%TEMP%\Originsun_Agent.zip"

echo [Info] Install target : %INSTALL_DIR%
echo.

:: Step 1: Create install directory
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if %errorlevel% neq 0 (
    echo [Error] Cannot create %INSTALL_DIR%. Try running as Administrator.
    pause
    exit /b 1
)
echo [Step 1/5] Install directory ready.

:: Step 2: Get the agent package (try NAS first, then HTTP)
if not exist "%NAS_ZIP%" goto :try_http
echo [Step 2/5] Copying package from NAS (~1GB). Please wait...
copy /Y "%NAS_ZIP%" "%ZIP_FILE%" >nul
if %errorlevel% neq 0 goto :try_http
echo [Step 2/5] Copy from NAS complete.
goto :extract

:try_http
echo [Step 2/5] Downloading package from server (~1GB). Please wait...
powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%SERVER_URL%/download_agent' -OutFile '%ZIP_FILE%'"
if %errorlevel% neq 0 (
    echo.
    echo [Error] Download failed. Please check:
    echo         1. You are connected to the company LAN
    echo         2. NAS or main server is reachable
    pause
    exit /b 1
)
echo [Step 2/5] Download complete.

:extract
:: Step 3: Extract
echo [Step 3/5] Extracting... (this may take 1-2 minutes)
powershell -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%INSTALL_DIR%' -Force"
if %errorlevel% neq 0 (
    echo [Error] Extraction failed. Ensure at least 2GB free on C:\.
    echo         Try running as Administrator.
    pause
    exit /b 1
)
del /f /q "%ZIP_FILE%" >nul 2>&1
echo [Step 3/5] Extraction complete.

:: Step 3.5: Install Python packages
if not exist "%INSTALL_DIR%\0225_requirements.txt" goto :skip_pip
echo [Step 3.5/5] Installing Python packages (first run may take 5-10 minutes)...
"%INSTALL_DIR%\python_embed\python.exe" -m pip install -r "%INSTALL_DIR%\0225_requirements.txt" --no-warn-script-location
echo [Step 3.5/5] Package install complete.
:skip_pip

:: Step 4: Configure Firewall
echo [Step 4/5] Configuring Windows Firewall...
netsh advfirewall firewall show rule name="Originsun Agent Port 8000" >nul 2>&1
if %errorlevel% neq 0 (
    echo [System] Requesting Administrator privileges to add Firewall rule...
    powershell -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Originsun Agent Port 8000\" dir=in action=allow protocol=TCP localport=8000' -Verb RunAs -WindowStyle Hidden"
)
echo [Step 4/5] Firewall ready.

:: Step 5: Create Desktop shortcut + Startup
echo [Step 5/5] Creating desktop shortcut and startup entry...
powershell -ExecutionPolicy Bypass -Command "$s=New-Object -ComObject WScript.Shell; $lnk=$s.CreateShortcut('%USERPROFILE%\Desktop\Originsun Agent.lnk'); $lnk.TargetPath='wscript.exe'; $lnk.Arguments='""%INSTALL_DIR%\start_hidden.vbs""'; $lnk.WorkingDirectory='%INSTALL_DIR%'; $lnk.IconLocation='%INSTALL_DIR%\logo.ico'; $lnk.Save()"
powershell -ExecutionPolicy Bypass -Command "$s=New-Object -ComObject WScript.Shell; $startup=$s.SpecialFolders.Item('Startup'); $lnk=$s.CreateShortcut($startup + '\Originsun Agent.lnk'); $lnk.TargetPath='wscript.exe'; $lnk.Arguments='""%INSTALL_DIR%\start_hidden.vbs""'; $lnk.WorkingDirectory='%INSTALL_DIR%'; $lnk.IconLocation='%INSTALL_DIR%\logo.ico'; $lnk.Save()"

echo.
echo ===================================================
echo   [OK] Installation complete!
echo ===================================================
echo.
echo   Desktop shortcut "Originsun Agent" created.
echo   The agent will auto-start on boot.
echo   Launching now...
echo ===================================================

start "" /D "%INSTALL_DIR%" wscript.exe "%INSTALL_DIR%\start_hidden.vbs"
ping 127.0.0.1 -n 6 > nul
echo.
echo   Agent is starting on http://localhost:8000
echo   You can close this window now.
echo.
pause
