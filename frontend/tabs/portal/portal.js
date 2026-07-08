/**
 * portal.js — 🎬 審批門戶 Tab（B1 看片審批客戶門戶，docs/BIZ_PLAN.md B1 段）
 *
 * 同源打 /api/v1/portal/*（帶 auth token）。「＋ 新增送審」（專案下拉 + 版本名 +
 * 影片路徑 + 📁 挑檔）→ 建立後顯示可複製的客戶連結（/review.html?token=）。
 * 連結列表按專案分組：狀態 pill（待審橙/修改中藍/已核准綠）/ 意見數（未解決紅字）
 * / 複製 / 開啟 / 改狀態 / 刪；點列展開意見列表（時間碼 + resolve checkbox）。
 * 風格照 intel / timesheets。
 */

import { esc } from '../website/website-utils.js';

const API = '/api/v1/portal';
const STATUSES = ['待審', '修改中', '已核准'];

async function tfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const headers = { 'Accept': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}) };
    if (opts.json !== undefined) {
        headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(opts.json);
        delete opts.json;
    }
    const r = await fetch(path, { ...opts, headers });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.json();
}

let _content = null;
let _projects = [];
let _links = [];
let _expanded = new Set();

export async function initPortalTab() {
    _content = document.getElementById('portal-content');
    if (!_content) return;
    _content.style.cssText = '';   // 移除「載入中…」的置中/padding inline 樣式
    _renderShell();
    await Promise.all([_loadProjects(), refreshLinks()]);
}

function _reviewUrl(token) {
    return location.origin + '/review.html?token=' + token;
}

async function _copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (_) {
        // http 非 secure context 時 clipboard API 不可用 → textarea fallback
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;opacity:0;';
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (_e) { /* noop */ }
        ta.remove();
        return ok;
    }
}

// ── 殼層 ─────────────────────────────────────────────────

function _renderShell() {
    _content.innerHTML = `
        <div style="text-align:left;color:#ccc;">
            <h2>🎬 審批門戶</h2>
            <div class="portal-sub">送審連結給客戶看片 — 時間軸精準留言 + 一鍵核准，取代 LINE 來回猜</div>
            <div class="portal-create">
                <select id="portal-new-project" style="min-width:180px;"><option value="">（載入專案中…）</option></select>
                <input id="portal-new-version" type="text" placeholder="版本名（初剪/一修/定剪…）" style="width:170px;">
                <input id="portal-new-path" type="text" placeholder="影片路徑（.mp4 / .mov / .m4v / .webm）" style="flex:1;min-width:240px;">
                <button id="portal-new-pick" class="portal-btn ghost" title="選擇影片檔">📁</button>
                <button id="portal-new-create" class="portal-btn">＋ 建立送審連結</button>
            </div>
            <div id="portal-created" style="display:none;"></div>
            <div id="portal-list"></div>
        </div>`;

    document.getElementById('portal-new-pick').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
            const d = await tfetch('/api/v1/utils/pick_file?title=' + encodeURIComponent('選擇送審影片'));
            if (d.error) { alert(d.message || '無法開啟檔案選擇器'); return; }
            if (d.path) document.getElementById('portal-new-path').value = d.path;
        } catch (err) {
            alert('選擇檔案失敗：' + (err.message || err));
        } finally { btn.disabled = false; }
    });
    document.getElementById('portal-new-create').addEventListener('click', _createLink);
}

async function _loadProjects() {
    const sel = document.getElementById('portal-new-project');
    if (!sel) return;
    try {
        const d = await tfetch('/api/v1/crm/projects');
        _projects = d.projects || [];
        sel.innerHTML = '<option value="">— 選擇專案 —</option>' + _projects.map(p =>
            `<option value="${esc(p.id)}">${esc(p.name)}${p.client_short_name ? '（' + esc(p.client_short_name) + '）' : ''}</option>`
        ).join('');
    } catch (e) {
        sel.innerHTML = `<option value="">專案載入失敗：${esc(e.message || e)}</option>`;
    }
}

async function _createLink() {
    const projectId = document.getElementById('portal-new-project').value;
    const version = document.getElementById('portal-new-version').value.trim();
    const path = document.getElementById('portal-new-path').value.trim();
    if (!projectId) { alert('請先選擇專案'); return; }
    if (!path) { alert('請填影片路徑（或按 📁 選擇檔案）'); return; }
    const btn = document.getElementById('portal-new-create');
    btn.disabled = true;
    try {
        const d = await tfetch(`${API}/links`, {
            method: 'POST',
            json: { project_id: projectId, version_label: version, video_path: path },
        });
        _showCreated(d.link);
        document.getElementById('portal-new-version').value = '';
        document.getElementById('portal-new-path').value = '';
        refreshLinks();
    } catch (err) {
        alert('建立失敗：' + (err.message || err));
    } finally { btn.disabled = false; }
}

function _showCreated(link) {
    const box = document.getElementById('portal-created');
    const url = _reviewUrl(link.token);
    box.style.display = '';
    box.className = 'portal-created';
    box.innerHTML = `
        <span>✅ 已建立「${esc(link.version_label || '未命名版本')}」— 把連結傳給客戶：</span>
        <code>${esc(url)}</code>
        <button class="portal-btn" id="portal-created-copy">複製</button>`;
    document.getElementById('portal-created-copy').addEventListener('click', async (e) => {
        const ok = await _copyText(url);
        e.target.textContent = ok ? '已複製 ✓' : '複製失敗';
    });
}

// ── 連結列表（按專案分組） ───────────────────────────────

