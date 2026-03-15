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
set "ZIP_FILE=%TEMP%\Originsun_Agent.zip"

echo [Info] Install target : %INSTALL_DIR%
echo [Info] Download source: %SERVER_URL%
echo.

:: Step 1: Create install directory
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if %errorlevel% neq 0 (
    echo [Error] Cannot create %INSTALL_DIR%. Try running as Administrator.
    pause
    exit /b 1
)
echo [Step 1/4] Install directory ready.

:: Step 2: Download the agent package from the main server
echo [Step 2/4] Downloading package (~300MB). Please wait...
powershell -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%SERVER_URL%/download_agent' -OutFile '%ZIP_FILE%'"
if %errorlevel% neq 0 (
    echo.
    echo [Error] Download failed. Please check:
    echo         1. You are connected to the company LAN
    echo         2. The main server is reachable at %SERVER_URL%
    pause
    exit /b 1
)
echo [Step 2/4] Download complete.

:: Step 3: Extract
echo [Step 3/4] Extracting... (this may take 1-2 minutes)
powershell -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%INSTALL_DIR%' -Force"
if %errorlevel% neq 0 (
    echo [Error] Extraction failed. Ensure at least 600MB free on C:\.
    echo         Try running as Administrator.
    pause
    exit /b 1
)
del /f /q "%ZIP_FILE%" >nul 2>&1
echo [Step 3/4] Extraction complete.

:: Step 3.5: Configure Firewall
echo [Step 3.5/4] Configuring Windows Firewall...
netsh advfirewall firewall show rule name="Originsun Agent Port 8000" >nul 2>&1
if %errorlevel% neq 0 (
    echo [System] Requesting Administrator privileges to add Firewall rule...
    powershell -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Originsun Agent Port 8000\" dir=in action=allow protocol=TCP localport=8000' -Verb RunAs -WindowStyle Hidden"
)
echo [Step 3.5/4] Firewall ready.

:: Step 4: Create Desktop shortcut
echo [Step 4/4] Creating desktop shortcut...
powershell -ExecutionPolicy Bypass -Command "$s=New-Object -ComObject WScript.Shell; $lnk=$s.CreateShortcut('%USERPROFILE%\Desktop\Originsun Agent.lnk'); $lnk.TargetPath='wscript.exe'; $lnk.Arguments='""%INSTALL_DIR%\start_hidden.vbs""'; $lnk.WorkingDirectory='%INSTALL_DIR%'; $lnk.IconLocation='shell32.dll,43'; $lnk.Save()"

:: Step 5: Add to Windows Startup
echo [Step 5/5] Adding to Windows Startup (auto-run on boot)...
powershell -ExecutionPolicy Bypass -Command "$s=New-Object -ComObject WScript.Shell; $startup=$s.SpecialFolders.Item('Startup'); $lnk=$s.CreateShortcut($startup + '\Originsun Agent.lnk'); $lnk.TargetPath='wscript.exe'; $lnk.Arguments='""%INSTALL_DIR%\start_hidden.vbs""'; $lnk.WorkingDirectory='%INSTALL_DIR%'; $lnk.IconLocation='shell32.dll,43'; $lnk.Save()"

echo.
echo ===================================================
echo   [OK] Installation complete!
echo ===================================================
echo.
echo A shortcut "Originsun Agent" has been placed on your Desktop.
echo The agent will now start automatically whenever you turn on your PC.
echo Launching now...
ping 127.0.0.1 -n 4 > nul
start "Originsun Agent" /D "%INSTALL_DIR%" wscript.exe "%INSTALL_DIR%\start_hidden.vbs"
