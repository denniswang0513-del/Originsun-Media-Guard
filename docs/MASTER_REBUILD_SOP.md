# Master 重建 SOP — 生產主控端（192.168.1.107）災難重建手冊

> **什麼時候用這份文件**：master 桌機硬碟掛掉 / 整台失竊 / Windows 壞到要重灌，
> 需要在一台新（或重灌後的）Windows 機器上把生產主控端完整重建起來。
> **目標**：照抄本文件，半天內恢復服務。
> **前提**：NAS（192.168.1.132）還活著。NAS 也掛掉的情境見 `BACKUP_RESTORE.md`
> 開頭的限制說明（目前無異地重建程序，屬 ROADMAP Phase E 待辦）。
>
> 最後校準：2026-07-07（v1.10.209），所有路徑/檔名皆實機盤點，非推測。

---

## 0. Master 的五重身分（重建時每一項都要接回）

| 身分 | 誰依賴它 | 對應驗證（§6） |
|------|---------|---------------|
| 生產 agent（port 8000） | 同事日常操作、所有 TAB | health / version |
| 機隊 OTA 來源（/download_update 即時打包 `C:\OriginsunAgent`） | 10 台 agent 更新 | agents 清單 + 推一台測試 |
| 官網 build 機（npm build → scp dist 到 NAS） | 對外網站更新 | 手動觸發 rebuild |
| claude runner（AI SEO / 英文翻譯 / 公布欄問答） | 排程 + NAS relay 過來的請求 | seo runner 測試 |
| 每日備份調度（03:00 → Google Drive） | 災難恢復能力本身 | 手動跑一次備份 |

---

## 1. 事前預防（⚠️ 趁 master 還活著現在就做，一次性）

每日備份 bundle（Google Drive）**只含**：DB dump、uploads、notion-media、
`settings.json.enc`、manifest。以下東西**不在 bundle 裡**，master 死了就永久消失：

| # | 項目 | 位置 | 動作 |
|---|------|------|------|
| 1 | **DHCP 保留 192.168.1.107** | 路由器管理介面 | 依 master 網卡 MAC 設保留（2026-07-07 盤點：目前是 DHCP 浮動配發，未保留！）。機隊 settings、NAS cloudflared、CORS 全都寫死這個 IP |
| 2 | 備份解密金鑰 | `C:\OriginsunAgent\credentials\backup_key.txt`（44 bytes） | 存密碼管理器（✅ 2026-07-06 已做） |
| 3 | **NAS SSH 私鑰** | `C:\Users\originsun\.ssh\id_originsun_nas`（+ `.pub`） | 複製到密碼管理器或加密隨身碟。遺失的話要用 NAS console 重新 authorized_keys（麻煩得多） |
| 4 | Google Drive API 憑證 | `C:\OriginsunAgent\credentials\credentials.json` + `token.json` | 同上存一份。遺失可去 Google Cloud Console 重建，但要重走 OAuth |
| 5 | 完整安裝包 | `C:\OriginsunAgent\Originsun_Agent.zip`（~1GB，含 python_embed） | ✅ 已存 NAS：`/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/Originsun_Agent.zip`（2026-07-07；日後大版本可重新 scp 覆蓋） |

> 建議把 2/3/4 打包成一個加密壓縮檔統一保管，內容極少變動。

---

## 2. Phase 1 — 新機基礎環境

新機或重灌後的 Windows 10/11，用**同一個使用者名稱 `originsun`** 登入
（程式碼多處以 `%USERPROFILE%` 推路徑；換名字要多改東西，不值得）。

1. **固定網路身分**：設定成 `192.168.1.107`（路由器 DHCP 保留綁新網卡 MAC，
   或本機手動固定 IP）。這一步不做，機隊 10 台的 `master_server`、
   NAS cloudflared 的 `foundry→192.168.1.107:8000` 路由全部要逐台改——先把 IP 接回來。
