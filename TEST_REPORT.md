# Originsun Media Guard Pro v1.6.0 — 功能測試報告

> **測試日期**: 2026-03-18（更新）/ 2026-03-16（初版）
> **測試環境**: Windows 11 Pro, Python 3.11, AMD 5950X (CPU only, no GPU)
> **測試方式**: Claude Code 自動化 (HTTP API + 瀏覽器截圖 + Python 單元測試)
> **伺服器**: `http://localhost:8000` via `python main.py`

---

## 測試摘要

| 分類 | 通過 | 失敗 | 跳過 | 小計 |
|------|:----:|:----:|:----:|:----:|
| A. 伺服器啟動 | 2 | 0 | 0 | 2 |
| B. 系統 API | 5 | 0 | 0 | 5 |
| C. 標準 TTS (Edge-TTS) | 4 | 0 | 0 | 4 |
| D. 台灣正音引擎 | 3 | 0 | 0 | 3 |
| E. F5-TTS 聲音克隆 | 2 | 0 | 0 | 2 |
| F. 聲音庫 (NAS) | 1 | 0 | 0 | 1 |
| G. 前端 UI | 5 | 0 | 0 | 5 |
| H. 其他功能 API | 2 | 0 | 0 | 2 |
| I. 遠端路徑驗證 | 3 | 0 | 0 | 3 |
| **合計** | **27** | **0** | **0** | **27** |

**整體結果: 27/27 通過 (100%)**

---

## 詳細結果

### A. 伺服器啟動

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| A1 | `python main.py` 無 import error | ✅ | 所有模組正常載入，含新增的 `utils.taiwan_normalizer`、`f5_tts` |
| A2 | 前端頁面可存取 (`GET /`) | ✅ | HTTP 200，index.html 正常回傳 |

### B. 系統 API

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| B1 | Health check (`GET /api/v1/health`) | ✅ | `{"status":"ok","service":"OriginsunTranscode","busy":false,"host":"AMD5950X"}` |
| B2 | Queue status (`GET /api/v1/status`) | ✅ | `{"status":"online","busy":false,"queue_length":0,"paused":false,"logs":[]}` |
| B3 | Version (`GET /api/v1/version`) | ✅ | `{"version":"1.5.0","build_date":"2026-03-13"}` |
| B4 | Settings load (`GET /api/settings/load`) | ✅ | 回傳含 `notifications`, `message_templates`, `notification_channels`, `compute_hosts` |
| B5 | Whisper models status (`GET /api/v1/models/status`) | ✅ | `{"status":{}}` |

### C. 標準 TTS (Edge-TTS)

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| C1 | 取得聲音列表 (`GET /api/v1/tts/voices`) | ✅ | 322 個聲音，其中 zh-TW 有 3 個 (HsiaoChen, YunJhe, HsiaoYu) |
| C2 | 聲音預覽串流 (`GET /api/v1/tts/preview`) | ✅ | HTTP 200, 9,504 bytes MP3 音訊 |
| C3 | 時長估算 (`POST /api/v1/tts/estimate`) | ✅ | `{"duration_seconds":1.43,"chars_per_second":2.8,"char_count":4}` |
| C4 | 標準 TTS 合成 (`POST /api/v1/tts_jobs`) | ✅ | 產出 `test_edge_tts.mp3` (18,720 bytes) |

### D. 台灣正音引擎

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| D1 | 讀取字典 (`GET /api/v1/tts/dictionary`) | ✅ | 14 筆 `vocab_mapping` + 10 筆 `pronunciation_hacks` |
| D2 | 更新字典 (`POST /api/v1/tts/dictionary`) | ✅ | 寫入→讀回→驗證→還原，round-trip 全部成功 |
| D3 | 正音引擎替換正確性 (單元測試) | ✅ | 6/6 測試案例通過 |

**D3 詳細測試案例：**

