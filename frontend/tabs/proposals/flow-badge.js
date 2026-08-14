/**
 * flow-badge.js — 清單每列的「階段 chip + 五格微型完成條」（§14.4 階段四B）。
 *
 * 清單掃一眼就要看得出**哪個案子哪一軌卡住**。所以畫的是五個小格子
 * （一軌一格、按完成比例填色），不是一條總進度 —— 總進度會把「企劃做完了但
 * 錢還沒收」跟「錢收了但東西沒交」壓成同一個數字，而那正是要分辨的東西。
 *
 * **只有畫面**。資料（抓 / 快取 / 排序值）在 `prop-list.js` —— 那個檔案在
 * 獨立企劃頁的清單首屏路徑上，而這裡要 utils.js（53KB），不能拖它下水。
 * 所以這支由**兩個渲染端**動態 import（它們本來就有 utils）。
 *
 * 判定完全不在前端：後端 `GET /crm/projects/flow/summary` 回每軌的 done/total，
 * 走的是詳情面板同一支 `_gather_facts` —— 清單顯示 3/5、點進去 2/5 是這個
 * 功能最容易失去信任的方式。
 *
 * 🔴 import closure 鐵則：只准 `tabs/proposals/` 與 `js/shared/`（見 flow-view）。
 * 🔴 UI 無 emoji（owner 2026-07-17 鐵則）。
 */
import { ensureStyle, esc } from '../../js/shared/utils.js';
import { TRACK_COLOR } from './prop-const.js';

const CSS = `
.pfb { display:inline-flex; align-items:center; gap:7px; white-space:nowrap; }
.pfb-stage { font-size:11px; padding:2px 8px; border-radius:999px;
    border:1px solid var(--pfb-line, #2e2e2e); color:var(--pfb-sub, #888); }
.pfb-stage.live { color:#fff; background:#1f538d; border-color:#1f538d; }
.pfb-stage.done { color:#9ccc65; background:#1d2a16; border-color:#3d5c2a; }
.pfb-stage.lost { color:#e88; background:#2a1618; border-color:#6e2b2b; }
html.plan-theme-light .pfb { --pfb-line:#e5e5e5; --pfb-sub:#737373; --pfb-cell:#e8e8e8; }
html.plan-theme-light .pfb-stage.done { color:#3f7a1f; background:#eef7e6;
    border-color:#cfe4bd; }
html.plan-theme-light .pfb-stage.lost { color:#b3261e; background:#fdecea;
    border-color:#f3c2bd; }
/* 一軌一格，格數改變不必動 CSS。填色由下往上長（像量杯），空的那格看得出來 */
.pfb-bars { display:inline-flex; gap:2px; }
.pfb-cell { width:8px; height:14px; border-radius:2px; overflow:hidden;
    background:var(--pfb-cell, #2a2a2a); display:flex; align-items:flex-end; }
.pfb-fill { width:100%; }
.pfb-none { font-size:11px; color:var(--pfb-sub, #888); }
`;

/** 階段 chip 的色階：未成案＝紅、歸檔＝已完成、其餘＝進行中。 */
function _stageCls(status) {
    if (status === '未成案') return 'lost';
    if (status === '歸檔') return 'done';
    return status ? 'live' : '';
}

/**
 * 一列的 HTML。`summary` 來自 `prop-list.flowOf(project_id)`。
 *
 * 沒有殼專案（或摘要還沒到）就畫一個「—」，**不畫空的格子** —— 空格子跟
 * 「五軌都掛零」長得一模一樣，那是謊報。
 */
export function flowCellsHtml(summary) {
    ensureStyle('pfb-css', CSS);
    if (!summary) return `<span class="pfb-none" title="尚未取得進度">—</span>`;
    const cells = (summary.tracks || []).map(t => {
        const pct = t.total ? Math.round((t.done / t.total) * 100) : 0;
        const color = TRACK_COLOR[t.key] || '#888';
        return `<span class="pfb-cell" title="${esc(t.label)} ${t.done}/${t.total}">`
             + `<span class="pfb-fill" style="height:${pct}%;background:${color}"></span>`
             + `</span>`;
    }).join('');
    const st = summary.status || '';
    return `<span class="pfb">`
         + (st ? `<span class="pfb-stage ${_stageCls(st)}">${esc(st)}</span>` : '')
         + `<span class="pfb-bars">${cells}</span></span>`;
}