function _fmtTc(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

function _commentHtml(c) {
    return `
        <div class="portal-comment${c.resolved ? ' resolved' : ''}" data-cid="${esc(c.id)}">
            <span class="tc">${_fmtTc(c.timecode_sec)}</span>
            <span class="body">${esc(c.body)}</span>
            <span class="who">${esc(c.author_name || '匿名')}</span>
            <label><input type="checkbox" data-resolve ${c.resolved ? 'checked' : ''}>已處理</label>
        </div>`;
}

function _linkHtml(l) {
    const when = (l.created_at || '').slice(0, 16).replace('T', ' ');
    const cnum = l.comment_total
        ? `💬 ${l.comment_total}${l.comment_unresolved ? ` <b class="unres">（未解決 ${l.comment_unresolved}）</b>` : ''}`
        : '💬 0';
    const approved = l.status === '已核准' && l.approved_by
        ? `<span class="meta">✅ ${esc(l.approved_by)} 核准</span>` : '';
    const open = _expanded.has(l.id);
    return `
        <div class="portal-link" data-id="${esc(l.id)}">
            <div class="portal-link-row" data-toggle>
                <span class="ver">${esc(l.version_label || '未命名版本')}</span>
                <span class="portal-pill s-${esc(l.status)}">${esc(l.status)}</span>
                <span class="cnum">${cnum}</span>
                ${approved}
                <span class="meta">${esc(when)}</span>
                ${l.expires_at ? `<span class="meta">⏰ ${esc(l.expires_at)} 到期</span>` : ''}
                <span class="spacer"></span>
                <button class="portal-btn ghost" data-act="copy" title="複製客戶連結">🔗 複製</button>
                <button class="portal-btn ghost" data-act="open" title="開啟客戶頁">開啟</button>
                <select data-act="status" title="改狀態">
                    ${STATUSES.map(s => `<option value="${s}" ${s === l.status ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
                <button class="portal-btn danger" data-act="del">刪</button>
            </div>
            <div class="portal-comments" style="display:${open ? 'block' : 'none'};">
                ${(l.comments || []).map(_commentHtml).join('')
                    || '<div style="color:#666;font-size:12px;">尚無客戶意見</div>'}
            </div>
        </div>`;
}

function _emptyHtml() {
    return `
        <div class="portal-empty">
            <b>🎬 還沒有送審連結 — 三步驟開始：</b><br>
            1. 上方選專案、填版本名（如「初剪」）、按 📁 選影片檔（proxy .mp4 最順）<br>
            2. 按「＋ 建立送審連結」→ 複製連結傳給客戶（LINE / Email 皆可，免登入）<br>
            3. 客戶在手機上看片、點時間軸留言、按「✅ 核准此版本」— 意見會即時出現在這裡
        </div>`;
}

async function refreshLinks() {
    const box = document.getElementById('portal-list');
    if (!box) return;
    try {
        _links = (await tfetch(`${API}/links`)).links || [];
    } catch (e) {
        box.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">列表載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    if (!_links.length) { box.innerHTML = _emptyHtml(); return; }

    // 按專案分組（維持後端 created_at desc 的組間順序）
    const groups = [];
    const byProject = new Map();
    for (const l of _links) {
        const key = l.project_id;
        if (!byProject.has(key)) {
            const g = { name: l.project_name || '（專案已刪）', links: [] };
            byProject.set(key, g);
            groups.push(g);
        }
        byProject.get(key).links.push(l);
    }
    box.innerHTML = groups.map(g => `
        <div class="portal-group">
            <h3>📁 ${esc(g.name)}</h3>
            ${g.links.map(_linkHtml).join('')}
        </div>`).join('');
    _bindLinkActions(box);
}

function _bindLinkActions(box) {
    box.querySelectorAll('.portal-link').forEach(card => {
        const lid = card.dataset.id;
        const link = _links.find(l => l.id === lid);
        if (!link) return;

        card.querySelector('[data-toggle]').addEventListener('click', (e) => {
            if (e.target.closest('[data-act]')) return;   // 動作鈕不觸發展開
            const panel = card.querySelector('.portal-comments');
            const open = panel.style.display !== 'none';
            panel.style.display = open ? 'none' : 'block';
            if (open) _expanded.delete(lid); else _expanded.add(lid);
        });

        card.querySelector('[data-act="copy"]').addEventListener('click', async (e) => {
            const ok = await _copyText(_reviewUrl(link.token));
            e.target.textContent = ok ? '已複製 ✓' : '複製失敗';
            setTimeout(() => { e.target.textContent = '🔗 複製'; }, 1500);
        });
        card.querySelector('[data-act="open"]').addEventListener('click', () => {
            window.open(_reviewUrl(link.token), '_blank');
        });
        card.querySelector('[data-act="status"]').addEventListener('change', async (e) => {
            try {
                await tfetch(`${API}/links/${lid}`, { method: 'PUT', json: { status: e.target.value } });
                refreshLinks();
            } catch (err) {
                alert('改狀態失敗：' + (err.message || err));
                e.target.value = link.status;
            }
        });
        card.querySelector('[data-act="del"]').addEventListener('click', async () => {
            if (!confirm(`確定刪除「${link.version_label || '未命名版本'}」送審連結？客戶連結會立即失效，意見一併刪除。`)) return;
            try {
                await tfetch(`${API}/links/${lid}`, { method: 'DELETE' });
                _expanded.delete(lid);
                refreshLinks();
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        });

        card.querySelectorAll('[data-resolve]').forEach(cb => {
            cb.addEventListener('change', async (e) => {
                const cid = e.target.closest('.portal-comment').dataset.cid;
                try {
                    await tfetch(`${API}/comments/${cid}/resolve`, { method: 'PUT' });
                    refreshLinks();
                } catch (err) {
                    alert('更新失敗：' + (err.message || err));
                    e.target.checked = !e.target.checked;
                }
            });
        });
    });
}
