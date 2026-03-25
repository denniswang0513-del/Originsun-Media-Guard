# 版本發布與 OTA 更新機制 (PUBLISHING.md)

> 此文件規範版本號管理、發布流程、OTA 更新架構，以及錯誤回復策略。
> 版本：v2（2026-03-25，重新設計，加入 rollback 機制）

---

## 一、架構總覽

```
主控端（開發者電腦 192.168.1.11）
│
├── 1. 改 code → git commit → git push
│
├── 2. python publish_update.py --version X.Y.Z --notes "..."
│   ├── 版本格式驗證（^\d+\.\d+\.\d+$）
│   ├── 版本遞增驗證（new > current）
│   ├── 原子寫入 version.json（先 .tmp 再 os.replace）
│   ├── 從 ota_manifest.py 讀取檔案清單（single source of truth）
│   └── 呼叫 build_agent_zip.py → 打包 Originsun_Agent.zip
│
├── 3. 主控端同時是 HTTP 伺服器，提供：
│   ├── GET /api/v1/version          → 回傳版本號
│   ├── GET /download_update         → 輕量 code-only ZIP（~20MB）
│   └── GET /download_agent          → 完整安裝 ZIP（~1GB）
│
└── 4. Agent 端（同事電腦）
    ├── update_agent.bat → 呼叫 update_agent.py（Python）
    │
    │   Phase 1: CHECK     連得到主控端嗎？
    │   Phase 2: COMPARE   遠端版本 > 本機？
    │   Phase 3: BACKUP    備份現有檔案到 _rollback/
    │   Phase 4: DOWNLOAD  GET /download_update → 解壓覆蓋
    │   Phase 5: PIP       pip install -r requirements_agent.txt
    │   Phase 6: PREFLIGHT preflight.py 驗證 critical imports
    │   Phase 7: CLEANUP   全部 OK → 刪 _rollback/
    │                      任一失敗 → ROLLBACK（還原 + 重裝舊 requirements）
    │
    └── uvicorn main:io_app --host 0.0.0.0 --port 8000
```

---

## 二、版本號規則（SemVer）

格式：`MAJOR.MINOR.PATCH`（純數字，不帶 `v` 前綴）

| 改動類型 | 規則 | 範例 |
|---------|------|------|
| Bug fix、文件、樣式微調 | PATCH +1 | 1.10.0 → 1.10.1 |
| 新功能、新 API、新頁籤 | MINOR +1 | 1.10.0 → 1.11.0 |
| 破壞性變更、DB schema 不相容 | MAJOR +1 | 1.10.0 → 2.0.0 |

### 版本號存放位置（3 處，必須一致）

| 檔案 | 欄位 | 格式 |
|------|------|------|
| `version.json` | `"version"` | `"1.10.0"` |
| `update_manifest.json` | `"version"` | `"1.10.0"` |
| `CLAUDE.md` | 頂部 banner | `> **版本**: v1.10.0（2026-03-25）` |

### 版本驗證（三層防護）

1. **發布時**（`publish_update.py`）：格式 `^\d+\.\d+\.\d+$` + new > current
2. **Agent 更新時**（`update_agent.py`）：解析失敗 → 拒絕更新
3. **前端**（`app.js`）：`_isOlderThan()` 數字比對

---

## 三、關鍵檔案清單

### 發布端（主控）

| 檔案 | 用途 |
|------|------|
| `ota_manifest.py` | **新** — Agent 需要的檔案/目錄唯一定義（single source of truth） |
| `publish_update.py` | 發布 CLI — 版本驗證 + 原子寫入 + 打包 |
| `build_agent_zip.py` | 打包 Originsun_Agent.zip — 從 ota_manifest 讀取清單 |
| `version.json` | 版本資訊 |
| `update_manifest.json` | pip 套件清單（自動掃描生成） |

### Agent 端

| 檔案 | 用途 |
|------|------|
| `update_agent.bat` | 薄啟動器（~30 行）— 偵測 Python → 呼叫 update_agent.py → 啟動 uvicorn |
| `update_agent.py` | **新** — 核心更新邏輯（backup → download → pip → preflight → rollback） |
| `preflight.py` | **新** — 更新後健康檢查（驗證 critical imports） |
| `requirements_agent.txt` | **新** — Agent 最小依賴（不含 torch/TTS/whisper/sqlalchemy） |
| `update_monitor.py` | 更新進度 HTTP server（port 8001） |

