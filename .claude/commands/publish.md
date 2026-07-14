一鍵發布新版本：bump 版號 → preflight → pytest gate → 打包 OTA ZIP → 驗證大小 → 同步 NAS website-api →（**dev 機必加**：deploy_to_prod 把碼推進生產主控 `C:\OriginsunAgent`）→ 金絲雀驗證 → 推送全部生產代理 → 驗證更新結果。封裝 `publish_update.py` + `deploy_to_prod` 並接手機隊 OTA 推送。

> 🔴 **dev 機發版的碼來源坑（此 skill 的核心）**：機隊 9 台更新時是去主控 8000 的 `/download_update` 抓 ZIP，而那個 ZIP 是**用 `C:\OriginsunAgent` 的碼即時重建**（[api_ota.py](../../routers/api_ota.py) `_PROD_DIR` / `download_update` 的 `base_dir`）。但 `publish_update.py` **從不寫 `C:\OriginsunAgent`**（只 bump `E:\Dev` version.json + 同步 NAS 官網）。所以在 dev 機上，光跑 `publish_update.py` 就推機隊 = 9 台抓到舊碼、只掛新版號（靜默壞掉）。**dev 機必須用 `deploy_to_prod`（Step 2.5）把碼推進 `C:\OriginsunAgent` 後，機隊才會拿到新碼。** 乾淨生產環境沒這坑（跑的就是本地碼）。

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

### Step 2：跑 publish_update.py（驗證 + 打包 + NAS 官網同步）

```powershell
E:\Dev\Originsun-Media-Guard\.venv\Scripts\python.exe publish_update.py --version <版號> --notes "<更新日誌>"
```
（乾淨環境請改成該環境的 venv python 與 repo 路徑。）

此腳本會自動完成：bump `E:\Dev` version.json → 掃 import 補 `requirements_agent.txt` → 寫 `update_manifest.json` → **preflight**（壞 import 直接 abort + 回滾）→ **pytest tests/unit gate**（測試紅 → abort + 回滾）→ 打包 ZIP → **驗證 OTA < 50MB**（過大 abort + 回滾）→ POST `127.0.0.1:8000/api/v1/internal/restart` 重啟主控端 → scp code 到 NAS website-api + `docker restart website-api` → 同步 nginx 硬 301 redirects。

**逐行讀輸出**：任何 `[ERROR]` / `Preflight 失敗` / `pytest 失敗` / `OTA ZIP 過大` → 發布已被腳本回滾，**停止**並把錯誤原文回報，不要往下走。

> 🔴 **dev 機關鍵**：這步**只**驗證 + bump `E:\Dev` version.json + 同步 NAS 官網 —— 它**不**把碼部署到生產主控 `C:\OriginsunAgent`（機隊 OTA 的碼來源）。它重啟 8000，但 8000 跑的是 `C:\OriginsunAgent` 舊碼 → 重啟後仍是舊碼舊版。所以 dev 機**必須**接著跑 Step 2.5，否則機隊拿到舊碼。乾淨生產環境（跑的就是本地碼）→ **跳過 Step 2.5**，直接 Step 3。

### Step 2.5：deploy_to_prod（**僅 dev 機**；跳過＝機隊拿舊碼靜默壞掉）

在 **dev 8001** 上呼叫 —— 它把 `E:\Dev` 碼複製進 `C:\OriginsunAgent`、bump 兩邊 version.json、**自動備份舊碼到 `_deploy_backup`**、重啟 8000、smoke check（版本+健康，最長約 2 分鐘），**失敗自動回滾舊碼**：

```powershell
$login = Invoke-RestMethod "http://127.0.0.1:8001/api/v1/auth/login" -Method Post -ContentType "application/json" -Body '{"username":"admin","password":"admin"}'
$tok = $login.token
$body = @{ version = "<版號>"; notes = "<更新日誌>" } | ConvertTo-Json
$dep = Invoke-RestMethod "http://127.0.0.1:8001/api/v1/deploy_to_prod" -Method Post -Headers @{Authorization="Bearer $tok"} -ContentType "application/json" -Body $body
$job = $dep.job_id
```

輪詢直到結束（smoke 最長約 2 分鐘）：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/v1/publish/status?job_id=$job" -Headers @{Authorization="Bearer $tok"}
```

- `status:done` → 生產主控已跑新版 + smoke 通過，繼續 Step 3。
- `status:error` → 讀 `message`/`log`。deploy_to_prod 有內建 smoke + **自動回滾**（重啟後不健康/版本不符會自動還原舊碼），生產通常已回舊版；**停止**回報，不推機隊。手動還原：`POST http://127.0.0.1:8001/api/v1/deploy_to_prod/rollback`。

> ⚠️ 這步會重啟同事正在用的生產主控 8000，並對**生產 DB** 跑 startup migration（create_all / `ALTER … IF NOT EXISTS` / 種子）。純新增/冪等，但確實動生產 —— dev 機發版務必先確認可短暫中斷。有內建備份+回滾，比手動 robocopy 安全，**不需**另外手動備份。

### Step 3：驗證生產主控金絲雀（8000）

```powershell
try { (Invoke-WebRequest "http://127.0.0.1:8000/api/v1/version" -TimeoutSec 3 -UseBasicParsing).Content } catch { "DOWN" }
```

確認：(a) version == 新版號；(b) 本次新功能的關鍵端點存在（查 `http://127.0.0.1:8000/openapi.json` 有無新路由）；(c) 啟動無錯。**金絲雀不乾淨 → 停止，別推機隊**（先 `deploy_to_prod/rollback`）。乾淨生產環境只需確認 version 已是新版號。

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

一張表回報：版號 `current → new`、OTA 檔數/大小、NAS website-api 同步成功與否、**deploy_to_prod 金絲雀（8000）結果**（dev 機才有）、主控端版本驗證、**每台機隊**的更新結果（done / failed / skipped-busy）。失敗或被跳過的明確列出，**不要粉飾**。

## 完成後

- 提醒：若有機因 busy 被跳過，需稍後補推。
- 不要自動 commit `version.json` / `update_manifest.json` / `requirements_agent.txt` 的變更，除非使用者要求；先把 `git status` 顯示給使用者。
