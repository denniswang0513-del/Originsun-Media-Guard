// journal.js — 人事管理 › 工作日誌（每週三問：順利與感謝 / 挑戰 / 學習）
// 三檢視：週誌（我的編輯卡 + 團隊卡片牆）/ 學習庫（搜尋 + 分頁）/ 依人回溯（個人時間軸）。
// UI 無 emoji（owner 鐵則）。API: /api/v1/journal/*（Bearer token；週=週一起算）。

import { authFetch as jfetch } from '../../js/shared/utils.js';
import { esc, debounce } from '../website/website-utils.js';

const el = (id) => document.getElementById(id);

// ── 週期 helpers（週=週一起算，日期一律當地時區手動組字避免 UTC 偏移） ──
const _pad = (n) => String(n).padStart(2, '0');
const _parseISO = (s) => { const [y, m, d] = String(s).split('-').map(Number); return new Date(y, m - 1, d); };
const _isoDate = (d) => `${d.getFullYear()}-${_pad(d.getMonth() + 1)}-${_pad(d.getDate())}`;

function _thisWeekStart() {
    const now = new Date();
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));   // Mon=0
    return _isoDate(d);
}
function _shiftWeek(iso, weeks) { const d = _parseISO(iso); d.setDate(d.getDate() + weeks * 7); return _isoDate(d); }

// 週區間標題：`YYYY/MM/DD – MM/DD`（週一至週日；同年省略右側年份，跨年顯示完整）
// ⚠ my.html 的 jrWeekRange 是本函式的鏡像（獨立頁不載 SPA 模組）— 改格式兩邊同步
function weekRange(weekStartISO) {
    const s = _parseISO(weekStartISO);
    const e = new Date(s.getFullYear(), s.getMonth(), s.getDate() + 6);
    const left = `${s.getFullYear()}/${_pad(s.getMonth() + 1)}/${_pad(s.getDate())}`;
    const right = (s.getFullYear() === e.getFullYear() ? '' : `${e.getFullYear()}/`)
        + `${_pad(e.getMonth() + 1)}/${_pad(e.getDate())}`;
    return `${left} – ${right}`;
}

// ── 狀態 ────────────────────────────────────────────────────────────────
// ⚠ 三問標籤與 my.html 的 JR_SECTIONS 互為鏡像 — 改文案兩邊同步
const BLOCKS = [
    ['wins', '順利的事與想感謝的人'],
    ['challenges', '遇到哪些挑戰'],
    ['learnings', '學到了什麼'],
];
let _view = 'week';
let _weekStart = _thisWeekStart();
let _people = null;                 // people 快取（學習庫 / 依人回溯共用）
let _learn = { q: '', username: '', limit: 20, offset: 0, items: [], total: 0 };
let _personSel = '';
let _flashMsg = '';                 // 儲存成功訊息（跨 re-render 顯示一次）

// ── 共用 render ─────────────────────────────────────────────────────────
function _renderAuthFail() {
    el('jr-view').innerHTML = '<div class="jr-empty">需要『工作日誌』權限</div>';
}
function _authFail(...rs) {
    if (rs.some(r => r && (r.status === 401 || r.status === 403))) { _renderAuthFail(); return true; }
    return false;
}
function _blockList(label, arr) {
    if (!arr || !arr.length) return '';
    return `<div class="jr-block"><h4>${esc(label)}</h4><ul>${arr.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`;
}

function _renderShell() {
    el('jr-content').innerHTML = `
        <h2>工作日誌</h2>
        <div class="jr-sub">每週三問：順利的事與想感謝的人、遇到哪些挑戰、學到了什麼。一行一條，週一起算。</div>
        <div class="jr-pills">
            ${[['week', '週誌'], ['learn', '學習庫'], ['person', '依人回溯']].map(([k, l]) =>
                `<button class="jr-pill ${_view === k ? 'active' : ''}" data-view="${k}">${l}</button>`).join('')}
        </div>
        <div id="jr-view"></div>`;
    el('jr-content').querySelectorAll('.jr-pill').forEach(btn => {
        btn.onclick = () => _showView(btn.dataset.view);
    });
}

async function _showView(view) {
    _view = view;
    document.querySelectorAll('#jr-root .jr-pill').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    if (view === 'week') return _loadWeek();
    if (view === 'learn') return _loadLearn();
    return _loadPerson();
}

