' Create desktop shortcut for Originsun Agent installer
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get desktop path
strDesktop = objShell.SpecialFolders("Desktop")
strShortcut = strDesktop & "\安裝 Originsun Agent.lnk"

' NAS path to installer
strTarget = "\\192.168.1.132\Container\AI_Workspace\agents\Originsun Media Guard Pro\Install_Originsun_Agent.bat"
strWorkDir = "\\192.168.1.132\Container\AI_Workspace\agents\Originsun Media Guard Pro"

' Create shortcut
Set objLink = objShell.CreateShortcut(strShortcut)
objLink.TargetPath = strTarget
objLink.WorkingDirectory = strWorkDir
objLink.Description = "Install Originsun Media Guard Pro Agent"
objLink.IconLocation = "shell32.dll,162"
objLink.Save

' Show completion message
MsgBox "✓ 快捷方式已建立於桌面" & vbCrLf & vbCrLf & _
        "檔案名: 安裝 Originsun Agent.lnk" & vbCrLf & _
        "目標: " & strTarget & vbCrLf & vbCrLf & _
        "點擊快捷方式即可開始安裝。", vbInformation, "建立成功"
