一鍵發布新版本：bump 版號 → preflight → 打包 OTA ZIP → 驗證大小 → 重啟主控端 → 同步 NAS website-api → 推送全部生產代理 → 驗證更新結果。封裝 `publish_update.py` 的完整發布流程並接手機隊 OTA 推送。

## 🔒 安全鐵則：本機（dev checkout）硬阻擋（違反即停止）

這台機器同時是 **dev checkout（`e:\Dev\Originsun-Media-Guard`）** 與 **生產主 agent（`C:\OriginsunAgent`，port 8000）** 的共存機。在 dev checkout 跑 `/publish` 會：

- bump dev 的 `version.json` + 打包 dev 程式碼
- 重啟 `127.0.0.1:8000`（**就是生產主 agent**）
- 把 dev 程式碼 **OTA 推送到真實生產機隊**（dev 的 `agents` 清單 = 192.168.1.120 / .107 等真機）
- scp 到 NAS website-api（影響對外官網）

這正是 CLAUDE.md §2.1 與 memory「本機絕不 /publish」要防的事。

### 啟動時第一步：環境偵測（強制）

```powershell
$pubRoot = (Get-Location).Path
"ROOT=$pubRoot"
```

判定規則：
- 若路徑落在 **`E:\Dev`** 之下（dev checkout）→ **這是 dev 機，預設拒絕**。
  - 除非 `$ARGUMENTS` 含通關語 **`CONFIRM-PROD-PUBLISH`**，否則**立即停止**，回報：
    > 🛑 偵測到 dev checkout（`e:\Dev`）。在此發版會把 dev 程式碼 OTA 推到真實生產機隊並重啟生產 agent。
    > 若你**確實**要從這台機器發版，請改打：`/publish CONFIRM-PROD-PUBLISH <版號> "<更新日誌>"`。
    > 正常情況請到乾淨生產環境（`C:\OriginsunAgent`）發版。
  - 含通關語 → 印一行確認「⚠️ 已帶通關語，將從 dev 機發版到生產機隊」，再繼續，並把通關語從版號/notes 參數解析中剔除。
- 若路徑**不**在 `E:\Dev` 下（乾淨生產環境）→ 不需通關語，直接照流程跑。

任何一步無法確定環境，**停止並回報**，不要猜。

## 參數 `$ARGUMENTS`

格式（順序彈性，通關語可有可無）：
```
/publish [CONFIRM-PROD-PUBLISH] [版號] ["更新日誌"]
```
- **通關語** `CONFIRM-PROD-PUBLISH`：僅 dev 機需要（見上）。
- **版號**：`X.Y.Z` 純數字。省略 → 互動詢問。
- **更新日誌**：引號包住的字串。省略 → 互動詢問。

範例：
- `/publish` → 互動模式（乾淨環境）
- `/publish 1.10.137 "修正備份衝突對話框"` → 直接帶版號發版
- `/publish CONFIRM-PROD-PUBLISH 1.10.137 "..."` → dev 機強制發版

## Workflow

### Step 0：環境偵測 + 通關語檢查（見上「安全鐵則」，未通過即停止）

### Step 1：決定版號與更新日誌

1. 讀 `version.json` 取得 `current_version`。
2. **版號**：
   - `$ARGUMENTS` 有帶版號 → 用它（先驗證 `^\d+\.\d+\.\d+$` 且大於 current）。
   - 沒帶 → 預設 **patch+1**（如 `1.10.136` → `1.10.137`），用 AskUserQuestion 或直接問使用者確認/改寫，Enter 沿用建議值。
3. **更新日誌**：
   - `$ARGUMENTS` 有帶 → 用它。
   - 沒帶 → 問使用者一句話描述本次變更（可用 `git log --oneline <current_tag>..HEAD` 草擬建議）。
4. **禁止**自己手改 `version.json` —— 一律交給 `publish_update.py`（它會做依賴掃描、preflight、OTA 驗證、自動重啟、回滾）。

### Step 2：跑 publish_update.py（核心發布）

```powershell
E:\Dev\Originsun-Media-Guard\.venv\Scripts\python.exe publish_update.py --version <版號> --notes "<更新日誌>"
```
（乾淨環境請改成該環境的 venv python 與 repo 路徑。）

此腳本會自動完成：bump version.json → 掃 import 補 `requirements_agent.txt` → 寫 `update_manifest.json` → **preflight**（壞 import 直接 abort + 回滾）→ 打包 ZIP → **驗證 OTA < 50MB**（過大 abort + 回滾）→ POST `127.0.0.1:8000/api/v1/internal/restart` 重啟主控端 → scp code 到 NAS + `docker restart website-api` → 同步 nginx 硬 301 redirects。

**逐行讀輸出**：任何 `[ERROR]` / `Preflight 失敗` / `OTA ZIP 過大` → 發布已被腳本回滾，**停止**並把錯誤原文回報，不要往下推機隊。

### Step 3：等主控端回來

重啟後等主控端恢復（最多 ~30s，每 3s 探一次）：
```powershell
try { (Invoke-WebRequest "http://127.0.0.1:8000/api/v1/version" -TimeoutSec 3 -UseBasicParsing).Content } catch { "DOWN" }
```
確認回傳的 version 已是新版號才繼續。

### Step 4：推送全部生產代理（OTA + busy check）

機隊推送端點 `POST /api/v1/agents/{id}/update` 在主控端，需 **admin JWT**。

1. **取 admin token**：
   ```powershell
   $login = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/auth/login" -Method Post -ContentType "application/json" -Body '{"username":"admin","password":"admin"}'
   $tok = $login.token
   ```
   （若預設帳密不對，向使用者要 admin 帳密。）
2. **列出機隊**：`GET /api/v1/agents`（帶 `Authorization: Bearer $tok`）。
3. **逐台推送**（預設 **不加 force**，讓 busy check 生效）：
   ```powershell
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/agents/<id>/update" -Method Post -Headers @{Authorization="Bearer $tok"}
   ```
   - 回 `409 / status:busy` → **不要自動強推**。回報該機正在跑的 `active_jobs`（task_type / project_name）與 queue_length，**問使用者**是否可中斷；得到明確同意才加 `?force=true` 重推（見 CLAUDE.md 規則 0：drone_meta 有 manifest 機制可續跑，但串帶 reel 需重跑）。
   - 其他錯誤 → 記錄該機 fail，繼續推下一台（不要一台失敗就中斷整隊）。
4. **驗證更新結果**：每台推送後輪詢 `GET /api/v1/agents/{id}/update_status?since=<發起時間>`，直到 done / failed（前 10s 該機重啟中，之後才探得到）。

### Step 5：總結回報

一張表回報：版號 `current → new`、OTA 檔數/大小、主控端重啟結果、NAS website-api 同步成功與否、**每台機隊**的更新結果（done / failed / skipped-busy）。失敗或被跳過的明確列出，**不要粉飾**。

## 完成後

- 提醒：若有機因 busy 被跳過，需稍後補推。
- 不要自動 commit `version.json` / `update_manifest.json` / `requirements_agent.txt` 的變更，除非使用者要求；先把 `git status` 顯示給使用者。
