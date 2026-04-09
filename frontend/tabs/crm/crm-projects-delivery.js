/**
 * crm-projects-delivery.js — 完稿結案 Tab
 * 封面、基本資訊、成品展示、創作過程、Credit、發佈控制
 */

import { crmFetch as _fetch, esc as _esc, fmtNum } from './crm-utils.js';
import { state } from './crm-projects-state.js';

// ── Helpers ────────────────────────────────────────────────

async function _uploadFile(endpoint, file) {
    const form = new FormData();
    form.append('file', file);
    const token = localStorage.getItem('auth_token');
    return fetch('/api/v1/crm' + endpoint, {
        method: 'POST',
        headers: token ? { 'Authorization': 'Bearer ' + token } : {},
        body: form,
    }).then(r => {
        if (!r.ok) throw new Error('Upload failed: ' + r.status);
        return r.json();
    });
}

function _section(title, content) {
    return `<div class="showcase-section">
        <div class="showcase-section-title">${title}</div>
        <div class="showcase-section-body">${content}</div>
    </div>`;
}

// ── Main Load ──────────────────────────────────────────────

let _lastProjectId = null;

export async function loadDeliveryTab(projectId) {
    const container = document.getElementById('proj-detail-delivery');
    if (!container) return;

    _lastProjectId = projectId;
    container.innerHTML = '<div class="crm-empty">載入中...</div>';

    let showcase;
    try {
        showcase = await _fetch('/projects/' + projectId + '/showcase');
    } catch (e) {
        // API may not exist yet — show empty state
        showcase = {
            cover_url: '', description: '', video_url: '', slug: '',
            tags: [], gallery: [], process_mode: 'gallery', process_items: [],
            credits: [], is_published: false, public_url: '', share_token: '',
        };
    }

    container.innerHTML = `
        <div class="showcase-wrap">
            ${_renderCover(showcase, projectId)}
            ${_renderInfo(showcase, projectId)}
            ${_renderGallery(showcase, projectId)}
            ${_renderProcess(showcase, projectId)}
            ${_renderCredits(showcase, projectId)}
            ${_renderPublish(showcase, projectId)}
        </div>
    `;

    _bindCoverEvents(container, projectId);
    _bindInfoEvents(container, projectId);
    _bindGalleryEvents(container, projectId);
    _bindProcessEvents(container, projectId);
    _bindCreditEvents(container, projectId);
    _bindPublishEvents(container, projectId);
}

// ── Cover ──────────────────────────────────────────────────

function _renderCover(sc, projectId) {
    const hasImg = sc.cover_url;
    const preview = hasImg
        ? `<img src="${_esc(sc.cover_url)}" class="showcase-cover-preview" alt="cover">`
        : '';
    return _section('封面', `
        <div class="showcase-cover-zone" id="showcase-cover-zone">
            ${preview}
            <div class="showcase-cover-placeholder ${hasImg ? 'hidden' : ''}">
                <div style="font-size:28px;opacity:0.4;">&#128247;</div>
                <div style="margin-top:6px;color:#6b7280;font-size:13px;">拖曳圖片至此，或點擊上傳</div>
            </div>
            <input type="file" id="showcase-cover-input" accept="image/*" style="display:none;">
        </div>
        ${hasImg ? '<button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-cover-remove" style="margin-top:6px;">移除封面</button>' : ''}
    `);
}

function _bindCoverEvents(container, projectId) {
    const zone = container.querySelector('#showcase-cover-zone');
    const input = container.querySelector('#showcase-cover-input');
    const removeBtn = container.querySelector('#showcase-cover-remove');

    if (!zone || !input) return;

    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) _uploadCover(projectId, file);
    });
    input.addEventListener('change', () => {
        if (input.files[0]) _uploadCover(projectId, input.files[0]);
    });

    if (removeBtn) {
        removeBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                await _fetch('/projects/' + projectId + '/showcase/cover', { method: 'DELETE' });
                loadDeliveryTab(projectId);
            } catch (err) { alert('移除失敗: ' + err.message); }
        });
    }
}

async function _uploadCover(projectId, file) {
    try {
        await _uploadFile('/projects/' + projectId + '/showcase/cover', file);
        loadDeliveryTab(projectId);
    } catch (err) { alert('上傳失敗: ' + err.message); }
}

