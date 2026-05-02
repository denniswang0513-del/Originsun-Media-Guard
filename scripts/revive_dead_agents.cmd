@echo off
REM 一鍵救活遠端 agent — 雙擊或從 cmd 跑都可以
REM 會 prompt 你輸入 admin 帳密一次,然後對所有 ping 得到的 agent 觸發 OriginsunBoot

cd /d "%~dp0\.."
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0revive_dead_agents.ps1"
echo.
pause
