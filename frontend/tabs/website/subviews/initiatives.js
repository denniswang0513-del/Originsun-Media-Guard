/**
 * initiatives.js — 公益 & 創作（公益合作 / 創作計畫 兩條線）
 *
 * 每條線一組案例（CRUD）。案例可「連動作品集的作品」（選作品 → 自動帶封面/標題、
 * 點擊跳作品頁），或「獨立案例」（自填標題/封面/外連）。
 * 另含兩頁的 hero 文案卡（copy.impact.* / copy.lab.*）。
 */
import {
    websiteFetch, esc, toastOk, toastErr, renderLoadError,
    openModal, closeModal, getApiBase, renderCopyCard,
} from '../website-utils.js';

const LINES = [
    { key: 'impact', label: '🤝 公益合作', color: '#10b981', path: '/impact' },
    { key: 'lab', label: '✦ 創作計畫', color: '#8b5cf6', path: '/lab' },
];

const COPY_BLOCKS = [
    { key: 'hero_eyebrow', label: 'Eyebrow 小字', type: 'text', placeholderZh: 'Impact / Lab' },
    { key: 'hero_title', label: '頁面大標', placeholderZh: '公益合作 / 創作計畫' },
    { key: 'intro', label: '介紹段落', long: true, placeholderZh: '留空用預設介紹文字' },
    { key: 'seo_desc', label: 'SEO 描述', type: 'text', placeholderZh: '留空用介紹段', hint: '建議 30–160 字' },
];

let _state = { items: [], works: [], settings: {} };
let _container = null;
let _editing = null;   // 編輯中的案例（null = 新增）
const _ini = (window._ini = window._ini || {});

// 封面相對路徑（/uploads/...）要補 API base 才看得到預覽
function _coverSrc(url) {
    if (!url) return '';
    return /^(https?:|data:|blob:)/i.test(url) ? url : `${getApiBase()}${url}`;
}
function _workLabel(w) {
    return (w.public_title || w.title || w.name || w.id || '').toString();
}

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🤝 公益 & 創作</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const [items, works, settings] = await Promise.all([
            websiteFetch('/api/website/admin/initiatives'),
            websiteFetch('/api/website/admin/works'),
            websiteFetch('/api/website/admin/settings'),
        ]);
        if (!isCurrent()) return;
        _state.items = items?.items || [];
        _state.works = works?.items || [];
        _state.settings = settings?.settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🤝 公益 & 創作', e);
        return;
    }
    _renderShell();
}

function _renderShell() {
    _container.innerHTML = `
        <h2>🤝 公益 & 創作 <span style="color:#888;font-size:12px;font-weight:400;">· 日常業務外的兩條線</span></h2>
        <p style="color:#888;font-size:12px;margin:-4px 0 14px;">
            首頁「關於我們」段的兩個按鈕連到 <code>/impact</code>（公益合作）與 <code>/lab</code>（創作計畫）。
            案例可<strong style="color:#ddd;">連動作品集的作品</strong>（自動帶封面/標題、點擊跳作品頁），或自填獨立案例。
        </p>
        ${LINES.map(_renderLineSection).join('')}
        ${renderCopyCard('copy.impact', _state.settings, COPY_BLOCKS, { title: '📝 公益合作 頁面文案（/impact）', note: '留空則用預設文案。' })}
        ${renderCopyCard('copy.lab', _state.settings, COPY_BLOCKS, { title: '📝 創作計畫 頁面文案（/lab）', note: '留空則用預設文案。' })}
    `;
}

function _renderLineSection(line) {
    const entries = _state.items.filter(i => i.line === line.key);
    return `
        <div class="card" style="border-left:3px solid ${line.color};margin-bottom:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <h3 style="color:#fff;margin:0;font-size:14px;">${line.label}
                    <span style="color:#888;font-weight:400;font-size:12px;">(${entries.length}) · <a href="${line.path}" target="_blank" style="color:#3b82f6;">看頁面 ↗</a></span>
                </h3>
                <button class="btn btn-sm" style="background:#059669;" onclick="window._ini.openCreate('${line.key}')">+ 新增案例</button>
            </div>
            ${entries.length
                ? `<table><thead><tr>
                        <th style="width:70px;">封面</th><th>標題</th><th style="width:90px;">連動</th>
                        <th style="width:60px;">年份</th><th style="width:55px;">排序</th><th style="width:55px;">顯示</th><th style="width:110px;">操作</th>
                   </tr></thead><tbody>${entries.map(_entryRow).join('')}</tbody></table>`
                : '<div style="color:#666;font-size:12px;padding:14px;text-align:center;border:1px dashed #2a2a2a;border-radius:4px;">尚無案例 — 按「+ 新增案例」</div>'}
        </div>`;
}