// ── Info (description, video, slug, tags) ──────────────────

function _renderInfo(sc, projectId) {
    const tagsHtml = (sc.tags || []).map((t, i) =>
        `<span class="showcase-tag">${_esc(t)}<button class="showcase-tag-remove" data-idx="${i}">&times;</button></span>`
    ).join('');

    return _section('基本資訊', `
        <div class="showcase-info-grid">
            <div class="crm-field crm-field-full">
                <label>專案描述</label>
                <textarea id="showcase-description" class="crm-input crm-textarea" rows="3" placeholder="簡短描述此專案...">${_esc(sc.description || '')}</textarea>
            </div>
            <div class="crm-field">
                <label>主影片 URL</label>
                <input id="showcase-video-url" type="url" class="crm-input" value="${_esc(sc.video_url || '')}" placeholder="https://vimeo.com/... 或 YouTube 連結">
            </div>
            <div class="crm-field">
                <label>SEO Slug</label>
                <input id="showcase-slug" type="text" class="crm-input" value="${_esc(sc.slug || '')}" placeholder="project-name">
            </div>
            <div class="crm-field crm-field-full">
                <label>標籤</label>
                <div class="showcase-tags-wrap">
                    <div class="showcase-tags-list" id="showcase-tags-list">${tagsHtml}</div>
                    <div class="showcase-tag-add-wrap">
                        <input type="text" class="showcase-tag-input" id="showcase-tag-input" placeholder="新增標籤 (Enter)">
                    </div>
                </div>
            </div>
        </div>
        <button class="crm-btn crm-btn-primary crm-btn-sm" id="showcase-save-info" style="margin-top:10px;">儲存基本資訊</button>
    `);
}

function _bindInfoEvents(container, projectId) {
    // Tag add via Enter
    const tagInput = container.querySelector('#showcase-tag-input');
    if (tagInput) {
        tagInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const val = tagInput.value.trim();
                if (!val) return;
                _addTag(container, val);
                tagInput.value = '';
            }
        });
    }

    // Tag remove
    container.querySelector('#showcase-tags-list')?.addEventListener('click', e => {
        const btn = e.target.closest('.showcase-tag-remove');
        if (btn) {
            btn.closest('.showcase-tag')?.remove();
        }
    });

    // Save
    container.querySelector('#showcase-save-info')?.addEventListener('click', async () => {
        const tags = [];
        container.querySelectorAll('#showcase-tags-list .showcase-tag').forEach(el => {
            const text = el.childNodes[0]?.textContent?.trim();
            if (text) tags.push(text);
        });
        const payload = {
            description: container.querySelector('#showcase-description')?.value || '',
            video_url: container.querySelector('#showcase-video-url')?.value || '',
            slug: container.querySelector('#showcase-slug')?.value || '',
            tags,
        };
        try {
            await _fetch('/projects/' + projectId + '/showcase', {
                method: 'PUT', body: JSON.stringify(payload),
            });
            alert('已儲存');
        } catch (err) { alert('儲存失敗: ' + err.message); }
    });
}

function _addTag(container, text) {
    const list = container.querySelector('#showcase-tags-list');
    if (!list) return;
    const idx = list.children.length;
    const span = document.createElement('span');
    span.className = 'showcase-tag';
    span.innerHTML = `${_esc(text)}<button class="showcase-tag-remove" data-idx="${idx}">&times;</button>`;
    list.appendChild(span);
}

// ── Gallery (成品展示) ─────────────────────────────────────

function _renderGallery(sc, projectId) {
    const items = (sc.gallery || []).map((img, i) => `
        <div class="showcase-gallery-item">
            <img src="${_esc(img.url)}" alt="gallery ${i}">
            <button class="showcase-gallery-del" data-id="${_esc(img.id || i)}" title="刪除">&times;</button>
        </div>
    `).join('');

    return _section('成品展示', `
        <div class="showcase-gallery-grid" id="showcase-gallery-grid">
            ${items}
            <div class="showcase-gallery-add" id="showcase-gallery-add" title="上傳圖片">
                <div style="font-size:24px;opacity:0.5;">+</div>
                <input type="file" id="showcase-gallery-input" accept="image/*" multiple style="display:none;">
            </div>
        </div>
    `);
}