2. 安裝 **Git for Windows**（含 Git Bash）。
3. 安裝 **Node.js 22 LTS**（官網 build 用；`node --version` 應為 v22.x）。
4. 確認 **OpenSSH Client** 存在（Windows 內建，`ssh -V` 驗證）。
5. 安裝 **claude CLI** 並登入（AI SEO / 翻譯 runner 用）：
   安裝後執行檔應在 `C:\Users\originsun\.local\bin\claude.exe`
   （`seo_runner._resolve_claude_exe` 只找這幾個位置：`~\.local\bin`、
   WinGet Links、`%LOCALAPPDATA%\Programs\claude`）。跑一次 `claude` 完成
   Max 帳號登入。

---

## 3. Phase 2 — 程式碼 + Python 環境（二選一）

### 路徑 A（快，建議）：從機隊任一台複製整包

每台 agent 的 `C:\OriginsunAgent` 都含同一套 `python_embed`（~3GB，含 torch）
與 `ffmpeg.exe / ffprobe.exe / exiftool.exe / exiftool_files / windows_helper`。

1. 挑一台在線 agent（如備檔電腦 192.168.1.120），把它的 `C:\OriginsunAgent`
   整個資料夾複製到新 master 的 `C:\OriginsunAgent`（網芳或隨身碟，~4GB）。
2. **刪掉複製過來的執行期檔**（那是該 agent 的，不是 master 的）：
   `settings.json`、`users.json`、`job_history.json`、`update_status.json`、
   `uvicorn_*.log`、`agent_server.log`。
3. 程式碼版本對齊：該 agent 的碼可能舊於 master 最後版本。從 GitHub 拉最新：
   ```powershell
   cd C:\OriginsunAgent
   git clone --depth 1 https://github.com/denniswang0513-del/Originsun-Media-Guard.git _fresh
   # 把 _fresh 內的程式碼檔（.py / core / routers / services / db / utils /
   # frontend / templates / website）覆蓋到 C:\OriginsunAgent，然後刪 _fresh
   ```
   （或先照舊版起服務，之後用正常 /publish 流程升上來。）

### 路徑 B（慢，機隊全滅時）：從零建

1. `git clone https://github.com/denniswang0513-del/Originsun-Media-Guard.git C:\OriginsunAgent`
2. 裝 Python 3.11（python.org），建 venv 並照 `requirements_server.txt`
   頭部指示一次裝齊：
   ```powershell
   cd C:\OriginsunAgent
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements_server.txt
   ```
   （`start_hidden.vbs` / `core/process_spawn.py` 的 find_python 順序是
   .venv → python_embed → 系統 Python，venv 存在就會被優先用。）
