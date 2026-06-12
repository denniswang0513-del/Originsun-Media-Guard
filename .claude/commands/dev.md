啟動 / 停止 / 查詢本機**開發伺服器**（dev checkout，port **8001**）。讓使用者透過任何連到這台 dev 機的 Claude Code session（本機或遠端）一鍵拉起開發環境，不必手動打 uvicorn 指令。

## 🔒 安全鐵則（違反即停止）

本機同時跑著**生產主 agent（C:\OriginsunAgent，port 8000）**，這個 skill **只能操作 8001 的開發伺服器**：

- **永遠只用 port 8001**。絕不啟動 8000、絕不 `python main.py`（預設 8000）、絕不 `start_hidden.vbs`。
- **stop / restart 只能停 8001 的 PID**。動手前必須用 `Get-NetTCPConnection -LocalPort 8001` 取得 PID 並確認，**嚴禁**用 `taskkill /IM python.exe`（會誤殺生產 agent 與其他 Python）。
- 任何一步若無法確定操作對象是 8001，**立即停止並回報**，不要猜。

完整背景見 CLAUDE.md §2.1「開發/生產共存守則」。

## 參數 `$ARGUMENTS`

- （空）或 `start` → 啟動開發伺服器
- `stop` → 停止開發伺服器
- `status` → 查詢狀態（不改動）
- `restart` → 先 stop 再 start

## Workflow

### `status`（也是 start/stop 前的探測）

PowerShell 探測 8001 是否在線：
```powershell
try { $r = Invoke-WebRequest "http://localhost:8001/api/v1/health" -TimeoutSec 3 -UseBasicParsing; "UP $($r.StatusCode)" } catch { "DOWN" }
```
回報：在線/離線；在線時順帶 `GET /api/v1/version` 顯示版本。

### `start`

1. **先探測**（上面）。若已 UP → 回報「開發伺服器已在執行：http://localhost:8001」並結束（冪等，不重複啟動）。
2. **確認環境**：`Test-Path E:\Dev\Originsun-Media-Guard\.venv\Scripts\python.exe` 與 `dev_start.ps1` 存在；缺則回報 fail。
3. **背景分離啟動**（用 `Start-Process` 讓它脫離 Claude session 持續存活，不要用 run_in_background）：
   ```powershell
   Start-Process powershell -ArgumentList '-ExecutionPolicy','Bypass','-File','E:\Dev\Originsun-Media-Guard\dev_start.ps1' -WorkingDirectory 'E:\Dev\Originsun-Media-Guard' -WindowStyle Minimized
   ```
4. **輪詢健康**：最多 ~40 秒，每 2 秒探一次 `http://localhost:8001/api/v1/health`，UP 即停止輪詢。
5. **回報**：
   - 成功 → `✅ 開發伺服器已啟動：http://localhost:8001`（LAN：`http://<本機IP>:8001`）。本機 IP 用 `(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -like '192.168.*'}).IPAddress | Select-Object -First 1`。
   - 逾時未 UP → 回報失敗，提示去看那個最小化的 PowerShell 視窗錯誤訊息。

### `stop`

1. 取 8001 的 PID：
   ```powershell
   $c = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
   if ($c) { $c.OwningProcess | Select-Object -Unique } else { "NONE" }
   ```
2. 若 NONE → 回報「開發伺服器未在執行」。
3. 若有 PID → **再次確認該 PID 監聽的是 8001 而非 8000**，然後 `Stop-Process -Id <pid> -Force`。
4. 探測確認已 DOWN → 回報 `🛑 開發伺服器已停止`。

### `restart`

依序執行 `stop` → 等 2 秒 → `start`。

## 「遠端啟動」的能力邊界（如實告知）

- 此 skill 在**執行工具的那台機器**上啟動 dev。只要你的 Claude Code session 連到這台 dev 機（本機終端、VS Code 遠端、或 web 連線），執行 `/dev` 就等於遠端啟動。
- **無法**喚醒一台完全關機 / 沒有任何監聽行程的機器（cold boot）。若需要真正冷啟動，得另外靠生產 8000 agent 加一個「spawn dev」端點、或 Windows 排程/SSH——那是另一個方案，需明確要求再做。

## 完成後回報

一行結果（URL 或狀態）+ 目前 8001 / 8000 兩者各自在線與否，讓使用者確認沒有誤動生產。
