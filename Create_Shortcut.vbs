Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
chromePath1 = WshShell.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe"
chromePath2 = WshShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe"
chromePath3 = WshShell.ExpandEnvironmentStrings("%LocalAppData%") & "\Google\Chrome\Application\chrome.exe"
chromePath = ""
If fso.FileExists(chromePath1) Then
    chromePath = chromePath1
ElseIf fso.FileExists(chromePath2) Then
    chromePath = chromePath2
ElseIf fso.FileExists(chromePath3) Then
    chromePath = chromePath3
End If
serverUrl = "http://localhost:8000"
icoPath = "D:\Antigravity\OriginsunTranscode\logo.ico"
sLinkFile = WshShell.SpecialFolders("Desktop") & "\Originsun Media Guard Web.lnk"
Set oLink = WshShell.CreateShortcut(sLinkFile)
If chromePath <> "" Then
    oLink.TargetPath = chromePath
    oLink.Arguments = "--app=" & serverUrl & " --start-fullscreen"
    If fso.FileExists(icoPath) Then
        oLink.IconLocation = icoPath
    Else
        oLink.IconLocation = chromePath & ", 0"
    End If
Else
    oLink.TargetPath = serverUrl
    If fso.FileExists(icoPath) Then
        oLink.IconLocation = icoPath
    End If
End If
oLink.Description = "Originsun Media Guard Pro Web Edition"
oLink.WindowStyle = 1
oLink.Save
MsgBox "桌面捷徑已成功建立！請查看桌面上的「Originsun Media Guard Web」。", vbInformation, "安裝成功"