### 後端 API

| 端點 | 用途 |
|------|------|
| `GET /api/v1/version` | 回傳本機版本 |
| `GET /api/v1/nas_version` | 回傳主控端版本 |
| `GET /download_update` | 下載輕量更新 ZIP |
| `POST /api/v1/control/update` | 觸發 Agent 更新 |
| `GET /api/v1/update_status` | **新** — 讀取更新進度 |

---

## 四、發布指令（Claude Code 標準做法）

### ✅ 正確做法

```powershell
d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe publish_update.py --version 1.11.0 --notes "新增 XXX 功能"
```

**必須**使用 `--version` + `--notes` 參數。不可用管線 stdin。

### 完整 FINISHING.md Step 7 模板

```powershell
# 1. 確認 version.json 當前版本
type version.json

# 2. 發布（帶參數）
python publish_update.py --version <VERSION> --notes "<NOTES>"

# 3. 驗證（三個檔案版本一致）
type version.json
type update_manifest.json

# 4. 補 commit
git add version.json update_manifest.json
git commit -m "chore(publish): v<VERSION> NAS deploy"
git push
```

### ❌ 禁止做法

| 做法 | 問題 |
|------|------|
| `--no-interactive` | 參數不存在，被忽略 |
| `printf "..." \| python publish_update.py` | stdin 對齊失敗，notes 寫進 version |
| `echo "..." \| python publish_update.py` | 同上 |
| 版本號帶 `v` 前綴 | Agent 端 PowerShell `[int]` 轉型失敗 |

---

## 五、Agent OTA 更新流程（新架構）

### update_agent.py 七階段流程

```
┌─ Phase 1: CHECK
│  └─ GET http://<master>/api/v1/version（5s timeout）
│     ├─ 連不到 → 跳過更新，exit 0
│     └─ 連到了 → 取得 REMOTE_VER
│
├─ Phase 2: COMPARE
│  ├─ 讀取本機 version.json → LOCAL_VER
│  ├─ parse_semver() 解析兩個版本
│  ├─ 解析失敗 → 跳過更新，exit 0
│  └─ REMOTE > LOCAL → 繼續
│
├─ Phase 3: BACKUP
│  ├─ 刪除舊的 _rollback/（如有）
│  └─ 複製 ota_manifest 定義的所有檔案/目錄到 _rollback/
│
├─ Phase 4: DOWNLOAD
│  ├─ 啟動 update_monitor.py（port 8001）
│  ├─ GET /download_update → %TEMP%/originsun_update.zip（300s timeout）
│  ├─ 解壓覆蓋到 INSTALL_DIR
│  └─ 失敗 → ROLLBACK
│
├─ Phase 5: PIP
│  ├─ pip install -r requirements_agent.txt（300s timeout）
│  ├─ 檢查 exit code
│  └─ 失敗 → ROLLBACK
│
├─ Phase 6: PREFLIGHT
│  ├─ python preflight.py
│  ├─ 檢查 exit code
│  └─ 失敗 → ROLLBACK
│
└─ Phase 7: CLEANUP
   ├─ 全部通過 → 刪除 _rollback/，exit 0
   └─ ROLLBACK = 從 _rollback/ 還原 + pip install 舊 requirements，exit 1
```

### Rollback 機制

```
_rollback/
├── main.py              ← 更新前的 main.py
├── config.py
├── version.json
├── requirements_agent.txt
├── frontend/            ← 完整目錄備份
├── routers/
├── core/
└── ...
```

ROLLBACK 步驟：
1. 從 `_rollback/` 複製所有檔案回 INSTALL_DIR
2. `pip install -r _rollback/requirements_agent.txt`（還原舊依賴）
3. 刪除 `_rollback/`
4. 寫入 `_update_status.json`：`{"status": "rolled_back", "error": "..."}`

---

## 六、依賴管理

### 兩套 requirements

