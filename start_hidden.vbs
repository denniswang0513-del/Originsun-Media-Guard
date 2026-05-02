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

' Rotate previous logs to .bak before starting uvicorn — gives us a one-cycle
' history so when uvicorn dies we can read the previous run's traceback even
' after the supervisor restarts it.
' On Error Resume Next：log 被 zombie process lock 時 MoveFile 會 raise；
' 即使 rotate 失敗也不能擋 uvicorn 啟動（log 可被覆寫，啟動才是關鍵）。
Dim fso, outLog, errLog
Set fso = CreateObject("Scripting.FileSystemObject")
outLog = sDir & "uvicorn_out.log"
errLog = sDir & "uvicorn_err.log"
On Error Resume Next
fso.DeleteFile outLog & ".bak", True
fso.MoveFile outLog, outLog & ".bak"
fso.DeleteFile errLog & ".bak", True
fso.MoveFile errLog, errLog & ".bak"
On Error GoTo 0

' Start uvicorn in background (hidden, no window) — stdout/stderr 各自落檔，
' 之後若 process 自死可從 uvicorn_err.log[.bak] 抓 traceback。
WshShell.Run "cmd /c cd /d " & Chr(34) & sDir & Chr(34) & " && " & pyExe & " -m uvicorn main:io_app --host 0.0.0.0 --port 8000 > " & Chr(34) & outLog & Chr(34) & " 2> " & Chr(34) & errLog & Chr(34), 0, False

Set fso = Nothing
Set WshShell = Nothing
