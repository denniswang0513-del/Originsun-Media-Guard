/**
 * redirects.js — 轉址管理（Legacy 頁面級 301）
 *
 * 管理 website_redirects 表：「舊站頁面 → 新站頁面」這種非作品/文章 slug 變動的轉址
 * （如 /commercial-film、/contact-us、/portfolio-category/*）。
 *
 * 注意：
 * - 作品換 slug 的轉址在「作品集」編輯頁的舊網址欄；文章在「部落格」編輯頁 —— 不在這裡。
 * - 存 from_path 用相對路徑（無結尾斜線）；nginx 生成時會自動補帶斜線變體。
 * - 寫入 → 60 秒後 Astro 重建（軟 301 生效）；硬 301（nginx）由 master 端 /publish 同步。
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError,
    openModal, closeModal,
} from '../website-utils.js';

let _state = { items: [], mergedCount: null, filter: '' };
let _container = null;
let _editing = null;
const _rd = (window._rd = window._rd || {});

const _isInterim = (it) => !!(it.note && it.note.includes('暫代'));

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🔀 轉址管理</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const [list, merged] = await Promise.all([
            websiteFetch('/api/website/admin/redirects'),
            websiteFetch('/api/website/redirects').catch(() => null),
        ]);
        if (!isCurrent()) return;
        _state.items = list?.items || [];
        _state.mergedCount = merged?.count ?? null;
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🔀 轉址管理', e);
        return;
    }
    _renderShell();
}

function _renderShell() {
    const items = _sorted();
    const interimN = _state.items.filter(_isInterim).length;
    const f = _state.filter.trim().toLowerCase();
    const shown = f
        ? items.filter(it => (it.from_path + ' ' + it.to_path + ' ' + (it.note || '')).toLowerCase().includes(f))
        : items;

    _container.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
            <h2 style="margin:0;">🔀 轉址管理 <span style="color:#888;font-size:12px;font-weight:400;">· Legacy 頁面級 301</span></h2>
            <button class="btn" style="background:#059669;" onclick="window._rd.openCreate()">+ 新增轉址</button>
        </div>
        <p style="color:#888;font-size:12px;margin:6px 0 12px;">
            舊站頁面 → 新站頁面的轉址（如 <code>/commercial-film</code> → <code>/works/category/commercial</code>）。
            <strong style="color:#ddd;">作品/文章換 slug</strong> 的轉址請到各自的編輯頁改舊網址欄，不在這裡。
            寫入後 60 秒 Astro 重建（軟 301 生效）；硬 301（nginx）由主控端 <code>/publish</code> 同步。
        </p>
        <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:12px;color:#999;">
            <span>Legacy 轉址：<strong style="color:#ddd;">${_state.items.length}</strong></span>
            ${interimN ? `<span style="color:#f59e0b;">其中暫代待對照：<strong>${interimN}</strong></span>` : ''}
            ${_state.mergedCount != null ? `<span>對外總轉址（含作品/文章）：<strong style="color:#ddd;">${_state.mergedCount}</strong></span>` : ''}
            <input id="redir-filter" placeholder="🔍 篩選 from/to/備註…" value="${esc(_state.filter)}"
                   oninput="window._rd.setFilter(this.value)"
                   style="margin-left:auto;background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:6px 9px;border-radius:4px;font-size:12px;min-width:220px;" />
        </div>
        ${shown.length ? `
            <table>
                <thead><tr>
                    <th>舊路徑 (from)</th><th style="width:24px;"></th><th>新路徑 (to)</th>
                    <th style="width:150px;">備註</th><th style="width:50px;">排序</th>
                    <th style="width:50px;">啟用</th><th style="width:110px;">操作</th>
                </tr></thead>
                <tbody>${shown.map(_row).join('')}</tbody>
            </table>` : `
            <div style="color:#666;font-size:12px;padding:24px;text-align:center;border:1px dashed #2a2a2a;border-radius:4px;">
                ${f ? '沒有符合篩選的轉址' : '尚無 legacy 轉址 — 按「+ 新增轉址」'}
            </div>`}
    `;
    const fi = document.getElementById('redir-filter');
    if (fi) { fi.focus(); fi.setSelectionRange(fi.value.length, fi.value.length); }
}

function _sorted() {
    return [..._state.items].sort((a, b) =>
        (a.sort_order - b.sort_order) || String(a.from_path).localeCompare(String(b.from_path)));
}

function _row(it) {
    return `
        <tr${it.visible ? '' : ' style="opacity:0.5;"'}>
            <td style="font-family:monospace;font-size:12px;color:#ddd;word-break:break-all;">${esc(it.from_path)}</td>
            <td style="color:#666;text-align:center;">→</td>
            <td style="font-family:monospace;font-size:12px;color:#93c5fd;word-break:break-all;">${esc(it.to_path)}</td>
            <td style="font-size:11px;color:#999;">
                ${_isInterim(it) ? '<span class="website-pill" style="background:#78350f;color:#fbbf24;">暫代</span> ' : ''}
                ${esc((it.note || '').replace('舊作品暫代→分類(待精準對照)', '').trim())}
            </td>
            <td style="color:#888;font-family:monospace;text-align:center;">${it.sort_order}</td>
            <td style="text-align:center;">${it.visible ? '✅' : '🚫'}</td>
            <td>
                <button class="btn btn-sm" onclick="window._rd.openEdit(${it.id})">編輯</button>
                <button class="btn btn-sm btn-danger" onclick="window._rd.del(${it.id})">🗑</button>
            </td>
        </tr>`;
}

_rd.setFilter = (v) => { _state.filter = v; _renderShell(); };

// ── Modal ──

_rd.openCreate = () => {
    _editing = { id: null, from_path: '', to_path: '', note: '', sort_order: 0, visible: true };
    _showModal('新增轉址');
};

_rd.openEdit = (id) => {
    const it = _state.items.find(x => x.id === id);
    if (!it) { toastErr('找不到此轉址'); return; }
    _editing = { ...it };
    _showModal('編輯轉址');
};

function _showModal(title) {
    const p = _editing;
    const css = `<style>
        #redir-modal label{color:#9aa0a6;font-size:11px;display:block;margin-bottom:3px;}
        #redir-modal input{background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:8px 9px;border-radius:4px;width:100%;box-sizing:border-box;font-size:13px;font-family:monospace;}
        #redir-modal .row{margin-bottom:12px;}
        #redir-modal .hint{color:#666;font-size:11px;margin-top:3px;font-family:inherit;}
    </style>`;
    const inner = css + `
        <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;color:#fff;font-size:15px;">${esc(title)}</h3>
            <button onclick="window._rd.close()" style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;width:30px;height:30px;border-radius:4px;">✕</button>
        </div>
        <div style="padding:18px;">
            <div class="row">
                <label>舊路徑 from（相對路徑，如 <code>/commercial-film</code>）</label>
                <input id="redir-from" value="${esc(p.from_path || '')}" placeholder="/old-page" />
                <div class="hint">結尾斜線會自動處理，兩種變體都會轉。</div>
            </div>
            <div class="row">
                <label>新路徑 to（如 <code>/works/category/commercial</code>）</label>
                <input id="redir-to" value="${esc(p.to_path || '')}" placeholder="/works/category/commercial" />
            </div>
            <div class="row">
                <label>備註（可選）</label>
                <input id="redir-note" value="${esc(p.note || '')}" placeholder="來源說明" style="font-family:inherit;" />
            </div>
            <div class="row" style="display:grid;grid-template-columns:90px 1fr;gap:14px;align-items:end;">
                <div><label>排序</label><input id="redir-sort" type="number" value="${p.sort_order || 0}" /></div>
                <label style="display:inline-flex;align-items:center;gap:6px;color:#ddd;cursor:pointer;font-family:inherit;font-size:13px;">
                    <input id="redir-visible" type="checkbox" ${p.visible ? 'checked' : ''} style="width:auto;" /> 啟用（關閉 = 暫停此轉址）</label>
            </div>
        </div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;">
            <button class="btn btn-ghost btn-sm" onclick="window._rd.close()">取消</button>
            <button class="btn" style="background:#3b82f6;" onclick="window._rd.save()">💾 儲存</button>
        </div>`;
    openModal('redir-modal', inner, { width: '560px' });
}

_rd.close = () => closeModal('redir-modal');

_rd.save = async () => {
    const v = (id) => document.getElementById(id);
    const from_path = v('redir-from').value.trim();
    const to_path = v('redir-to').value.trim();
    if (!from_path || !to_path) { toastErr('舊路徑與新路徑都要填'); return; }
    if (!from_path.startsWith('/') || !to_path.startsWith('/')) { toastErr('路徑要以 / 開頭'); return; }
    const body = {
        from_path, to_path,
        note: v('redir-note').value.trim() || null,
        sort_order: Number(v('redir-sort').value) || 0,
        visible: v('redir-visible').checked,
    };
    try {
        if (_editing.id) {
            await websiteFetch(`/api/website/admin/redirects/${_editing.id}`, { method: 'PUT', body });
        } else {
            await websiteFetch('/api/website/admin/redirects', { method: 'POST', body });
        }
        toastOk('已儲存（軟 301 於重建後生效；硬 301 待 /publish 同步）');
        _rd.close();
        await _reload();
    } catch (e) { toastErr(e.message); }
};

_rd.del = async (id) => {
    const it = _state.items.find(x => x.id === id);
    if (!confirm(`刪除轉址「${(it && it.from_path) || '#' + id}」？`)) return;
    try {
        await websiteFetch(`/api/website/admin/redirects/${id}`, { method: 'DELETE' });
        _state.items = _state.items.filter(x => x.id !== id);
        toastOk('已刪除');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

async function _reload() {
    const list = await websiteFetch('/api/website/admin/redirects');
    _state.items = list?.items || [];
    const merged = await websiteFetch('/api/website/redirects').catch(() => null);
    _state.mergedCount = merged?.count ?? _state.mergedCount;
    _renderShell();
}
