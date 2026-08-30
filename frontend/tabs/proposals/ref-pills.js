/**
 * ref-pills.js — 片庫卡片的狀態 pill（總覽獨立頁與 SPA 片庫 tab 共用一份）
 * ---
 * 用詞定案（docs/REFERENCE_LIBRARY.md §12.7，owner 指定）：
 * - 「已建檔」＝影片已封存進 NAS（archive_status）
 * - 研究旗標（curated）改叫「研究完成／研究中」—— 原本它叫「已建檔」，與封存撞名
 *
 * 兩個頁面的 .pill / .pill.ok / .pill.used 樣式類名相同，這裡只產 HTML。
 */

// 逃脫走 dom.js（理由同上：textContent 不逃脫引號）
import { esc } from '../../js/shared/dom.js';

// archive_status → 顯示（空字串 = 還沒排到，不顯示 pill 以免整庫都掛「待建檔」噪音）
export const ARCHIVE_LABEL = {
    done: { text: '已建檔', cls: 'ok' },
    downloading: { text: '建檔中…', cls: '' },
    pending: { text: '待建檔', cls: '' },
    retry: { text: '建檔重試中', cls: 'warn' },
    failed: { text: '下載失敗', cls: 'err' },      // 衍生狀態：retry 連敗（archiveLabelFor 判定）
    unavailable: { text: '原連結已失效', cls: 'err' },
    excluded: { text: '不建檔', cls: '' },
};

// 與 services/reference_archiver._ALERT_TRIES 同步：連敗 3 次（含 yt-dlp 自救重試）
// 已發過告警 —— 卡片也要轉紅「下載失敗」，別再用溫和的「建檔重試中」蓋住問題。
const FAILED_TRIES = 3;

/** archive 狀態 → 顯示標籤（retry 連敗升級成「下載失敗」的唯一判定點）。 */
export function archiveLabelFor(r) {
    if (r.archive_status === 'retry' && (r.archive_tries || 0) >= FAILED_TRIES)
        return ARCHIVE_LABEL.failed;
    return ARCHIVE_LABEL[r.archive_status];
}

/** 卡片 foot 區的 pill 列（研究狀態 + 建檔狀態 + 引用數 + 前幾個分類）。 */
export function refPills(r) {
    const out = [];
    out.push(r.curated ? '<span class="pill ok">研究完成</span>'
                       : '<span class="pill">研究中</span>');
    const a = archiveLabelFor(r);
    if (a) out.push(`<span class="pill ${a.cls}">${a.text}</span>`);
    if (r.links_count) out.push(`<span class="pill used">被引用 ${r.links_count}</span>`);
    [].concat(r.facets?.category || [], r.facets?.technique || []).slice(0, 3)
        .forEach(f => out.push(`<span class="pill">${esc(f)}</span>`));
    return out.join('');
}
