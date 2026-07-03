# 資料備份與還原 Runbook（Backup & Restore）

> 對象：值班/維運人員。目標：出事時照抄指令就能還原。
> 系統：Originsun Media Guard —— `mediaguard` Postgres DB + 官網上傳媒體。
> 相關程式：`services/website/backup_service.py`（Layer 2）、`drive_sync.py`（Drive 上傳）、
> `scripts/setup_gdrive_backup.py`（一次性授權）。後台入口：admin Tab →「🗄 資料備份」。

---

## 1. 架構總覽

兩層獨立備份，互不依賴：

| 層 | 在哪跑 | 觸發 | 內容 / 產物 | 放哪 | 保留 |
|----|--------|------|-------------|------|------|
| **Layer 1** | QNAP NAS（`192.168.1.132`）crontab `.../Website/backups/backup_originsun.sh` | 每日 **02:30**（常駐，master 關機也照跑） | `mediaguard_<ts>.pgc`（pg_dump 自訂格式）+ `uploads_<ts>.tar.gz` | NAS 本地 `/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/` | 14 份 |
| **Layer 2** | master（Windows）排程 `backup_service.py` | 每日 **03:00** | ssh NAS `pg_dump -Fc mediaguard` + `tar uploads/` 拉回 master ＋本地 `website/public/notion-media` → 打包 `originsun_backup_<ts>.tar.gz`（內含 `mediaguard.pgc` + `uploads.tar.gz` + 選配 `notion-media.tar.gz` + `manifest.json`）→ 上傳 Google Drive（私有） | master 本地 `<app>/backups/` ＋ 你的 Google Drive「Originsun Backups」 | 14 日 + 6 月（每月留最新一份） |

- **Layer 1** = NAS 就近副本，救得快、獨立於 master。
- **Layer 2** = off-site（Google Drive）+ master 本地，含 notion-media，救得全。
- Layer 2 只有握有 NAS SSH key 的 master 會跑（fleet agents 無 key → 早退）。
  後台「立即備份」在 NAS 容器上按時，會透過 `MASTER_RELAY_URL` forward 給 master 執行。

**NAS 連線常數**（本文件所有 NAS 指令共用）：

| 項目 | 值 |
|------|-----|
| SSH 目標 | `admin@192.168.1.132` |
| SSH key（master 上） | `~/.ssh/id_originsun_nas` |
| NAS docker 執行檔 | `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker` |
| NAS 官網目錄 | `/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website` |
| Postgres 容器 | `originsun_postgres` |
| DB / DB user | `mediaguard` / `originsun`（`docker exec` 免密碼） |
| Layer 1 備份目錄 | `<NAS 官網目錄>/backups/` |

> Windows 上一律從 **Git Bash** 下 ssh。標準呼叫格式（下面會反覆用）：
> ```bash
> ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 "<remote cmd>"
> ```

---

## 2. 一次性 Google Drive 設定（只做一次，且必須在 master）

> Layer 2 要把備份上傳到你的 Google Drive，需先做一次 OAuth 授權。
> **必須在 master 執行**（OAuth 瀏覽器 + `credentials/` 憑證都在 master）。

### 2.1 在 Google Cloud Console 建立 OAuth 桌面用戶端

1. 前往 <https://console.cloud.google.com/>，建立（或選擇）一個專案。
2. 「API 和服務」→「啟用 API 和服務」→ 搜尋並啟用 **Google Drive API**。
3. 「API 和服務」→「憑證」→「+ 建立憑證」→「OAuth 用戶端 ID」。
   - 若首次，先設「OAuth 同意畫面」：User Type 選「外部」，填名稱/支援信箱，
     並把**自己的 Google 帳號加進「測試使用者」**（否則授權會被拒）。
4. 應用程式類型選 **桌面應用程式（Desktop app）** → 建立。
5. 「下載 JSON」，改名為 `credentials.json`。
6. 放到 master 的 `credentials/credentials.json`（`credentials/` 已在 `.gitignore`）。

### 2.2 跑授權腳本

```bash
cd <repo root>          # 例：e:\Dev\Originsun-Media-Guard（或 master 的 C:\OriginsunAgent）
python scripts/setup_gdrive_backup.py
```

腳本會：檢查 `credentials.json` → 開瀏覽器完成 OAuth（產生 `credentials/token.json`）→
在 Drive 建立「Originsun Backups」資料夾並**印出 folder id** → 印出下一步。

> 缺 google 套件時腳本會提示：
> `pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib`

### 2.3 貼進後台並啟用

到 admin Tab →「🗄 資料備份」卡片：

1. 把 folder id 貼進「**Google Drive 資料夾 ID**」。
2. 按「**儲存設定**」。
3. 勾「**啟用排程**」（每日 03:00 自動打包上傳）。
4. 按「**立即備份一次**」→ 確認狀態成功、Drive 出現 `originsun_backup_<ts>.tar.gz`。