// ── 檢視 1：週誌 ────────────────────────────────────────────────────────
async function _loadWeek() {
    const v = el('jr-view');
    v.innerHTML = '<div class="jr-empty">載入中…</div>';
    const q = '?start=' + encodeURIComponent(_weekStart);
    const [mr, wr] = await Promise.all([
        jfetch('/api/v1/journal/mine' + q),
        jfetch('/api/v1/journal/week' + q),
    ]);
    if (_authFail(mr, wr)) return;
    if (!mr.ok || !wr.ok) { v.innerHTML = `<div class="jr-empty">載入失敗（${mr.status} / ${wr.status}）</div>`; return; }
    _renderWeek(await mr.json(), await wr.json());
}

function _personCard(j) {
    const empty = BLOCKS.every(([k]) => !(j[k] || []).length);
    return `<div class="jr-person-card">
        <div class="jr-person-name">${esc(j.username)}</div>
        ${empty ? '<div class="jr-empty">（空白）</div>' : BLOCKS.map(([k, label]) => _blockList(label, j[k])).join('')}
    </div>`;
}

function _renderWeek(mine, week) {
    const isThisWeek = _weekStart === _thisWeekStart();
    const journals = week.journals || [];
    el('jr-view').innerHTML = `
        <div class="jr-weeknav">
            <button class="jr-btn ghost" id="jr-prev">‹ 上一週</button>
            <div class="jr-weektitle">
                <span>${weekRange(week.week_start || _weekStart)}</span>
                ${isThisWeek ? '' : '<button class="jr-btn ghost small" id="jr-today">回到本週</button>'}
            </div>
            <button class="jr-btn ghost" id="jr-next">下一週 ›</button>
        </div>

        <div class="jr-card">
            <h3>我的週誌</h3>
            ${BLOCKS.map(([k, label]) => `
                <div class="jr-block">
                    <h4>${label}</h4>
                    <textarea id="jr-ta-${k}" placeholder="一行一條" ${mine.editable ? '' : 'disabled'}>${esc((mine[k] || []).join('\n'))}</textarea>
                </div>`).join('')}
            <div class="jr-form">
                ${mine.editable
                    ? '<button class="jr-btn" id="jr-save">儲存</button><span id="jr-save-msg"></span>'
                    : '<span class="jr-hint">僅能編輯至下一週</span>'}
            </div>
        </div>

        <div class="jr-card">
            <h3>團隊週誌（${journals.length}）</h3>
            ${journals.length
                ? `<div class="jr-wall">${journals.map(_personCard).join('')}</div>`
                : '<div class="jr-empty">這一週還沒有人寫日誌</div>'}
        </div>`;
    el('jr-prev').onclick = () => { _weekStart = _shiftWeek(_weekStart, -1); _loadWeek(); };
    el('jr-next').onclick = () => { _weekStart = _shiftWeek(_weekStart, 1); _loadWeek(); };
    const today = el('jr-today');
    if (today) today.onclick = () => { _weekStart = _thisWeekStart(); _loadWeek(); };
    const save = el('jr-save');
    if (save) save.onclick = _saveMine;
    if (_flashMsg) {
        const m = el('jr-save-msg');
        if (m) { m.textContent = _flashMsg; m.className = 'jr-msg-ok'; setTimeout(() => { m.textContent = ''; }, 2500); }
        _flashMsg = '';
    }
}

async function _saveMine() {
    const body = {};
    for (const [k] of BLOCKS)
        body[k] = el('jr-ta-' + k).value.split('\n').map(s => s.trim()).filter(Boolean);
    const r = await jfetch('/api/v1/journal/mine?start=' + encodeURIComponent(_weekStart), { method: 'PUT', body });
    if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        const text = r.status === 403 ? '已超出可編輯期間（僅能編輯至下一週）'
            : r.status === 401 ? '需要『工作日誌』權限'
            : (d.detail || '儲存失敗');
        const msg = el('jr-save-msg');
        if (msg) { msg.textContent = text; msg.className = 'jr-msg-err'; }
        return;
    }
    _flashMsg = '已儲存';
    // PUT 已回 /mine 同形回應 — 只重抓 /week 讓團隊牆同步，省一次 /mine
    const mine = await r.json();
    const wr = await jfetch('/api/v1/journal/week?start=' + encodeURIComponent(_weekStart));
    if (wr.ok) _renderWeek(mine, await wr.json());
    else _loadWeek();
}

// ── people（學習庫 / 依人回溯共用） ─────────────────────────────────────
async function _fetchPeople() {
    if (_people) return _people;
    const r = await jfetch('/api/v1/journal/people');
    if (_authFail(r)) return null;   // 已渲染無權限畫面，呼叫端直接 return
    if (!r.ok) { _people = []; return _people; }
    _people = (await r.json()).people || [];
    return _people;
}

