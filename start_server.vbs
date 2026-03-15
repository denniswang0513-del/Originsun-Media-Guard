' Originsun Media Guard – Hidden Server Launcher
' 雙擊此檔案可在背景無視窗啟動伺服器
' Double-click to start the server silently in the background.

Dim oShell, sDir, sPython, sArgs

Set oShell = CreateObject("WScript.Shell")

' 取得 .vbs 檔所在目錄作為工作目錄
sDir    = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sPython = sDir & "python_embed\python.exe"
sArgs   = "-m uvicorn main:io_app --host 0.0.0.0 --port 8000"

' Run(cmd, windowStyle, waitOnReturn)
'   windowStyle = 0  → 完全隱藏，不顯示任何視窗
'   waitOnReturn = False → 不等待程序結束，立即返回
oShell.Run Chr(34) & sPython & Chr(34) & " " & sArgs, 0, False

WScript.Quit
