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

// 條目旗標（owner 2026-09-05 §14：求助條目大家都看得到）。只有「挑戰」「其他」兩區可標。
export const FLAGS = [['help', '需要協助'], ['discuss', '想討論']];
export const FLAG_LABEL = Object.fromEntries(FLAGS);
export const FLAGGABLE = new Set(['challenges', 'others']);
// 回覆掛在哪張表：後端 journal_replies.entry_table（journal_wins／journal_challenges／…）
export const entryTable = (k) => 'journal_' + k;

/** 一區的條目，統一成物件：後端新版回 entries.{k}=[{id, content, project_id, flag}]；舊版只有字串陣列。 */
export function entriesOf(j, k) {
    const rich = j && j.entries && Array.isArray(j.entries[k]) ? j.entries[k] : null;
    if (rich) return rich.map(e => (typeof e === 'string' ? { id: '', content: e, project_id: '', project_name: '', flag: '' }
        : { id: e.id || '', content: e.content || '', project_id: e.project_id || '', project_name: e.project_name || '', flag: e.flag || '' }));
    return ((j && j[k]) || []).map(s => ({ id: '', content: String(s || ''), project_id: '', project_name: '', flag: '' }));
}

/** 某條目的主管回覆（entry_table 認 journal_<k> 或 <k> 兩種寫法）。 */
export function repliesFor(j, k, entryId) {
    if (!entryId) return [];
    return ((j && j.replies) || []).filter(r => r.entry_id === entryId && (r.entry_table === entryTable(k) || r.entry_table === k));
}

/** 自動區「上週做了什麼」：worklog=[{project_id, project_name, days:[{date, items:[{note, work_type, stage_name}]}]}]（不含小時）。
 *  c.wl／c.wlProj／c.wlDay 是 class。（「帶入」勾選 2026-09-07 owner 拿掉） */
export function worklogHtml(worklog, c = {}) {
    const list = worklog || [];
    if (!list.length) return `<div class="${c.empty || ''}">（這週沒有專案紀錄）</div>`;
    const md = (iso) => { const [, m, d] = String(iso || '').split('-'); return m && d ? `${Number(m)}/${Number(d)}` : ''; };
    return list.map(p => `<div class="${c.wl || 'wl'}">
        <div class="${c.wlProj || 'wl-pj'}">${_esc(p.project_name || '（未掛案）')}<span class="${c.wlDays || 'wl-days'}">${(p.days || []).length} 天</span></div>
        <ul>${(p.days || []).map(d => (d.items || []).map(i => {
            const stage = [i.work_type, i.stage_name].filter(Boolean).join(' · ');
            return `<li><span class="${c.wlDay || 'wl-day'}">${md(d.date)}</span>${_esc(i.note || '')}${stage ? ` <span class="${c.pillStage || 'pill-stage'}">${_esc(stage)}</span>` : ''}</li>`;
        }).join('')).join('')}</ul></div>`).join('');
}

