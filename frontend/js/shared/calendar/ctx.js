// ────────────────────────────────────────────────────────────────────────────
// calendar/ctx.js — 行事曆共用元件的狀態、端點、日期小工具（docs/CALENDAR_PLAN.md §5.1）。
// 三個宿主（公布欄／專案詳情／手機）掛同一份：mountCalendar({ host, fetch, scope, hooks })。
// 字彙、顏色全部從 /api/v1/calendar/options 與 /events 帶，這裡不寫死任何一個狀態字或色碼。
// ────────────────────────────────────────────────────────────────────────────

export const API = '/api/v1/calendar';

/** 模組層單例（一個 document 一份；宿主換頁重新 mount 會覆蓋）。 */
export const z = {
    host: null, fetch: null, hooks: {}, esc: (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    s: {
        view: 'month',          // month／week／day
        weekMode: 'time',       // 週檢視：time（時間軸）／people（人員列）
        scope: 'all',           // all／me／project:<id>
        lockedScope: '',        // 專案詳情宿主鎖 project:<id>
        anchor: '',             // 目前看的日期（月：該月任一天；週：那週任一天；日：那天）
        events: [], me: {}, colors: null, options: null,
        busy: false,
    },
};

export const pad = (n) => String(n).padStart(2, '0');
export const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const today = () => iso(new Date());
export const parse = (s) => new Date(s + 'T00:00:00');
export const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
export const mondayOf = (s) => addDays(s, -((parse(s).getDay() + 6) % 7));
export const monthStart = (s) => s.slice(0, 8) + '01';
export const monthEnd = (s) => { const d = parse(monthStart(s)); d.setMonth(d.getMonth() + 1); d.setDate(0); return iso(d); };
export const DOW = ['日', '一', '二', '三', '四', '五', '六'];
export const md = (s) => s ? `${+s.slice(5, 7)}/${+s.slice(8, 10)}` : '';
export const mdw = (s) => s ? `${md(s)}（${DOW[parse(s).getDay()]}）` : '';

/** 這個 view 要抓的日期範圍 [from, to]（月＝格子從週一到週日補滿）。 */
export function range() {
    const a = z.s.anchor || today();
    if (z.s.view === 'day') return [a, a];
    if (z.s.view === 'week') { const m = mondayOf(a); return [m, addDays(m, 6)]; }
    const m0 = monthStart(a), m1 = monthEnd(a);
    return [mondayOf(m0), addDays(mondayOf(m1), 6)];
}

/** 事件的顏色（hex）：拍攝／工作種類／里程碑／休假，全部照後端那張表。 */
export function colorOf(ev) {
    const hex = (z.s.colors && z.s.colors.hex) || {};
    if (ev.kind === 'shoot') return hex.shoot || '#f5511d';
    if (ev.kind === 'schedule') return hex[ev.sub_kind] || hex.work || '#3f51b5';
    if (ev.kind === 'plan') return hex.work || '#3f51b5';
    if (ev.kind === 'milestone') return hex.milestone || '#8e24aa';
    if (ev.kind === 'leave') return hex.leave || '#616161';
    return '#9aa0a8';
}
export const KIND_LABEL = { shoot: '拍攝', schedule: '工作', plan: '計畫', milestone: '里程碑', leave: '休假', holiday: '假日' };
export function kindLabel(ev) {
    if (ev.kind === 'schedule') return ({ work: '工作', meeting: '會議', out: '外出', other: '其他' })[ev.sub_kind] || '工作';
    return KIND_LABEL[ev.kind] || ev.kind;
}
/** 一件事涵蓋哪幾天（多日事件月格每天都畫）。 */
export function daysOf(ev) {
    const out = [];
    let d = ev.date;
    const end = ev.end_date && ev.end_date > ev.date ? ev.end_date : ev.date;
    for (let i = 0; i < 60 && d <= end; i++) { out.push(d); d = addDays(d, 1); }
    return out;
}
export const timeLabel = (ev) => ev.start_time ? ev.start_time + (ev.end_time ? '–' + ev.end_time : '') : '';

export async function api(path, opts) { return z.fetch(API + path, opts); }

/** 抓目前範圍的事件（帶 me／colors）。 */
export async function loadEvents() {
    const [from, to] = range();
    const scope = z.s.lockedScope || z.s.scope;
    const d = await api(`/events?from=${from}&to=${to}&scope=${encodeURIComponent(scope)}`);
    z.s.events = d.events || [];
    z.s.me = d.me || {};
    z.s.colors = d.colors || z.s.colors;
    return d;
}
export async function loadOptions(force = false) {
    if (z.s.options && !force) return z.s.options;
    z.s.options = await api('/options');
    if (z.s.options.colors) z.s.colors = z.s.options.colors;
    return z.s.options;
}
