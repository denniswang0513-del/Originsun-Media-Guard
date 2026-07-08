/**
 * footage.js — 🎞️ 素材庫 Tab（B5 逐字稿全文檢索，BIZ_PLAN B5）
 *
 * MASTER 同源功能：打 /api/v1/footage/*（帶 auth token）。
 * 搜逐字稿內容/檔名/專案 → 命中回上下文 snippet（前端拿 q 對 snippet <mark> 高亮）。
 * 「掃描資料夾」把既有影片 + 同名 .txt/.srt 逐字稿建索引。
 */

import { esc } from '../website/website-utils.js';

async function ffetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        ...opts,
        headers: {
            'Accept': 'application/json',
            ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
            ...(token ? { 'Authorization': 'Bearer ' + token } : {}),
        },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.json();
}

let _content = null;
let _q = '';
let _projFilter = '';
let _extFilter = '';
let _projects = [];
let _scanPoll = null;

export async function initFootageTab() {
    _content = document.getElementById('ft-content');
    if (!_content) return;
    try {
        _projects = (await ffetch('/api/v1/crm/projects').catch(() => ({ projects: [] }))).projects || [];
    } catch { _projects = []; }
    await refresh();
}

function _fmtDur(sec) {
    if (!sec) return '';
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.round(sec % 60);
    return (h ? h + ':' : '') + String(m).padStart(h ? 2 : 1, '0') + ':' + String(s).padStart(2, '0');
}

function _hi(snippet, q) {
    // 先 esc 再對 q 做 <mark> 包裹（順序關鍵：避免注入）
    const safe = esc(snippet || '');
    if (!q) return safe;
    const safeQ = esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return safe.replace(new RegExp('(' + safeQ + ')', 'gi'), '<mark>$1</mark>');
}

