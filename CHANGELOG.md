# Originsun Media Guard Pro — 工作日誌

> 記錄每次開發變更，格式：日期 → 變更項目 → 影響範圍

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
