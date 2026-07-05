/**
 * crm-projects-closing.js — 結案作業（官網製作收件匣）
 *
 * 「專案管理」下的第 3 個子分頁。列出狀態＝「結案作業」的專案（AM 把已結案專案
 * 推進到「結案作業」才進來），讓 PM 依「官網階段」
 * 逐一把作品做上官網：編輯官網內容（嵌 showcase-edit iframe overlay）、
 * 發佈/下架、切換階段、預覽對外頁。
 *
 * 所有 API 走 master CRM（/api/v1/crm，crmFetch）。RBAC 由 crm-projects.js
 * 的子分頁 gate（管理員 or website_admin）＋後端再閘一次。
 *
 * 慣例對齊：dark theme、.crm-toolbar / .crm-list-header / .crm-row / .crm-empty
 * 等既有 class；iframe overlay 複用 website/subviews/works.js 的樣式與 postMessage
 * 協定（showcase-saved / showcase-title-change）。
 */

import { crmFetch, esc } from './crm-utils.js';

// 對外站基底 — 預覽作品頁用（與 website-utils.js 的 NAS_PUBLIC_BASE 一致）。
// 假設：closing 收件匣不引入 website-utils（避免跨 tab side-effect），故此處直接常數化。
const PUBLIC_SITE_BASE = 'https://www.originsun-studio.com';

let _container = null;
let _items = [];
let _filterStage = '';
let _search = '';

// ── 入口 ─────────────────────────────────────────────────────
export async function init(container) {
    _container = container;
    container.innerHTML = `
    <div class="crm-root" style="height:100%;min-height:0;">
      <div class="crm-toolbar">
        <input id="closing-search" type="search" placeholder="搜尋專案名稱..." class="crm-search-input">
        <select id="closing-filter-stage" class="crm-select">
          <option value="">全部階段</option>
          <option value="待製作">待製作</option>
          <option value="製作中">製作中</option>
          <option value="已上線">已上線</option>
          <option value="不上官網">不上官網</option>
        </select>
        <div class="crm-toolbar-right">
          <button id="closing-refresh" class="crm-btn crm-btn-secondary">重新整理</button>
        </div>
      </div>
      <div class="crm-body">
        <div class="crm-list-panel" style="flex:1;">
          <div class="crm-list-header">
            <span style="flex:1.6;min-width:80px;">專案名稱</span>
            <span style="flex:1;min-width:60px;">客戶</span>
            <span style="flex:0.8;min-width:70px;">完成日</span>
            <span style="flex:0.7;min-width:60px;">官網階段</span>
            <span style="flex:1.4;min-width:130px;">完成度</span>
            <span style="flex:0.3;min-width:24px;text-align:center;">⭐</span>
            <span style="flex:1.7;min-width:190px;text-align:right;">動作</span>
          </div>
          <div id="closing-list-body">
            <div class="crm-empty">載入中…</div>
          </div>
        </div>
      </div>
    </div>`;

    // ── Toolbar 事件 ──
    const searchEl = container.querySelector('#closing-search');
    if (searchEl) searchEl.addEventListener('input', (e) => { _search = e.target.value.trim(); _render(); });
    const stageEl = container.querySelector('#closing-filter-stage');
    if (stageEl) stageEl.addEventListener('change', (e) => { _filterStage = e.target.value; _render(); });
    const refreshEl = container.querySelector('#closing-refresh');
    if (refreshEl) refreshEl.addEventListener('click', () => _loadList());

    // ── List body 事件委派 ──
    const body = container.querySelector('#closing-list-body');
    if (body) body.addEventListener('click', _onListClick);

    await _loadList();
}

// ── 資料載入 ─────────────────────────────────────────────────
async function _loadList() {
    const body = _container?.querySelector('#closing-list-body');
    if (body && _items.length === 0) body.innerHTML = `<div class="crm-empty">載入中…</div>`;
    try {
        const data = await crmFetch('/projects/closing');
        _items = (data && data.items) || [];
    } catch (e) {
        if (body) body.innerHTML = `<div class="crm-empty" style="color:#fca5a5;">載入失敗：${esc(e.message)}</div>`;
        return;
    }
    _render();
}

