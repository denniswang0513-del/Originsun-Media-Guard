@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  Originsun - 一鍵救活遠端 agent (純 CMD, 無需 PowerShell)
REM ============================================================
REM  雙擊執行,或從 cmd 跑都可以。
REM  會 prompt 一次 admin 帳密,然後對所有 ping 得到的 agent
REM  遠端觸發 OriginsunBoot 排程任務。
REM
REM  Agent 列表硬編碼在下面,加減 agent 時改這裡 (跟 master
REM  agents.json 同步)。
REM ============================================================

echo.
echo ===========================================
echo   Originsun - revive dead agents
echo ===========================================
echo.

set REVIVE_COUNT=0
set OFFLINE_COUNT=0
set ALIVE_COUNT=0

echo [1/4] Ping check...
echo.

REM --- Agent 列表 ---
call :check "錄音室白(master)"     192.168.1.107   agent_2
call :check "SOCA電腦"             192.168.1.89   soca
call :check "備檔電腦"             192.168.1.120  agent_5
call :check "公用電腦(剪輯側)"     192.168.1.8    agent_8
call :check "公用電腦(器材側)"     192.168.1.5    agent_4
call :check "剪輯室"               192.168.1.75   agent_3
call :check "婕妤電腦"             192.168.1.109  agent_7
call :check "念栩電腦"             192.168.1.145  agent_9
call :check "禮瑜電腦"             192.168.1.121  agent_6
call :check "配音室黑"             192.168.1.107  agent

echo.
echo   ALIVE: !ALIVE_COUNT! / 可遠端救: !REVIVE_COUNT! / 機器關: !OFFLINE_COUNT!

if !REVIVE_COUNT! equ 0 (
    echo.
    echo [OK] 沒有需要遠端救援的 agent
    goto :show_offline
)

echo.
echo [2/4] 需要 admin 帳密 ^(用於遠端 schtasks^)
echo.
set /p ADMIN_USER=    Username:
echo.
echo     注意: 密碼會顯示在螢幕上,跑完按 Enter 會自動 cls 清掉
set /p ADMIN_PASS=    Password:

echo.
echo [3/4] 遠端觸發 OriginsunBoot...
echo.
call :trigger_all

echo.
echo [4/4] 等 30 秒讓 uvicorn 啟動...
timeout /t 30 /nobreak >nul

echo.
echo ========== 救援結果 ==========
echo.
call :verify_all

:show_offline
if !OFFLINE_COUNT! gtr 0 (
    echo.
    echo 機器關了 / 沒網,需到現場處理:
    for /L %%i in (1,1,!OFFLINE_COUNT!) do (
        echo   - !OFFLINE_%%i_NAME! ^(!OFFLINE_%%i_IP!^)
    )
)

echo.
echo 完成. 按 Enter 關閉視窗 ^(會 cls 清掉密碼^).
pause >nul
cls
exit /b 0


REM ====================== Subroutines ======================

:check
REM %1=name %2=ip %3=id
set NAME=%~1
set IP=%~2
set ID=%~3

ping -n 1 -w 1000 !IP! >nul 2>nul
if !errorlevel! neq 0 (
    echo   [機器關    ] !NAME! ^(!IP!^)
    set /a OFFLINE_COUNT+=1
    set OFFLINE_!OFFLINE_COUNT!_NAME=!NAME!
    set OFFLINE_!OFFLINE_COUNT!_IP=!IP!
    goto :eof
)

curl -s --max-time 2 -o nul http://!IP!:8000/api/v1/version 2>nul
if !errorlevel! equ 0 (
    echo   [ALIVE     ] !NAME! ^(!IP!^)
    set /a ALIVE_COUNT+=1
    goto :eof
)

echo   [PROCESS死 ] !NAME! ^(!IP!^) -- 可遠端救
set /a REVIVE_COUNT+=1
set REVIVE_!REVIVE_COUNT!_NAME=!NAME!
set REVIVE_!REVIVE_COUNT!_IP=!IP!
set REVIVE_!REVIVE_COUNT!_ID=!ID!
goto :eof


:trigger_all
for /L %%i in (1,1,!REVIVE_COUNT!) do (
    call :trigger_one %%i
)
goto :eof


:trigger_one
REM %1=index
set IDX=%~1
set N=!REVIVE_%IDX%_NAME!
set IP=!REVIVE_%IDX%_IP!
<nul set /p="    !N! (!IP!)... "
schtasks /S \\!IP! /U !ADMIN_USER! /P "!ADMIN_PASS!" /Run /TN OriginsunBoot >nul 2>nul
if !errorlevel! equ 0 (
    echo OK
    set REVIVE_%IDX%_OK=1
    goto :eof
)
schtasks /S \\!IP! /U !ADMIN_USER! /P "!ADMIN_PASS!" /Run /TN OriginsunAgent >nul 2>nul
if !errorlevel! equ 0 (
    echo OK ^(OriginsunAgent^)
    set REVIVE_%IDX%_OK=1
    goto :eof
)
echo FAILED ^(帳密錯 / SMB 不通 / 任務不存在^)
set REVIVE_%IDX%_OK=0
goto :eof


:verify_all
set REVIVED=0
set FAILED=0
for /L %%i in (1,1,!REVIVE_COUNT!) do (
    call :verify_one %%i
)
echo.
echo   救活: !REVIVED! / 失敗: !FAILED!
goto :eof


:verify_one
set IDX=%~1
set N=!REVIVE_%IDX%_NAME!
set IP=!REVIVE_%IDX%_IP!
if not "!REVIVE_%IDX%_OK!"=="1" (
    echo   [FAIL] !N! ^(!IP!^) - schtasks 觸發失敗
    set /a FAILED+=1
    goto :eof
)
curl -s --max-time 3 -o nul http://!IP!:8000/api/v1/version 2>nul
if !errorlevel! equ 0 (
    echo   [OK]   !N! 救活了
    set /a REVIVED+=1
) else (
    echo   [FAIL] !N! ^(!IP!^) - 觸發 OK 但 agent 沒起,RDP 進去看
    set /a FAILED+=1
)
goto :eof