function _entryRow(it) {
    const cover = it.cover_url || it.work_cover || '';
    const title = it.title || it.work_title || '(未命名)';
    let linkBadge;
    if (it.project_id) {
        if (it.work_missing) linkBadge = '<span class="website-pill" style="background:#7f1d1d;color:#fff;">作品已刪</span>';
        else if (!it.work_public) linkBadge = '<span class="website-pill" style="background:#78350f;color:#fff;">作品未公開</span>';
        else linkBadge = '<span class="website-pill" style="background:#1e3a5f;color:#fff;">作品集</span>';
    } else {
        linkBadge = '<span style="color:#666;font-size:11px;">獨立</span>';
    }
    return `
        <tr>
            <td>${cover
                ? `<img src="${esc(_coverSrc(cover))}" style="width:56px;height:32px;object-fit:cover;border-radius:3px;" onerror="this.style.opacity=0.2" />`
                : '<div style="width:56px;height:32px;background:#0d0d0d;border-radius:3px;"></div>'}</td>
            <td><div style="color:#ddd;">${esc(title)}</div>${it.summary ? `<div style="color:#666;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px;">${esc(it.summary)}</div>` : ''}</td>
            <td>${linkBadge}</td>
            <td style="color:#888;font-family:monospace;font-size:11px;">${it.year || '-'}</td>
            <td style="color:#888;font-family:monospace;">${it.sort_order}</td>
            <td style="text-align:center;">${it.visible ? '✅' : '🚫'}</td>
            <td>
                <button class="btn btn-sm" onclick="window._ini.openEdit(${it.id})">編輯</button>
                <button class="btn btn-sm btn-danger" onclick="window._ini.del(${it.id})">🗑</button>
            </td>
        </tr>`;
}

// ── Modal ──

_ini.openCreate = (line) => {
    _editing = { id: null, line, project_id: '', title: '', summary: '', cover_url: '', link_url: '', year: '', sort_order: 0, visible: true };
    _showModal('新增案例');
};

_ini.openEdit = (id) => {
    const it = _state.items.find(x => x.id === id);
    if (!it) { toastErr('找不到此案例'); return; }
    _editing = { ...it, project_id: it.project_id || '', year: it.year || '' };
    _showModal('編輯案例');
};

function _showModal(title) {
    const p = _editing;
    const lineLabel = (LINES.find(l => l.key === p.line) || {}).label || p.line;
    const workOpts = ['<option value="">（無 — 獨立案例）</option>']
        .concat(_state.works.map(w =>
            `<option value="${esc(w.id)}" ${p.project_id === w.id ? 'selected' : ''}>${esc(_workLabel(w))}</option>`)).join('');
    const css = `<style>
        #ini-modal label{color:#9aa0a6;font-size:11px;display:block;margin-bottom:3px;}
        #ini-modal input,#ini-modal textarea,#ini-modal select{background:#0d0d0d;border:1px solid #333;color:#f0f0f0;padding:7px 9px;border-radius:4px;width:100%;box-sizing:border-box;font-size:13px;font-family:inherit;}
        #ini-modal .row{margin-bottom:12px;}
    </style>`;
    const inner = css + `
        <div style="padding:14px 18px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;color:#fff;font-size:15px;">${esc(title)} <span style="color:#888;font-size:12px;font-weight:400;">· ${esc(lineLabel)}</span></h3>
            <button onclick="window._ini.close()" style="background:#252525;border:1px solid #333;color:#aaa;cursor:pointer;width:30px;height:30px;border-radius:4px;">✕</button>
        </div>
        <div style="padding:18px;">
            <div class="row">
                <label>連動作品（選了就自動帶封面/標題、點擊跳作品頁；獨立案例選「無」）</label>
                <select id="ini-work">${workOpts}</select>
            </div>
            <div class="row">
                <label>標題 <span style="color:#666;">（連動作品時留空 = 用作品標題）</span></label>
                <input id="ini-title" value="${esc(p.title || '')}" placeholder="案例標題" />
            </div>
            <div class="row">
                <label>簡述</label>
                <textarea id="ini-summary" rows="2" placeholder="一兩句介紹">${esc(p.summary || '')}</textarea>
            </div>
            <div class="row">
                <label>封面圖 <span style="color:#666;">（連動作品時留空 = 用作品封面）</span></label>
                <div style="display:flex;gap:10px;align-items:flex-start;">
                    <div id="ini-cover-prev" style="width:96px;height:54px;flex-shrink:0;background:#0d0d0d;border:1px solid #2a2a2a;border-radius:4px;overflow:hidden;">
                        ${p.cover_url ? `<img src="${esc(_coverSrc(p.cover_url))}" style="width:100%;height:100%;object-fit:cover;" />` : ''}
                    </div>
                    <div style="flex:1;">
                        <div style="display:flex;gap:6px;margin-bottom:5px;">
                            <label class="btn btn-sm btn-ghost" style="margin:0;cursor:pointer;font-size:11px;">📤 上傳
                                <input type="file" accept="image/*" style="display:none;" onchange="window._ini.uploadCover(this)" />
                            </label>
                            <input id="ini-cover" value="${esc(p.cover_url || '')}" placeholder="或貼圖片網址 / 留空用作品封面"
                                   oninput="window._ini.refreshCoverPrev(this.value)" style="flex:1;" />
                        </div>
                    </div>
                </div>
            </div>
            <div class="row" style="display:grid;grid-template-columns:1fr 90px 90px;gap:10px;">
                <div><label>外部連結 <span style="color:#666;">（獨立案例用；連動作品時忽略）</span></label>
                    <input id="ini-link" value="${esc(p.link_url || '')}" placeholder="https://…" /></div>
                <div><label>年份</label><input id="ini-year" type="number" value="${esc(p.year || '')}" placeholder="2025" /></div>
                <div><label>排序</label><input id="ini-sort" type="number" value="${p.sort_order || 0}" /></div>
            </div>
            <div class="row"><label style="display:inline-flex;align-items:center;gap:6px;color:#ddd;cursor:pointer;">
                <input id="ini-visible" type="checkbox" ${p.visible ? 'checked' : ''} style="width:auto;" /> 對外顯示</label></div>
        </div>
        <div style="padding:12px 18px;border-top:1px solid #2a2a2a;display:flex;justify-content:flex-end;gap:8px;">
            <button class="btn btn-ghost btn-sm" onclick="window._ini.close()">取消</button>
            <button class="btn" style="background:#3b82f6;" onclick="window._ini.save()">💾 儲存</button>
        </div>`;
    openModal('ini-modal', inner, { width: '640px' });
}

