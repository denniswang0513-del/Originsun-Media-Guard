/**
 * ts-projects.js — 工時的「專案表（burn）＋專案檔案」渲染（唯一正本）。
 *
 * 工作追蹤分頁（tabs/timesheets/timesheets.js）與員工工作台「專案查詢」（/my.html）都 import 這裡：
 * 專案表（專案／狀態／案型／已投入／預算／剩餘／消耗率／列數／最後填報，可排序）、
 * 專案檔案（總時數、人數、天數、預算與消耗率、分類組成、各人、各月、類似專案、時間軸），
 * 以及看板／時間軸／逐日共用的列片段（hoursLabel／typeTag／projLink／dayLog）。
 *
 * 主題：顏色走 CSS 變數 --tsp-*（預設深色）；白底頁在外層容器覆寫變數。
 * 動作一律用 data-ts-action（open-project／proj-pop／type-edit／budget／compare-add）——
 * 宿主自己委派：分頁全接、工作台只接 open-project／proj-pop（其餘鈕用 editable=false 不畫）。
 */
import { esc, ensureStyle } from './dom.js';
import { createSortable, sortableTh } from '../../tabs/crm/crm-utils.js';
import { hbars } from './svg-charts.js';

const CSS = `
.tsp { --tsp-ink:#eee; --tsp-sub:#888; --tsp-dim:#666; --tsp-tag:#9ca3af; --tsp-plan:#93c5fd; --tsp-warn:#f59e0b;
    --tsp-link:#93c5fd; --tsp-card:#232323; --tsp-line:#333; --tsp-row:#2c2c2c; --tsp-chip-ink:#aaa; --tsp-badge:#2c2c2c; --tsp-badge-ink:#9ca3af;
    --tsp-month:#262626; --tsp-head:#262626; }
.tsp-ink { color:var(--tsp-ink,#eee); } .tsp-sub { color:var(--tsp-sub,#888); } .tsp-dim { color:var(--tsp-dim,#666); }
.tsp-tag { color:var(--tsp-tag,#9ca3af); } .tsp-plan { color:var(--tsp-plan,#93c5fd); } .tsp-warn { color:var(--tsp-warn,#f59e0b); }
.tsp .ts-card { background:var(--tsp-card); border:1px solid var(--tsp-line); border-radius:8px; padding:14px 16px; margin-bottom:14px; }
.tsp .ts-card h3 { color:var(--tsp-ink); font-size:14px; margin:0 0 10px; font-weight:600; }
.tsp table { width:100%; border-collapse:collapse; font-size:12.5px; }
.tsp th { text-align:left; color:var(--tsp-sub); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--tsp-line); white-space:nowrap; }
.tsp td { padding:6px 8px; border-bottom:1px solid var(--tsp-row); color:var(--tsp-ink); }
.tsp td.num, .tsp th.num { text-align:right; font-variant-numeric:tabular-nums; }
.tsp .ts-chip { display:inline-block; background:var(--tsp-card); border:1px solid var(--tsp-line); border-radius:8px; padding:8px 14px; margin:0 8px 8px 0; font-size:12px; color:var(--tsp-chip-ink); }
.tsp .ts-chip b { color:var(--tsp-ink); font-size:16px; font-weight:600; margin-right:4px; }
.tsp .ts-link { color:var(--tsp-link); cursor:pointer; } .tsp .ts-link:hover { text-decoration:underline; }
.tsp .ts-badge { display:inline-block; margin-left:6px; padding:1px 6px; border-radius:4px; font-size:10.5px; background:var(--tsp-badge); color:var(--tsp-badge-ink); vertical-align:middle; }
.tsp .ts-badge.warn { background:#78350f; color:#fbbf24; }
.tsp .ts-pct { display:inline-block; min-width:64px; text-align:right; padding:2px 8px; border-radius:6px; font-weight:600; }
.tsp .ts-note { color:var(--tsp-sub); font-size:11.5px; margin-top:8px; line-height:1.6; }
.tsp table.ts-daylog th, .tsp table.ts-daylog td { text-align:left; vertical-align:top; }
.tsp table.ts-daylog th.num, .tsp table.ts-daylog td.num { text-align:right; }
.tsp tr.ts-month td { background:var(--tsp-month); }
.tsp .crm-sort-ind { font-size:10px; opacity:.6; margin-left:2px; }
`;
export function ensureTsProjectsStyle() { ensureStyle('ts-projects-css', CSS); }