| 輸入 | 預期輸出 | 結果 |
|------|---------|:----:|
| `視頻` | `影片` | ✅ |
| `這個視頻的質量很好` | `這個影片的品質很好` | ✅ |
| `垃圾分類很重要` | `勒色分類很重要` | ✅ |
| `請激活軟件` | `請啟用軟體` | ✅ |
| `Hello World` | `Hello World` | ✅ |
| *(空字串)* | *(空字串)* | ✅ |

### E. F5-TTS 聲音克隆

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| E1 | F5-TTS 可用性 (`GET /api/v1/tts/f5_status`) | ✅ | `{"available":true}` |
| E2 | 聲音克隆合成 (直接呼叫 `_f5_clone_sync`) | ✅ | 產出 `clone_direct.wav` (5,139,468 bytes) |

**E2 效能紀錄：**
- 參考音訊：`260312_113334_Tr1.WAV` (5.1 MB, 48kHz, 27秒→自動裁切至12秒)
- 合成文字：`你好，這是語音克隆的測試`
- 執行時間：~19 分鐘 (CPU only, 無 GPU)
- 模型：`F5TTS_v1_Base` (model_1250000.safetensors, ~1.2GB)

### F. 聲音庫 (NAS)

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| F1 | 列出聲音角色 (`GET /api/v1/voice_profiles`) | ✅ | 回傳 `[]`（NAS 尚無角色，API 正常） |

### G. 前端 UI

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| G1 | 首頁載入 + 7 個頁籤顯示 | ✅ | 備份、比對、Proxy、串帶、報表、逐字稿、語音合成 全部可見 |
| G2 | TTS Tab 三子頁切換 | ✅ | 📢 標準 TTS → 🎙️ 聲音克隆 → 📖 正音字典，切換正常 |
| G3 | 標準 TTS 子頁 UI | ✅ | 語系/區域/性別篩選、聲音選擇+試聽、語速/音高滑桿、正音開關、文字框、時長估算 |
| G4 | 聲音克隆子頁 UI | ✅ | "F5-TTS 已就緒" 綠色提示、NAS 聲音庫、參考音訊拖放區、正音開關 |
| G5 | 正音字典子頁 UI | ✅ | 用語轉換表格(14筆)、發音校正表格、新增/刪除行、重新載入/儲存字典按鈕 |

### H. 其他功能 API

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| H1 | 檔案列表 (`POST /api/v1/list_dir`) | ✅ | 掃描專案目錄，回傳 12 個影片檔案 |
| H2 | 任務控制 (`POST /api/v1/control/pause`) | ✅ | 無任務時回傳 `{"status":"paused"}`，端點正常 |

### I. 遠端路徑驗證 (`POST /api/v1/validate_paths`) — 2026-03-18 新增

> **用途**：轉 Proxy / 串帶指定遠端算力主機時，提交前先驗證路徑的磁碟機是否存在、路徑是否可存取

**測試條件：**

| 項目 | 路徑 |
|------|------|
| CARD A（來源） | `U:\20260205_寬微廣告_影片中文化\09_Export\01_Check` |
| CARD B（來源） | `R:\ProjectYaun\20260304_幾莫_鴻海科技獎短影音\09_Export\01_Check` |
| 目的地 | `D:\Antigravity\OriginsunTranscode\test_zone` |

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| I1 | 驗證存在路徑 — CARD_A (`U:`) + CARD_B (`R:`) + DEST (`D:`) | ✅ | 三個路徑 `drive_exists=true`, `path_exists=true` |
| I2 | 驗證不存在磁碟機 — `X:\fake_path` + `Z:\nonexistent` | ✅ | 兩個路徑 `drive_exists=false`，正確回報錯誤 |
| I3 | 混合驗證 — 存在路徑 + 不存在磁碟機 + 不存在路徑 | ✅ | `U:` 路徑✅、`Z:\fake` 磁碟機不存在❌、`D:` 目的✅、`C:\NonExistent999` 路徑不存在❌ |