3. 手動補二進制：`ffmpeg.exe`、`ffprobe.exe`、`exiftool.exe` + `exiftool_files\`
   放到 `C:\OriginsunAgent` 根目錄（來源：NAS 上的 Originsun_Agent.zip 副本，見 §1-5）。
4. Whisper / F5-TTS 模型會在首次使用時自動從 HuggingFace 下載（各 ~1GB+），
   不用手動處理，但首次轉錄/TTS 會慢。

### 共同：官網 build 依賴

```powershell
cd C:\OriginsunAgent\website
npm install
```

---

## 4. Phase 3 — 還原 secrets 與執行期資料

1. **從 Google Drive 下載最新備份 bundle**（`originsun_backup_<ts>.tar.gz`，
   用瀏覽器登 Drive 手動下載即可）。解包與 `settings.json.enc` 解密的照抄式
   指令見 **`BACKUP_RESTORE.md` §3**（需要 §1-2 保管的 `backup_key.txt`）。
2. 解密出的 `settings.json` 放回 `C:\OriginsunAgent\settings.json`。
   裡面已含：`jwt_secret`（機隊+NAS 共用，**不能換新的**，換了全部 401）、
   `database_url`（生產 mediaguard）、`master_server`、通知 webhook/token、
   `machine_id` 等。
3. `credentials\` 三件套放回：`backup_key.txt`、`credentials.json`、`token.json`
   （來源：§1 的異地副本）。
4. SSH 私鑰放回 `C:\Users\originsun\.ssh\id_originsun_nas`（+ `.pub`）。
   驗證：`ssh -i C:\Users\originsun\.ssh\id_originsun_nas admin@192.168.1.132 "echo OK"`
   （⚠️ NAS SSH 只在 LAN 通，admin home 是 /root。）
5. `users.json` 不用還原——使用者資料的 source of truth 在 DB（雙寫架構），
   服務起來後首次寫入會自動重建 JSON fallback。
6. `taiwan_dict.json` git 裡有基準版；若曾在 prod 熱編輯過、且 bundle 沒涵蓋，
   熱編輯內容會遺失（可接受，重新編）。
7. **DB / uploads 不用動**——它們活在 NAS（本 SOP 前提），master 只是接回去。
   本機 `uploads\projects\` 作品圖若有遺失，NAS 端仍有 rebuild 時同步過去的副本，
   可從 NAS 撈回（路徑見 `docker/INDEX.md`）。

---

## 5. Phase 4 — 開機自啟 + 起服務

1. 開機自啟走 **Startup folder 捷徑**（不是 schtasks——那條路 2026-05 已連根拔除，
   Session 0 會讓資料夾選擇器全滅，見 CLAUDE.md 版本歷程 v1.10.130）：
   捷徑 `Originsun Agent.lnk` 放
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`，
   目標指向 `C:\OriginsunAgent\start_hidden.vbs`（可跑 `Create_Shortcut.vbs` 自動建）。
2. 首次啟動：桌面雙擊 `start_hidden.vbs`（確保跑在使用者 Session 1）。
3. 等 ~30 秒，驗 `http://127.0.0.1:8000/api/v1/health`。

---

## 6. Phase 5 — 驗證清單（全過才算重建完成）

```powershell
# 1. 服務健康 + 版本
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/version

# 2. DB 接通（回 db 而非 json fallback）
Invoke-RestMethod http://127.0.0.1:8000/api/v1/agents   # 需 admin token；source 應為 "db"

# 3. NAS SSH（官網 rebuild 與備份都靠它）
ssh -i $env:USERPROFILE\.ssh\id_originsun_nas admin@192.168.1.132 "echo NAS_OK"

# 4. 機隊可見：後台「專案總覽」機器卡片應綠燈（agent 的 master_server 本來就指 .107，IP 接回即自動恢復）

# 5. 官網 rebuild 全鏈路：後台官網管理按「立即重建」→ build → scp NAS → 對外站更新

# 6. claude runner：官網管理 AI SEO 按「立即執行」一件，確認不再報「找不到 claude CLI」

# 7. 備份鏈路：後台備份卡片按「立即備份」→ 確認 Google Drive 出現新 bundle

# 8. 通知鏈路：任意跑一個小任務，Google Chat 應收到完成通知
```

外部路由不用動的原因：cloudflared（NAS 容器）指向 `192.168.1.107:8000`、
機隊 settings 指向同 IP——只要 §2-1 把 IP 接回，全部自動恢復。

---

## 7. （選配）dev checkout 重建

生產恢復後才做。`git clone` 到 `E:\Dev\Originsun-Media-Guard` → 建 `.venv` 裝
`requirements_server.txt` + `pytest pytest-asyncio pytest-mock httpx ruff` →
`npm install`（根目錄 eslint + website/）→ 從 `C:\OriginsunAgent` 複製
`ffmpeg.exe`/`ffprobe.exe` → dev 用 `dev_start.ps1`（8001）。
共存守則見 CLAUDE.md §2.1（dev 只用 8001、絕不本機 /publish）。

---

## 相關文件

- `docs/BACKUP_RESTORE.md` — DB / uploads / settings 的備份與還原（本 SOP 的 §4 依賴它）
- `docker/INDEX.md` — NAS 容器拓樸（cloudflared / nginx / website-api / postgres）
- `PUBLISHING.md` + `/publish` skill — 重建後的正常發版流程
- CLAUDE.md §2.1 — dev / 生產共存守則