// ── 日期 ──
const _WD = ['日', '一', '二', '三', '四', '五', '六'];
export function dayLabel(ymd) {
    const [y, m, d] = String(ymd || '').split('-').map(Number);
    if (!y) return '';
    return `${m}/${d}（${_WD[new Date(y, m - 1, d).getDay()]}）`;
}
/** YYYY-MM-DD ±n 天（本地日期，不走 toISOString）。工時分頁與員工頁同一份。 */
export function shiftDays(ymd, n) {
    const [y, m, d] = String(ymd || '').split('-').map(Number);
    const dt = new Date(y, m - 1, d + n);
    return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

// ── 一列的共用片段（看板卡片／週表格／時間軸／逐日都吃這幾支）──
export const isPlan = i => i.status === 'plan';                       // 後端 row_state 決定，這裡不重判
export function hoursLabel(i) {
    return isPlan(i)
        ? `<span class="tsp-plan">計畫 ${i.planned_hours} h</span>`
        : `<b class="tsp-ink">${i.hours} h</b>${i.planned_hours ? `<span class="tsp-dim"> ／計畫 ${i.planned_hours}</span>` : ''}`;
}
/** 「[分類 · 階段]」（owner §12：顯示「分類 · 階段」）。 */
export const typeTag = i => {
    const t = [i.work_type, i.stage_name].filter(Boolean).join(' · ');
    return t ? `<span class="tsp-tag">[${esc(t)}]</span> ` : '';
};
export const projLink = (name, pid) => (name || pid)
    ? `<span class="ts-link" data-ts-action="proj-pop" data-name="${esc(name || '')}" data-pid="${esc(pid || '')}" title="點開這個案的執行狀態">${esc(name || '(空白)')}</span>`
    : '<span class="tsp-sub">(空白)</span>';   // 沒案名沒 id 的列沒有東西可以看
export const srcTag = i => (i.source === 'manual' ? '' : '<span class="tsp-dim"> · Sheet</span>');

/** 逐日流水＝四欄表（日期、人員／專案、內容、備註、使用時數）；primary＝第二欄放什麼。同一天多列只在第一列印日期。 */
export function dayLogRows(days, primary) {
    return days.map(day => day.items.map((i, k) => `<tr>
        <td class="tsp-sub" style="white-space:nowrap;">${k === 0 ? esc(dayLabel(day.date)) : ''}</td>
        <td style="white-space:nowrap;">${primary === 'project_name' ? projLink(i.project_name, i.project_id) : `<span class="tsp-ink">${esc(i[primary] || '(空白)')}</span>`}</td>
        <td>${typeTag(i)}${esc(i.task_note || '')}</td>
        <td class="tsp-tag">${esc(i.remark || '')}</td>
        <td class="num" style="white-space:nowrap;">${hoursLabel(i)}</td>
    </tr>`).join('')).join('');
}
export function dayLogTable(bodyHtml, primary) {
    return `<table class="ts-daylog"><thead><tr><th style="width:110px;">日期</th><th style="width:160px;">${primary === 'project_name' ? '專案' : '人員'}</th><th>內容</th><th style="width:22%;">備註</th><th class="num" style="width:110px;">使用時數</th></tr></thead>
        <tbody>${bodyHtml}</tbody></table>`;
}
export function dayLog(days, primary) {
    return days.length ? dayLogTable(dayLogRows(days, primary), primary) : '<div class="tsp-dim">還沒有紀錄</div>';
}
/** 逐日流水按月分段（每月小計），專案時間軸用：整個案的執行狀態一路看到底。 */
export function dayLogByMonth(days, primary) {
    if (!days.length) return '<div class="tsp-dim">還沒有紀錄</div>';
    const groups = [];
    for (const day of days) {
        const m = day.date.slice(0, 7);
        if (!groups.length || groups[groups.length - 1].m !== m) groups.push({ m, days: [], hours: 0 });
        const g = groups[groups.length - 1];
        g.days.push(day);
        g.hours += day.items.reduce((a, i) => a + (i.hours || 0), 0);
    }
    // 一張表：每月一列小計當分隔，底下接那個月的逐日列
    return dayLogTable(groups.map(g => `<tr class="ts-month"><td colspan="5" style="padding:6px 8px;">
            <b class="tsp-ink">${esc(g.m)}</b><span class="tsp-sub"> ｜ ${g.days.length} 天 ｜ ${Math.round(g.hours * 10) / 10} h</span></td></tr>${dayLogRows(g.days, primary)}`).join(''), primary);
}

// ── 專案表（burn）──
export function pctClass(pct) {
    if (pct == null) return 'none';
    if (pct >= 100) return 'over';
    if (pct >= 90) return 'hi';
    if (pct >= 60) return 'mid';
    return 'low';
}
export function pctStyle(pct) {
    if (pct == null) return 'background:#2c2c2c;color:#999;';
    if (pct >= 100) return 'background:#7f1d1d;color:#fca5a5;';
    if (pct >= 90) return 'background:#78350f;color:#fbbf24;';
    if (pct >= 60) return 'background:#1e3a5f;color:#93c5fd;';
    return 'background:#064e3b;color:#6ee7b7;';
}

/** 點欄頭排序器（同 crm-utils.createSortable；getters 只寫這一份）。 */
export function createBurnSorter({ storageKey, panelId, onChange, defaultSort }) {
    return createSortable({
        storageKey, panelId, onChange,
        defaultSort: defaultSort || { key: '', dir: 'asc' },
        getters: {
            client: p => p.client || '',
            project: p => p.project_name || p.project_id || '',
            status: p => p.status || '',
            type: p => p.project_type || '',
            used: p => p.hours_used ?? '',
            budget: p => p.budget_hours ?? '',
            remaining: p => p.remaining ?? '',
            pct: p => p.pct ?? '',
            rows: p => p.rows ?? '',
            last: p => p.last_entry || '',
        },
    });
}

export const BURN_THEAD = `<tr>
    ${sortableTh('client', '客戶')}${sortableTh('project', '專案')}${sortableTh('status', '狀態')}${sortableTh('type', '案型')}${sortableTh('used', '已投入(h)', 'class="num"')}${sortableTh('budget', '預算(h)', 'class="num"')}
    ${sortableTh('remaining', '剩餘(h)', 'class="num"')}${sortableTh('pct', '消耗率', 'class="num"')}${sortableTh('rows', '列數', 'class="num"')}${sortableTh('last', '最後填報')}
</tr>`;

/** burn 表的案型格：editable＝點到才變下拉（工作追蹤）；否則純文字。 */
export function burnTypeCellHtml(p, editable) {
    const cur = p.project_type || '';
    if (!editable) return `<span class="${cur ? 'tsp-tag' : 'tsp-dim'}">${cur ? esc(cur) : '—'}</span>`;
    // 平時只畫文字，點到才變成下拉（317 列 × 11 個 option 畫一次就是幾千個節點）
    return `<span class="ts-link ${cur ? 'tsp-tag' : 'tsp-warn'}" data-ts-action="type-edit" data-pid="${esc(p.project_id)}" data-cur="${esc(cur)}"
                title="點一下改案型（預期毛利／建議預算照這個算）">${cur ? esc(cur) : '— 案型 —'} ▾</span>`;
}
function _budgetCell(p, editable) {
    if (p.budget_hours != null) return p.budget_hours;
    if (p.suggested_hours == null) return '<span class="tsp-dim">未設</span>';
    return editable
        ? `<span class="ts-link" data-ts-action="budget" data-pid="${esc(p.project_id)}" data-cur="${p.suggested_hours}" title="依私帳設定的預期毛利（${esc(p.project_type || '')}）與日成本算的建議，點一下就套用">建議 ${p.suggested_hours}</span>`
        : `<span class="tsp-dim" title="建議預算">建議 ${p.suggested_hours}</span>`;
}

/**
 * 專案表的表身。projects＝/summary 的 projects（已依排序器排好）；opts：
 *   editable（案型可改、建議預算可套用）、emptyText。
 */
export function burnTbodyHtml(projects, opts = {}) {
    const rows = (projects || []).map(p => `
        <tr>
            <td class="tsp-sub" style="white-space:nowrap;">${p.client ? esc(p.client) : '<span class="tsp-dim">—</span>'}</td>
            <td><span class="ts-link" data-ts-action="open-project" data-name="${esc(p.project_name || '')}" data-pid="${esc(p.project_id)}">${esc(p.project_name || p.project_id)}</span>
                ${p.stale ? '<span class="ts-badge warn" title="進行中但 7 天沒工時">停滯</span>' : ''}</td>
            <td class="tsp-sub">${esc(p.status || '')}</td>
            <td>${burnTypeCellHtml(p, opts.editable)}</td>
            <td class="num">${p.hours_used}</td>
            <td class="num">${_budgetCell(p, opts.editable)}</td>
            <td class="num${p.remaining != null && p.remaining < 0 ? ' neg' : ''}">${p.remaining ?? '—'}</td>
            <td class="num"><span class="ts-pct ${pctClass(p.pct)}" style="${pctStyle(p.pct)}">${p.pct != null ? p.pct + '%' : '—'}</span></td>
            <td class="num tsp-sub">${p.rows}</td>
            <td class="tsp-sub">${esc(p.last_entry || '')}</td>
        </tr>`).join('');
    return rows || `<tr><td colspan="10" class="tsp-dim" style="text-align:center;">${esc(opts.emptyText || '尚無已對映專案')}</td></tr>`;
}

export function burnTableHtml(tbodyHtml, id = 'ts-burn-table') {
    ensureTsProjectsStyle();
    return `<table id="${id}"><thead>${BURN_THEAD}</thead><tbody>${tbodyHtml}</tbody></table>`;
}

// ── 專案檔案 ──
/** [(label, hours, pct?)…] → 共用的水平佔比條（js/shared/svg-charts.hbars）。 */
export function bars(pairs, width) {
    // width＝viewBox 的設計寬；卡片比它窄時整張圖（含字）等比縮小，白底工作台的三欄卡只有 300 多 px，
    // 用預設 560 字會縮到 9px（owner 2026-09-05「小到看不見」）→ 呼叫端依卡寬傳 chartWidth
    return hbars((pairs || []).map(([label, value, pct]) => ({ label, value, pct })),
                 { width: width || 560, formatValue: v => `${v} h`, showPct: (pairs || []).some(p => p[2] != null), emptyText: '—' });
}

/**
 * 專案檔案（/timesheets/project 的回應）。opts：
 *   modal（沒有頁首）、head（非 modal 的頁首 html）、backHtml（回清單鈕）、
 *   editable（加入比較／改預算／匯出 CSV 鈕；工作台不畫）、compareNames（比較清單）。
 */
export function projectFileHtml(d, opts = {}) {
    ensureTsProjectsStyle();
    const pct = d.pct == null ? '—' : d.pct + '%';
    const key = d.project_id ? 'id:' + d.project_id : d.project_name;    // 比較清單的鍵：整個案用 id
    const compare = opts.compareNames || [];
    const inCompare = compare.includes(key);
    const statusPill = d.status ? `<span class="ts-badge" style="font-size:12px;padding:2px 8px;">${esc(d.status)}</span>` : (d.mapped ? '' : '<span class="ts-badge warn">未對映</span>');
    const sheetNames = (d.sheet_names || []).filter(n => n !== d.project_name);
    const similar = d.similar || [];
    const actions = opts.editable ? `
            <button class="ts-btn ghost" data-ts-action="compare-add" data-name="${esc(key)}">${inCompare ? '已在比較清單' : '加入比較'}</button>
            ${compare.length ? `<button class="ts-btn" data-ts-action="view" data-view="compare">並排比較（${compare.length}）</button>` : ''}
            ${d.mapped ? `<button class="ts-btn ghost" data-ts-action="budget" data-pid="${esc(d.project_id)}" data-cur="${d.budget_hours ?? d.suggested_hours ?? ''}">改預算</button>` : ''}
            <button class="ts-btn ghost" data-ts-action="export-project" data-name="${esc(d.project_name)}" data-pid="${esc(d.project_id || '')}">匯出 CSV</button>` : '';
    return `${opts.modal ? '' : (opts.head || '')}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">
            ${opts.modal ? '' : (opts.backHtml || '')}
            <b class="tsp-ink" style="font-size:15px;">${esc(d.project_name)}</b>${statusPill}
            <span style="flex:1;"></span>${actions}
        </div>
        ${sheetNames.length ? `<div class="ts-note" style="margin:0 0 10px;">Sheet 上的案名：${sheetNames.map(esc).join('、')}</div>` : ''}
        <div style="margin-bottom:12px;">
            <span class="ts-chip"><b>${d.total}</b>總時數</span>
            <span class="ts-chip"><b>${d.people}</b>人</span>
            <span class="ts-chip"><b>${d.span_days}</b>天（${esc(d.first || '—')} → ${esc(d.last || '—')}）</span>
            <span class="ts-chip"><b>${d.budget_hours ?? '—'}</b>預算 h　<span class="ts-pct ${pctClass(d.pct)}" style="${pctStyle(d.pct)}">${pct}</span></span>
            ${d.quote_days != null ? `<span class="ts-chip"><b>${d.quote_days}</b>報價人日（≈ ${d.quote_hours} h）</span>` : ''}
            ${d.suggested_hours != null ? `<span class="ts-chip" title="依私帳設定：合約未稅 ×（1−${esc(d.project_type || '')}預期毛利）÷ 日成本 × 每日工時"><b>${d.suggested_hours}</b>建議預算 h</span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;">
            <div class="ts-card" style="margin:0;"><h3>分類組成</h3>${bars(d.composition, opts.chartWidth)}</div>
            <div class="ts-card" style="margin:0;"><h3>各人</h3>${bars(d.by_person, opts.chartWidth)}</div>
            <div class="ts-card" style="margin:0;"><h3>各月</h3>${bars(d.by_month, opts.chartWidth)}</div>
            <div class="ts-card" style="margin:0;"><h3>類似專案（自動推薦，人再挑）</h3>
                ${similar.length ? similar.map(([n, sc]) => `<div style="display:flex;gap:8px;align-items:center;font-size:12px;margin:4px 0;">
                    <span class="ts-link" data-ts-action="open-project" data-name="${esc(n)}" style="flex:1;">${esc(n)}</span>
                    <span class="tsp-dim">${Math.round(sc * 100)}%</span>
                    ${opts.editable ? `<button class="ts-btn ghost" data-ts-action="compare-add" data-name="${esc(n)}" style="padding:2px 8px;">${compare.includes(n) ? '已加' : '加入比較'}</button>` : ''}</div>`).join('')
                : '<div class="tsp-dim">沒有像的案（同客戶／案名相似／時數量級接近）</div>'}
            </div>
        </div>
        <div class="ts-card" style="margin-top:14px;"><h3>時間軸（整個案，共 ${(d.timeline || []).length} 個有紀錄的日子）</h3>
            ${dayLogByMonth(d.timeline || [], 'staff_name')}
        </div>`;
}
