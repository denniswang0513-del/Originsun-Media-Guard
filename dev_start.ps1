# Originsun 開發伺服器 — port 8001（避開正式 Agent 的 8000）
Set-Location -Path $PSScriptRoot
Write-Host "啟動開發伺服器 → http://localhost:8001  (Ctrl+C 停止)" -ForegroundColor Cyan
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn main:io_app --host 0.0.0.0 --port 8001 --reload
