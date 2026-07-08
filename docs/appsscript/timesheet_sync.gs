/**
 * timesheet_sync.gs — 工時 Google Sheet → Originsun Media Guard 自動同步
 * （N2 階段 0，藍圖 §3.6：團隊照常填 Sheet、系統自動吸資料）
 *
 * ── 安裝步驟（一次性，約 5 分鐘）──────────────────────────────
 * 1. 打開工時試算表 → 擴充功能 → Apps Script → 貼上本檔全部內容
 * 2. 改下方 CONFIG：SHEET_NAME、欄位位置（A=1, B=2, ...）、TOKEN
 *    - TOKEN 到後台取：管理員登入系統 → GET /api/v1/timesheets/ingest_token
 *      （或請 Claude 撈給你）
 * 3. 執行一次 syncNewRows（工具列 ▶）→ 首次會要求授權 → 允許
 *    - 看執行紀錄：顯示「inserted: N」代表通了
 * 4. 左側「觸發條件」→ 新增 → syncNewRows → 時間驅動 → 每小時
 * 5. 完成。之後每小時自動把新列送進系統，重複列後端自動去重（冪等）。
 *
 * 運作方式：用 Script Properties 記「已同步到第幾列」，每次只送新列；
 * 就算重跑/重疊，後端以列內容 hash 去重，不會重複入庫。
 * 改舊列不會自動重送 — 改了歷史列就把 LAST_ROW_KEY 重設（executeResetMarker）
 * 讓它全量重掃一次（後端冪等，安全）。
 */

// ═══ CONFIG — 依你的表調整 ═══════════════════════════════════
var CONFIG = {
  API_URL: 'https://foundry.originsun-studio.com/api/v1/timesheets/ingest',
  TOKEN: '貼上 ingest_token',        // ← GET /api/v1/timesheets/ingest_token
  SHEET_NAME: '工作紀錄',            // ← 分頁名稱
  START_ROW: 2,                      // 資料起始列（跳過表頭）
  COL: {                             // 欄位位置（A=1, B=2, C=3 ...）
    DATE: 2,                         // 日期
    STAFF: 3,                        // 員工
    PROJECT: 4,                      // 專案
    TASK: 6,                         // 工作內容
    HOURS: 7,                        // 時數
    BUDGET: 12,                      // 專案預算時數（沒有就設 0 跳過）
  },
  BATCH: 200,                        // 每次 POST 最多幾列
};
// ═════════════════════════════════════════════════════════════

var LAST_ROW_KEY = 'omg_ts_last_synced_row';

function syncNewRows() {
  var sheet = SpreadsheetApp.getActive().getSheetByName(CONFIG.SHEET_NAME);
  if (!sheet) throw new Error('找不到分頁: ' + CONFIG.SHEET_NAME);

  var props = PropertiesService.getScriptProperties();
  var lastSynced = parseInt(props.getProperty(LAST_ROW_KEY) || '0', 10);
  if (lastSynced < CONFIG.START_ROW - 1) lastSynced = CONFIG.START_ROW - 1;

  var lastRow = sheet.getLastRow();
  if (lastRow <= lastSynced) {
    Logger.log('無新列（已同步到 %s / 表尾 %s）', lastSynced, lastRow);
    return;
  }

  var maxCol = Math.max(CONFIG.COL.DATE, CONFIG.COL.STAFF, CONFIG.COL.PROJECT,
                        CONFIG.COL.TASK, CONFIG.COL.HOURS, CONFIG.COL.BUDGET || 1);
  var from = lastSynced + 1;
  var values = sheet.getRange(from, 1, lastRow - lastSynced, maxCol).getValues();

  var rows = [];
  for (var i = 0; i < values.length; i++) {
    var v = values[i];
    var hours = parseFloat(v[CONFIG.COL.HOURS - 1]) || 0;
    var staff = String(v[CONFIG.COL.STAFF - 1] || '').trim();
    if (!staff && !hours) continue;   // 空列跳過
    var d = v[CONFIG.COL.DATE - 1];
    var dateStr = (d instanceof Date)
        ? Utilities.formatDate(d, 'Asia/Taipei', 'yyyy/MM/dd')
        : String(d || '').trim();
    var budget = CONFIG.COL.BUDGET
        ? (parseFloat(v[CONFIG.COL.BUDGET - 1]) || null) : null;
    rows.push({
      date: dateStr,
      staff: staff,
      project: String(v[CONFIG.COL.PROJECT - 1] || '').trim(),
      task: String(v[CONFIG.COL.TASK - 1] || '').trim(),
      hours: hours,
      budget: budget,
    });
  }

  var sentUpTo = lastSynced;
  for (var start = 0; start < rows.length; start += CONFIG.BATCH) {
    var batch = rows.slice(start, start + CONFIG.BATCH);
    var resp = UrlFetchApp.fetch(CONFIG.API_URL, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-Timesheet-Token': CONFIG.TOKEN },
      payload: JSON.stringify({ rows: batch }),
      muteHttpExceptions: true,
    });
    var code = resp.getResponseCode();
    if (code !== 200) {
      // 失敗不推進 marker → 下次觸發整段重送（後端冪等）
      throw new Error('同步失敗 HTTP ' + code + ': ' + resp.getContentText().slice(0, 300));
    }
    Logger.log('批次 OK: %s', resp.getContentText().slice(0, 200));
  }

  sentUpTo = lastRow;
  props.setProperty(LAST_ROW_KEY, String(sentUpTo));
  Logger.log('同步完成：送 %s 列，marker → %s', rows.length, sentUpTo);
}

/** 歷史列有修改時手動執行：重設 marker → 下次 syncNewRows 全量重掃（後端去重，安全）。 */
function executeResetMarker() {
  PropertiesService.getScriptProperties().deleteProperty(LAST_ROW_KEY);
  Logger.log('marker 已重設，下次同步將全量重掃');
}
