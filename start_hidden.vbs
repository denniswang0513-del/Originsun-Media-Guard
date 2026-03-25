Set WshShell = CreateObject("WScript.Shell")
Dim sDir
sDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' Kill any existing process on port 8000 before starting
WshShell.Run "cmd /c taskkill /F /IM python.exe 2>nul & taskkill /F /IM uvicorn.exe 2>nul", 0, True
WScript.Sleep 2000

' Detect Python: .venv > python_embed > system python
Dim pyExe
If CreateObject("Scripting.FileSystemObject").FileExists(sDir & ".venv\Scripts\python.exe") Then
    pyExe = Chr(34) & sDir & ".venv\Scripts\python.exe" & Chr(34)
ElseIf CreateObject("Scripting.FileSystemObject").FileExists(sDir & "python_embed\python.exe") Then
    pyExe = Chr(34) & sDir & "python_embed\python.exe" & Chr(34)
Else
    pyExe = "python"
End If

' Run OTA updater first (if exists), then start server
Dim updaterPy
updaterPy = sDir & "update_agent.py"
If CreateObject("Scripting.FileSystemObject").FileExists(updaterPy) Then
    WshShell.Run "cmd /c cd /d " & Chr(34) & sDir & Chr(34) & " && " & pyExe & " " & Chr(34) & updaterPy & Chr(34), 0, True
End If

' Start uvicorn in background (hidden, no window)
WshShell.Run "cmd /c cd /d " & Chr(34) & sDir & Chr(34) & " && " & pyExe & " -m uvicorn main:io_app --host 0.0.0.0 --port 8000", 0, False

Set WshShell = Nothing
