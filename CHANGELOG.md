# Originsun Media Guard Pro — 工作日誌

> 記錄每次開發變更，格式：日期 → 變更項目 → 影響範圍

---

## [1.8.3] - 2026-03-19

### 新增
- `main.py` — 新增 `_periodic_version_check()` 背景任務，每 10 分鐘向主控端檢查版號，有更新時透過 Socket.IO `update_available` 事件主動推播給所有連線前端
- `main.py` — 新增 `_read_local_version()` 和 `_is_newer()` 輔助函式（語意版本比較，支援 v 前綴）
- `frontend/app.js` — 新增 `socket.on('update_available')` 監聽，後端推播時立即顯示紅色更新徽章

### 修改
- `frontend/app.js` — 修正 `checkAgentVersion()` 中 `nasVersionUrl` 死三元運算（兩邊路徑相同），改為正確區分本機/遠端路徑
- `frontend/app.js` — 新增 `latestVersion === 'unknown'` 時的 fallback 邏輯，防止版本檢查靜默失敗
- `frontend/app.js` — 新增 `stripV()` 處理版本號 v 前綴，避免 `parseInt('v1')` 產生 NaN
- `routers/api_system.py` — `/api/v1/nas_version` 當 `master_server` 未設定時，改為回傳本機版號（主控端自身不再回傳 unknown）
- `version.json` — 移除版本號中的 `v` 前綴（`v1.8.2` → `1.8.2`），統一格式

### 技術備註
- v1.7.0 的舊前端無 `update_available` 監聽，仍需透過主控端網頁（192.168.1.11:8000）查看更新通知，或手動更新一次
- 後端推播不取代前端輪詢，兩者互補：前端每次載入仍會執行 `checkAgentVersion()`，後端則在瀏覽器開啟期間持續推播

---

## [1.8.2] - 2026-03-18

### 修改
- `version.json` — 版本號更新至 v1.8.2
- `build_agent_zip.py` — 重新打包 `Originsun_Agent.zip`，確保新安裝取得最新版本

### 技術備註
- `Install_Originsun_Agent.bat` 下載的 ZIP 由 `/download_agent` 端點提供，需手動執行 `build_agent_zip.py` 重新打包才能更新 ZIP 內容
- 串帶來源路徑確認正確：備份觸發串帶時一律使用 `local_root`（原始素材），不受轉檔勾選影響
- 報表歷史路徑預設值（`nas_paths.web_report_dir`）已在 v1.7.2 設定正確，若顯示空白請確認 NAS 上 `reports_index.json` 是否存在

---

## [1.8.1] - 2026-03-18

### 新增
- `bootstrap.py` — 改寫為雙模式腳本（初始安裝 + `--update` OTA 更新），僅使用 stdlib
- `routers/api_system.py` — 新增 `GET /bootstrap.ps1` 動態 PowerShell 端點，一行指令完成升級
- `routers/api_system.py` — 新增 `GET /download_updater` 獨立遷移 bat 下載端點
- `frontend/app.js` — 新增 migration overlay UI（`_showMigrationOverlay`），引導 v1.7.0 使用者升級
- `frontend/app.js` — 新增 `_pollForMigrationDone()`，自動偵測升級完成並重新載入頁面

### 修改
- `config.py` — `web_report_dir` 預設值改為 `\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport`，新安裝自動帶入
- `core/worker.py` — 串帶來源一律使用原始素材（`local_root`），不再依據是否勾選轉檔切換路徑
- `build_agent_zip.py` — 打包清單加入 `bootstrap.py`
- `frontend/app.js` — `updateAgent()` 對舊版 Agent 自動下載升級工具 + 顯示引導 overlay

### 技術備註
- v1.7.0 → v1.8.x 首次升級因瀏覽器安全限制無法全自動，需使用者執行一次下載的 bat 檔
- 升級完成後，`_pollForMigrationDone()` 偵測到新版本會自動移除 overlay 並重新載入
- 後續所有更新（v1.8.x+）皆為真正一鍵更新

---

## [1.8.0] - 2026-03-18

### 新增
- `routers/api_system.py` — 新增 `GET /download_update` 端點，即時打包輕量 OTA 更新 ZIP（~200KB，僅程式碼）
- `config.py` — 新增 `master_server` 預設值（`http://192.168.1.11:8000`），供 Agent 識別主控端位址

### 修改
- `routers/api_system.py` — `GET /api/v1/nas_version` 改為透過 HTTP 從主控端取得版號（取代 NAS SMB 讀取）
- `routers/api_system.py` — `POST /api/v1/control/update` 傳遞 `master_server` URL 給更新腳本
- `update_agent.bat` — OTA 更新從 NAS xcopy 改為 HTTP 下載 + 解壓（PowerShell `Invoke-WebRequest`）
- `frontend/index.html` — 更新進度步驟文字：「從 NAS 同步」→「從伺服器下載」
- `frontend/app.js` — 更新 tooltip 與確認對話框文字

