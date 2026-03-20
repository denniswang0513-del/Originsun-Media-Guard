$startupFolder = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupFolder 'OriginsunMediaGuard.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($shortcutPath)
$sc.TargetPath = 'D:\Antigravity\OriginsunTranscode\start_hidden.vbs'
$sc.WorkingDirectory = 'D:\Antigravity\OriginsunTranscode'
$sc.Description = 'Originsun Media Guard Pro - Auto Start'
$sc.Save()
Write-Host "Startup shortcut created at: $shortcutPath"
