/**
 * credits.js — 演職員管理（職位庫 + 模板）
 *
 * 2 個 card：
 *   1. 職位庫（CRUD website_credit_roles）— inline 編輯表格
 *   2. 模板（CRUD website_credit_templates）— 卡片網格 + Modal 編輯（左右兩欄職位選擇器）
 *
 * 任一寫入後端會 mark_dirty → 60s debounce 觸發 Astro rebuild。
 */
import {
    websiteFetch, esc, toastOk, toastErr,
    renderLoadError, readRowPatch, openModal, closeModal,
    emptyRow, emptyHint,
} from '../website-utils.js';

let _state = { roles: [], templates: [] };
let _container = null;

// 模板 Modal 暫存（編輯時把選中的 role_ids 拉出來操作；存檔才送 PUT）
let _modalState = { id: null, name: '', description: '', selected: [] /* role_ids in order */ };


// ── 共用 helpers ──

const _sortByOrder = (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id;

function _roleLabel(r, smallColor = '#888') {
    const en = r.name_en
        ? `<small style="color:${smallColor};margin-left:6px;">${esc(r.name_en)}</small>`
        : '';
    return `${esc(r.name_zh)}${en}`;
}


export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🎭 演職員</h2><div style="color:#888;padding:20px;">載入中…</div>';

    try {
        const [roles, tpls] = await Promise.all([
            websiteFetch('/api/website/admin/credit_roles'),
            websiteFetch('/api/website/admin/credit_templates'),
        ]);
        if (!isCurrent()) return;
        _state.roles = roles?.items || [];
        _state.templates = tpls?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        const hint = e.status === 404
            ? 'NAS website-api 可能跑舊版（沒 admin_credits router）。請在 master 跑 /publish 同步後端到 NAS。'
            : '';
        renderLoadError(container, '🎭 演職員', e, hint);
        return;
    }

    _renderAll();
}


function _renderAll() {
    _container.innerHTML = `
        <h2>🎭 演職員 <span style="color:#888;font-size:13px;font-weight:400;">· 職位庫管理 + 模板組合</span></h2>
        <div style="display:grid;grid-template-columns:1fr;gap:16px;max-width:1100px;">
            ${_cardRoles()}
            ${_cardTemplates()}
        </div>
    `;
}


// ===== Card 1：職位庫 =====
function _cardRoles() {
    const sorted = [..._state.roles].sort(_sortByOrder);
    const rows = sorted.map(r => `
        <tr>
            <td><input type="number" data-id="${r.id}" data-field="sort_order" value="${r.sort_order ?? 0}" style="width:60px;" /></td>
            <td><input data-id="${r.id}" data-field="name_zh" value="${esc(r.name_zh)}" style="width:100%;" /></td>
            <td><input data-id="${r.id}" data-field="name_en" value="${esc(r.name_en || '')}" style="width:100%;" /></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${r.id}" data-field="visible" ${r.visible ? 'checked' : ''} /></td>
            <td style="text-align:center;color:${(r.usage_count || 0) > 0 ? '#8be8a5' : '#666'};font-size:12px;">${r.usage_count || 0} 件</td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="window._credits.saveRole(${r.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._credits.deleteRole(${r.id})">🗑</button>
            </td>
        </tr>
    `).join('');

    return `<div id="credits-roles-card" class="card" style="border-left:3px solid #8b5cf6;">
        <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">📋 職位庫
            <span style="color:#888;font-size:11px;font-weight:400;">· ${_state.roles.length} 項</span>
        </h3>
        <div style="display:grid;grid-template-columns:1fr 1fr 80px 80px auto;gap:8px;margin-bottom:8px;align-items:end;">
            <div><label style="color:#888;font-size:11px;">中文名稱</label>
                <input id="role-new-zh" type="text" style="width:100%;" placeholder="導演" /></div>
            <div><label style="color:#888;font-size:11px;">英文名稱</label>
                <input id="role-new-en" type="text" style="width:100%;" placeholder="Director" /></div>
            <div><label style="color:#888;font-size:11px;">排序</label>
                <input id="role-new-sort" type="number" value="0" style="width:100%;" /></div>
            <div><label style="color:#888;font-size:11px;">顯示</label>
                <input id="role-new-visible" type="checkbox" checked style="height:30px;" /></div>
            <button class="btn" onclick="window._credits.createRole()">+ 新增</button>
        </div>
        <table>
            <thead><tr>
                <th style="width:60px;">排序</th>
                <th>中文名稱</th>
                <th>英文名稱</th>
                <th style="width:60px;text-align:center;">顯示</th>
                <th style="width:80px;text-align:center;">使用數</th>
                <th style="width:120px;"></th>
            </tr></thead>
            <tbody>${rows || emptyRow(6, '尚無職位，新增上方第一條')}</tbody>
        </table>
    </div>`;
}


