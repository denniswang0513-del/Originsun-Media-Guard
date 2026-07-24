// journal.js — 人事管理 › 工作日誌（每週四問：順利與感謝 / 挑戰 / 學習 / 其他）
// 三檢視：週誌（我的編輯卡 + 團隊卡片牆）/ 學習庫（搜尋 + 分頁）/ 依人回溯（個人時間軸）。
// UI 無 emoji（owner 鐵則）。API: /api/v1/journal/*（Bearer token；週=週一起算）。

// 單一正本在 journal-core（/journal.html 官網風頁共用）：四問標籤/週期運算/
// 序列化契約/渲染 helpers/API；esc/debounce 也經由它 re-export（正本在 CRM utils）
import { BLOCKS, HINT_EDIT_WINDOW, MSG_EDIT_WINDOW, api, blockList, debounce,
         esc, isAuthFail, itemsToLines, linesToItems, personCard,
         shiftWeek as _shiftWeek, thisWeekStart as _thisWeekStart,
         weekRange } from '../../js/shared/journal-core.js';

const el = (id) => document.getElementById(id);
// SPA 深色語彙的 class 組（官網風 /journal.html 傳自己的一組）
const _CARD_CLS = { name: 'jr-person-name', empty: 'jr-empty', block: 'jr-block' };

// ── 狀態 ────────────────────────────────────────────────────────────────
let _view = 'week';
let _weekStart = _thisWeekStart();
let _people = null;                 // people 快取（學習庫 / 依人回溯共用）
let _learn = { q: '', username: '', limit: 20, offset: 0, items: [], total: 0 };
let _personSel = '';
let _flashMsg = '';                 // 儲存成功訊息（跨 re-render 顯示一次）

// ── 共用 render ─────────────────────────────────────────────────────────
function _authFail(...rs) {
    if (isAuthFail(...rs)) {
        el('jr-view').innerHTML = '<div class="jr-empty">需要『工作日誌』權限</div>';
        return true;
    }
    return false;
}

function _renderShell() {
    el('jr-content').innerHTML = `
        <h2>工作日誌</h2>
        <div class="jr-sub">每週四問：順利的事與想感謝的人、遇到哪些挑戰、學到了什麼、其他主題。一行一條，週一起算。</div>
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
    const [mr, wr] = await Promise.all([
        api.mine(_weekStart),
        api.week(_weekStart),
    ]);
    if (_authFail(mr, wr)) return;
    if (!mr.ok || !wr.ok) { v.innerHTML = `<div class="jr-empty">載入失敗（${mr.status} / ${wr.status}）</div>`; return; }
    _renderWeek(await mr.json(), await wr.json());
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
                    <textarea id="jr-ta-${k}" placeholder="一行一條" ${mine.editable ? '' : 'disabled'}>${esc(itemsToLines(mine[k]))}</textarea>
                </div>`).join('')}
            <div class="jr-form">
                ${mine.editable
                    ? '<button class="jr-btn" id="jr-save">儲存</button><span id="jr-save-msg"></span>'
                    : `<span class="jr-hint">${HINT_EDIT_WINDOW}</span>`}
            </div>
        </div>

        <div class="jr-card">
            <h3>團隊週誌（${journals.length}）</h3>
            ${journals.length
                ? `<div class="jr-wall">${journals.map(j => personCard(j, esc(j.username), { ...(_CARD_CLS), card: 'jr-person-card' })).join('')}</div>`
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
        body[k] = linesToItems(el('jr-ta-' + k).value);
    const r = await api.saveMine(_weekStart, body);
    if (!r.ok) {
        const d = r.json ? await r.json().catch(() => ({})) : {};
        const text = r.status === 403 ? MSG_EDIT_WINDOW
            : r.status === 401 ? '需要『工作日誌』權限'
            : (d.detail || '儲存失敗');
        const msg = el('jr-save-msg');
        if (msg) { msg.textContent = text; msg.className = 'jr-msg-err'; }
        return;
    }
    _flashMsg = '已儲存';
    // PUT 已回 /mine 同形回應 — 只重抓 /week 讓團隊牆同步，省一次 /mine
    const mine = await r.json();
    const wr = await api.week(_weekStart);
    if (wr.ok) _renderWeek(mine, await wr.json());
    else _loadWeek();
}

// ── people（學習庫 / 依人回溯共用） ─────────────────────────────────────
async function _fetchPeople() {
    if (_people) return _people;
    const r = await api.people();
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
    // 空值由 api.learnings 剔除 — state 直接丟
    const r = await api.learnings({ q: _learn.q, username: _learn.username,
                                    limit: _learn.limit, offset: _learn.offset });
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
    const r = await api.person(_personSel);
    if (_authFail(r)) return;
    if (!r.ok) { list.innerHTML = `<div class="jr-empty">載入失敗（${r.status}）</div>`; return; }
    const journals = (await r.json()).journals || [];
    if (!journals.length) { list.innerHTML = '<div class="jr-empty">此人還沒有週誌</div>'; return; }
    list.innerHTML = journals.map(j =>
        personCard(j, weekRange(j.week_start), { ...(_CARD_CLS), card: 'jr-timeline-card' })).join('');
}

export async function initJournalTab() {
    _renderShell();
    await _showView(_view);
}
