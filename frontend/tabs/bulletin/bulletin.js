/**
 * bulletin.js — 📌 公布欄 Tab 主進入點
 *
 * ⚠️ MASTER 同源功能：打 master 自己的 /api/v1/bulletin（帶 auth token），
 *    不走 NAS website-api。因此 *不用* websiteFetch / getApiBase，改用本檔的 bfetch。
 *
 * 結構複用官網 Tab：左側子視圖導覽 + 右側內容容器。兩個子視圖共用同一個 board：
 *    📋 待辦提醒（todo）  — 全量主清單
 *    🌐 官網與社群（webso）— 分類鏡頭（category ∈ 官網/社群），快速新增自動帶分類
 * board 直接 render 進 #bulletin-content。
 *
 * 視覺（.card/.btn/.btn-sm/.btn-ghost/website-pill/_inp()）與 toast/modal 沿用
 * 官網 Tab 的 website-utils.js（純 DOM 工具，無 NAS 副作用）。
 */

import { esc, toastOk, toastErr, openModal, closeModal } from '../website/website-utils.js';

// ── 同源 fetch helper（帶 JWT，master /api/v1/bulletin）──
async function bfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        ...opts,
        headers: {
            'Accept': 'application/json',
            ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
            ...(token ? { 'Authorization': 'Bearer ' + token } : {}),
            ...(opts.headers || {}),
        },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.status === 204 ? null : r.json();
}

// ── 狀態 ──
let _items = [];
let _showDone = false;     // 預設隱藏已完成
let _content = null;
let _view = 'todo';        // 目前子視圖（VIEWS 的 key）
const _bl = (window._bl = window._bl || {});

// 子視圖設定：cats=null 顯示全部；有 cats 時為分類鏡頭（同一筆待辦兩邊都看得到，
// 這是刻意的 — 待辦提醒是主清單、官網與社群是過濾視角）。
const VIEWS = {
    todo:  { title: '📋 待辦提醒', sub: '內部公布欄 / 團隊待辦', cats: null, addCat: '' },
    webso: { title: '🌐 官網與社群', sub: '官網 / 社群相關待辦（分類=官網、社群）', cats: ['官網', '社群'], addCat: '官網' },
};

// priority → [中文, 底色, 文字色]（high=紅 / med=琥珀 / low=灰）
const PRIORITY = {
    high: ['高', '#7f1d1d', '#fca5a5'],
    med:  ['中', '#78350f', '#fbbf24'],
    low:  ['低', '#3f3f46', '#d4d4d8'],
};
// status → [中文, 底色, 文字色]（doing=藍 pill）
const STATUS = {
    todo:  ['待辦',   '#3f3f46', '#d4d4d8'],
    doing: ['進行中', '#1e3a5f', '#93c5fd'],
    done:  ['完成',   '#064e3b', '#6ee7b7'],
};

const _LOADING_HTML = '<div style="color:#888;padding:40px;text-align:center;">載入中…</div>';

function _inp() {
    return 'background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:7px 9px;border-radius:4px;width:100%;box-sizing:border-box;font-size:13px;font-family:inherit;';
}

// ── 進入點 ──
export async function initBulletinTab() {
    const nav = document.getElementById('bulletin-nav');
    if (nav) {
        nav.querySelectorAll('.website-nav-btn').forEach(btn => {
            btn.addEventListener('click', () => _switchSubview(btn.dataset.subview));
        });
    }
    await _switchSubview('todo');
}
window.initBulletinTab = initBulletinTab;

