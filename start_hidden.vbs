Set WshShell = CreateObject("WScript.Shell")
Dim sDir
sDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run Chr(34) & sDir & "update_agent.bat" & Chr(34) & " hidden", 0, False
Set WshShell = Nothing