### 技術備註
- OTA 更新完全不依賴 NAS SMB，Agent 直接從主控端 `http://192.168.1.11:8000/download_update` 下載
- 更新 ZIP 排除 `python_embed/`、`ffmpeg.exe`、`ffprobe.exe`、`windows_helper/`，僅包含程式碼與前端
- 套件需求變動時（`0225_requirements.txt`），更新腳本會自動執行 `pip install`
- 主控端的 `master_server` 設定留空即可（不會從自己更新自己）

---

## [1.7.2] - 2026-03-18

### 修改
- `main.py` — 移除啟動時自動掛載 `\\192.168.1.132\Container` 的程式碼，節省約 10 秒啟動時間
- `routers/api_system.py` — NAS 版本檢查改從 `settings.json` 的 `nas_paths.ota_dir` 讀取路徑
- `routers/api_report.py` — 報表歷史索引路徑改從 `settings.json` 的 `nas_paths.web_report_dir` 讀取
- `routers/api_tts.py` — 聲音庫路徑改從 `settings.json` 的 `nas_paths.voice_dir` 讀取（含 legacy fallback）
- `core/report_job.py` — NAS Web 發佈路徑改從 `settings.json` 讀取，未設定時優雅跳過
- `publish_update.py` — OTA 發布路徑改從 `settings.json` 讀取，未設定時跳過 NAS 同步
- `Install_Originsun_Agent.bat` — 移除 NAS ZIP 路徑，一律使用 HTTP 下載
- `update_agent.bat` — 移除 NAS 路徑硬編碼
- `config.py` — `nas_paths` 預設設定新增 `ota_dir`、`web_report_dir`、`voice_dir` 三個欄位
- `tests/integration/test_api_validate_paths.py` — UNC 範例路徑更新

### 技術備註
- 所有原本硬編碼的 `\\192.168.1.132\Container\AI_Workspace\...` 路徑統一改由 `settings.json` 的 `nas_paths` 區塊管理
- 未設定 NAS 路徑時，相關功能（OTA、報表歷史、聲音庫）會優雅跳過而非報錯
- `server.py` 為遺留檔案，不在此次清理範圍

---

## [1.7.1] - 2026-03-18

### 修復
- `core_engine.py` — 修正 `self._logger` → `self.log` 屬性錯誤，導致轉檔任務失敗
- `core/worker.py` — 串帶設定（解析度/編碼/燒入時間碼）從 UI 讀取，不再硬編碼 720P/H.264
- `core/worker.py` — 報表設定（名稱/輸出資料夾/膠卷/技術規格/校驗碼）從 UI 讀取，不再硬編碼
- `core/worker.py` — transcode/concat/report 各階段獨立 try-catch 隔離，一步失敗不阻斷後續
- `core/worker.py` — 來源目錄不存在時 emit 警告 log 而非靜默跳過
- `frontend/tabs/report/report.js` — `copyPublicUrl` 新增 `execCommand('copy')` fallback，解決 HTTP 環境下 clipboard API 被瀏覽器攔截的問題
- `frontend/tabs/backup/backup.js` — 任務提交失敗時顯示 alert 錯誤提示

### 變更
- `core/schemas.py` — `BackupRequest` 新增串帶/報表設定欄位（向下相容）
- `frontend/tabs/report/report.js` — 移除「開啟資料夾」「HTML」「PDF」按鈕，僅保留「複製公開網址」

---

## [1.7.0] - 2026-03-18

### 新增
- `routers/api_system.py` — 新增 `POST /api/v1/validate_paths` 遠端路徑驗證 API
- `core/schemas.py` — 新增 `ValidatePathsRequest` Pydantic 模型
- `frontend/js/shared/utils.js` — 新增 `validateRemotePaths()` 共用前端函式
- `frontend/tabs/transcode/transcode.js` — 轉 Proxy dispatch 前驗證遠端主機路徑
- `frontend/tabs/concat/concat.js` — 串帶送出前驗證遠端主機路徑
- `routers/api_queue.py` — 佇列管理 API（列表、重排、緊急標記）
- `routers/api_job_history.py` — 任務歷史 API
- `frontend/tabs/projects/` — 專案總覽頁籤（機器狀態、佇列管理、Agent 管理）
- `core/state.py` — 任務狀態管理、併發控制、Job 生命週期
- `core/worker.py` — 重構：併發任務執行、多類型分派
- `tests/` — 完整測試套件（75 個測試：20 unit + 30 integration + 5 smoke + 20 e2e）
- `pytest.ini` — pytest 設定
- `TEST_INSTRUCTIONS.md` — 測試套件建置指令（17 個指令）
- `.github/workflows/test.yml` — CI 設定（GitHub Actions）