| 檔案 | 用途 | 包含 |
|------|------|------|
| `requirements.txt` | 主控端（完整） | fastapi + torch + TTS + whisper + sqlalchemy + ... |
| `requirements_agent.txt` | Agent 端（最小） | fastapi + uvicorn + socketio + xxhash + psutil + Jinja2 |

### Agent 不安裝的套件

| 套件 | 原因 |
|------|------|
| torch, torchaudio | ~2GB，Agent 不做 TTS |
| TTS, f5-tts | 語音合成只在主控端 |
| faster-whisper | 語音辨識只在主控端 |
| sqlalchemy, asyncpg | DB 可選，Agent 用 JSON fallback |
| playwright | 報表 PDF 只在主控端 |

### 程式碼 import guard

所有 server-only 依賴的 import 必須用 try/except 包裹：

```python
# db/session.py
try:
    from sqlalchemy.ext.asyncio import create_async_engine, ...
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False

# routers/api_auth.py
try:
    from core.google_auth import verify_google_id_token
except ImportError:
    verify_google_id_token = None
```

---

## 七、Preflight 健康檢查

`preflight.py` 驗證的 critical imports：

```python
CRITICAL_IMPORTS = [
    "fastapi",
    "uvicorn",
    "socketio",
    "xxhash",
    "jinja2",
    "core.socket_mgr",
    "core.state",
    "config",
]
```

**不檢查**（可選模組）：torch, torchaudio, TTS, faster-whisper, sqlalchemy, google.oauth2

---

## 八、錯誤回復矩陣

| 階段 | 失敗情況 | 回復方式 |
|------|---------|---------|
| CHECK | 主控端連不到 | 跳過更新，用現有版本啟動 ✅ |
| COMPARE | version.json 格式錯 | 跳過更新，記 log ✅ |
| BACKUP | 磁碟滿/權限錯 | 中止更新，現有版本不受影響 ✅ |
| DOWNLOAD | 下載/解壓失敗 | ROLLBACK 從 _rollback/ 還原 ✅ |
| PIP | 套件安裝失敗 | ROLLBACK + 重裝舊 requirements ✅ |
| PREFLIGHT | critical import 壞了 | ROLLBACK + 重裝舊 requirements ✅ |
| ROLLBACK 本身 | 還原失敗 | 記 log，bat 仍嘗試啟動 uvicorn（最壞情況需手動重裝） |

---

## 九、發布檢查清單

每次發布前確認：

- [ ] `version.json` 版本號正確（純數字 X.Y.Z）
- [ ] `CLAUDE.md` 頂部版本一致
- [ ] `CHANGELOG.md` 已新增本次紀錄
- [ ] `ROADMAP.md` 已打勾完成項目
- [ ] Git commit + push 成功
- [ ] `publish_update.py --version X.Y.Z --notes "..."` 執行成功
- [ ] 執行後 `version.json` 版本號仍正確
- [ ] 執行後 `update_manifest.json` 版本號一致
- [ ] 補 commit publish 產生的檔案變動

---

## 十、常見問題

### Q1: Agent 更新卡住（「主服務和監控都無回應」）

**可能原因**：
- version.json 版本號被寫壞（notes 字串）
- requirements 含 Agent 不需要的重套件（asyncpg 需要 C 編譯器）
- preflight 失敗後 rollback 也失敗

**解法**：
1. 修正主控端 version.json + update_manifest.json
2. 確認 requirements_agent.txt 只含最小依賴
3. 重新 publish
4. Agent 端手動殺 python 進程 → 重啟 update_agent.bat

### Q2: 新增 Agent 端需要的 pip 套件

1. 加到 `requirements_agent.txt`
2. 發布新版本
3. Agent 更新時 Phase 5 會自動安裝

### Q3: 新增 server-only 套件

1. 加到 `requirements.txt`（主控端）
2. **不要**加到 `requirements_agent.txt`
3. 在程式碼中用 try/except import guard
4. 主控端手動 `pip install`

### Q4: 向下相容（舊 Agent 用舊 BAT）

新的 `update_agent.bat` 會在下一次更新時被 ZIP 覆蓋到 Agent 端。
但第一次更新仍用舊 BAT 邏輯（無 rollback）。
若第一次更新成功，後續就用新架構了。