async function refresh() {
    try {
        const params = new URLSearchParams();
        if (_q) params.set('q', _q);
        if (_projFilter) params.set('project_id', _projFilter);
        if (_extFilter) params.set('ext', _extFilter);
        const [res, st] = await Promise.all([
            ffetch('/api/v1/footage/search?' + params.toString()),
            ffetch('/api/v1/footage/stats'),
        ]);
        _content.innerHTML = _render(res, st);
        _bind();
    } catch (e) {
        _content.innerHTML = `<div style="color:#f87171;padding:24px;">素材庫載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _render(res, st) {
    const totalHours = Math.round((st.total_duration_sec || 0) / 360) / 10;
    const projOpts = _projects.map(p => `<option value="${esc(p.id)}"${p.id === _projFilter ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
    const bar = `
        <h2>🎞️ 素材庫</h2>
        <div class="ft-sub">搜逐字稿內容、檔名、專案 — 重用一段素材 = 省一天拍攝</div>
        <div style="margin-bottom:10px;">
            <span class="ft-chip"><b>${st.total}</b>素材</span>
            <span class="ft-chip"><b>${totalHours}</b>小時</span>
            <span class="ft-chip"><b>${st.with_transcript}</b>有逐字稿</span>
            <span class="ft-chip"><b>${st.projects}</b>專案</span>
        </div>
        <div class="ft-bar">
            <input id="ft-q" type="search" placeholder="搜逐字稿內容、檔名、專案…" value="${esc(_q)}">
            <select id="ft-proj"><option value="">全部專案</option>${projOpts}</select>
            <select id="ft-ext"><option value="">全部格式</option>
                ${['.mp4', '.mov', '.mxf', '.mkv', '.avi'].map(e => `<option value="${e}"${e === _extFilter ? ' selected' : ''}>${e}</option>`).join('')}</select>
            <button class="ft-btn" data-ft="search">搜尋</button>
            <button class="ft-btn ghost" data-ft="scan">📂 掃描資料夾</button>
        </div>
        <div id="ft-scan-slot"></div>`;

    if (!res.items.length) {
        const empty = _q
            ? `<div class="ft-card" style="text-align:center;color:#888;">找不到符合「${esc(_q)}」的素材</div>`
            : `<div class="ft-card" style="max-width:640px;">
                <h3 style="color:#ddd;font-size:14px;margin:0 0 8px;">🚀 素材庫還是空的</h3>
                <ol>
                    <li>按上面的 <b>📂 掃描資料夾</b>，選你的素材根目錄</li>
                    <li>系統掃出所有影片、抽 metadata、把<b>同名的 .txt/.srt 逐字稿</b>一起建索引</li>
                    <li>之後就能用逐字稿內容搜片段——「找講過『永續』的訪談」「找所有海岸空拍」</li>
                </ol>
                <div class="ft-note">逐字稿來自「AI 逐字稿」功能產的 .txt/.srt（放在影片同資料夾、同檔名）。
                    沒有逐字稿的影片也會入索引，只是只能用檔名/專案搜。</div>
            </div>`;
        return bar + empty;
    }

    const rows = res.items.map(f => {
        const meta = [f.project_name, _fmtDur(f.duration_sec), f.resolution, f.fps ? f.fps + 'fps' : '', f.ext]
            .filter(Boolean).join(' · ');
        const tags = (f.tags || []).map(t => `<span class="ft-tag" data-ft="untag" data-id="${esc(f.id)}" data-tag="${esc(t)}" title="點擊移除">${esc(t)}</span>`).join('');
        return `<div class="ft-card">
            <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
                <div style="flex:1;min-width:0;">
                    <div class="ft-fname">${esc(f.file_name)}${f.has_transcript ? '' : ' <span style="color:#666;font-size:11px;font-weight:400;">(無逐字稿)</span>'}</div>
                    <div class="ft-meta">${esc(meta)}</div>
                </div>
                <div style="display:flex;gap:4px;flex-shrink:0;">
                    <button class="ft-btn ghost" data-ft="open" data-id="${esc(f.id)}" style="padding:3px 8px;">📂 位置</button>
                    <button class="ft-btn ghost" data-ft="addtag" data-id="${esc(f.id)}" style="padding:3px 8px;">+ tag</button>
                </div>
            </div>
            ${f.snippet ? `<div class="ft-snippet">${_hi(f.snippet, _q)}</div>` : ''}
            ${tags ? `<div class="ft-tags">${tags}</div>` : ''}
        </div>`;
    }).join('');
    return bar + `<div style="color:#777;font-size:12px;margin-bottom:8px;">${res.count} 筆結果</div>` + rows;
}

function _bind() {
    const qEl = document.getElementById('ft-q');
    if (qEl) qEl.addEventListener('keydown', e => { if (e.key === 'Enter') { _q = qEl.value.trim(); refresh(); } });

    _content.querySelectorAll('[data-ft]').forEach(node => {
        const act = node.dataset.ft;
        node.addEventListener('click', async () => {
            try {
                if (act === 'search') {
                    _q = document.getElementById('ft-q').value.trim();
                    _projFilter = document.getElementById('ft-proj').value;
                    _extFilter = document.getElementById('ft-ext').value;
                    refresh();
                } else if (act === 'scan') {
                    _openScan();
                } else if (act === 'open') {
                    await ffetch('/api/v1/footage/' + node.dataset.id + '/open', { method: 'POST' });
                } else if (act === 'untag') {
                    const id = node.dataset.id, tag = node.dataset.tag;
                    const cur = await ffetch('/api/v1/footage/search?' + new URLSearchParams({ q: '', limit: '200' }));
                    const row = cur.items.find(x => x.id === id);
                    const tags = (row ? row.tags : []).filter(t => t !== tag);
                    await ffetch('/api/v1/footage/' + id + '/tags', { method: 'PUT', body: { tags } });
                    refresh();
                } else if (act === 'addtag') {
                    const t = prompt('新增標籤：');
                    if (!t || !t.trim()) return;
                    const id = node.dataset.id;
                    const cur = await ffetch('/api/v1/footage/search?' + new URLSearchParams({ q: '', limit: '200' }));
                    const row = cur.items.find(x => x.id === id);
                    const tags = [...(row ? row.tags : []), t.trim()];
                    await ffetch('/api/v1/footage/' + id + '/tags', { method: 'PUT', body: { tags } });
                    refresh();
                }
            } catch (e) { alert('操作失敗: ' + (e.message || e)); }
        });
    });
}

async function _openScan() {
    const slot = document.getElementById('ft-scan-slot');
    if (!slot) return;
    const projOpts = _projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    slot.innerHTML = `<div class="ft-card">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <input id="ft-scan-path" placeholder="素材根目錄路徑（如 D:\\Projects\\2026）" style="flex:1;min-width:240px;">
            <button class="ft-btn ghost" data-scan="pick">📁</button>
            <select id="ft-scan-proj"><option value="">不綁專案</option>${projOpts}</select>
            <button class="ft-btn" data-scan="go">開始掃描</button>
            <button class="ft-btn ghost" data-scan="close">取消</button>
        </div>
        <div id="ft-scan-msg" class="ft-note"></div>
    </div>`;
    slot.querySelector('[data-scan="close"]').addEventListener('click', () => { slot.innerHTML = ''; });
    slot.querySelector('[data-scan="pick"]').addEventListener('click', async () => {
        try {
            const r = await fetch('/api/v1/utils/pick_folder', { headers: { 'Authorization': 'Bearer ' + localStorage.getItem('auth_token') } });
            const d = await r.json();
            if (d.path) document.getElementById('ft-scan-path').value = d.path;
        } catch { /* pick 取消 */ }
    });
    slot.querySelector('[data-scan="go"]').addEventListener('click', async () => {
        const root = document.getElementById('ft-scan-path').value.trim();
        if (!root) return alert('先填資料夾路徑');
        const msg = document.getElementById('ft-scan-msg');
        try {
            await ffetch('/api/v1/footage/scan', { method: 'POST', body: {
                root_path: root, project_id: document.getElementById('ft-scan-proj').value || null,
            } });
            msg.textContent = '掃描中…';
            if (_scanPoll) clearInterval(_scanPoll);
            _scanPoll = setInterval(async () => {
                const st = await ffetch('/api/v1/footage/scan/status');
                if (!st.running) {
                    clearInterval(_scanPoll); _scanPoll = null;
                    const r = st.last_result || {};
                    msg.textContent = r.error ? ('失敗：' + r.error)
                        : `完成：掃 ${r.scanned} 檔、新增 ${r.indexed}、更新 ${r.updated}${r.truncated ? '（達上限 2000 截斷）' : ''}`;
                    refresh();
                } else {
                    msg.textContent = '掃描中…（掃大量檔案可能需數分鐘）';
                }
            }, 2000);
        } catch (e) { msg.textContent = '啟動失敗：' + (e.message || e); }
    });
}