### 修改
- `core_engine.py` — 新增 MOCK_FFMPEG gate，支援測試模式
- `config.py` — 新增 agents、concurrency 預設設定
- `frontend/app.js` — 整合專案總覽頁籤、佇列管理 UI
- `frontend/index.html` — 新增專案總覽頁籤按鈕
- `main.py` — 掛載 api_queue、api_job_history 路由
- `CLAUDE.md` — 同步更新版本號、目錄結構、API 表格（v1.6.0 → v1.7.0）
- `TEST_REPORT.md` — 新增遠端路徑驗證測試結果（27/27 → 含新增 6 項）
- `FINISHING.md` — 修正 CHANGELOG 格式範例未包在程式碼區塊的問題

### 技術備註
- 遠端路徑驗證使用 `os.path.splitdrive()` + `os.path.exists()` 檢查磁碟機與路徑
- 前端 `validateRemotePaths()` 透過跨域 fetch 呼叫遠端 Agent API
- 備份功能不需驗證（一定在本機執行），僅轉 Proxy 和串帶需要
- 測試套件使用 pytest + pytest-asyncio + httpx（ASGI transport）+ Playwright（E2E）
- E2E 測試在 port 18000 啟動獨立 uvicorn 實例，設定 MOCK_FFMPEG=1

---

## [1.6.0] - 2026-03-16

### TTS 功能完善與正式上線

**新增**

- `tts_engine.py` — 智能文字前處理管線 `_prepare_gen_text()`
  - `_convert_numbers_in_text()`：阿拉伯數字轉中文讀法（百分比、年份、序數、長數字逐位讀）
  - `_num_to_zh()`：非負整數轉中文數字（支援萬/億位）
  - 句邊界注入：CJK 標點後自動加空格，幫助 F5-TTS 正確分塊
  - 句末標點保證：無標點時自動補句號
- `tts_engine.py` — 智能文字補齊 `_pad_text_for_inference()`
  - 偵測 F5-TTS 分塊後最後一塊是否過短（< 15 bytes）
  - 若過短則補充「…」虛擬文字，推論後自動修剪對應音訊
  - 解決聲音複製末端截斷問題
- `frontend/tabs/transcribe/` — 新增「📂 選擇資料夾」按鈕
  - 自動列出資料夾內所有影音檔案並加入來源清單

**修改**

- UI 用語統一：「克隆」→「複製」、「合成」→「生成」（前端 + 後端全面替換）
- 移除所有「🚧 開發中」標記：
  - `frontend/index.html` — TTS 頁籤按鈕的 amber 徽章與特殊配色
  - `frontend/tabs/tts/tts.html` — 頂部橘色警告橫幅
  - `frontend/app.js` — `switchTab()` 中 TTS 頁籤的 amber 特殊樣式邏輯
- `CLAUDE.md` — 同步更新版本號、目錄結構、用語（v1.5.0 → v1.6.0）

**技術備註**

- F5-TTS 的 `chunk_text()` 會將文字分塊，最後一塊若 < 10 bytes 會觸發 `local_speed=0.3` 覆寫，導致時長計算錯誤與音訊截斷。`_pad_text_for_inference()` 透過補齊虛擬文字避開此邊界條件。
- 阿拉伯數字對 F5-TTS 屬 OOD（訓練集外分佈），直接輸入會被跳過或亂讀。`_convert_numbers_in_text()` 在推論前將所有數字轉為中文讀法。
- 推論後修剪使用 byte 比例估算虛擬文字對應的音訊長度，`prepared_bytes` 在補齊前記錄，確保只修剪真正的虛擬文字音訊。

**影響範圍**

| 檔案 | 動作 |
|------|------|
| `tts_engine.py` | 修改（+300 行前處理邏輯） |
| `routers/api_tts.py` | 修改（用語：合成→生成） |
| `frontend/index.html` | 修改（移除開發中標記） |
| `frontend/app.js` | 修改（移除 TTS amber 特殊樣式） |
| `frontend/tabs/tts/tts.html` | 修改（移除警告橫幅、用語統一） |
| `frontend/tabs/tts/tts.js` | 修改（用語：克隆→複製、合成→生成） |
| `frontend/tabs/transcribe/transcribe.html` | 修改（新增資料夾按鈕） |
| `frontend/tabs/transcribe/transcribe.js` | 修改（新增 pickTranscribeFolder） |
| `version.json` | 修改（v1.6.0） |
| `CLAUDE.md` | 修改（版本號 + 內容同步） |
| `ROADMAP.md` | 修改（更新 Phase H 狀態） |

---

## 2026-03-16

### TTS 系統升級：XTTS v2 → F5-TTS + 台灣正音引擎

**變更項目**