_ini.close = () => closeModal('ini-modal');

_ini.refreshCoverPrev = (url) => {
    const prev = document.getElementById('ini-cover-prev');
    if (prev) prev.innerHTML = url ? `<img src="${esc(_coverSrc(url))}" style="width:100%;height:100%;object-fit:cover;" onerror="this.style.opacity=0.2" />` : '';
};

_ini.uploadCover = async (input) => {
    const f = input.files && input.files[0];
    if (!f) return;
    try {
        const fd = new FormData();
        fd.append('file', f);
        const r = await websiteFetch('/api/website/admin/initiatives/upload-cover', { method: 'POST', body: fd });
        const el = document.getElementById('ini-cover');
        if (el) el.value = r.url;
        _ini.refreshCoverPrev(r.url);
        toastOk('已上傳');
    } catch (e) { toastErr('上傳失敗：' + (e.message || e)); }
};

_ini.save = async () => {
    const v = (id) => document.getElementById(id);
    const body = {
        line: _editing.line,
        project_id: v('ini-work').value || null,
        title: v('ini-title').value.trim() || null,
        summary: v('ini-summary').value.trim() || null,
        cover_url: v('ini-cover').value.trim() || null,
        link_url: v('ini-link').value.trim() || null,
        year: v('ini-year').value ? Number(v('ini-year').value) : null,
        sort_order: Number(v('ini-sort').value) || 0,
        visible: v('ini-visible').checked,
    };
    // 連動作品 + 沒標題時，後端會 fallback 作品標題；獨立案例則需標題
    if (!body.project_id && !body.title) { toastErr('獨立案例需填標題（或選一個連動作品）'); return; }
    try {
        if (_editing.id) {
            await websiteFetch(`/api/website/admin/initiatives/${_editing.id}`, { method: 'PUT', body });
        } else {
            await websiteFetch('/api/website/admin/initiatives', { method: 'POST', body });
        }
        toastOk('已儲存（對外網站 60 秒後重建）');
        _ini.close();
        const items = await websiteFetch('/api/website/admin/initiatives');
        _state.items = items?.items || [];
        _renderShell();
    } catch (e) { toastErr(e.message); }
};

_ini.del = async (id) => {
    const it = _state.items.find(x => x.id === id);
    if (!confirm(`刪除案例「${(it && (it.title || it.work_title)) || '#' + id}」？`)) return;
    try {
        await websiteFetch(`/api/website/admin/initiatives/${id}`, { method: 'DELETE' });
        _state.items = _state.items.filter(x => x.id !== id);
        toastOk('已刪除');
        _renderShell();
    } catch (e) { toastErr(e.message); }
};
