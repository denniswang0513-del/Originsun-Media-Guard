/**
 * bulletin.js — 📌 公布欄 Tab 主進入點
 *
 * ⚠️ MASTER 同源功能：打 master 自己的 /api/v1/bulletin（帶 auth token），
 *    不走 NAS website-api。因此 *不用* websiteFetch / getApiBase，改用本檔的 bfetch。
 *
 * 結構複用官網 Tab：左側子視圖導覽 + 右側內容容器。目前只有一個子視圖：
 *    📋 待辦提醒（todo）。board 直接 render 進 #bulletin-content。
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
const _bl = (window._bl = window._bl || {});

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
    if (!_content) return;
    if (name === 'todo') {
        _content.innerHTML = _LOADING_HTML;
        try {
            await _load();
        } catch (e) {
            _content.innerHTML = `<div style="color:#f88;padding:24px;">載入失敗：${esc(e.message || e)}</div>`;
            return;
        }
        _renderBoard();
    }
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
    const visible = _showDone ? _items : _items.filter(it => it.status !== 'done');
    const doneCount = _items.filter(it => it.status === 'done').length;

    const list = visible.length
        ? visible.map(_card).join('')
        : `<div style="color:#888;padding:48px 24px;text-align:center;font-size:14px;">目前沒有待辦 🎉</div>`;

    _content.innerHTML = `
        <h2 style="margin:0 0 4px;">📋 待辦提醒 <span style="color:#888;font-size:12px;font-weight:400;">· 內部公布欄 / 團隊待辦</span></h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">釘選的項目會排在最前面。勾選左側圓圈標記完成。</p>

        <!-- 快速新增 -->
        <div class="card" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <input id="bl-add-title" placeholder="輸入待辦標題後按 Enter 或「+ 新增」" style="${_inp()};flex:1 1 260px;min-width:200px;" />
            <select id="bl-add-prio" title="優先級" style="${_inp()};max-width:110px;">
                <option value="high">🔴 高</option>
                <option value="med" selected>🟠 中</option>
                <option value="low">⚪ 低</option>
            </select>
            <input id="bl-add-cat" placeholder="分類（選填）" style="${_inp()};max-width:150px;" />
            <button class="btn" style="background:#059669;" onclick="window._bl.add()">+ 新增</button>
        </div>

        <!-- 篩選 -->
        <div style="display:flex;align-items:center;gap:10px;margin:6px 0 12px;font-size:12px;color:#999;">
            <button class="btn btn-sm btn-ghost" onclick="window._bl.toggleFilter()">
                ${_showDone ? '👁 顯示：全部' : '🙈 顯示：未完成'}
            </button>
            <span>共 ${_items.length} 則${doneCount ? ` · 已完成 ${doneCount}` : ''}</span>
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

    const prioPill = `<span class="website-pill" style="background:${p[1]};color:${p[2]};">${p[0]}</span>`;
    // 進行中額外顯示藍 pill（待辦/完成分別以預設樣式/刪除線呈現）
    const doingPill = it.status === 'doing'
        ? `<span class="website-pill" style="background:${STATUS.doing[1]};color:${STATUS.doing[2]};">${STATUS.doing[0]}</span>` : '';
    const catPill = it.category
        ? `<span class="website-pill">🏷 ${esc(it.category)}</span>` : '';
    const pinIcon = it.pinned ? '📌' : '📍';
    const pinStyle = it.pinned ? 'opacity:1;' : 'opacity:0.4;';

    const statusSel = `
        <select onchange="window._bl.setStatus(${it.id}, this.value)"
                style="background:#0d0d0d;border:1px solid #333;color:#ccc;padding:3px 6px;border-radius:4px;font-size:11px;">
            <option value="todo"  ${it.status === 'todo' ? 'selected' : ''}>待辦</option>
            <option value="doing" ${it.status === 'doing' ? 'selected' : ''}>進行中</option>
            <option value="done"  ${it.status === 'done' ? 'selected' : ''}>完成</option>
        </select>`;

    return `
        <div class="bl-card ${isDone ? 'bl-done' : ''}">
            <div class="bl-check ${isDone ? 'on' : ''}" onclick="window._bl.toggleDone(${it.id})" title="標記${isDone ? '未完成' : '完成'}">${isDone ? '✓' : ''}</div>
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span class="bl-title" onclick="window._bl.edit(${it.id})">${esc(it.title || '(未命名)')}</span>
                    ${prioPill}${doingPill}${catPill}
                </div>
                ${it.note ? `<div class="bl-note">${esc(it.note)}</div>` : ''}
            </div>
            <div style="display:flex;align-items:center;gap:4px;flex-shrink:0;">
                ${statusSel}
                <button class="bl-icon-btn" style="${pinStyle}" onclick="window._bl.togglePin(${it.id})" title="${it.pinned ? '取消釘選' : '釘選'}">${pinIcon}</button>
                <button class="bl-icon-btn" onclick="window._bl.move(${it.id},-1)" ${atTop ? 'disabled' : ''} title="上移">▲</button>
                <button class="bl-icon-btn" onclick="window._bl.move(${it.id},1)" ${atBottom ? 'disabled' : ''} title="下移">▼</button>
                <button class="bl-icon-btn" onclick="window._bl.edit(${it.id})" title="編輯">✏️</button>
                <button class="bl-icon-btn" onclick="window._bl.del(${it.id})" title="刪除" style="color:#f87171;">🗑</button>
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

_bl.move = async (id, dir) => {
    const arr = _items.slice();
    const i = arr.findIndex(x => x.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
    // 樂觀更新排序（避免閃動），再送後端；失敗則重抓校正
    _items = arr;
    _renderBoard();
    try {
        await bfetch('/api/v1/bulletin/reorder', { method: 'POST', body: { ordered_ids: arr.map(x => x.id) } });
        await _reload();
    } catch (e) { toastErr(e.message); await _reload(); }
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
            <label style="color:#9aa0a6;font-size:12px;">分類</label>
            <input id="bl-edit-cat" value="${esc(it.category || '')}" placeholder="選填" style="${_inp()};max-width:220px;" />
            <label style="color:#9aa0a6;font-size:12px;">釘選</label>
            <label style="color:#ddd;display:inline-flex;gap:6px;align-items:center;font-size:13px;">
                <input id="bl-edit-pinned" type="checkbox" ${it.pinned ? 'checked' : ''} style="width:auto;" /> 📌 釘選到最前面
            </label>
        </div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;">
            <button class="btn btn-ghost btn-sm" onclick="window._bl.closeEdit()">取消</button>
            <button class="btn" style="background:#059669;" onclick="window._bl.saveEdit(${it.id})">✓ 儲存</button>
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