async function _switchSubview(name) {
    _content = document.getElementById('bulletin-content');
    const nav = document.getElementById('bulletin-nav');
    if (nav) nav.querySelectorAll('.website-nav-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.subview === name));
    if (!_content || !VIEWS[name]) return;
    _view = name;
    _content.innerHTML = _LOADING_HTML;
    try {
        await _load();
    } catch (e) {
        _content.innerHTML = `<div style="color:#f88;padding:24px;">載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    _renderBoard();
}

async function _load() {
    const data = await bfetch('/api/v1/bulletin');
    _items = (data && data.items) || [];
}

// _load + re-render，並可選擇把焦點還給快速新增輸入框
async function _reload(focusAdd = false) {
    await _load();
    _renderBoard();
    if (focusAdd) document.getElementById('bl-add-title')?.focus();
}

// ── 渲染 board ──
function _renderBoard() {
    if (!_content) return;
    const view = VIEWS[_view] || VIEWS.todo;
    const scoped = view.cats
        ? _items.filter(it => view.cats.includes((it.category || '').trim()))
        : _items;
    const visible = _showDone ? scoped : scoped.filter(it => it.status !== 'done');
    const doneCount = scoped.filter(it => it.status === 'done').length;

    const list = visible.length
        ? visible.map(_card).join('')
        : `<div style="color:#888;padding:48px 24px;text-align:center;font-size:14px;">目前沒有待辦 🎉</div>`;

    _content.innerHTML = `
        <h2 style="margin:0 0 4px;">${view.title} <span style="color:#888;font-size:12px;font-weight:400;">· ${view.sub}</span></h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">釘選的項目會排在最前面。勾選左側圓圈標記完成。</p>

        <!-- 快速新增 -->
        <div class="card" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input id="bl-add-title" placeholder="輸入待辦標題後按 Enter 或「+ 新增」" style="${_inp()};flex:1 1 260px;min-width:200px;" />
            <select id="bl-add-prio" title="優先級" style="${_inp()};max-width:110px;">
                <option value="high">🔴 高</option>
                <option value="med" selected>🟠 中</option>
                <option value="low">⚪ 低</option>
            </select>
            <input id="bl-add-cat" placeholder="分類（選填）" value="${esc(view.addCat)}" style="${_inp()};max-width:150px;" />
            <button class="btn" style="background:#059669;" onclick="window._bl.add()">+ 新增</button>
        </div>

        <!-- 篩選 -->
        <div style="display:flex;align-items:center;gap:10px;margin:6px 0 12px;font-size:12px;color:#999;">
            <button class="btn btn-sm btn-ghost" onclick="window._bl.toggleFilter()">
                ${_showDone ? '👁 顯示：全部' : '🙈 顯示：未完成'}
            </button>
            <span>共 ${scoped.length} 則${doneCount ? ` · 已完成 ${doneCount}` : ''}</span>
        </div>

        <!-- 清單 -->
        <div id="bl-list">${list}</div>
    `;

    const addInput = document.getElementById('bl-add-title');
    if (addInput) {
        addInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); _bl.add(); }
        });
    }
}

function _card(it) {
    const p = PRIORITY[it.priority] || PRIORITY.med;
    const isDone = it.status === 'done';
    const fullIdx = _items.findIndex(x => x.id === it.id);
    const atTop = fullIdx <= 0;
    const atBottom = fullIdx >= _items.length - 1;
    const toClaude = it.assignee === 'claude';
    // 分類鏡頭不提供 ▲▼：reorder 動的是全量主清單的順序，在過濾視角下移動
    // 會跳到看不見的位置，誤導大於價值
    const canReorder = !(VIEWS[_view] || VIEWS.todo).cats;

    const prioPill = `<span class="website-pill" style="background:${p[1]};color:${p[2]};">${p[0]}</span>`;
    // 進行中額外顯示藍 pill（待辦/完成分別以預設樣式/刪除線呈現）
    const doingPill = it.status === 'doing'
        ? `<span class="website-pill" style="background:${STATUS.doing[1]};color:${STATUS.doing[2]};">${STATUS.doing[0]}</span>` : '';
    const catPill = it.category
        ? `<span class="website-pill">🏷 ${esc(it.category)}</span>` : '';
    // 交給 Claude 追蹤徽章（tier B）— 紫底
    const assignPill = toClaude
        ? `<span class="website-pill" style="background:#3b1d5f;color:#c4b5fd;">🤖 交給 Claude</span>` : '';
    const pinIcon = it.pinned ? '📌' : '📍';
    const pinStyle = it.pinned ? 'opacity:1;' : 'opacity:0.4;';

    const statusSel = `
        <select onchange="window._bl.setStatus('${it.id}', this.value)"
                style="background:#0d0d0d;border:1px solid #333;color:#ccc;padding:3px 6px;border-radius:4px;font-size:11px;">
            <option value="todo"  ${it.status === 'todo' ? 'selected' : ''}>待辦</option>
            <option value="doing" ${it.status === 'doing' ? 'selected' : ''}>進行中</option>
            <option value="done"  ${it.status === 'done' ? 'selected' : ''}>完成</option>
        </select>`;

    // 「交給 Claude」toggle（assignee）：未指派→交出、已指派→收回（label 隨狀態切換）
    const assignBtn = toClaude
        ? `<button class="btn btn-sm btn-ghost" style="white-space:nowrap;color:#c4b5fd;" onclick="window._bl.toggleAssignee('${it.id}')" title="改回我處理">↩︎ 收回</button>`
        : `<button class="btn btn-sm btn-ghost" style="white-space:nowrap;" onclick="window._bl.toggleAssignee('${it.id}')" title="交給 Claude 追蹤">🤖 交給 Claude</button>`;

    return `
        <div class="bl-card ${isDone ? 'bl-done' : ''}">
            <div class="bl-check ${isDone ? 'on' : ''}" onclick="window._bl.toggleDone('${it.id}')" title="標記${isDone ? '未完成' : '完成'}">${isDone ? '✓' : ''}</div>
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span class="bl-title" onclick="window._bl.edit('${it.id}')">${esc(it.title || '(未命名)')}</span>
                    ${prioPill}${doingPill}${catPill}${assignPill}
                </div>
                ${it.note ? `<div class="bl-note">${esc(it.note)}</div>` : ''}
                ${it.activity ? `<div class="bl-activity"><div class="bl-activity-hd">🤖 Claude 進度</div>${esc(it.activity)}</div>` : ''}
            </div>
            <div style="display:flex;align-items:center;gap:4px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end;">
                ${statusSel}
                ${assignBtn}
                <button class="bl-icon-btn" onclick="window._bl.chat('${it.id}')" title="🤖 問 Claude（唯讀諮詢）">🤖</button>
                <button class="bl-icon-btn" style="${pinStyle}" onclick="window._bl.togglePin('${it.id}')" title="${it.pinned ? '取消釘選' : '釘選'}">${pinIcon}</button>
                ${canReorder ? `<button class="bl-icon-btn" onclick="window._bl.move('${it.id}',-1)" ${atTop ? 'disabled' : ''} title="上移">▲</button>
                <button class="bl-icon-btn" onclick="window._bl.move('${it.id}',1)" ${atBottom ? 'disabled' : ''} title="下移">▼</button>` : ''}
                <button class="bl-icon-btn" onclick="window._bl.edit('${it.id}')" title="編輯">✏️</button>
                <button class="bl-icon-btn" onclick="window._bl.del('${it.id}')" title="刪除" style="color:#f87171;">🗑</button>
            </div>
        </div>`;
}

// ── mutations（每次動作後 re-fetch + re-render，確保排序/狀態一致）──

_bl.add = async () => {
    const t = document.getElementById('bl-add-title');
    const title = (t?.value || '').trim();
    if (!title) { toastErr('請輸入標題'); t?.focus(); return; }
    const priority = document.getElementById('bl-add-prio')?.value || 'med';
    const category = (document.getElementById('bl-add-cat')?.value || '').trim();
    try {
        await bfetch('/api/v1/bulletin', { method: 'POST', body: { title, priority, category } });
        toastOk('已新增');
        await _reload(true);
    } catch (e) { toastErr(e.message); }
};

_bl.toggleFilter = () => { _showDone = !_showDone; _renderBoard(); };

_bl.toggleDone = async (id) => {
    const it = _items.find(x => x.id === id);
    if (!it) return;
    const next = it.status === 'done' ? 'todo' : 'done';
    try {
        await bfetch(`/api/v1/bulletin/${id}`, { method: 'PUT', body: { status: next } });
        await _reload();
    } catch (e) { toastErr(e.message); }
};

_bl.setStatus = async (id, value) => {
    try {
        await bfetch(`/api/v1/bulletin/${id}`, { method: 'PUT', body: { status: value } });
        await _reload();
    } catch (e) { toastErr(e.message); }
};

_bl.togglePin = async (id) => {
    const it = _items.find(x => x.id === id);
    if (!it) return;
    try {
        await bfetch(`/api/v1/bulletin/${id}`, { method: 'PUT', body: { pinned: !it.pinned } });
        await _reload();
    } catch (e) { toastErr(e.message); }
};

// 交給 Claude ↔ 收回（assignee 在 'me'/'claude' 間切換）
_bl.toggleAssignee = async (id) => {
    const it = _items.find(x => x.id === id);
    if (!it) return;
    const next = it.assignee === 'claude' ? 'me' : 'claude';
    try {
        await bfetch(`/api/v1/bulletin/${id}`, { method: 'PUT', body: { assignee: next } });
        toastOk(next === 'claude' ? '已交給 Claude' : '已收回');
        await _reload();
    } catch (e) { toastErr(e.message); }
};

_bl.move = async (id, dir) => {
    const arr = _items.slice();
    const i = arr.findIndex(x => x.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
    // 與其他操作一致：送後端後重抓（board 未樂觀變動，失敗維持原樣即可）。
    try {
        await bfetch('/api/v1/bulletin/reorder', { method: 'POST', body: { ordered_ids: arr.map(x => x.id) } });
        await _reload();
    } catch (e) { toastErr(e.message); }
};

_bl.del = async (id) => {
    if (!confirm('確定刪除這則待辦？')) return;
    try {
        await bfetch(`/api/v1/bulletin/${id}`, { method: 'DELETE' });
        toastOk('已刪除');
        await _reload();
    } catch (e) { toastErr(e.message); }
};

// ── 編輯 modal ──
_bl.edit = (id) => {
    const it = _items.find(x => x.id === id);
    if (!it) return;
    const prioOpt = (v, label) => `<option value="${v}" ${it.priority === v ? 'selected' : ''}>${label}</option>`;
    const statOpt = (v, label) => `<option value="${v}" ${it.status === v ? 'selected' : ''}>${label}</option>`;
    const inner = `
        <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;color:#fff;font-size:15px;">✏️ 編輯待辦</h3>
            <button onclick="window._bl.closeEdit()" style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;width:30px;height:30px;border-radius:4px;">✕</button>
        </div>
        <div style="padding:18px;display:grid;grid-template-columns:auto 1fr;gap:12px 14px;align-items:center;">
            <label style="color:#9aa0a6;font-size:12px;">標題</label>
            <input id="bl-edit-title" value="${esc(it.title || '')}" style="${_inp()}" />
            <label style="color:#9aa0a6;font-size:12px;align-self:start;padding-top:6px;">備註</label>
            <textarea id="bl-edit-note" rows="3" placeholder="細節說明（選填）" style="${_inp()};resize:vertical;">${esc(it.note || '')}</textarea>
            <label style="color:#9aa0a6;font-size:12px;">優先級</label>
            <select id="bl-edit-prio" style="${_inp()};max-width:160px;">
                ${prioOpt('high', '🔴 高')}${prioOpt('med', '🟠 中')}${prioOpt('low', '⚪ 低')}
            </select>
            <label style="color:#9aa0a6;font-size:12px;">狀態</label>
            <select id="bl-edit-status" style="${_inp()};max-width:160px;">
                ${statOpt('todo', '待辦')}${statOpt('doing', '進行中')}${statOpt('done', '完成')}
            </select>
            <label style="color:#9aa0a6;font-size:12px;">指派</label>
            <select id="bl-edit-assignee" style="${_inp()};max-width:160px;">
                <option value="me" ${it.assignee !== 'claude' ? 'selected' : ''}>我</option>
                <option value="claude" ${it.assignee === 'claude' ? 'selected' : ''}>🤖 交給 Claude</option>
            </select>
            <label style="color:#9aa0a6;font-size:12px;">分類</label>
            <input id="bl-edit-cat" value="${esc(it.category || '')}" placeholder="選填" style="${_inp()};max-width:220px;" />
            <label style="color:#9aa0a6;font-size:12px;">釘選</label>
            <label style="color:#ddd;display:inline-flex;gap:6px;align-items:center;font-size:13px;">
                <input id="bl-edit-pinned" type="checkbox" ${it.pinned ? 'checked' : ''} style="width:auto;" /> 📌 釘選到最前面
            </label>
        </div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;">
            <button class="btn btn-ghost btn-sm" onclick="window._bl.closeEdit()">取消</button>
            <button class="btn" style="background:#059669;" onclick="window._bl.saveEdit('${it.id}')">✓ 儲存</button>
        </div>`;
    openModal('bl-edit-modal', inner, { width: '560px' });
};

_bl.closeEdit = () => closeModal('bl-edit-modal');

_bl.saveEdit = async (id) => {
    const v = (elId) => document.getElementById(elId);
    const title = (v('bl-edit-title')?.value || '').trim();
    if (!title) { toastErr('標題不可為空'); return; }
    const patch = {
        title,
        note: v('bl-edit-note')?.value || '',
        priority: v('bl-edit-prio')?.value || 'med',
        status: v('bl-edit-status')?.value || 'todo',
        assignee: v('bl-edit-assignee')?.value || 'me',
        category: (v('bl-edit-cat')?.value || '').trim(),
        pinned: !!v('bl-edit-pinned')?.checked,
    };
    try {
        await bfetch(`/api/v1/bulletin/${id}`, { method: 'PUT', body: patch });
        toastOk('已儲存');
        _bl.closeEdit();
        await _reload();
    } catch (e) { toastErr(e.message); }
};

// ══════════════════════════════════════════════════════════
// 🤖 問 Claude — 唯讀諮詢對話（per item）
// ══════════════════════════════════════════════════════════
//
// 送出流程：POST /ask 立刻回 {status:'asking'}，Claude 在背景跑 ~20-40s。故
// 樂觀把使用者訊息 + 「思考中…」placeholder 貼進 thread、停用送出鈕，接著每
// 2.5s 輪詢 GET /bulletin/{id}，直到 conversation 長出 claude 新訊息才重繪收尾。
//
// 過期輪詢防護：用兩個模組級變數把關 —
//   _chatItemId   目前開著的諮詢 modal 對應的 item id（closeChat 設回 null）
//   _chatPollToken 單調遞增；開窗 / 關窗 / 每次送出都 ++，使先前那輪輪詢失效
// 每次 send 開始時擷取 myToken = ++_chatPollToken，之後每個 await 邊界都重驗
//   (_chatItemId === id && _chatPollToken === myToken)，不符就靜默停止，
//   絕不碰已關閉 / 已換人 / 已被新一次送出取代的 modal DOM。
let _chatItemId = null;
let _chatPollToken = 0;

function _msgBubble(m) {
    const isUser = m.role === 'user';
    const label = isUser ? '你' : '🤖 Claude';
    const side = isUser ? 'bl-chat-user' : 'bl-chat-claude';
    return `<div class="bl-chat-row ${side}"><div class="bl-chat-label">${label}</div>` +
           `<div class="bl-chat-bubble">${esc(m.text || '')}</div></div>`;
}

function _threadHtml(convo) {
    if (!Array.isArray(convo) || !convo.length) {
        return `<div class="bl-chat-empty">問 Claude 關於這則待辦的任何事（怎麼做、幫我起草、研究…）</div>`;
    }
    return convo.map(_msgBubble).join('');
}

// 重繪 thread：convo 主體 + 可選「思考中…」placeholder + 可選錯誤行，並捲到底
function _paintThread(convo, thinking = false, errorLine = '') {
    const el = document.getElementById('bl-chat-thread');
    if (!el) return;
    let html = _threadHtml(convo);
    if (thinking) {
        html += `<div class="bl-chat-row bl-chat-claude"><div class="bl-chat-label">🤖 Claude</div>` +
                `<div class="bl-chat-bubble bl-chat-thinking">思考中…</div></div>`;
    }
    if (errorLine) html += `<div class="bl-chat-err">${esc(errorLine)}</div>`;
    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;
}

function _setSendDisabled(disabled) {
    const btn = document.getElementById('bl-chat-send');
    if (btn) { btn.disabled = disabled; btn.textContent = disabled ? '送出中…' : '送出'; }
}

_bl.chat = (id) => {
    const it = _items.find(x => x.id === id);
    if (!it) return;
    _chatItemId = id;
    _chatPollToken++;   // 新開一輪對話上下文；任何殘留輪詢即刻失效
    const inner = `
        <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
                <div style="min-width:0;">
                    <h3 style="margin:0 0 3px;color:#fff;font-size:15px;">🤖 問 Claude</h3>
                    <div style="color:#c4b5fd;font-size:13px;word-break:break-word;">${esc(it.title || '(未命名)')}</div>
                </div>
                <button onclick="window._bl.closeChat()" style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;width:30px;height:30px;border-radius:4px;flex-shrink:0;">✕</button>
            </div>
            <div style="color:#777;font-size:11px;margin-top:8px;">唯讀諮詢：Claude 只給建議/草稿，不會改系統</div>
        </div>
        <div id="bl-chat-thread" class="bl-chat-thread">${_threadHtml(it.conversation)}</div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;gap:8px;align-items:flex-end;">
            <textarea id="bl-chat-input" rows="2" placeholder="輸入問題…（Enter 送出 · Shift+Enter 換行）" style="${_inp()};resize:vertical;flex:1;"></textarea>
            <button id="bl-chat-send" class="btn" style="background:#7c3aed;flex-shrink:0;" onclick="window._bl.send('${it.id}')">送出</button>
        </div>`;
    openModal('bl-chat-modal', inner, { width: '600px' });
    const ta = document.getElementById('bl-chat-input');
    if (ta) {
        ta.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _bl.send(id); }
        });
        ta.focus();
    }
    // 開窗後把既有對話捲到底
    const thread = document.getElementById('bl-chat-thread');
    if (thread) thread.scrollTop = thread.scrollHeight;
};

_bl.closeChat = () => {
    _chatItemId = null;
    _chatPollToken++;   // 讓任何進行中的輪詢在下一個守衛點停止
    closeModal('bl-chat-modal');
};

_bl.send = async (id) => {
    if (_chatItemId !== id) return;   // modal 已關 / 已換人
    const ta = document.getElementById('bl-chat-input');
    const msg = (ta?.value || '').trim();
    if (!msg) { ta?.focus(); return; }

    const it = _items.find(x => x.id === id);
    // baseConvo = 送出前已知對話；baseLen 之後 +1（POST 同步塞入的使用者訊息），
    // 再 +1（背景 claude 回覆）→ 輪詢偵測「長度 > baseLen+1」即代表回覆到位。
    const baseConvo = (it && Array.isArray(it.conversation)) ? it.conversation.slice() : [];
    const baseLen = baseConvo.length;
    const optimistic = baseConvo.concat([{ role: 'user', text: msg }]);

    _paintThread(optimistic, true);   // 樂觀：使用者訊息 + 思考中…
    if (ta) ta.value = '';
    _setSendDisabled(true);

    const myToken = ++_chatPollToken;   // 這次送出專屬 token；關窗 / 再送都會使其失效

    try {
        await bfetch(`/api/v1/bulletin/${id}/ask`, { method: 'POST', body: { message: msg } });
    } catch (e) {
        if (_chatItemId === id && _chatPollToken === myToken) {
            _paintThread(optimistic, false, `送出失敗：${e.message || e}`);
            _setSendDisabled(false);
        }
        return;
    }

    const started = Date.now();
    const CAP_MS = 210000;   // 3.5 分鐘上限
    const poll = async () => {
        // 守衛：modal 關了 / 換 item / 又送了新一則 → 靜默停止
        if (_chatItemId !== id || _chatPollToken !== myToken) return;
        if (Date.now() - started > CAP_MS) {
            _paintThread(optimistic, false, 'Claude 回覆逾時（可能仍在背景處理，稍後重開此對話查看）');
            _setSendDisabled(false);
            return;
        }
        let data = null;
        try {
            data = await bfetch(`/api/v1/bulletin/${id}`);
        } catch { /* 暫時性失敗：忽略，下一輪再試 */ }
        if (_chatItemId !== id || _chatPollToken !== myToken) return;   // await 後再驗一次
        const conv = data && Array.isArray(data.conversation) ? data.conversation : null;
        if (conv && conv.length > baseLen + 1) {
            if (it) it.conversation = conv;   // 更新快取，供下次送出算 baseLen
            _paintThread(conv, false);
            _setSendDisabled(false);
            return;
        }
        setTimeout(poll, 2500);
    };
    setTimeout(poll, 2500);
};