/** 帶 Bearer token 的 payload（modules／access_level）——前端只拿它決定要不要畫回覆框／設定鈕，守衛在後端。 */
export function tokenGrants() {
    try {
        const tok = localStorage.getItem('auth_token') || '';
        const part = tok.split('.')[1] || '';
        const json = atob(part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '='));
        const p = JSON.parse(decodeURIComponent(Array.from(json, ch => '%' + ch.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
        const modules = Array.isArray(p.modules) ? p.modules : [];
        const admin = Number(p.access_level || 0) >= 3;
        return { username: p.username || p.sub || '', modules, admin, has: (m) => admin || modules.includes(m) };
    } catch (_) { return { username: '', modules: [], admin: false, has: () => false }; }
}

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
export { ensurePasteBase, renderRich, pasteThumbs } from './paste-image.js';
import { renderRich as _renderRich } from './paste-image.js';

export function blockList(label, arr, blockCls) {
    if (!arr || !arr.length) return '';
    return `<div class="${blockCls}"><h4>${_esc(label)}</h4><ul>${arr.map(x => `<li>${_renderRich(x)}</li>`).join('')}</ul></div>`;
}

/** 心情（owner 2026-09-05：讚／愛心／笑）—— 順序與後端 core.journal_logic.REACTION_KINDS 一致。 */
export const REACTIONS = [['like', '\u{1F44D}'], ['love', '\u2764\uFE0F'], ['laugh', '\u{1F602}']];   // 舊三種（DB 裡存的是這三個字）
/** 長按選單的整格 emoji（owner 2026-09-07「可以更多元」）：新按的 kind 就是 emoji 本身；舊三種照舊存字。 */
export const EMOJI_MENU = ['\u{1F44D}', '\u2764\uFE0F', '\u{1F602}', '\u{1F389}', '\u{1F44F}', '\u{1F525}', '\u{1F4AA}', '\u{1F64F}',
    '\u{1F62E}', '\u{1F622}', '\u{1F914}', '\u{1F440}', '\u2728', '\u{1F4AF}', '\u{1F973}', '\u{1F60D}',
    '\u{1F923}', '\u{1F60E}', '\u{1FAF6}', '\u{1F64C}', '\u{1F62D}', '\u{1F929}', '\u2615', '\u{1F37B}',
    '\u{1F3AC}', '\u{1F3A5}', '\u{1F4F7}', '\u2705', '\u{1F680}', '\u2B50', '\u{1F4A1}', '\u{1F91D}'];
const _LEGACY_KIND = { '\u{1F44D}': 'like', '\u2764\uFE0F': 'love', '\u{1F602}': 'laugh' };
/** emoji → 要送給後端的 kind（舊三種送字，其餘送 emoji 本身）。 */
export const kindOf = (glyph) => _LEGACY_KIND[glyph] || glyph;
/** kind → 畫出來的 emoji。 */
export const glyphOf = (kind) => (REACTIONS.find(([k]) => k === kind) || [])[1] || kind;
/** r 裡真正的心情種類（去掉 mine／names／users 這些附帶欄位；舊三種排前面）。 */
export const kindsIn = (r) => { const d = r || {}; const extra = Object.keys(d).filter(k => !['mine', 'names', 'users'].includes(k) && !REACTIONS.some(([x]) => x === k) && (d[k] || 0) > 0); return [...REACTIONS.map(([k]) => k), ...extra]; };

/** 一條條目下的心情列；r={like,love,laugh,mine:[]}；canReact=false 只顯示有數字的。 */
export function reactionBar(r, ids, c = {}, canReact = false) {
    const d = r || { like: 0, love: 0, laugh: 0, mine: [] };
    const mine = new Set(d.mine || []);
    const btns = kindsIn(d).map(k => {
        const g = glyphOf(k);
        const n = d[k] || 0;
        if (!canReact && !n) return '';
        const attrs = canReact ? ` data-react="${k}" data-react-j="${_esc(ids.journalId || '')}" data-react-t="${_esc(ids.entryTable || '')}" data-react-e="${_esc(ids.entryId || '')}"` : ' disabled';
        return `<button type="button" class="${c.reactBtn || 'react-btn'}${mine.has(k) ? ' on' : ''}"${attrs}>${g}${n ? ` <span class="n">${n}</span>` : ''}</button>`;
    }).join('');
    return btns ? `<div class="${c.react || 'react'}">${btns}</div>` : '';
}

/** 縮寫圓頭（同 Google Chat）：中文取後兩字、英文取前兩字母；顏色由名字決定。 */
const _AV_COLORS = ['#c9372c', '#b45309', '#15803d', '#1d4ed8', '#7c3aed', '#0f766e', '#be185d', '#4d7c0f'];
export function avatarHtml(name, c = {}) {
    const n = String(name || '').trim();
    const cjk = /[\u3400-\u9fff]/.test(n);
    const ini = cjk ? n.slice(-2) : n.slice(0, 2).toUpperCase();
    let h = 0; for (const ch of n) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return `<span class="${c.av || 'av'}" style="background:${_AV_COLORS[h % _AV_COLORS.length]}" title="${_esc(n)}">${_esc(ini)}</span>`;
}

/** 標題列的愛心（owner 2026-09-05）：整份週記一個心情；人列只留姓名＋愛心。
 *  r={like,love,laugh,mine:[],names:{kind:[顯示名]}}。有人按了 → 愛心前面出現他們的頭（最多 3 顆＋N），
 *  滑過／點頭像出浮層列「誰按了什麼」；沒人按＝空心低調。canReact=false 只顯示。 */
export function heartHtml(r, ids, canReact = false, c = {}) {
    const d = r || { like: 0, love: 0, laugh: 0, mine: [], names: {} };
    const names = d.names || {};
    const kinds = kindsIn(d);
    const total = kinds.reduce((s, k) => s + (d[k] || 0), 0);
    const mine = (d.mine || [])[0] || '';
    const glyph = mine ? glyphOf(mine) : (total ? '\u2764\uFE0F' : '\u2661');
    const everyone = kinds.flatMap(k => (names[k] || []).map(n => ({ n, g: glyphOf(k) })));
    const shown = everyone.slice(0, 3), more = everyone.length - shown.length;
    const avs = everyone.length
        ? `<span class="${c.avs || 'avs'}" data-heart-pop>${shown.map(x => avatarHtml(x.n, c)).join('')}${more > 0 ? `<span class="${c.av || 'av'} more">+${more}</span>` : ''}</span>` : '';
    const pop = everyone.length
        ? `<div class="${c.heartPop || 'heart-pop'}">${kinds.map(k => (names[k] || []).length
            ? `<div class="hp-kind"><span class="hp-g">${glyphOf(k)}</span>${(names[k] || []).map(n => `<span class="hp-who">${avatarHtml(n, c)}${_esc(n)}</span>`).join('')}</div>` : '').join('')}</div>` : '';
    const attrs = canReact ? ` data-heart data-heart-mine="${_esc(mine)}" data-heart-j="${_esc(ids.journalId || '')}" data-heart-t="${_esc(ids.entryTable || 'work_journals')}" data-heart-e="${_esc(ids.entryId || '')}"` : ' disabled';
    const title = total ? '' : (canReact ? '點一下給愛心，長按換心情' : '');
    return `<span class="${c.heartWrap || 'heart-wrap'}">${avs}<button type="button" class="${c.heart || 'heart'}${mine ? ' on' : ''}${total ? ' has' : ''}"${title ? ` title="${_esc(title)}"` : ''}${attrs}>${glyph}</button>${pop}</span>`;
}

/** 一條回覆（灰底）。 */
export function replyHtml(r, c = {}) {
    const t = r.created_at ? String(r.created_at).slice(5, 16).replace('T', ' ') : '';
    return `<div class="${c.reply || 'reply'}"><b>${_esc(r.display_name || r.username || '')}</b>${_renderRich(r.content || '')}${t ? `<span class="${c.replyTime || 'reply-t'}">${_esc(t)}</span>` : ''}</div>`;
}

/** 一區的條目清單（新版：含掛案／旗標／既有回覆／心情列）。opts：projectName(id)、canReact、reactions、journalId。 */
export function entryBlock(j, k, label, c, opts = {}) {
    const items = entriesOf(j, k);
    if (!items.length) return '';
    const name = opts.projectName || (() => '');
    return `<div class="${c.block}"><h4>${_esc(label)}</h4><ul>${items.map(e => {
        const pj = e.project_name || (e.project_id ? name(e.project_id) : '');
        const reps = repliesFor(j, k, e.id);
        return `<li>${_renderRich(e.content)}${pj ? ` <span class="${c.pillProj || 'pill-proj'}">${_esc(pj)}</span>` : ''}${
            e.flag && FLAG_LABEL[e.flag] ? ` <span class="${(c.pillFlag || 'pill-flag') + ' ' + e.flag}">${FLAG_LABEL[e.flag]}</span>` : ''}${
            reps.map(r => replyHtml(r, c)).join('')}${
            e.id ? reactionBar((opts.reactions || {})[e.id], { journalId: opts.journalId, entryTable: entryTable(k), entryId: e.id }, c, !!opts.canReact) : ''}</li>`;
    }).join('')}</ul></div>`;
}

// c = {card, name, empty, block} class 名；title 由呼叫端決定（人名或週區間）並自行 esc。
// opts（可省，舊呼叫端不變）：worklog（畫自動區）、projectName(id)、canReact、flags（標題旁的求助 pill）。
/** 週記的旗標 pill（求助／學到／…）：personCard 與 journal.html 的團隊牆同一份標記。 */
export function flagPills(j, c = {}) {
    const flags = j.flags || {};
    return FLAGS.map(([f, l]) => (flags[f] ? `<span class="${(c.pillFlag || 'pill-flag') + ' ' + f}">${l} ${flags[f]}</span>` : '')).join('');
}

export function personCard(j, title, c, opts = {}) {
    const empty = BLOCKS.every(([k]) => !entriesOf(j, k).length);
    const flags = j.flags || {};
    const pills = flagPills(j, c);
    const worklog = opts.worklog && (j.worklog || []).length
        ? `<div class="${c.block}"><h4>做了什麼</h4>${worklogHtml(j.worklog, c)}</div>` : '';
    return `<div class="${c.card}"><div class="${c.name}">${title}${pills}</div>${worklog}
        ${empty && !worklog ? `<div class="${c.empty}">（空白）</div>` : BLOCKS.map(([k, label]) => entryBlock(j, k, label, c, { ...opts, journalId: j.id || j.journal_id || '' })).join('')}
    </div>`;
}

// ── API（帶 Bearer；fetch 層網路錯誤回 {ok:false, status:0} sentinel，
//    消費端一律先查 ok/isAuthFail 再 json()；status 0 = 連線失敗可顯專屬文案）──
const _q = (start) => start ? '?start=' + encodeURIComponent(start) : '';
const _safe = (p) => p.catch(() => ({ ok: false, status: 0 }));

let _projOptsPromise = null;
export const api = {
    mine: (start) => _safe(authFetch('/api/v1/journal/mine' + _q(start))),
    saveMine: (start, body) => _safe(authFetch('/api/v1/journal/mine' + _q(start), { method: 'PUT', body })),
    submitMine: (start) => _safe(authFetch('/api/v1/journal/mine/submit' + _q(start), { method: 'POST', body: {} })),
    reply: (body) => _safe(authFetch('/api/v1/journal/reply', { method: 'POST', body })),
    help: (weeks = 8) => _safe(authFetch('/api/v1/journal/help?weeks=' + weeks)),
    // 掛案子用的專案清單：/timesheets/project_options（timesheets 拿整份；綁定人員檔案的員工也給）
    projectOptions: () => {
        if (!_projOptsPromise) {
            _projOptsPromise = _safe(authFetch('/api/v1/timesheets/project_options'))
                .then(r => (r.ok ? r.json() : { projects: [] }))
                .then(d => (d && d.projects) || [])
                .catch(() => []);
        }
        return _projOptsPromise;
    },
    week: (start) => _safe(authFetch('/api/v1/journal/week' + _q(start))),
    react: (body) => _safe(authFetch('/api/v1/journal/react', { method: 'POST', body })),   // authFetch 自己 JSON 化：再 stringify 一次會變成字串 body → 422（2026-09-05 踩到）
    // 空字串/null 參數自動剔除 — 呼叫端直接把 state 丟進來即可
    learnings: (params) => _safe(authFetch('/api/v1/journal/learnings?' + new URLSearchParams(
        Object.fromEntries(Object.entries(params).filter(([, v]) => v !== '' && v != null))).toString())),
    people: () => _safe(authFetch('/api/v1/journal/people')),
    person: (username) => _safe(authFetch('/api/v1/journal/person?username=' + encodeURIComponent(username))),
};