// ===== Card 2：模板 =====
function _cardTemplates() {
    const sorted = [..._state.templates].sort(_sortByOrder);
    const cards = sorted.map(t => _templateCard(t)).join('');
    const addCard = `
        <div onclick="window._credits.openTemplateModal(null)"
             style="padding:14px;border-radius:6px;background:transparent;
                    border:2px dashed #3a3a3a;cursor:pointer;
                    display:flex;align-items:center;justify-content:center;
                    min-height:140px;color:#888;font-size:13px;
                    transition:all 0.15s;"
             onmouseover="this.style.borderColor='#3b82f6';this.style.color='#ccc';"
             onmouseout="this.style.borderColor='#3a3a3a';this.style.color='#888';">
            + 新增模板
        </div>
    `;
    const empty = !sorted.length
        ? emptyHint('尚無模板，點下方「+ 新增模板」建立第一個', { gridFull: true })
        : '';

    return `<div class="card" style="border-left:3px solid #06b6d4;">
        <h3 style="color:#fff;margin:0 0 6px;font-size:15px;">📦 模板
            <span style="color:#888;font-size:11px;font-weight:400;">· ${_state.templates.length} 個</span>
        </h3>
        <p style="color:#888;font-size:12px;margin:0 0 12px;">
            模板讓建立作品時一鍵展開常用職位組合，可在作品中再增刪。
        </p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;">
            ${empty}
            ${cards}
            ${addCard}
        </div>
    </div>`;
}


function _templateCard(t) {
    const roles = Array.isArray(t.roles) ? t.roles : [];
    const chips = roles.map(r =>
        `<span class="website-pill" style="margin:0 4px 4px 0;">${_roleLabel(r)}</span>`
    ).join('');
    const desc = t.description || '';

    return `<div style="padding:14px;border-radius:6px;background:#1a1a1a;border:1px solid #2a2a2a;
                       display:flex;flex-direction:column;gap:8px;">
        <div style="font-size:14px;color:#fff;font-weight:600;line-height:1.3;">${esc(t.name)}</div>
        ${desc
            ? `<div style="font-size:12px;color:#aaa;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">${esc(desc)}</div>`
            : '<div style="font-size:12px;color:#666;font-style:italic;">（無說明）</div>'}
        <div style="line-height:1.8;">${chips || '<span style="color:#666;font-size:11px;">尚無職位</span>'}</div>
        <div style="color:#666;font-size:11px;">共 ${roles.length} 個職位</div>
        <div style="display:flex;gap:6px;margin-top:auto;padding-top:6px;border-top:1px solid #2a2a2a;">
            <button class="btn btn-sm" onclick="window._credits.openTemplateModal(${t.id})">✎ 編輯</button>
            <button class="btn btn-sm btn-danger" onclick="window._credits.deleteTemplate(${t.id})">🗑 刪除</button>
        </div>
    </div>`;
}