---

## 3. 還原（Restore）

> 先決定用哪個來源：
> - **Layer 2 bundle**（Drive 或 master 本地 `<app>/backups/originsun_backup_<ts>.tar.gz`）→ 見 3.1，要先解包。
> - **Layer 1 NAS 檔**（`mediaguard_<ts>.pgc`、`uploads_<ts>.tar.gz`）→ 已是散檔，直接跳 3.3 / 3.4。
>
> DB 還原是**破壞性**操作（會覆蓋現有資料）。動手前，先另存一份當下狀態當保險：
> ```bash
> ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
>   "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker exec originsun_postgres \
>    pg_dump -U originsun -Fc mediaguard" > safety_before_restore.pgc
> ```

### 3.1 解開 Layer 2 bundle

從 Drive 下載（或用 master 本地）`originsun_backup_<ts>.tar.gz`，然後：

```bash
tar xzf originsun_backup_<ts>.tar.gz
# 產出：
#   mediaguard.pgc         ← DB（自訂格式）— CRM 客戶/專案/報價/帳務 + 官網 + 使用者 全在此
#   uploads.tar.gz         ← 官網上傳媒體
#   settings.json.enc      ← 設定檔（jwt_secret/db url/SMTP 密碼）Fernet 加密（見 3.4b）
#   notion-media.tar.gz    ← 選配（若該次有 notion 圖）
#   manifest.json          ← 時間戳 / 版本 / 各檔 sha256（可拿來核對完整性）
```

（可先看 `manifest.json` 確認 `ts`、`app_version`、各檔 `sha256` 對得上。）

### 3.2 把 .pgc 送進容器

pg_restore 要能讀到 `.pgc`。用 `docker cp` 複製到容器內 `/tmp`：

```bash
# 在 NAS 上（先把 mediaguard.pgc 放到 NAS 某處，例如 <NAS 官網目錄>/backups/restore/）
DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
$DOCKER cp /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/restore/mediaguard.pgc \
  originsun_postgres:/tmp/mediaguard.pgc
```

從 master 用 Git Bash 走 ssh 一次做完（把本機 .pgc 串進容器再 cp 亦可先 scp 到 NAS 再 cp）：

```bash
# 1) 先把本機 mediaguard.pgc scp 到 NAS
scp -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes mediaguard.pgc \
  admin@192.168.1.132:/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/restore/mediaguard.pgc

# 2) 再 docker cp 進容器
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker cp \
   /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/restore/mediaguard.pgc \
   originsun_postgres:/tmp/mediaguard.pgc"
```

### 3.3 還原 DB（自訂格式，pg_restore）

> `--clean --if-exists`：還原前先 DROP 現有物件（若存在）再重建 → 等於整庫覆蓋成備份內容。
> 沒有這兩個旗標的話，既有表格會導致 restore 衝突。`-v` = verbose，方便看進度/錯誤。

DB **還在、只是要覆蓋內容**（最常見情況）：

```bash
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker exec -i originsun_postgres \
   pg_restore -U originsun -d mediaguard --clean --if-exists -v /tmp/mediaguard.pgc"
```

DB **被整個刪掉了**（需先建空庫再還原）：

```bash
# 先建庫（已存在會報 already exists，可忽略）
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker exec originsun_postgres \
   createdb -U originsun mediaguard"

# 再還原（全新庫可省略 --clean --if-exists，但帶著也無害）
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker exec -i originsun_postgres \
   pg_restore -U originsun -d mediaguard --clean --if-exists -v /tmp/mediaguard.pgc"
```

> 若不想先 `docker cp`，也可把 .pgc 直接串進 stdin（`-i` 讓 exec 收 stdin）：
> ```bash
> ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
>   "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker exec -i originsun_postgres \
>    pg_restore -U originsun -d mediaguard --clean --if-exists -v" < mediaguard.pgc
> ```

### 3.4 還原 uploads 媒體

`uploads.tar.gz` 解到官網目錄，會落成 `uploads/`：

```bash
# 先把 uploads.tar.gz scp 到 NAS，再在 NAS 上解開
scp -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes uploads.tar.gz \
  admin@192.168.1.132:/tmp/uploads.tar.gz

ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "tar xzf /tmp/uploads.tar.gz -C /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/"
# → 產生 /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/uploads/
```

**notion-media**（若 bundle 內有 `notion-media.tar.gz`）：解到 **master** 的
`website/public/notion-media/`（這批圖是 full-sync 產物，不在 NAS uploads/）：

```bash
# 在 master 的 repo root 執行
tar xzf notion-media.tar.gz -C website/public/
# → website/public/notion-media/
```

