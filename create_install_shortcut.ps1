# 在 NAS 建立 Install_or_Update 的 .lnk 捷徑。
# 員工雙點擊 .lnk 時等同 cmd /c <bat>，Attachment Manager 不會攔截。
# 使用方式（在 NAS 有寫入權限的電腦上執行一次即可）：
#   powershell -ExecutionPolicy Bypass -File create_install_shortcut.ps1

$nasFolder = "\\192.168.1.132\Container\AI_Workspace\Originsun_Web\Agents"
$batPath = Join-Path $nasFolder "Install_or_Update.bat"
$lnkPath = Join-Path $nasFolder "安裝_點我.lnk"

if (-not (Test-Path $batPath)) {
    Write-Host "[ERROR] bat not found: $batPath" -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = "$env:SystemRoot\System32\cmd.exe"
$lnk.Arguments = "/c `"$batPath`""
$lnk.WorkingDirectory = "$env:TEMP"
$lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,176"
$lnk.Description = "Originsun Agent 安裝/更新"
$lnk.Save()

Write-Host "[OK] 捷徑建立完成: $lnkPath" -ForegroundColor Green
Write-Host "     員工改雙點擊這個 .lnk 即可（不會被 Attachment Manager 擋）。"