// ── 檢視 2：學習庫 ──────────────────────────────────────────────────────
async function _loadLearn() {
    const v = el('jr-view');
    v.innerHTML = '<div class="jr-empty">載入中…</div>';
    if (await _fetchPeople() === null) return;
    _learn.offset = 0;
    v.innerHTML = `
        <div class="jr-card">
            <div class="jr-form" style="margin-bottom:10px;">
                <input type="text" id="jr-l-q" placeholder="搜尋學習內容" value="${esc(_learn.q)}" style="width:220px;">
                <select id="jr-l-user">
                    <option value="">全部人員</option>
                    ${(_people || []).map(p => `<option value="${esc(p.username)}" ${p.username === _learn.username ? 'selected' : ''}>${esc(p.username)}</option>`).join('')}
                </select>
            </div>
            <div id="jr-learn-list"><div class="jr-empty">載入中…</div></div>
        </div>`;
    el('jr-l-q').oninput = debounce((ev) => {
        _learn.q = ev.target.value.trim(); _learn.offset = 0; _refreshLearn(false);
    }, 350);
    el('jr-l-user').onchange = (ev) => { _learn.username = ev.target.value; _learn.offset = 0; _refreshLearn(false); };
    await _refreshLearn(false);
}

async function _refreshLearn(append) {
    const q = new URLSearchParams();
    if (_learn.q) q.set('q', _learn.q);
    if (_learn.username) q.set('username', _learn.username);
    q.set('limit', _learn.limit);
    q.set('offset', _learn.offset);
    const r = await jfetch('/api/v1/journal/learnings?' + q.toString());
    if (_authFail(r)) return;
    const list = el('jr-learn-list');
    if (!list) return;                       // 使用者已切到別的檢視
    if (!r.ok) { list.innerHTML = `<div class="jr-empty">載入失敗（${r.status}）</div>`; return; }
    const d = await r.json();
    _learn.items = append ? _learn.items.concat(d.items || []) : (d.items || []);
    _learn.total = d.total ?? _learn.items.length;
    if (!_learn.items.length) { list.innerHTML = '<div class="jr-empty">還沒有累積的學習紀錄</div>'; return; }
    list.innerHTML = `
        ${_learn.items.map(it => `
            <div class="jr-learn-item">
                <div class="jr-learn-content">${esc(it.content)}</div>
                <div class="jr-learn-meta">${esc(it.username)} · ${weekRange(it.week_start)}</div>
            </div>`).join('')}
        ${_learn.items.length < _learn.total
            ? '<div style="text-align:center;margin-top:10px;"><button class="jr-btn ghost" id="jr-l-more">載入更多</button></div>'
            : ''}`;
    const more = el('jr-l-more');
    if (more) more.onclick = () => { _learn.offset += _learn.limit; _refreshLearn(true); };
}

// ── 檢視 3：依人回溯 ────────────────────────────────────────────────────
async function _loadPerson() {
    const v = el('jr-view');
    v.innerHTML = '<div class="jr-empty">載入中…</div>';
    if (await _fetchPeople() === null) return;
    v.innerHTML = `
        <div class="jr-card">
            <div class="jr-form" style="margin-bottom:10px;">
                <select id="jr-p-user">
                    <option value="">請選擇人員</option>
                    ${(_people || []).map(p => `<option value="${esc(p.username)}" ${p.username === _personSel ? 'selected' : ''}>${esc(p.username)}（${p.weeks} 週）</option>`).join('')}
                </select>
            </div>
            <div id="jr-person-list"><div class="jr-empty">請先選擇人員</div></div>
        </div>`;
    el('jr-p-user').onchange = (ev) => { _personSel = ev.target.value; _refreshPerson(); };
    if (_personSel) await _refreshPerson();
}

async function _refreshPerson() {
    const list = el('jr-person-list');
    if (!list) return;
    if (!_personSel) { list.innerHTML = '<div class="jr-empty">請先選擇人員</div>'; return; }
    list.innerHTML = '<div class="jr-empty">載入中…</div>';
    const r = await jfetch('/api/v1/journal/person?username=' + encodeURIComponent(_personSel));
    if (_authFail(r)) return;
    if (!r.ok) { list.innerHTML = `<div class="jr-empty">載入失敗（${r.status}）</div>`; return; }
    const journals = (await r.json()).journals || [];
    if (!journals.length) { list.innerHTML = '<div class="jr-empty">此人還沒有週誌</div>'; return; }
    list.innerHTML = journals.map(j => `
        <div class="jr-timeline-card">
            <div class="jr-person-name">${weekRange(j.week_start)}</div>
            ${BLOCKS.map(([k, label]) => _blockList(label, j[k])).join('') || '<div class="jr-empty">（空白）</div>'}
        </div>`).join('');
}

export async function initJournalTab() {
    _renderShell();
    await _showView(_view);
}