### 3.4b 還原 settings.json（解密）

bundle 內的 `settings.json.enc` 是 **Fernet 加密**（因為會上 Google Drive）。解密需要
金鑰 `credentials/backup_key.txt`——**這把金鑰只存在 master 本地、不在 bundle 裡**（所以
Drive 外洩也解不開）。

> 🔴 **前提**：你手上要有這把金鑰。master 若整台掛掉、`credentials/backup_key.txt` 也沒了，
> 就**永遠解不開** `settings.json.enc`。所以當初有另存一份金鑰到密碼管理器/安全處 → 拿它來還原。
> （settings.json 是設定+密鑰，不是核心資料；真沒有金鑰就重設 settings.json 即可，DB 資料不受影響。）

```bash
# 在有金鑰的機器（master 或你另存金鑰處）跑。KEY 換成你的 backup_key.txt 內容。
python -c "
from cryptography.fernet import Fernet
key = open(r'C:\OriginsunAgent\credentials\backup_key.txt','rb').read().strip()  # 或直接貼字串
data = Fernet(key).decrypt(open('settings.json.enc','rb').read())
open('settings.json','wb').write(data)
print('settings.json 已還原')
"
# → 把還原出的 settings.json 放回 master 的 app 根目錄（C:\OriginsunAgent\settings.json）
```

### 3.5 直接用 Layer 1 NAS 散檔還原

NAS 本地 `.../Website/backups/` 內的 `mediaguard_<ts>.pgc`、`uploads_<ts>.tar.gz`
**已經是散檔**（不用先 `tar xzf` 解 bundle）。流程與上面完全相同，只是省掉解包：

```bash
DOCKER=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
BK=/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups

# DB：直接把 NAS 上的 .pgc cp 進容器再 restore（在 NAS 本機一氣呵成）
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "$DOCKER cp $BK/mediaguard_<ts>.pgc originsun_postgres:/tmp/r.pgc && \
   $DOCKER exec -i originsun_postgres pg_restore -U originsun -d mediaguard --clean --if-exists -v /tmp/r.pgc"

# uploads：直接在 NAS 解到官網目錄
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "tar xzf $BK/uploads_<ts>.tar.gz -C /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/"
```

---

## 4. 還原後

1. **重建對外網站**：到 admin Tab 觸發「**重建**」（rebuild trigger）——
   任一內容寫入都會 `mark_dirty()` → 60s debounce → Astro rebuild → 對外網站更新。
   還原完 DB/媒體後手動觸發一次重建，確保 `dist/` 反映還原後內容。
2. **驗證內容與圖片**：開對外站，抽查作品/文章/分類是否回來、圖片能載入
   （uploads/ 圖 + notion-media/ 圖都要看）。
3. 若 DB restore 有跳 error，回頭看 `pg_restore -v` 輸出；`--clean --if-exists`
   對「原本不存在的物件」發的 DROP 警告可忽略，真正要注意的是資料表匯入的 ERROR。

---

## 5. 驗證備份健康（日常巡檢）

**A. 後台狀態卡**（最快）：admin Tab →「🗄 資料備份」看：
- `is_master` / `gdrive_ready`（授權是否就緒）、`enabled`（排程是否開）。
- `last_run_at` / `last_run_summary`（上次跑的時間、bundle 名、大小、Drive 連結、有無錯誤）。

**B. NAS Layer 1 log**：

```bash
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "tail -n 40 /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/backup.log"
```

列一下 NAS 本地備份檔（確認每天有新檔、保留 14 份）：

```bash
ssh -i ~/.ssh/id_originsun_nas -o IdentitiesOnly=yes admin@192.168.1.132 \
  "ls -lh /share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/backups/"
```

**C. master 本地 Layer 2 bundle**：

```bash
ls -lh "<app>/backups/"      # 應見最新 originsun_backup_<ts>.tar.gz + staging/ 暫存夾
```

**D. Google Drive**：開 Drive 的「Originsun Backups」資料夾，確認最新
`originsun_backup_<ts>.tar.gz` 存在、日期是今天、檔案大小合理（不是 0）。
（Layer 2 上傳為**私有**，只有你的帳號看得到；程式用 `drive.file` scope，只列得到自己建的檔。）

---

## 6. 速查

| 情境 | 去哪 |
|------|------|
| 首次設定 Drive 授權 | §2（master 上 `python scripts/setup_gdrive_backup.py`） |
| 從 Drive/bundle 還原 | §3.1 解包 → §3.2/§3.3 DB → §3.4 uploads → §4 重建 |
| 從 NAS 就近還原 | §3.5（散檔，免解包） |
| 只想確認備份還活著 | §5 A/B/D |
| 還原前保命備份 | §3 開頭的 `pg_dump > safety_before_restore.pgc` |