function _bindGalleryEvents(container, projectId) {
    const addBtn = container.querySelector('#showcase-gallery-add');
    const input = container.querySelector('#showcase-gallery-input');
    if (!addBtn || !input) return;

    addBtn.addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
        for (const file of input.files) {
            try {
                await _uploadFile('/projects/' + projectId + '/showcase/gallery', file);
            } catch (err) { console.error('Gallery upload error:', err); }
        }
        loadDeliveryTab(projectId);
    });

    // Delete gallery items
    container.querySelector('#showcase-gallery-grid')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.showcase-gallery-del');
        if (!btn) return;
        const id = btn.dataset.id;
        try {
            await _fetch('/projects/' + projectId + '/showcase/gallery/' + id, { method: 'DELETE' });
            loadDeliveryTab(projectId);
        } catch (err) { alert('刪除失敗: ' + err.message); }
    });
}

// ── Process (創作過程) ─────────────────────────────────────

const PROCESS_MODES = [
    { key: 'gallery', icon: '&#128248;', label: 'Gallery' },
    { key: 'media', icon: '&#127909;', label: '圖片+影片' },
    { key: 'timeline', icon: '&#128197;', label: '時間軸' },
];

const TIMELINE_PHASES = ['前期', '拍攝', '後期'];

function _renderProcess(sc, projectId) {
    const mode = sc.process_mode || 'gallery';
    const modeButtons = PROCESS_MODES.map(m =>
        `<button class="showcase-process-mode-btn ${mode === m.key ? 'active' : ''}" data-mode="${m.key}">${m.icon} ${m.label}</button>`
    ).join('');

    let content = '';
    const items = sc.process_items || [];

    if (mode === 'gallery') {
        const imgs = items.filter(it => it.type === 'image');
        content = `
            <div class="showcase-gallery-grid" id="showcase-process-grid">
                ${imgs.map((img, i) => `
                    <div class="showcase-gallery-item">
                        <img src="${_esc(img.url)}" alt="process ${i}">
                        <button class="showcase-process-del" data-id="${_esc(img.id || i)}" title="刪除">&times;</button>
                    </div>
                `).join('')}
                <div class="showcase-gallery-add" id="showcase-process-add" title="上傳圖片">
                    <div style="font-size:24px;opacity:0.5;">+</div>
                    <input type="file" id="showcase-process-input" accept="image/*" multiple style="display:none;">
                </div>
            </div>`;
    } else if (mode === 'media') {
        content = `<div class="showcase-media-list" id="showcase-media-list">
            ${items.map((it, i) => `
                <div class="showcase-media-item" data-id="${_esc(it.id || i)}">
                    ${it.type === 'image' ? `<img src="${_esc(it.url)}" class="showcase-media-thumb">` : ''}
                    <div class="showcase-media-info">
                        <input type="text" class="crm-input showcase-media-caption" value="${_esc(it.caption || '')}" placeholder="說明文字">
                        ${it.type === 'video' ? `<input type="url" class="crm-input showcase-media-url" value="${_esc(it.url || '')}" placeholder="影片 URL">` : ''}
                    </div>
                    <button class="showcase-process-del" data-id="${_esc(it.id || i)}" title="刪除">&times;</button>
                </div>
            `).join('')}
            <div style="display:flex;gap:6px;margin-top:8px;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-process-add-img">+ 圖片</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-process-add-video">+ 影片 URL</button>
                <input type="file" id="showcase-process-input" accept="image/*" style="display:none;">
            </div>
        </div>`;
    } else if (mode === 'timeline') {
        content = TIMELINE_PHASES.map(phase => {
            const phaseItems = items.filter(it => it.phase === phase);
            return `<div class="showcase-timeline-phase">
                <div class="showcase-timeline-phase-title">${phase}</div>
                <div class="showcase-timeline-phase-body">
                    ${phaseItems.map((it, i) => `
                        <div class="showcase-timeline-item" data-id="${_esc(it.id || '')}">
                            ${it.type === 'image' ? `<img src="${_esc(it.url)}" class="showcase-media-thumb">` : ''}
                            <input type="text" class="crm-input" value="${_esc(it.caption || '')}" placeholder="描述..." style="flex:1;">
                            <button class="showcase-process-del" data-id="${_esc(it.id || '')}" title="刪除">&times;</button>
                        </div>
                    `).join('')}
                    <div style="display:flex;gap:6px;margin-top:6px;">
                        <button class="crm-btn crm-btn-secondary crm-btn-sm showcase-timeline-add-img" data-phase="${phase}">+ 圖片</button>
                        <button class="crm-btn crm-btn-secondary crm-btn-sm showcase-timeline-add-text" data-phase="${phase}">+ 文字</button>
                    </div>
                </div>
            </div>`;
        }).join('');
        content += '<input type="file" id="showcase-process-input" accept="image/*" style="display:none;">';
    }

    return _section('創作過程', `
        <div class="showcase-process-modes" id="showcase-process-modes">${modeButtons}</div>
        <div class="showcase-process-content" id="showcase-process-content">${content}</div>
    `);
}

