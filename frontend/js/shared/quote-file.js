// 報價單 PDF 的檔名（桌機報價分頁、專案頁報價子頁、手機版報價分頁共用，零 import 的葉節點）：
//   YYYYMMDD_客戶代稱_專案名_源日報價單.pdf
// 日期取 quote_date、沒有就今天（本地時區，別用 toISOString：台北早上八點前會差一天）；
// 空段落略過（不留兩個底線）；檔名禁字一律換 -
function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function quotePdfFilename(q, projectName = q.project_name, clientShort = q.client_short_name) {
    const d = ((q.quote_date || '').substring(0, 10) || today()).replace(/-/g, '');
    const stem = [d, clientShort || '', projectName || '', '源日報價單'].filter(Boolean).join('_');
    return stem.replace(/[\\/:*?"<>|]/g, '-') + '.pdf';
}