// ── 渲染 ─────────────────────────────────────────────────────
function _render() {
    const body = _container?.querySelector('#closing-list-body');
    if (!body) return;

    const q = _search.toLowerCase();
    const rows = _items.filter(it => {
        if (_filterStage && (it.stage || '待製作') !== _filterStage) return false;
        if (q && !String(it.name || '').toLowerCase().includes(q)) return false;
        return true;
    });

    if (rows.length === 0) {
        body.innerHTML = `<div class="crm-empty">${_items.length === 0 ? '目前沒有「結案作業」中的專案。把已結案專案的狀態改成「結案作業（送官網製作）」即可進來。' : '沒有符合條件的專案'}</div>`;
        return;
    }

    body.innerHTML = rows.map(_renderRow).join('');
}

function _renderRow(it) {
    const stage = it.stage || '待製作';
    const c = it.completeness || {};
    const featured = it.public_featured
        ? '<span title="首頁精選" style="color:#fbbf24;">★</span>'
        : '<span title="未精選" style="color:#4b5563;">☆</span>';
    const canPreview = !!(it.public && it.slug);
    const publishLabel = it.showcase_published ? '下架' : '發佈';

    return `
    <div class="crm-row" data-id="${esc(it.id)}" style="cursor:default;">
      <div style="flex:1.6;min-width:80px;font-weight:600;color:#e0e0e0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(it.name)}</div>
      <div style="flex:1;min-width:60px;color:#9ca3af;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(it.client_name) || '—'}</div>
      <div style="flex:0.8;min-width:70px;color:#6b7280;font-size:12px;">${it.completion_date ? esc(String(it.completion_date).substring(0, 10)) : '—'}</div>
      <div style="flex:0.7;min-width:60px;">${_stageBadge(stage)}</div>
      <div style="flex:1.4;min-width:130px;display:flex;gap:4px;flex-wrap:wrap;">
        ${_chip('影片', c.video)}${_chip('圖', c.images)}${_chip('過程', c.process)}${_chip('credits', c.credits)}
      </div>
      <div style="flex:0.3;min-width:24px;text-align:center;font-size:14px;">${featured}</div>
      <div style="flex:1.7;min-width:190px;display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap;">
        <button class="crm-btn crm-btn-primary crm-btn-sm" data-action="edit" data-id="${esc(it.id)}">編輯官網內容</button>
        <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="publish" data-id="${esc(it.id)}">${publishLabel}</button>
        <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="stage-toggle" data-id="${esc(it.id)}">階段 ▾</button>
        ${canPreview ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="preview" data-slug="${esc(it.slug)}">預覽</button>` : ''}
      </div>
    </div>`;
}

function _stageBadge(stage) {
    const styles = {
        '待製作': 'background:#374151;color:#9ca3af;',
        '製作中': 'background:#3b2a1f;color:#fb923c;',
        '已上線': 'background:#14532d;color:#86efac;',
        '不上官網': 'background:#262626;color:#6b7280;',
    };
    const st = styles[stage] || styles['待製作'];
    return `<span style="display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap;${st}">${esc(stage)}</span>`;
}

function _chip(label, ok) {
    const on = !!ok;
    const bg = on ? '#14532d' : '#2a2a2a';
    const color = on ? '#86efac' : '#6b7280';
    const mark = on ? '✓' : '✗';
    return `<span title="${esc(label)}${on ? '：已完成' : '：未完成'}" style="display:inline-flex;align-items:center;gap:2px;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:${bg};color:${color};">${mark}${esc(label)}</span>`;
}

// ── 列表動作（事件委派）─────────────────────────────────────
function _onListClick(e) {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const id = btn.dataset.id;
    if (action === 'edit') return _editContent(id);
    if (action === 'publish') return _togglePublish(id);
    if (action === 'stage-toggle') return _openStageMenu(btn, id);
    if (action === 'preview') return _preview(btn.dataset.slug);
}

async function _editContent(id) {
    try {
        const r = await crmFetch(`/projects/${id}/showcase/generate-edit-token`, { method: 'POST' });
        const url = r.url || (r.token ? `/showcase-edit.html?token=${encodeURIComponent(r.token)}` : '');
        if (!url) throw new Error('未取得編輯連結');
        const it = _items.find(x => String(x.id) === String(id));
        _openEditPanel(url, `編輯：${(it && it.name) || id}`);
    } catch (e) {
        alert('無法開啟編輯：' + e.message);
    }
}

async function _togglePublish(id) {
    try {
        await crmFetch(`/projects/${id}/showcase/publish`, { method: 'POST' });
        await _loadList();
    } catch (e) {
        alert('發佈/下架失敗：' + e.message);
    }
}

async function _setStage(id, stage) {
    try {
        await crmFetch(`/projects/${id}/website-stage`, { method: 'PATCH', body: JSON.stringify({ stage }) });
        await _loadList();
    } catch (e) {
        alert('階段更新失敗：' + e.message);
    }
}

function _preview(slug) {
    if (!slug) return;
    window.open(`${PUBLIC_SITE_BASE}/works/${encodeURIComponent(slug)}`, '_blank', 'noopener');
}

// ── 「階段」小選單（position:fixed，避免被 list overflow 裁切）─────
function _openStageMenu(anchorBtn, id) {
    _closeStageMenu();
    const menu = document.createElement('div');
    menu.id = 'closing-stage-menu';
    menu.style.cssText = 'position:fixed;z-index:9500;background:#1e1e1e;border:1px solid #3a3a3a;border-radius:6px;padding:4px;box-shadow:0 4px 16px rgba(0,0,0,0.5);min-width:120px;';
    const opts = ['製作中', '待製作', '不上官網'];
    menu.innerHTML = opts.map(s =>
        `<div class="_closing-stage-item" data-stage="${esc(s)}" style="padding:6px 12px;font-size:13px;color:#e0e0e0;cursor:pointer;border-radius:4px;">${esc(s)}</div>`
    ).join('');
    document.body.appendChild(menu);
    const rect = anchorBtn.getBoundingClientRect();
    menu.style.top = (rect.bottom + 2) + 'px';
    // 靠右對齊按鈕右緣，避免超出視窗
    menu.style.left = Math.max(8, rect.right - 120) + 'px';
    menu.querySelectorAll('._closing-stage-item').forEach(el => {
        el.addEventListener('mouseenter', () => { el.style.background = '#333'; });
        el.addEventListener('mouseleave', () => { el.style.background = ''; });
        el.addEventListener('click', async () => {
            const stage = el.dataset.stage;
            _closeStageMenu();
            await _setStage(id, stage);
        });
    });
}

function _closeStageMenu() {
    const m = document.getElementById('closing-stage-menu');
    if (m) m.remove();
}

// 點選單外 → 關閉（module 只 import 一次，故此 listener 只註冊一次）
document.addEventListener('click', (e) => {
    if (e.target.closest('[data-action="stage-toggle"]')) return; // 讓 toggle 自己開/切換
    if (e.target.closest('#closing-stage-menu')) return;
    _closeStageMenu();
});

// ── 編輯 overlay（嵌 /showcase-edit.html iframe，複用 works.js 協定）────
let _msgInstalled = false;
function _installMsgListener() {
    if (_msgInstalled) return;
    _msgInstalled = true;
    window.addEventListener('message', (e) => {
        const t = e && e.data && e.data.type;
        if (t === 'showcase-saved') {
            _loadList();
        } else if (t === 'showcase-title-change') {
            const titleEl = document.getElementById('closing-edit-title');
            if (titleEl && e.data.title) titleEl.textContent = `編輯：${e.data.title}`;
        }
    });
}

function _ensureOverlay() {
    _installMsgListener();
    if (document.getElementById('closing-edit-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'closing-edit-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9000;display:none;align-items:stretch;justify-content:flex-end;';
    overlay.innerHTML = `
        <div style="width:80%;max-width:1000px;height:100%;background:#0e0e0e;border-left:1px solid #2a2a2a;display:flex;flex-direction:column;box-shadow:-8px 0 24px rgba(0,0,0,0.6);">
            <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;border-bottom:1px solid #2a2a2a;background:#161616;flex-shrink:0;">
                <strong id="closing-edit-title" style="color:#fff;font-size:14px;flex:1;">編輯官網內容</strong>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" id="closing-edit-close">關閉並重新整理</button>
            </div>
            <iframe id="closing-edit-iframe" style="flex:1;width:100%;border:0;background:#0e0e0e;"></iframe>
        </div>`;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) _closeOverlay(); });
    document.body.appendChild(overlay);
    document.getElementById('closing-edit-close').addEventListener('click', _closeOverlay);
}

function _openEditPanel(url, title) {
    _ensureOverlay();
    const overlay = document.getElementById('closing-edit-overlay');
    const iframe = document.getElementById('closing-edit-iframe');
    const titleEl = document.getElementById('closing-edit-title');
    if (titleEl) titleEl.textContent = title || '編輯官網內容';
    if (iframe) iframe.src = url;
    if (overlay) overlay.style.display = 'flex';
}

function _closeOverlay() {
    const overlay = document.getElementById('closing-edit-overlay');
    if (!overlay) return;
    overlay.style.display = 'none';
    const iframe = document.getElementById('closing-edit-iframe');
    if (iframe) iframe.src = 'about:blank';
    _loadList();
}

export default { init };