// ===== 模板 Modal =====
function _openTemplateModalUI() {
    const isEdit = _modalState.id != null;
    const title = isEdit ? '✎ 編輯模板' : '➕ 新增模板';
    openModal('credits-tpl-modal', `
        <div style="padding:18px 20px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
            <h3 style="color:#fff;margin:0;font-size:16px;">${title}</h3>
            <button class="btn btn-sm btn-ghost" onclick="window._credits.closeTemplateModal()">✕</button>
        </div>
        <div style="padding:18px 20px;">
            <div style="margin-bottom:14px;">
                <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">模板名稱（必填）</label>
                <input id="tpl-name" type="text" value="${esc(_modalState.name)}" style="width:100%;" placeholder="如：商業廣告" />
            </div>
            <div style="margin-bottom:14px;">
                <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">說明（選填）</label>
                <textarea id="tpl-desc" rows="2" style="width:100%;" placeholder="這個模板套用於哪些作品？">${esc(_modalState.description)}</textarea>
            </div>
            <div style="margin-bottom:8px;">
                <label style="color:#888;font-size:11px;">職位組合</label>
            </div>
            <div id="tpl-picker" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                ${_renderPickerColumns()}
            </div>
        </div>
        <div style="padding:14px 20px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;">
            <button class="btn btn-ghost" onclick="window._credits.closeTemplateModal()">取消</button>
            <button class="btn" onclick="window._credits.saveTemplate()">💾 儲存</button>
        </div>
    `, { width: '760px' });

    // 即時把 input 變動寫回 _modalState，避免 _refreshPicker 重渲染時遺失草稿。
    const nameEl = document.getElementById('tpl-name');
    const descEl = document.getElementById('tpl-desc');
    if (nameEl) nameEl.oninput = e => { _modalState.name = e.target.value; };
    if (descEl) descEl.oninput = e => { _modalState.description = e.target.value; };
}


function _renderPickerColumns() {
    const selectedIds = new Set(_modalState.selected);
    // 左欄：可選（依職位庫 sort_order 排），排除已選
    const available = [..._state.roles]
        .filter(r => !selectedIds.has(r.id))
        .sort(_sortByOrder);

    // 右欄：已選（依 _modalState.selected 順序，可手動拖曳改順序）
    const roleById = new Map(_state.roles.map(r => [r.id, r]));
    const chosen = _modalState.selected.map(id => roleById.get(id)).filter(Boolean);

    const left = available.map(r => `
        <div onclick="window._credits.addRole(${r.id})"
             style="padding:6px 10px;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:4px;
                    cursor:pointer;font-size:12px;color:#ddd;
                    display:flex;justify-content:space-between;align-items:center;
                    transition:all 0.1s;"
             onmouseover="this.style.background='#333';this.style.borderColor='#3b82f6';"
             onmouseout="this.style.background='#2a2a2a';this.style.borderColor='#3a3a3a';">
            <span>${_roleLabel(r)}</span>
            <span style="color:#3b82f6;font-weight:600;">+</span>
        </div>
    `).join('');

    const right = chosen.map((r, idx) => `
        <div draggable="true"
             data-role-id="${r.id}"
             ondragstart="window._credits.onDragStart(event, ${r.id})"
             ondragover="window._credits.onDragOver(event)"
             ondrop="window._credits.onDrop(event, ${r.id})"
             ondragend="window._credits.onDragEnd(event)"
             style="padding:6px 10px;background:#1e3a5f;border:1px solid #3b82f6;border-radius:4px;
                    cursor:move;font-size:12px;color:#fff;
                    display:flex;justify-content:space-between;align-items:center;
                    transition:opacity 0.15s;">
            <span style="display:flex;align-items:center;gap:8px;">
                <span style="color:#666;cursor:grab;">↕</span>
                <span style="color:#666;font-size:10px;width:16px;">${idx + 1}.</span>
                <span>${_roleLabel(r, '#aaa')}</span>
            </span>
            <span onclick="event.stopPropagation();window._credits.removeRole(${r.id})"
                  style="color:#f87171;font-weight:700;cursor:pointer;padding:0 4px;">✕</span>
        </div>
    `).join('');

    return `
        <div>
            <div style="color:#888;font-size:11px;margin-bottom:6px;">可選職位（${available.length}）</div>
            <div style="display:flex;flex-direction:column;gap:4px;max-height:340px;overflow-y:auto;
                        padding:8px;background:#161616;border:1px solid #2a2a2a;border-radius:4px;min-height:240px;">
                ${left || '<div style="color:#666;font-size:11px;padding:12px;text-align:center;">全部已選或職位庫為空</div>'}
            </div>
        </div>
        <div>
            <div style="color:#888;font-size:11px;margin-bottom:6px;">已選（依此順序展開 · ${chosen.length}）</div>
            <div id="tpl-picker-right"
                 ondragover="window._credits.onDragOver(event)"
                 ondrop="window._credits.onDropEnd(event)"
                 style="display:flex;flex-direction:column;gap:4px;max-height:340px;overflow-y:auto;
                        padding:8px;background:#161616;border:1px solid #2a2a2a;border-radius:4px;min-height:240px;">
                ${right || '<div style="color:#666;font-size:11px;padding:12px;text-align:center;">點左側職位加入</div>'}
            </div>
        </div>
    `;
}


