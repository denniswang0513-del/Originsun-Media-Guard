@echo on
title Originsun Installer DEBUG
set "LOG=%TEMP%\originsun_install_debug.log"
echo. > "%LOG%"
echo === Install DEBUG started at %date% %time% === >> "%LOG%"
echo SELF=%~f0 >> "%LOG%"
echo CWD=%CD% >> "%LOG%"
echo USER=%USERNAME% >> "%LOG%"
echo TEMP=%TEMP% >> "%LOG%"

echo.
echo ============================================================
echo   Originsun Installer DEBUG
echo   Log file: %LOG%
echo ============================================================
echo.
echo [DEBUG] Path of this bat: %~f0
echo [DEBUG] Current directory: %CD%
echo [DEBUG] User: %USERNAME%
echo.

set "SELF=%~f0"
set "FIRST2=%SELF:~0,2%"
echo [DEBUG] First 2 chars of path: "%FIRST2%"
echo FIRST2=%FIRST2% >> "%LOG%"

if "%FIRST2%"=="\\" (
    echo [DEBUG] Detected UNC path. Would normally copy to TEMP.
    echo [DEBUG] Copying to %TEMP%\Originsun_DEBUG_copy.bat ...
    copy /Y "%SELF%" "%TEMP%\Originsun_DEBUG_copy.bat" >> "%LOG%" 2>&1
    echo [DEBUG] Copy exit code: %errorlevel%
    echo Copy errorlevel=%errorlevel% >> "%LOG%"
    if exist "%TEMP%\Originsun_DEBUG_copy.bat" (
        echo [DEBUG] Local copy exists.
    ) else (
        echo [DEBUG] Local copy FAILED to create!
    )
) else (
    echo [DEBUG] Path is local, not UNC.
)

echo.
echo [DEBUG] Testing admin check...
net session >nul 2>&1
echo [DEBUG] net session errorlevel: %errorlevel%
echo net session errorlevel=%errorlevel% >> "%LOG%"
if %errorlevel% equ 0 (
    echo [DEBUG] Running as Administrator.
) else (
    echo [DEBUG] NOT running as administrator.
)

echo.
echo [DEBUG] Testing server connectivity...
powershell -Command "try { (Invoke-WebRequest -Uri 'http://192.168.1.107:8000/api/v1/version' -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host ('ERROR: ' + $_.Exception.Message) }"
echo.

echo [DEBUG] Existing C:\OriginsunAgent?
if exist "C:\OriginsunAgent\main.py" (
    echo   YES, main.py found.
) else (
    echo   NO or incomplete.
)

echo.
echo === DEBUG END ===
echo.
echo Log saved to: %LOG%
echo.
type "%LOG%"
echo.
echo Press any key to close...
pause
