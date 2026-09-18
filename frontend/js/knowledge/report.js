/**
 * report.js — 研究週報／月報的畫面（docs/KNOWLEDGE_BASE_PLAN.md §9.5）。
 *
 * owner 2026-09-18：「我想要有個週報或月報」＋「這些報表可以使用連結」。
 * 報告本身是後端產的 md 檔（services/knowledge_report.py），這裡只負責列清單與畫內容。
 *
 * 連結長這樣：`/knowledge.html#report/weekly-2026-W38` —— Discord 推的那則就是帶這個，
 * 所以 index.js 在 mount 時與 hashchange 時都要看一次 hash。
 *
 * 只 import ctx 與 md-lite（葉節點）；要回書架走 `nav`（同 chat.js／extend.js 的規矩）。
 */
import { mdToHtml } from '../shared/md-lite.js';
import { S, nav, api, esc, errText, alive, toast } from './ctx.js';

const KIND_LABEL = { weekly: '週報', monthly: '月報' };
/** 正在畫的那份報告 id。openReport 自己設 location.hash 也會觸發 index.js 的 hashchange 監聽
 *  （那時 S.book 已經是 null）→ openFromHash → 又 openReport 同一份 → 抓兩次、畫兩次
 *  （2026-09-19 /polish BUG-11）。看到同一份就不再開；回清單／回書架時清掉。 */
let _open = '';

function _shell(inner) {
    S.root.innerHTML = `
        <div class="kb-top">
            <button type="button" class="kb-link kb-back" data-kact="back">← 書架</button>
            <h2>研究報告</h2>
            <div class="why">研究助理每週找到的東西，週一整理成週報，每月一號整理成月報。
                同一份內容也存成 md 檔放在書架旁邊的 reports 資料夾。</div>
        </div>
        ${inner}`;
}

export function renderReports() {
    S.book = null; S.chapter = null;
    const rows = S.reports || [];
    _shell(rows.length
        ? `<div class="kb-shelf">${rows.map((r) => `
            <div class="kb-card" data-kact="report" data-id="${esc(r.id)}" role="button" tabindex="0">
                <div class="l"><div class="t">${esc(r.title)}</div>
                    <div class="s"><span class="a">${esc(KIND_LABEL[r.kind] || r.kind)}</span></div></div>
                <div class="r"><div class="m"><span>${Math.max(1, Math.round((r.bytes || 0) / 1024))} KB</span></div></div>
            </div>`).join('')}</div>`
        : `<div class="kb-empty">還沒有報告。研究助理收到東西之後，週一會產出第一份週報。</div>`);
}

export async function loadReports() {
    _open = '';
    if (location.hash.startsWith('#report/')) location.hash = '';
    try {
        S.reports = await api('/reports');
    } catch (e) {
        if (!alive()) return;
        S.reports = [];
        toast('讀報告失敗：' + errText(e), true);
    }
    if (alive()) renderReports();
}

export async function openReport(id) {
    try {
        const d = await api(`/reports/${encodeURIComponent(id)}`);
        if (!alive()) return;
        S.book = null; S.chapter = null;
        _open = id;
        location.hash = '#report/' + id;
        _shell(`<div class="kb-report kb-md">${mdToHtml(d.md || '')}</div>
            <button type="button" class="kb-totop" data-kact="to-top" aria-label="回到頂端">↑</button>`);
    } catch (e) {
        if (!alive()) return;
        toast('打不開這份報告：' + errText(e), true);
        loadReports();
    }
}

/** Discord 推的連結點進來時走這條；不是報告連結就回 false 讓書架照常畫。 */
export function openFromHash() {
    const m = /^#report\/([A-Za-z0-9-]+)$/.exec(location.hash || '');
    if (!m) return false;
    if (m[1] === _open) return true;        // 就是正在畫的這份（自己設 hash 觸發的那一下）
    openReport(m[1]);
    return true;
}

/** 從報告回書架時把 hash 清掉，不然重新整理又跳回報告。 */
export function leaveReports() {
    _open = '';
    if (location.hash.startsWith('#report/')) location.hash = '';
    if (nav.renderShelf) nav.renderShelf();
}
