#requires -Version 5.1
<#
.SYNOPSIS
  Originsun — 一鍵救活遠端 agent

.DESCRIPTION
  在 master 機器上跑,自動:
    1. 從 master /api/v1/agents 撈當前 agent 列表
    2. ping 每一台,分類「remote 可救」vs「機器整台關了 (要去現場)」
    3. 對 pingable 的,用 schtasks /S 遠端觸發 OriginsunBoot 任務
    4. 等 30 秒讓新 uvicorn 啟動,重新從 master 撈各台健康狀態
    5. 印出結果表格 + 仍需現場處理的清單

  所有 agent 機器需要:
    - 同一組 admin 帳密(輸入一次給全部用)
    - SMB / RPC 對 master 開放(workgroup 預設 OK)
    - OriginsunBoot 排程任務存在(裝 agent 時就有)

.PARAMETER MasterUrl
  master 端點,預設 http://127.0.0.1:8000

.PARAMETER Credential
  遠端 admin 帳密。沒給的話會 prompt 一次。

.EXAMPLE
  .\scripts\revive_dead_agents.ps1
  # 互動模式,會 prompt 帳密

.EXAMPLE
  $cred = Get-Credential
  .\scripts\revive_dead_agents.ps1 -Credential $cred
  # 預先準備 credential 物件
#>

[CmdletBinding()]
param(
    [string]$MasterUrl = "http://127.0.0.1:8000",
    [PSCredential]$Credential
)

$ErrorActionPreference = 'Continue'

function Test-PingFast {
    param([string]$IP)
    # PS 5.1 沒 -TimeoutSeconds,改用 cmd ping -w
    $r = & cmd /c "ping -n 1 -w 1000 $IP 2>&1"
    return ($r -match "TTL=")
}

function Invoke-RemoteTask {
    param([string]$IP, [string]$User, [string]$Password, [string]$TaskName)
    # schtasks /S 接 \\IP,/U \U,/P "<密碼>"
    $args = @("/S", "\\$IP", "/U", $User, "/P", $Password, "/Run", "/TN", $TaskName)
    $out = & schtasks $args 2>&1
    return @{ ExitCode = $LASTEXITCODE; Output = ($out -join "`n") }
}

# ── 1. 取 agent 列表 ──
Write-Host ""
Write-Host "[1/5] 從 master 取 agent 列表..." -ForegroundColor Cyan
try {
    $agents = (Invoke-RestMethod -Uri "$MasterUrl/api/v1/agents" -TimeoutSec 5).agents
} catch {
    Write-Host "  master 連不到 ($MasterUrl): $_" -ForegroundColor Red
    exit 1
}
Write-Host "  Found $($agents.Count) agents"

# ── 2. Ping check + agent 健康狀態 ──
Write-Host ""
Write-Host "[2/5] Ping check + agent 狀態..." -ForegroundColor Cyan
$results = foreach ($a in $agents) {
    $ip = ($a.url -replace 'https?://([^:/]+).*', '$1')
    $pingable = Test-PingFast -IP $ip
    $alive = $false
    $version = $null
    if ($pingable) {
        try {
            $v = Invoke-RestMethod -Uri "$($a.url)/api/v1/version" -TimeoutSec 2
            $alive = $true
            $version = $v.version
        } catch {}
    }
    [PSCustomObject]@{
        Id           = $a.id
        Name         = $a.name
        IP           = $ip
        Url          = $a.url
        Pingable     = $pingable
        AlreadyAlive = $alive
        VersionBefore = $version
        Triggered    = $false
        Message      = ""
        VersionAfter = $null
        Reborn       = $false
    }
}

$results | ForEach-Object {
    $tag = if ($_.AlreadyAlive) { "ALIVE     " }
           elseif ($_.Pingable) { "PROCESS死 " }
           else                 { "機器關機  " }
    $ver = if ($_.VersionBefore) { "v$($_.VersionBefore)" } else { "-" }
    Write-Host ("  {0,-10} {1,-22} {2,-15} {3}" -f $tag, $_.Name, $_.IP, $ver)
}

$needRevive = $results | Where-Object { $_.Pingable -and -not $_.AlreadyAlive }
$cantReach  = $results | Where-Object { -not $_.Pingable }

if ($needRevive.Count -eq 0) {
    Write-Host ""
    Write-Host "[OK] 沒有需要遠端救援的 agent!" -ForegroundColor Green
    if ($cantReach.Count -gt 0) {
        Write-Host ""
        Write-Host "⚠️  以下 $($cantReach.Count) 台仍需到現場 (機器關機/睡眠/沒網):" -ForegroundColor Yellow
        $cantReach | ForEach-Object { Write-Host "  - $($_.Name) ($($_.IP))" }
    }
    exit 0
}