function _bindProcessEvents(container, projectId) {
    // Mode switching
    container.querySelector('#showcase-process-modes')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.showcase-process-mode-btn');
        if (!btn) return;
        const mode = btn.dataset.mode;
        try {
            await _fetch('/projects/' + projectId + '/showcase', {
                method: 'PUT', body: JSON.stringify({ process_mode: mode }),
            });
            loadDeliveryTab(projectId);
        } catch (err) { alert('切換模式失敗: ' + err.message); }
    });

    // Gallery mode: add images
    const processAdd = container.querySelector('#showcase-process-add');
    const processInput = container.querySelector('#showcase-process-input');
    if (processAdd && processInput) {
        processAdd.addEventListener('click', () => processInput.click());
    }

    if (processInput) {
        processInput.addEventListener('change', async () => {
            for (const file of processInput.files) {
                try {
                    await _uploadFile('/projects/' + projectId + '/showcase/process', file);
                } catch (err) { console.error('Process upload error:', err); }
            }
            loadDeliveryTab(projectId);
        });
    }

    // Media mode: add image/video
    container.querySelector('#showcase-process-add-img')?.addEventListener('click', () => {
        processInput?.click();
    });
    container.querySelector('#showcase-process-add-video')?.addEventListener('click', async () => {
        const url = prompt('輸入影片 URL:');
        if (!url) return;
        try {
            await _fetch('/projects/' + projectId + '/showcase/process', {
                method: 'POST', body: JSON.stringify({ type: 'video', url, caption: '' }),
            });
            loadDeliveryTab(projectId);
        } catch (err) { alert('新增失敗: ' + err.message); }
    });

    // Timeline mode: add per phase
    container.querySelectorAll('.showcase-timeline-add-img').forEach(btn => {
        btn.addEventListener('click', () => {
            const phase = btn.dataset.phase;
            const input = container.querySelector('#showcase-process-input');
            if (input) {
                input.dataset.phase = phase;
                input.click();
            }
        });
    });
    container.querySelectorAll('.showcase-timeline-add-text').forEach(btn => {
        btn.addEventListener('click', async () => {
            const phase = btn.dataset.phase;
            const caption = prompt('輸入描述文字:');
            if (!caption) return;
            try {
                await _fetch('/projects/' + projectId + '/showcase/process', {
                    method: 'POST', body: JSON.stringify({ type: 'text', phase, caption }),
                });
                loadDeliveryTab(projectId);
            } catch (err) { alert('新增失敗: ' + err.message); }
        });
    });

    // Delete process items
    container.querySelector('#showcase-process-content')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.showcase-process-del');
        if (!btn) return;
        const id = btn.dataset.id;
        try {
            await _fetch('/projects/' + projectId + '/showcase/process/' + id, { method: 'DELETE' });
            loadDeliveryTab(projectId);
        } catch (err) { alert('刪除失敗: ' + err.message); }
    });
}

// ── Credits ────────────────────────────────────────────────

