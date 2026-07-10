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
    if (body) body.addEventListener('change', _onListChange);

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
      <div style="flex:1.6;min-width:80px;display:flex;align-items:center;gap:6px;overflow:hidden;">
        <span style="font-weight:600;color:#e0e0e0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(it.name)}</span>
        ${_progressBadge(it.summary)}
      </div>
      <div style="flex:1;min-width:60px;color:#9ca3af;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(it.client_name) || '—'}</div>
      <div style="flex:0.8;min-width:70px;color:#6b7280;font-size:12px;">${it.completion_date ? esc(String(it.completion_date).substring(0, 10)) : '—'}</div>
      <div style="flex:0.7;min-width:60px;">${_stageBadge(stage)}${stage === '已上線'
        ? (it.verified
            ? '<span title="rebuild 後對外頁實測 200 通過" style="color:#86efac;font-size:11px;margin-left:3px;">✓</span>'
            : '<span title="等待下次發布時自動驗證對外頁" style="color:#f59e0b;font-size:11px;margin-left:3px;">驗證中</span>')
        : ''}</div>
      <div style="flex:1.4;min-width:130px;display:flex;gap:4px;flex-wrap:wrap;">
        ${_chip('影片', c.video)}${_chip('圖', c.images)}${_chip('說明', c.description)}${_chip('credits', c.credits)}
      </div>
      <div style="flex:0.3;min-width:24px;text-align:center;font-size:14px;">${featured}</div>
      <div style="flex:1.7;min-width:190px;display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap;">
        <button class="crm-btn crm-btn-primary crm-btn-sm" data-action="edit" data-id="${esc(it.id)}">編輯官網內容</button>
        <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="publish" data-id="${esc(it.id)}">${publishLabel}</button>
        <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="stage-toggle" data-id="${esc(it.id)}">階段 ▾</button>
        ${canPreview ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="preview" data-slug="${esc(it.slug)}">預覽</button>` : ''}
        <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="add-work" data-id="${esc(it.id)}" title="在此專案下新增一個官網作品">＋ 新增影片作品</button>
      </div>
    </div>${_renderWorksBlock(it)}`;
}

// ── 子作品進度徽章（summary.total − skipped > 1 才顯示）──────
function _progressBadge(summary) {
    if (!summary) return '';
    const effTotal = (summary.total || 0) - (summary.skipped || 0);
    if (effTotal <= 1) return '';
    const live = summary.live || 0;
    const allLive = !!summary.all_live || live >= effTotal;
    const st = allLive ? 'background:#14532d;color:#86efac;' : 'background:#3b2a1f;color:#fb923c;';
    return `<span title="子作品上線進度（不計「不上官網」）" style="display:inline-block;padding:1px 7px;border-radius:999px;font-size:10px;font-weight:600;white-space:nowrap;${st}">${live}/${effTotal} 已上線</span>`;
}

// ── works 子列區塊（>1 個作品才顯示，避免單作品時的視覺雜訊）──
function _renderWorksBlock(it) {
    const works = it.works || [];
    if (works.length <= 1) return '';
    return `
    <div style="margin:2px 8px 10px 28px;padding:4px 10px;background:#141414;border:1px solid #262626;border-left:2px solid #3b82f6;border-radius:6px;">
      ${works.map(_renderWorkRow).join('')}
    </div>`;
}

