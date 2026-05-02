' Originsun Agent launcher — delegates to core/process_spawn.py.
'
' Replaces the v1.10.x chain (taskkill /F /IM python.exe → sleep → cmd /c
' update_agent.py → log-rotate → cmd /c "...uvicorn... > log") which had:
'   - Trailing-backslash + quote-escape collisions in cmd /c parsing
'   - Console takedown CTRL_CLOSE_EVENT killing uvicorn 2-7 min after launch
'   - taskkill /IM python.exe nuking unrelated Python apps on the machine
'
' New flow: vbs spawns process_spawn.py once (Window=0, Wait=False). That
' helper kill-port-only-on-8000, rotates logs, optionally OTA-updates, then
' Popen's uvicorn with DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP. uvicorn
' is fully detached from any console — survives parent vbs/wscript exit.
'
' Used by:
'   - OriginsunBoot scheduled task (/sc onlogon trigger on user logon)
'   - bootstrap.ps1 final step (post-OTA restart)
'   - Desktop shortcut (manual launch)
' All three callers benefit from the same clean spawn semantics.

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim sDir
sDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
If Right(sDir, 1) = "\" Then sDir = Left(sDir, Len(sDir) - 1)

Dim pyExe
If fso.FileExists(sDir & "\.venv\Scripts\python.exe") Then
    pyExe = sDir & "\.venv\Scripts\python.exe"
ElseIf fso.FileExists(sDir & "\python_embed\python.exe") Then
    pyExe = sDir & "\python_embed\python.exe"
Else
    pyExe = "python"
End If

Dim spawnPy
spawnPy = sDir & "\core\process_spawn.py"

' Direct CreateProcess via WshShell.Run — no cmd, no console handle inheritance.
' Window=0 hidden, Wait=False fire-and-forget (helper exits in ~3s itself).
' --wait 0 because there's no parent endpoint to outlive on cold start.
sh.Run Chr(34) & pyExe & Chr(34) & " " & Chr(34) & spawnPy & Chr(34) & " --restart --ota --wait 0", 0, False

Set fso = Nothing
Set sh = Nothing
