# Originsun Media Guard Pro — 工作日誌

> 記錄每次開發變更，格式：日期 → 變更項目 → 影響範圍

---

## 2026-03-16

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
