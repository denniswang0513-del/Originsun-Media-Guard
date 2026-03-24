Set WshShell = CreateObject("WScript.Shell")
Dim sDir
sDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
' Kill any existing process on port 8000 before starting
WshShell.Run "cmd /c taskkill /F /IM python.exe 2>nul & taskkill /F /IM uvicorn.exe 2>nul", 0, True
WScript.Sleep 2000
WshShell.Run Chr(34) & sDir & "update_agent.bat" & Chr(34) & " hidden", 0, False
Set WshShell = Nothing
