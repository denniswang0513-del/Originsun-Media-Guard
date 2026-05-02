@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  Originsun - 把 master 從 Session 0 拉回 Session 1
REM ============================================================
REM  雙擊執行 (從你 Windows 桌面 / 檔案總管雙擊),會在你的 Session 1
REM  context 跑,因此重啟出來的 master 也會在 Session 1。
REM
REM  什麼時候用:
REM  - 點資料夾按鈕沒彈窗 (master 卡 Session 0)
REM  - master 跑在 Services session (taskmgr 顯示 Session ID 0)
REM  - tkinter picker 視窗看不到
REM
REM  運作方式:
REM  1. 殺 port 8000 上的 master (任何 session)
REM  2. 等 2 秒讓 port 釋放
REM  3. 用 wscript 起 start_hidden.vbs - 此 cmd 是 Session 1,
REM     wscript 繼承 = Session 1, 之後 master 也是 Session 1
REM ============================================================

echo.
echo ===========================================
echo   Migrate master to Session 1
echo ===========================================
echo.

set INSTALL_DIR=
if exist "D:\Antigravity\OriginsunTranscode\start_hidden.vbs" set INSTALL_DIR=D:\Antigravity\OriginsunTranscode
if exist "C:\OriginsunAgent\start_hidden.vbs" set INSTALL_DIR=C:\OriginsunAgent
if exist "%~dp0..\start_hidden.vbs" set INSTALL_DIR=%~dp0..

if "%INSTALL_DIR%"=="" (
    echo [ERROR] 找不到 Originsun 安裝目錄 ^(找了 D:\Antigravity\OriginsunTranscode / C:\OriginsunAgent / 上層目錄^)
    pause
    exit /b 1
)

echo [1/3] Master 安裝目錄: %INSTALL_DIR%

echo [2/3] 殺 port 8000 上的舊 master...
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo     killing PID %%P
    taskkill /F /PID %%P >nul 2>nul
)
timeout /t 2 /nobreak >nul

echo [3/3] 用 wscript 起 start_hidden.vbs ^(繼承本 cmd 的 Session^)...
wscript "%INSTALL_DIR%\start_hidden.vbs"

echo.
echo [OK] 已觸發新 master 啟動 ^(會花 5-10 秒^)。
echo.
echo 5 秒後驗證 master session...
timeout /t 8 /nobreak >nul

for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo.
    echo 新 master PID: %%P
    powershell -NoProfile -Command "$p = Get-Process -Id %%P; Write-Host ('Session: ' + $p.SessionId + (& {if ($p.SessionId -eq 1) {' (OK - 桌面 session, picker 會工作)'} else {' (still wrong session)'}}))"
)

echo.
echo 完成. 按任意鍵關閉.
pause >nul