function _refreshPicker() {
    const picker = document.getElementById('tpl-picker');
    if (picker) picker.innerHTML = _renderPickerColumns();
}


// ===== window._credits namespace =====
const _credits = (window._credits = window._credits || {});


// ── 職位庫 ──
_credits.createRole = async () => {
    const body = {
        name_zh: document.getElementById('role-new-zh').value.trim(),
        name_en: document.getElementById('role-new-en').value.trim() || null,
        sort_order: Number(document.getElementById('role-new-sort').value || 0),
        visible: document.getElementById('role-new-visible').checked,
    };
    if (!body.name_zh) { toastErr('中文名稱必填'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/credit_roles', { method: 'POST', body });
        _state.roles.push({ ...created, usage_count: created.usage_count ?? 0 });
        toastOk('已新增職位');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};

_credits.saveRole = async (id) => {
    const patch = readRowPatch('#credits-roles-card [data-id]', id);
    try {
        const updated = await websiteFetch(`/api/website/admin/credit_roles/${id}`, { method: 'PUT', body: patch });
        const idx = _state.roles.findIndex(r => r.id === id);
        const orderChanged = idx >= 0 && _state.roles[idx].sort_order !== updated.sort_order;
        if (idx >= 0) _state.roles[idx] = { ..._state.roles[idx], ...updated };
        toastOk('已更新');
        if (orderChanged) _renderAll();
    } catch (e) { toastErr(e.message); }
};

_credits.deleteRole = async (id) => {
    const r = _state.roles.find(x => x.id === id);
    if (!r) return;
    const usage = r.usage_count || 0;
    const warn = usage > 0
        ? `⚠ 這個職位被 ${usage} 件作品使用中，刪除後這些作品的此職位掛載會一併消失。\n\n確定刪除「${r.name_zh}」？`
        : `確定刪除「${r.name_zh}」？`;
    if (!confirm(warn)) return;
    try {
        await websiteFetch(`/api/website/admin/credit_roles/${id}`, { method: 'DELETE' });
        _state.roles = _state.roles.filter(x => x.id !== id);
        // TODO 後端 cascade：DELETE credit_roles/{id} 時 mutate templates.role_ids 移除該 id。
        // 目前後端不 cascade（JSONB 無 FK），下次重抓 templates 仍會帶 dead id；
        // hydrate_template 會 skip 找不到的 id 不影響顯示，但 DB 殘留需手動清。
        // 前端就地清 _state.templates 只是視覺即時感，重抓會打回原形。
        _state.templates.forEach(t => {
            if (Array.isArray(t.role_ids)) t.role_ids = t.role_ids.filter(rid => rid !== id);
            if (Array.isArray(t.roles)) t.roles = t.roles.filter(rr => rr.id !== id);
        });
        toastOk('已刪除');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};


// ── 模板 ──
_credits.openTemplateModal = (id) => {
    if (id == null) {
        _modalState = { id: null, name: '', description: '', selected: [] };
    } else {
        const t = _state.templates.find(x => x.id === id);
        if (!t) { toastErr('找不到模板'); return; }
        _modalState = {
            id: t.id,
            name: t.name || '',
            description: t.description || '',
            selected: Array.isArray(t.role_ids) ? [...t.role_ids] : [],
        };
    }
    _openTemplateModalUI();
};

_credits.closeTemplateModal = () => {
    closeModal('credits-tpl-modal');
};

_credits.addRole = (roleId) => {
    if (!_modalState.selected.includes(roleId)) {
        _modalState.selected.push(roleId);
        _refreshPicker();
    }
};

_credits.removeRole = (roleId) => {
    _modalState.selected = _modalState.selected.filter(id => id !== roleId);
    _refreshPicker();
};

// 拖曳：從右欄某項拖到右欄另一項上方 → 插到該項之前
let _dragRoleId = null;

_credits.onDragStart = (e, roleId) => {
    _dragRoleId = roleId;
    if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', String(roleId)); } catch {}
    }
    if (e.currentTarget && e.currentTarget.style) e.currentTarget.style.opacity = '0.4';
};

_credits.onDragOver = (e) => {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
};

_credits.onDrop = (e, targetRoleId) => {
    e.preventDefault();
    e.stopPropagation();
    if (_dragRoleId == null || _dragRoleId === targetRoleId) return;
    const arr = _modalState.selected;
    const fromIdx = arr.indexOf(_dragRoleId);
    const toIdx = arr.indexOf(targetRoleId);
    if (fromIdx < 0 || toIdx < 0) return;
    arr.splice(fromIdx, 1);
    const newToIdx = arr.indexOf(targetRoleId);
    arr.splice(newToIdx, 0, _dragRoleId);
    _dragRoleId = null;
    _refreshPicker();
};

_credits.onDropEnd = (e) => {
    // drop 在右欄空白 → 移到末尾
    e.preventDefault();
    if (_dragRoleId == null) return;
    const arr = _modalState.selected;
    const fromIdx = arr.indexOf(_dragRoleId);
    if (fromIdx < 0) return;
    arr.splice(fromIdx, 1);
    arr.push(_dragRoleId);
    _dragRoleId = null;
    _refreshPicker();
};

_credits.onDragEnd = (e) => {
    if (e.currentTarget && e.currentTarget.style) e.currentTarget.style.opacity = '1';
    _dragRoleId = null;
};

_credits.saveTemplate = async () => {
    const nameEl = document.getElementById('tpl-name');
    const descEl = document.getElementById('tpl-desc');
    const name = (nameEl?.value || '').trim();
    const description = (descEl?.value || '').trim();
    if (!name) { toastErr('模板名稱必填'); return; }

    const body = {
        name,
        description: description || null,
        role_ids: [..._modalState.selected],
    };

    try {
        let saved;
        if (_modalState.id == null) {
            body.sort_order = _state.templates.length;
            saved = await websiteFetch('/api/website/admin/credit_templates', { method: 'POST', body });
            _state.templates.push(saved);
            toastOk('已新增模板');
        } else {
            saved = await websiteFetch(`/api/website/admin/credit_templates/${_modalState.id}`, {
                method: 'PUT', body,
            });
            const idx = _state.templates.findIndex(t => t.id === _modalState.id);
            if (idx >= 0) _state.templates[idx] = saved;
            toastOk('已更新模板');
        }
        closeModal('credits-tpl-modal');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};

_credits.deleteTemplate = async (id) => {
    const t = _state.templates.find(x => x.id === id);
    if (!t) return;
    if (!confirm(`確定刪除模板「${t.name}」？\n\n此操作不影響職位庫，也不影響已套用此模板建立的作品。`)) return;
    try {
        await websiteFetch(`/api/website/admin/credit_templates/${id}`, { method: 'DELETE' });
        _state.templates = _state.templates.filter(x => x.id !== id);
        toastOk('已刪除');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};