# ── 3. 取 admin credential ──
Write-Host ""
Write-Host "[3/5] 需要 admin 帳密來 remote schtasks ($($needRevive.Count) 台)" -ForegroundColor Cyan
if (-not $Credential) {
    $Credential = Get-Credential -Message "輸入所有 agent 機器共用的 admin 帳密"
}
$user = $Credential.GetNetworkCredential().UserName
$pass = $Credential.GetNetworkCredential().Password

# ── 4. 遠端觸發 OriginsunBoot ──
Write-Host ""
Write-Host "[4/5] 遠端觸發 OriginsunBoot..." -ForegroundColor Cyan
foreach ($r in $needRevive) {
    Write-Host -NoNewline "  $($r.Name) ($($r.IP))... "
    # 先試 OriginsunBoot,失敗試 OriginsunAgent (舊版任務名)
    $rs = Invoke-RemoteTask -IP $r.IP -User $user -Password $pass -TaskName "OriginsunBoot"
    if ($rs.ExitCode -ne 0) {
        $rs = Invoke-RemoteTask -IP $r.IP -User $user -Password $pass -TaskName "OriginsunAgent"
    }
    if ($rs.ExitCode -eq 0) {
        $r.Triggered = $true
        $r.Message = "schtasks /run sent OK"
        Write-Host "OK" -ForegroundColor Green
    } else {
        $r.Message = "schtasks failed: " + ($rs.Output -split "`n" | Select-Object -First 1)
        Write-Host "FAILED" -ForegroundColor Yellow
        Write-Host "    -> $($r.Message)" -ForegroundColor DarkYellow
    }
}

# ── 5. 等 30 秒 + 重驗 ──
Write-Host ""
Write-Host "[5/5] 等 30 秒讓 agents 啟動,然後重新檢查..." -ForegroundColor Cyan
for ($i = 30; $i -gt 0; $i -= 5) {
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 5
}
Write-Host ""

foreach ($r in $needRevive | Where-Object Triggered) {
    try {
        $v = Invoke-RestMethod -Uri "$($r.Url)/api/v1/version" -TimeoutSec 3
        $r.VersionAfter = $v.version
        $r.Reborn = $true
    } catch {
        $r.VersionAfter = "still offline"
    }
}

# ── 報告 ──
Write-Host ""
Write-Host "========== 救援結果 ==========" -ForegroundColor Cyan
$results | Format-Table @{N='Name'; E='Name'; W=22},
                       @{N='IP';   E='IP';   W=15},
                       @{N='Before'; E={ if ($_.AlreadyAlive) { "v$($_.VersionBefore)" } elseif ($_.Pingable) { "process死" } else { "機器關" } }; W=12},
                       @{N='After';  E={ if ($_.Reborn) { "v$($_.VersionAfter)" } elseif ($_.AlreadyAlive) { "v$($_.VersionBefore)" } elseif (-not $_.Pingable) { "-" } else { "still down" } }; W=12},
                       @{N='Status'; E={
                           if ($_.AlreadyAlive)             { "已是健康" }
                           elseif ($_.Reborn)               { "✓ 救活了" }
                           elseif (-not $_.Pingable)        { "✗ 機器關" }
                           elseif ($_.Triggered)            { "✗ 觸發了但沒起" }
                           else                             { "✗ 觸發失敗" }
                       }; W=15} -AutoSize

$totalRevived = ($results | Where-Object Reborn).Count
$totalAlready = ($results | Where-Object AlreadyAlive).Count
$totalDown = $results.Count - $totalRevived - $totalAlready

Write-Host "已健康: $totalAlready / 救活: $totalRevived / 仍 DOWN: $totalDown" -ForegroundColor $(if ($totalDown -eq 0) {'Green'} else {'Yellow'})

if ($totalDown -gt 0) {
    Write-Host ""
    Write-Host "⚠️  以下仍需處理:" -ForegroundColor Yellow
    $results | Where-Object { -not $_.AlreadyAlive -and -not $_.Reborn } | ForEach-Object {
        $hint = if (-not $_.Pingable) {
            "機器整台關了/睡了/沒網 → 到現場按電源 / Wake-on-LAN"
        } elseif ($_.Triggered) {
            "schtasks 觸發 OK 但 agent 沒起 → RDP 進去手動雙擊 start_hidden.vbs"
        } else {
            "schtasks /S 失敗 (帳密或 SMB 問題) → 個別 RDP 處理"
        }
        Write-Host "  - $($_.Name) ($($_.IP)): $hint"
    }
}
Write-Host ""