function _renderCredits(sc, projectId) {
    const credits = sc.credits || [];
    const rows = credits.map((c, i) => `
        <div class="showcase-credit-row" data-idx="${i}">
            <span class="showcase-credit-role">${_esc(c.role || '—')}</span>
            <span class="showcase-credit-name">${_esc(c.name)}</span>
            ${c.resume_url ? `<a href="${_esc(c.resume_url)}" target="_blank" class="crm-btn crm-btn-secondary crm-btn-sm" title="履歷" style="padding:2px 6px;">&#128279;</a>` : ''}
            <button class="showcase-credit-del" data-idx="${i}" title="移除">&times;</button>
        </div>
    `).join('');

    return _section('Credit', `
        <div class="showcase-credits-list" id="showcase-credits-list">${rows}</div>
        <div style="display:flex;gap:6px;margin-top:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-credits-auto">自動載入團隊</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-credits-add">+ 手動新增</button>
        </div>
    `);
}

function _bindCreditEvents(container, projectId) {
    // Auto-load from team
    container.querySelector('#showcase-credits-auto')?.addEventListener('click', async () => {
        try {
            const data = await _fetch('/projects/' + projectId + '/showcase/credits/auto');
            if (data.credits && data.credits.length > 0) {
                await _fetch('/projects/' + projectId + '/showcase/credits', {
                    method: 'PUT', body: JSON.stringify({ credits: data.credits }),
                });
                loadDeliveryTab(projectId);
            } else {
                alert('此專案尚未配置團隊人員');
            }
        } catch (err) { alert('載入失敗: ' + err.message); }
    });

    // Manual add
    container.querySelector('#showcase-credits-add')?.addEventListener('click', async () => {
        const name = prompt('姓名:');
        if (!name) return;
        const role = prompt('職稱:') || '';
        try {
            await _fetch('/projects/' + projectId + '/showcase/credits', {
                method: 'POST', body: JSON.stringify({ name, role }),
            });
            loadDeliveryTab(projectId);
        } catch (err) { alert('新增失敗: ' + err.message); }
    });

    // Delete credit
    container.querySelector('#showcase-credits-list')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('.showcase-credit-del');
        if (!btn) return;
        const idx = btn.dataset.idx;
        try {
            await _fetch('/projects/' + projectId + '/showcase/credits/' + idx, { method: 'DELETE' });
            loadDeliveryTab(projectId);
        } catch (err) { alert('刪除失敗: ' + err.message); }
    });
}

// ── Publish Control ────────────────────────────────────────

function _renderPublish(sc, projectId) {
    const published = !!sc.published;
    const statusText = published ? '已發佈' : '未發佈';
    const statusClass = published ? 'showcase-status-published' : 'showcase-status-draft';
    const publicUrl = published ? `${location.origin}/showcase.html?id=${projectId}` : '';

    return _section('發佈控制', `
        <div class="showcase-publish-bar" id="showcase-publish-bar">
            <span class="showcase-publish-status ${statusClass}">${statusText}</span>
            <div style="display:flex;gap:6px;align-items:center;">
                ${publicUrl ? `<a href="${_esc(publicUrl)}" target="_blank" class="crm-btn crm-btn-secondary crm-btn-sm">預覽</a>` : ''}
                <button class="crm-btn ${published ? 'crm-btn-secondary' : 'crm-btn-primary'} crm-btn-sm" id="showcase-toggle-publish">
                    ${published ? '取消發佈' : '發佈'}
                </button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" id="showcase-copy-share" title="複製協作連結">&#128279; 協作連結</button>
            </div>
        </div>
        ${publicUrl ? `<div style="margin-top:6px;font-size:12px;color:#6b7280;">公開連結: <a href="${_esc(publicUrl)}" target="_blank" style="color:#9ca3af;">${_esc(publicUrl)}</a></div>` : ''}
        ${sc.edit_token ? `<div style="margin-top:4px;font-size:12px;color:#6b7280;">協作連結: <code style="color:#9ca3af;">${_esc(location.origin + '/showcase-edit.html?token=' + sc.edit_token)}</code></div>` : ''}
    `);
}

function _bindPublishEvents(container, projectId) {
    container.querySelector('#showcase-toggle-publish')?.addEventListener('click', async () => {
        try {
            await _fetch('/projects/' + projectId + '/showcase/publish', { method: 'POST' });
            loadDeliveryTab(projectId);
        } catch (err) { alert('操作失敗: ' + err.message); }
    });

    container.querySelector('#showcase-copy-share')?.addEventListener('click', async () => {
        try {
            const data = await _fetch('/projects/' + projectId + '/showcase/generate-edit-token', { method: 'POST', body: '{}' });
            const url = location.origin + data.url;
            await navigator.clipboard.writeText(url);
            alert('已複製協作連結');
        } catch (err) { alert('取得連結失敗: ' + err.message); }
    });
}

// ── Init (called once at module load) ──────────────────────

export function initDeliveryHandlers() {
    // No global window.* handlers needed — all events are bound per-render
}