- 移除 Coqui XTTS v2 語音克隆引擎，改用 F5-TTS（Flow Matching 零樣本克隆）
- 移除 `main.py` 頂部的 PyTorch `weights_only` monkey-patch（XTTS 專用，F5-TTS 不需要）
- 新增 torchcodec + torchaudio Windows 相容性修補（`tts_engine.py` 頂部）
  - patch `os.add_dll_directory` 跳過無效路徑（torchcodec `[WinError 87]`）
  - patch `torchaudio.load` 改用 soundfile（torchaudio 2.10 移除 backend 系統）
  - 新增 `_transcribe_ref_audio()` 用 faster-whisper 取代 transformers ASR pipeline
- 新增台灣正音引擎 `utils/taiwan_normalizer.py`
  - `normalize_for_taiwan_tts(text)` 依序套用 vocab_mapping + pronunciation_hacks
  - 最長優先替換，避免子字串衝突
  - 每次呼叫即時讀取字典，支援熱更新
- 新增 `taiwan_dict.json` 正音字典
  - 14 筆用語轉換（視頻→影片、軟件→軟體、質量→品質...）
  - 10 筆發音校正（垃圾→勒色、企鵝→氣鵝、角色→腳色...）
- 新增字典 API 端點（`GET/POST /api/v1/tts/dictionary`），支援前端即時編輯
- 新增 F5-TTS 克隆端點（`POST /api/v1/tts_jobs/clone`）
- 新增 F5-TTS 狀態端點（`GET /api/v1/tts/f5_status`）
- 移除舊 XTTS 端點：`/tts/xtts_status`、`/tts/xtts_download`、`/tts_jobs/profile`
- 前端 TTS 頁籤重構為 3 個子頁：
  - 📢 標準 TTS：Edge-TTS 聲音篩選 + 語速/音高 + 正音引擎開關
  - 🎙️ 聲音克隆：F5-TTS 狀態 + NAS 聲音庫 + 參考音訊拖放 + 進度條
  - 📖 正音字典：表格編輯器 + 新增/刪除行 + 儲存/重新載入
- 標準 TTS 合成路由加入正音引擎前處理
- 完成 24/24 項自動化測試（TEST_REPORT.md）
- 更新 CLAUDE.md 交接文件（§1、§3、§5、§7.8、§8、§10）

**影響範圍**

| 檔案 | 動作 |
|------|------|
| `main.py` | 修改（移除 PyTorch patch） |
| `tts_engine.py` | 重寫（Edge-TTS + F5-TTS + torchcodec 修補） |
| `routers/api_tts.py` | 重寫（移除 XTTS，新增 F5-TTS + 字典 API） |
| `frontend/tabs/tts/tts.html` | 重寫（3 子頁 UI） |
| `frontend/tabs/tts/tts.js` | 重寫（3 子頁邏輯，~400 行） |
| `taiwan_dict.json` | 新增 |
| `utils/__init__.py` | 新增 |
| `utils/taiwan_normalizer.py` | 新增 |
| `TEST_REPORT.md` | 新增（24/24 測試通過） |
| `CLAUDE.md` | 更新（XTTS→F5-TTS 全面同步） |

---

### 專案清理與文件校正

**變更項目**

- 移除死代碼：`app.js` 中 `report_ready` Socket.IO 監聽器（18 行）
  - 該事件後端從未 emit，功能已由 `report_job_done` 完整覆蓋
  - 同時清除不存在的 `badge_cloud_report` DOM 引用
- 移除空目錄：`frontend/tabs/proxy/`
  - Transcode 頁籤已涵蓋所有 Proxy 轉檔功能，此目錄為開發遺留物
- 清理根目錄 24 個測試/暫存檔案
  - 5 個測試腳本（`test_*.py`）
  - 9 個測試音檔（`test*.mp3`、`*.wav`）
  - 10 個暫存文字/日誌（`cmd_*.txt`、`stderr.log` 等）
- 重寫 `CLAUDE.md`，修正多處與程式碼不一致的內容
  - 修正 4 個錯誤的 API 路徑（如 `/api/v1/transcode_jobs` → `/api/v1/jobs/transcode`）
  - 更新 Socket.IO 事件清單（12 個，移除 `report_ready`）
  - 更新目錄結構（7 個頁籤，移除 `proxy/`）
  - 補充完整 API 端點表格（47 個端點）
  - 補充完整 Pydantic Schema 清單（14 個 class）
- 建立 `ROADMAP.md` 技術路線圖

**影響範圍**

| 檔案 | 動作 |
|------|------|
| `frontend/app.js` | 修改（移除 18 行） |
| `frontend/tabs/proxy/` | 刪除 |
| 根目錄 24 個測試檔 | 刪除 |
| `CLAUDE.md` | 重寫 |
| `ROADMAP.md` | 新增 |
