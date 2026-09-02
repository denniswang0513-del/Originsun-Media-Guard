/**
 * timesheet_sync.gs — 工時 Google Sheet → Originsun Media Guard 自動同步
 * （N2 階段 0；docs/TIMESHEET_IMPORT_PLAN.md Phase D）
 *
 * ── 安裝步驟（一次性，約 5 分鐘）──────────────────────────────
 * 1. 打開工時試算表 → 擴充功能 → Apps Script → 貼上本檔全部內容
 * 2. 改下方 CONFIG.TOKEN（管理員登入 → GET /api/v1/timesheets/ingest_token，或請 Claude 撈）
 *    分頁名與欄位已照 2026-09-02 的真表設好，除非表改版否則不用動
 * 3. 🔴 歷史列已由 scripts/import_timesheets.py 一次匯入 → 先執行一次
 *    executeSetMarkerToEnd（工具列 ▶）把兩個分頁的 marker 設到表尾，
 *    之後只送新列。（就算不設，後端以列內容 hash 去重，也不會重複入庫 —— 只是白送 9,800 列）
 * 4. 執行一次 syncNewRows → 首次會要求授權 → 允許；執行紀錄應顯示 inserted: 0
 * 5. 左側「觸發條件」→ 新增 → syncNewRows → 時間驅動 → 每小時
 *
 * ── 這張表的形狀（規劃 §1.1）────────────────────────────────
 * 真正的輸入面是兩個分頁，都從列 7 起、A–E＝日期/人員/專案/內容/製作時間(時)、底部追加：
 *   「工作紀錄表」    正職（本檔的值）
 *   「助理工作紀錄表」助理（另一本試算表的 IMPORTRANGE；getValues() 拿得到算好的值）
 * 🔴 不讀「總表（勿動）」—— 它是 ORDER BY 日期 DESC 的 QUERY，新列在最上面，
 *    「記到第幾列」的 marker 在它上面不成立。
 *
 * 運作方式：每個分頁各記一個 marker（Script Properties），每次只送 marker 之後的新列；
 * 後端以列內容 hash 去重，重跑／重疊都安全。改舊列不會自動重送 —— 改了歷史列就
 * executeResetMarkers 讓它全量重掃一次（後端冪等）。
 *
 * 日期送 yyyy/MM/dd 字串 —— 後端 hash 用的是字串，歷史匯入腳本也是同一格式（規劃 D9）。
 */
// ═══ CONFIG ═══════════════════════════════════════════════════
var CONFIG = {
  API_URL: 'https://foundry.originsun-studio.com/api/v1/timesheets/ingest',
  TOKEN: '貼上 ingest_token',        // ← GET /api/v1/timesheets/ingest_token
  SHEETS: [                          // 兩個輸入分頁；各自記 marker
    { name: '工作紀錄表',     startRow: 7 },
    { name: '助理工作紀錄表', startRow: 7 },
  ],
  COL: { DATE: 1, STAFF: 2, PROJECT: 3, TASK: 4, HOURS: 5 },   // A–E
  BATCH: 200,                        // 每次 POST 最多幾列
};
// ═════════════════════════════════════════════════════════════

function markerKey_(sheetName) { return 'omg_ts_marker_' + sheetName; }

/** 一個分頁：讀 marker 之後的新列 → 分批 POST → 成功才推進 marker。 */
function syncSheet_(cfg) {
  var sheet = SpreadsheetApp.getActive().getSheetByName(cfg.name);
  if (!sheet) throw new Error('找不到分頁: ' + cfg.name);
  var props = PropertiesService.getScriptProperties();
  var key = markerKey_(cfg.name);
  var lastSynced = parseInt(props.getProperty(key) || '0', 10);
  if (lastSynced < cfg.startRow - 1) lastSynced = cfg.startRow - 1;

  var lastRow = sheet.getLastRow();
  // 🔴 IMPORTRANGE 失連時分頁會整段空白 —— 那不是「沒有新列」，是資料不見了。
  //    列數比 marker 少就 throw，別靜靜略過（規劃 §8 風險）。
  if (lastRow < lastSynced) {
    throw new Error(cfg.name + ' 列數 ' + lastRow + ' 少於已同步的 ' + lastSynced + '（IMPORTRANGE 失連？）');
  }
  if (lastRow <= lastSynced) {
    Logger.log('%s：無新列（已同步到 %s / 表尾 %s）', cfg.name, lastSynced, lastRow);
    return 0;
  }
  var from = lastSynced + 1;
  var maxCol = Math.max(CONFIG.COL.DATE, CONFIG.COL.STAFF, CONFIG.COL.PROJECT, CONFIG.COL.TASK, CONFIG.COL.HOURS);
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
    rows.push({
      date: dateStr,
      staff: staff,
      project: String(v[CONFIG.COL.PROJECT - 1] || '').trim(),
      task: String(v[CONFIG.COL.TASK - 1] || '').trim(),
      hours: hours,
    });
  }
  for (var start = 0; start < rows.length; start += CONFIG.BATCH) {
    var batch = rows.slice(start, start + CONFIG.BATCH);
    var resp = UrlFetchApp.fetch(CONFIG.API_URL, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-Timesheet-Token': CONFIG.TOKEN },
      payload: JSON.stringify({ rows: batch, source: 'sheet' }),
      muteHttpExceptions: true,
    });
    var code = resp.getResponseCode();
    if (code !== 200) {
      // 失敗不推進 marker → 下次觸發整段重送（後端冪等）
      throw new Error(cfg.name + ' 同步失敗 HTTP ' + code + ': ' + resp.getContentText().slice(0, 300));
    }
    Logger.log('%s 批次 OK: %s', cfg.name, resp.getContentText().slice(0, 200));
  }
  props.setProperty(key, String(lastRow));
  Logger.log('%s：送 %s 列，marker → %s', cfg.name, rows.length, lastRow);
  return rows.length;
}

/** 觸發條件掛這支：兩個分頁各跑一次；一個失敗不擋另一個（各自的 marker 各自推進）。 */
function syncNewRows() {
  var errors = [];
  for (var i = 0; i < CONFIG.SHEETS.length; i++) {
    try { syncSheet_(CONFIG.SHEETS[i]); }
    catch (e) { errors.push(String(e)); Logger.log('ERROR %s', e); }
  }
  if (errors.length) throw new Error(errors.join(' | '));
}

/** 歷史列已用 import_timesheets.py 匯過 → 把 marker 設到表尾，之後只送新列。安裝時跑一次。 */
function executeSetMarkerToEnd() {
  var props = PropertiesService.getScriptProperties();
  for (var i = 0; i < CONFIG.SHEETS.length; i++) {
    var cfg = CONFIG.SHEETS[i];
    var sheet = SpreadsheetApp.getActive().getSheetByName(cfg.name);
    if (!sheet) throw new Error('找不到分頁: ' + cfg.name);
    props.setProperty(markerKey_(cfg.name), String(sheet.getLastRow()));
    Logger.log('%s marker → %s', cfg.name, sheet.getLastRow());
  }
}

/** 歷史列有修改時手動執行：重設 marker → 下次 syncNewRows 全量重掃（後端去重，安全）。 */
function executeResetMarkers() {
  var props = PropertiesService.getScriptProperties();
  for (var i = 0; i < CONFIG.SHEETS.length; i++) {
    props.deleteProperty(markerKey_(CONFIG.SHEETS[i].name));
  }
  Logger.log('兩個 marker 已重設，下次同步將全量重掃');
}
