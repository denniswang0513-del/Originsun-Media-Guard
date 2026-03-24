$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '"D:\Antigravity\OriginsunTranscode\start_hidden.vbs"'
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
Register-ScheduledTask -TaskName 'OriginsunMediaGuard' -Action $action -Trigger $trigger -Principal $principal -Force
Write-Host "Task 'OriginsunMediaGuard' registered successfully."
