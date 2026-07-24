// journal-core.js — 週工作日誌共用核心（內部分頁 tabs/journal/journal.js 與
// 官網風獨立頁 /journal.html 共同 import；殺掉四問標籤/週期運算/序列化/渲染/API 的鏡像複製）。
// my.html 已改為 iframe 內嵌 /journal.html?embed=1（2026-07-24）——**已無鏡像**，
// 四問文案／週區間格式改這裡即可，不要再為它另建一份。
// 第三個 module 消費者出現時，再考慮把各頁的 fetch 流程/分頁控制器也提上來。
import { authFetch } from './utils.js';

// esc/debounce 單一正本在 CRM utils（經 website-utils 轉出）— 這裡 re-export
// 讓兩個消費端只需 import 本模組，不用各自跨到 website tab。
export { esc, debounce } from '../../tabs/website/website-utils.js';
import { esc as _esc } from '../../tabs/website/website-utils.js';

// 四問標籤。key 集合必須與後端 api_journal._SECTION_MODELS 一致（有 pytest 守衛）
// —— 這裡漏一個後端 key，儲存時 PUT 少該欄 → 後端當空陣列 → 該區被靜默清空。
export const BLOCKS = [
    ['wins', '順利的事與想感謝的人'],
    ['challenges', '遇到哪些挑戰'],
    ['learnings', '學到了什麼'],
    ['others', '其他主題'],
];

// 副標（兩個頁面共用；問數與標籤由 BLOCKS 導出 — 加第五問自動跟上）
export const SUBTITLE = `每週${'一二三四五六七八九'[BLOCKS.length - 1]}問：`
    + `${BLOCKS.map(([, l]) => l).join('、')}。一行一條，週一起算。`;

// 可編輯窗文案（後端規則：當週可編輯至下一週；PUT 超窗回 403）
export const HINT_EDIT_WINDOW = '僅能編輯至下一週';
export const MSG_EDIT_WINDOW = '已超出可編輯期間（僅能編輯至下一週）';

// ── 週期 helpers（週=週一起算，日期一律當地時區手動組字避免 UTC 偏移） ──
const pad2 = (n) => String(n).padStart(2, '0');
const parseISO = (s) => { const [y, m, d] = String(s).split('-').map(Number); return new Date(y, m - 1, d); };
const isoDate = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

export function thisWeekStart() {
    const now = new Date();
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));   // Mon=0
    return isoDate(d);
}

export function shiftWeek(iso, weeks) {
    const d = parseISO(iso); d.setDate(d.getDate() + weeks * 7); return isoDate(d);
}

// 週區間標題：`YYYY/MM/DD – MM/DD`（週一至週日；同年省略右側年份，跨年顯示完整）
export function weekRange(weekStartISO) {
    const s = parseISO(weekStartISO);
    const e = new Date(s.getFullYear(), s.getMonth(), s.getDate() + 6);
    const left = `${s.getFullYear()}/${pad2(s.getMonth() + 1)}/${pad2(s.getDate())}`;
    const right = (s.getFullYear() === e.getFullYear() ? '' : `${e.getFullYear()}/`)
        + `${pad2(e.getMonth() + 1)}/${pad2(e.getDate())}`;
    return `${left} – ${right}`;
}

// ── textarea「一行一條」↔ API 陣列（PUT body 契約，後端 clean_entries 為權威）──
export const linesToItems = (text) =>
    String(text || '').split('\n').map(s => s.trim()).filter(Boolean);
export const itemsToLines = (arr) => (arr || []).join('\n');

// ── 共用判斷 / 渲染（class 由各頁傳入 — SPA 深色 jr-* / 官網風白底各自的語彙）──
export const isAuthFail = (...rs) =>
    rs.some(r => r && (r.status === 401 || r.status === 403));

// ── 貼圖 token → <img>：正本在 paste-image.js（上傳與渲染同一契約的兩半）——
// 這裡 re-export 讓週誌兩個消費端維持單一 import 來源（同 esc/debounce 慣例）。
export { ensurePasteBase, renderRich } from './paste-image.js';
import { renderRich as _renderRich } from './paste-image.js';

export function blockList(label, arr, blockCls) {
    if (!arr || !arr.length) return '';
    return `<div class="${blockCls}"><h4>${_esc(label)}</h4><ul>${arr.map(x => `<li>${_renderRich(x)}</li>`).join('')}</ul></div>`;
}

// c = {card, name, empty, block} class 名；title 由呼叫端決定（人名或週區間）並自行 esc
export function personCard(j, title, c) {
    const empty = BLOCKS.every(([k]) => !(j[k] || []).length);
    return `<div class="${c.card}"><div class="${c.name}">${title}</div>
        ${empty ? `<div class="${c.empty}">（空白）</div>` : BLOCKS.map(([k, label]) => blockList(label, j[k], c.block)).join('')}
    </div>`;
}

// ── API（帶 Bearer；fetch 層網路錯誤回 {ok:false, status:0} sentinel，
//    消費端一律先查 ok/isAuthFail 再 json()；status 0 = 連線失敗可顯專屬文案）──
const _q = (start) => start ? '?start=' + encodeURIComponent(start) : '';
const _safe = (p) => p.catch(() => ({ ok: false, status: 0 }));

export const api = {
    mine: (start) => _safe(authFetch('/api/v1/journal/mine' + _q(start))),
    saveMine: (start, body) => _safe(authFetch('/api/v1/journal/mine' + _q(start), { method: 'PUT', body })),
    week: (start) => _safe(authFetch('/api/v1/journal/week' + _q(start))),
    // 空字串/null 參數自動剔除 — 呼叫端直接把 state 丟進來即可
    learnings: (params) => _safe(authFetch('/api/v1/journal/learnings?' + new URLSearchParams(
        Object.fromEntries(Object.entries(params).filter(([, v]) => v !== '' && v != null))).toString())),
    people: () => _safe(authFetch('/api/v1/journal/people')),
    person: (username) => _safe(authFetch('/api/v1/journal/person?username=' + encodeURIComponent(username))),
};