**前端整合測試（瀏覽器 JS console）：**

| # | 測試項 | 結果 | 備註 |
|---|--------|:----:|------|
| I-FE1 | `validateRemotePaths('localhost:8001', ['Z:\\test', 'C:\\Windows'])` | ✅ | `ok: false`, errors: `["磁碟機 Z: 不存在"]` |
| I-FE2 | `validateRemotePaths('localhost:8001', ['C:\\NonExistentFolder12345'])` | ✅ | `ok: false`, errors: `["路徑不存在: C:\\NonExistentFolder12345"]` |
| I-FE3 | `validateRemotePaths('localhost:8001', ['C:\\Windows', 'C:\\Users'])` | ✅ | `ok: true` |

**涉及檔案：**
- `core/schemas.py` — 新增 `ValidatePathsRequest`
- `routers/api_system.py` — 新增 `POST /api/v1/validate_paths`
- `frontend/js/shared/utils.js` — 新增 `validateRemotePaths()` 共用函式
- `frontend/tabs/transcode/transcode.js` — `submitTranscode()` dispatch 前驗證
- `frontend/tabs/concat/concat.js` — `submitConcat()` 送出前驗證

---

## 測試中修復的 Bug

| # | 問題 | 檔案 | 修復方式 |
|---|------|------|---------|
| 1 | F5TTS 建構參數 `model_type` 已重命名為 `model` | `tts_engine.py` | 改為 `F5TTS(model="F5TTS_v1_Base", ...)` |
| 2 | torchcodec `os.add_dll_directory('.')` 在 Windows 崩潰 | `tts_engine.py` | patch `os.add_dll_directory` 捕捉 OSError |
| 3 | torchaudio 2.10 移除 `_backend` 子模組 | `tts_engine.py` | patch `torchaudio.load` 改用 soundfile |
| 4 | F5-TTS 內建 ASR (transformers pipeline) 觸發 torchcodec | `tts_engine.py` | 使用 faster-whisper 自行轉錄參考音訊 |

---

## 未測試項目（需人工或特殊環境）

| 項目 | 原因 |
|------|------|
| 備份 (`POST /api/v1/jobs`) | 需要記憶卡來源目錄 + NAS |
| 轉檔 (`POST /api/v1/jobs/transcode`) | 需要影片素材 |
| 串接 (`POST /api/v1/jobs/concat`) | 需要多個影片 |
| 驗證 (`POST /api/v1/jobs/verify`) | 需要已備份的對照目錄 |
| Whisper 語音辨識 | 需下載模型 (~3GB) |
| 視覺報表 | 需要影片素材 + NAS |
| 資料夾選擇器 (`pick_folder` / `pick_file`) | 需 GUI 互動 (tkinter) |
| OTA 更新 | 需 NAS 上有新版本 |
| 通知系統 | 需 Google Chat Webhook / LINE Token |
| 分散式轉檔 | 需多台代理端電腦 |
| 遠端路徑驗證（端對端） | 需多台代理端電腦，本次僅驗證 API + 前端函式 |

---

## 結論

**27/27 項自動化測試全部通過。** 測試過程中發現並即時修復了 4 個 Bug，主要來自 F5-TTS 安裝時升級的 torchaudio 2.10 / torchcodec 在 Windows 上的相容性問題。

重點驗證成果：
- ✅ Edge-TTS 標準語音合成正常
- ✅ F5-TTS 聲音克隆功能正常（CPU 推論約 19 分鐘）
- ✅ 台灣正音引擎字典替換準確（6/6 單元測試通過）
- ✅ 字典 API 熱更新機制正常（write → read-back 一致）
- ✅ 前端三子頁 UI 完整且切換正常
- ✅ 所有系統 API 端點回應正確
- ✅ 遠端路徑驗證 API + 前端整合正常（3+3 項測試通過，2026-03-18 新增）