function _renderWorkRow(w) {
    const stage = w.stage || '待製作';
    const publishLabel = w.published ? '下架' : '發佈';
    const verifiedMark = stage === '已上線'
        ? (w.verified
            ? '<span title="rebuild 後對外頁實測 200 通過" style="color:#86efac;font-size:11px;margin-left:3px;">✓</span>'
            : '<span title="等待下次發布時自動驗證對外頁" style="color:#f59e0b;font-size:11px;margin-left:3px;">驗證中</span>')
        : '';
    const primaryTag = w.is_primary
        ? '<span title="主作品" style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:#1e3a5f;color:#93c5fd;flex-shrink:0;">主</span>'
        : '';
    return `
      <div style="display:flex;align-items:center;gap:8px;padding:4px 2px;border-bottom:1px solid #1f1f1f;flex-wrap:wrap;">
        <div style="flex:1.6;min-width:120px;display:flex;align-items:center;gap:6px;overflow:hidden;">
          ${primaryTag}
          <span style="color:#d1d5db;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(w.title || '（未命名作品）')}</span>
        </div>
        <div style="flex:0.8;min-width:90px;white-space:nowrap;">${_stageBadge(stage)}${verifiedMark}</div>
        <div style="flex:0.6;min-width:110px;">${_workStageSelect(w)}</div>
        <div style="flex:1;min-width:150px;display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap;">
          <button class="crm-btn crm-btn-primary crm-btn-sm" data-action="work-edit" data-work-id="${esc(w.id)}">編輯</button>
          <button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="work-publish" data-work-id="${esc(w.id)}">${publishLabel}</button>
          ${w.slug ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-action="preview" data-slug="${esc(w.slug)}">預覽</button>` : ''}
          ${(!w.is_primary && !w.published) ? `<button class="crm-btn crm-btn-danger crm-btn-sm" data-action="work-delete" data-work-id="${esc(w.id)}" title="刪除此子作品（主作品不可刪；已發布請先下架）">刪除</button>` : ''}
        </div>
      </div>`;
}

// 作品階段 select — PATCH /works/{id}/stage 只收 待製作/製作中/不上官網；
// 「已上線」由發佈流程控制，若目前是已上線就以 disabled option 呈現現值。
function _workStageSelect(w) {
    const opts = ['待製作', '製作中', '不上官網'];
    const cur = w.stage || '待製作';
    const extra = opts.includes(cur) ? '' : `<option value="${esc(cur)}" selected disabled>${esc(cur)}</option>`;
    return `<select class="crm-select" data-action="work-stage" data-work-id="${esc(w.id)}" title="切換此作品的官網階段" style="font-size:11px;padding:2px 4px;">
        ${extra}${opts.map(s => `<option value="${esc(s)}"${s === cur ? ' selected' : ''}>${esc(s)}</option>`).join('')}
    </select>`;
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
    if (action === 'add-work') return _addWork(id);
    if (action === 'work-edit') return _editWork(btn.dataset.workId);
    if (action === 'work-publish') return _toggleWorkPublish(btn.dataset.workId);
    if (action === 'work-delete') return _deleteWork(btn.dataset.workId);
}

// works 子列的階段 select（change 事件委派）
function _onListChange(e) {
    const sel = e.target.closest('select[data-action="work-stage"]');
    if (!sel) return;
    _setWorkStage(sel.dataset.workId, sel.value);
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

// ── 逐作品動作（1 專案 : N 作品）─────────────────────────────
async function _addWork(projectId) {
    try {
        const r = await crmFetch(`/projects/${projectId}/works`, { method: 'POST', body: '{}' });
        if (!r || !r.edit_url) throw new Error('未取得編輯連結');
        _openEditPanel(r.edit_url, `編輯：${r.title || '新作品'}`);
    } catch (e) {
        alert('新增作品失敗：' + e.message);
    }
}

async function _editWork(workId) {
    try {
        const r = await crmFetch(`/works/${workId}/edit-token`);
        const url = r.url || (r.token ? `/showcase-edit.html?token=${encodeURIComponent(r.token)}` : '');
        if (!url) throw new Error('未取得編輯連結');
        let title = '';
        for (const it of _items) {
            const w = (it.works || []).find(x => String(x.id) === String(workId));
            if (w) { title = w.title || it.name; break; }
        }
        _openEditPanel(url, `編輯：${title || workId}`);
    } catch (e) {
        alert('無法開啟編輯：' + e.message);
    }
}

async function _toggleWorkPublish(workId) {
    try {
        await crmFetch(`/works/${workId}/publish`, { method: 'POST' });
        await _loadList();
    } catch (e) {
        alert('發佈/下架失敗：' + e.message);
    }
}

async function _deleteWork(workId) {
    let title = workId;
    for (const it of _items) {
        const w = (it.works || []).find(x => String(x.id) === String(workId));
        if (w) { title = w.title || workId; break; }
    }
    if (!confirm(`確定刪除子作品「${title}」？\n（連帶清除其分類 / SEO / 翻譯狀態，無法復原）`)) return;
    try {
        await crmFetch(`/works/${workId}`, { method: 'DELETE' });
        await _loadList();
    } catch (e) {
        alert('刪除失敗：' + e.message);
    }
}

async function _setWorkStage(workId, stage) {
    try {
        await crmFetch(`/works/${workId}/stage`, { method: 'PATCH', body: JSON.stringify({ stage }) });
    } catch (e) {
        alert('階段更新失敗：' + e.message);
    }
    await _loadList();  // 成功→帶回新階段；失敗→還原 select 顯示
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
