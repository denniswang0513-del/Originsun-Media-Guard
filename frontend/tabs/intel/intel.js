/**
 * intel.js — 📡 產業情報 Tab（P-c：第四個 AI runner，docs/PREPROD_PLAN.md C 段）
 *
 * 同源打 /api/v1/intel/*（帶 auth token）。狀態列（enabled 開關 / 上次執行摘要 /
 * ▶ 立即抓取）+ 分類/狀態篩選 + 搜尋 + 情報卡片列表（分數配色、分類 pill、
 * 截止日紅字倒數、⭐ 收藏 / 🗂 封存 / 📑 轉提案）+ ⚙ 來源管理折疊卡
 * （來源 CRUD + 逐源 enabled toggle + cron）。風格照 timesheets / proposals。
 */

import { esc } from '../website/website-utils.js';

const API = '/api/v1/intel';
const CATEGORIES = ['標案', '補助', '產業', '技術', '競品', '未分類'];
const STATUS_OPTIONS = [
    ['', '全部狀態'], ['new', '新進'], ['starred', '⭐ 收藏'],
    ['archived', '封存'], ['converted', '已轉提案'],
];

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
let _settings = null;
let _sources = [];
let _filters = { status: '', category: '', q: '' };
let _qTimer = null;
let _srcOpen = false;

export async function initIntelTab() {
    _content = document.getElementById('intel-content');
    if (!_content) return;
    _content.style.cssText = '';   // 移除「載入中…」的置中/padding inline 樣式
    _renderShell();
    await Promise.all([refreshStatus(), refreshItems(), refreshSources()]);
}

// ── 殼層 ─────────────────────────────────────────────────

function _renderShell() {
    _content.innerHTML = `
        <div style="text-align:left;color:#ccc;">
            <h2>📡 產業情報</h2>
            <div class="intel-sub">標案 / 補助 / 產業動態每日自動蒐集 + AI 摘要評分 — 高分商機一鍵轉提案</div>
            <div class="intel-status" id="intel-status">載入設定中…</div>
            <div class="intel-toolbar">
                <select id="intel-f-status">
                    ${STATUS_OPTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select>
                <select id="intel-f-category"><option value="">全部分類</option>
                    ${CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join('')}</select>
                <input id="intel-q" type="text" placeholder="🔍 搜尋標題/摘要…" style="width:200px;">
            </div>
            <div id="intel-items"></div>
            <div class="intel-card" id="intel-src-card">
                <h3 id="intel-src-toggle">⚙ 來源管理 <span id="intel-src-arrow">▸</span></h3>
                <div id="intel-src-body" style="display:none;"></div>
            </div>
        </div>`;

    document.getElementById('intel-q').addEventListener('input', (e) => {
        clearTimeout(_qTimer);
        _qTimer = setTimeout(() => { _filters.q = e.target.value.trim(); refreshItems(); }, 300);
    });
    for (const [id, key] of [['intel-f-status', 'status'], ['intel-f-category', 'category']]) {
        document.getElementById(id).addEventListener('change', (e) => {
            _filters[key] = e.target.value;
            refreshItems();
        });
    }
    document.getElementById('intel-src-toggle').addEventListener('click', () => {
        _srcOpen = !_srcOpen;
        document.getElementById('intel-src-body').style.display = _srcOpen ? 'block' : 'none';
        document.getElementById('intel-src-arrow').textContent = _srcOpen ? '▾' : '▸';
    });
}

// ── 狀態列（enabled / 上次執行 / 立即抓取） ───────────────

function _summaryText(s) {
    if (!s) return '尚未執行過';
    if (s.status === 'disabled') return '上次觸發時 runner 未啟用';
    const parts = [`新 ${s.new ?? 0}`, `存 ${s.saved ?? 0}`];
    if (s.degraded) parts.push(`降級 ${s.degraded}`);
    if (s.truncated) parts.push(`截斷 ${s.truncated}`);
    if ((s.errors || []).length) parts.push(`⚠ 錯誤 ${s.errors.length}`);
    return parts.join(' / ');
}

async function refreshStatus() {
    const bar = document.getElementById('intel-status');
    if (!bar) return;
    try {
        _settings = await tfetch(`${API}/settings`);
    } catch (e) {
        bar.innerHTML = `<span style="color:#f87171;">設定載入失敗：${esc(e.message || e)}</span>`;
        return;
    }
    const lastAt = _settings.last_run_at
        ? new Date(_settings.last_run_at * 1000).toLocaleString('zh-TW', { hour12: false }) : '—';
    const errs = (_settings.last_run_summary && _settings.last_run_summary.errors) || [];
    bar.innerHTML = `
        <label><input type="checkbox" id="intel-enabled" ${_settings.enabled ? 'checked' : ''}>
            啟用每日抓取（cron：<code style="color:#93c5fd;">${esc(_settings.cron || '')}</code>）</label>
        <span class="run-meta" ${errs.length ? `title="${esc(errs.join('\n'))}"` : ''}>上次執行：<b>${esc(lastAt)}</b>
            · ${esc(_summaryText(_settings.last_run_summary))}</span>
        ${_settings.running ? '<span style="color:#fbbf24;">⏳ 執行中…</span>' : ''}
        <button class="intel-btn" id="intel-run" ${_settings.running ? 'disabled' : ''}>▶ 立即抓取</button>
        <button class="intel-btn ghost" id="intel-refresh" title="重新整理列表與狀態">↻</button>`;

    document.getElementById('intel-enabled').addEventListener('change', async (e) => {
        try {
            await tfetch(`${API}/settings`, { method: 'PUT', json: { enabled: e.target.checked } });
            refreshStatus();
        } catch (err) {
            alert('設定更新失敗：' + (err.message || err));
            e.target.checked = !e.target.checked;
        }
    });
    document.getElementById('intel-run').addEventListener('click', async () => {
        if (!_settings.enabled) { alert('runner 尚未啟用 — 請先打開「啟用每日抓取」開關'); return; }
        try {
            const d = await tfetch(`${API}/run`, { method: 'POST' });
            if (d.status === 'busy') { alert('已有一輪在跑，請稍後'); return; }
            alert('已在背景開始抓取 — 抓取+AI 摘要需數分鐘，稍後按 ↻ 更新');
            refreshStatus();
        } catch (err) { alert('啟動失敗：' + (err.message || err)); }
    });
    document.getElementById('intel-refresh').addEventListener('click', () => {
        refreshStatus();
        refreshItems();
    });
}

// ── 情報列表 ─────────────────────────────────────────────

function _scoreCls(score) {
    if (score >= 70) return 'hi';
    if (score >= 40) return 'mid';
    return 'lo';
}

function _deadlineHtml(deadline) {
    if (!deadline) return '';
    const days = Math.ceil((new Date(deadline + 'T23:59:59') - Date.now()) / 86400000);
    if (isNaN(days)) return '';
    if (days < 0) return `<span class="intel-deadline over">⏰ ${esc(deadline)} 已截止</span>`;
    return `<span class="intel-deadline${days < 7 ? ' urgent' : ''}">⏰ 截止 ${esc(deadline)}（剩 ${days} 天）</span>`;
}

function _itemHtml(it) {
    const converted = it.status === 'converted';
    const actions = converted
        ? '<span class="intel-badge">✅ 已轉提案</span>'
        : `
        <button class="intel-btn ghost" data-act="star" title="${it.status === 'starred' ? '取消收藏' : '收藏'}"
            style="${it.status === 'starred' ? 'border-color:#f59e0b;color:#fbbf24;' : ''}">⭐</button>
        <button class="intel-btn ghost" data-act="${it.status === 'archived' ? 'unarchive' : 'archive'}"
            title="${it.status === 'archived' ? '解封存' : '封存'}">${it.status === 'archived' ? '↩' : '🗂'}</button>
        <button class="intel-btn ghost" data-act="convert" title="建立提案草稿（提案庫）">📑 轉提案</button>`;
    const when = (it.created_at || '').slice(0, 16).replace('T', ' ');
    return `
        <div class="intel-item${it.status === 'archived' ? ' archived' : ''}" data-id="${esc(it.id)}">
            <div class="intel-score ${_scoreCls(it.score)}">${it.score}<span class="sl">分</span></div>
            <div class="intel-body">
                <div>
                    <span class="intel-pill c-${esc(it.category)}">${esc(it.category)}</span>
                    <a class="title" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title || '(無標題)')}</a>
                </div>
                ${it.summary ? `<div class="intel-summary">${esc(it.summary)}</div>` : ''}
                <div class="intel-meta">${esc(it.source_name || '（來源已刪）')} · ${esc(when)}${_deadlineHtml(it.deadline)}</div>
            </div>
            <div class="intel-actions">${actions}</div>
        </div>`;
}

function _emptyHtml() {
    if (_filters.status || _filters.category || _filters.q) {
        return '<div class="intel-empty" style="text-align:center;">沒有符合篩選條件的情報</div>';
    }
    return `
        <div class="intel-empty">
            <b>📡 還沒有情報 — 三步驟開始自動蒐集：</b><br>
            1. 在下方「⚙ 來源管理」加入 RSS 來源（建議：政府電子採購網 RSS、文化部/文策院公告、產業媒體如動腦/Campaign）<br>
            2. 打開上方「啟用每日抓取」開關（每天 08:30 自動跑）<br>
            3. 按「▶ 立即抓取」跑第一輪 — AI 會自動摘要、分類、評分、抽標案截止日
        </div>`;
}

async function refreshItems() {
    const box = document.getElementById('intel-items');
    if (!box) return;
    try {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(_filters)) if (v) params.set(k, v);
        const qs = params.toString();
        const d = await tfetch(`${API}/items` + (qs ? '?' + qs : ''));
        const items = d.items || [];
        box.innerHTML = items.length ? items.map(_itemHtml).join('') : _emptyHtml();
        _bindItemActions(box);
    } catch (e) {
        box.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">情報載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _bindItemActions(box) {
    box.querySelectorAll('.intel-item [data-act]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const iid = btn.closest('.intel-item').dataset.id;
            const act = btn.dataset.act;
            try {
                if (act === 'convert') {
                    if (!confirm('確定轉提案？將在提案庫建立一筆草稿（tags=產業情報）')) return;
                    const d = await tfetch(`${API}/items/${iid}/convert`, { method: 'POST' });
                    alert('已建立提案草稿 ✅（proposal_id: ' + d.proposal_id + '）\n到「📑 提案庫」補客戶/提案日等欄位');
                } else {
                    await tfetch(`${API}/items/${iid}/${act}`, { method: 'POST' });
                }
                refreshItems();
            } catch (err) { alert('操作失敗：' + (err.message || err)); }
        });
    });
}

// ── 來源管理折疊卡 ───────────────────────────────────────

function _srcRow(s) {
    const kw = (s.keywords || []).join(', ');
    const last = s.last_fetched_at ? s.last_fetched_at.slice(0, 16).replace('T', ' ') : '—';
    return `
        <tr data-sid="${esc(s.id)}">
            <td>${esc(s.name)}</td>
            <td class="url"><a href="${esc(s.url)}" target="_blank" rel="noopener" style="color:#888;">${esc(s.url)}</a></td>
            <td>${esc(kw || '（全收）')}</td>
            <td style="color:#777;">${esc(last)}</td>
            <td><label style="cursor:pointer;"><input type="checkbox" data-src-enabled ${s.enabled ? 'checked' : ''}></label></td>
            <td><button class="intel-btn danger" data-src-del style="padding:3px 8px;font-size:11px;">刪</button></td>
        </tr>`;
}

async function refreshSources() {
    const body = document.getElementById('intel-src-body');
    if (!body) return;
    try {
        _sources = (await tfetch(`${API}/sources`)).sources || [];
    } catch (e) {
        body.innerHTML = `<div style="color:#f87171;padding:12px;">來源載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    document.getElementById('intel-src-toggle').firstChild.textContent = `⚙ 來源管理（${_sources.length}） `;
    body.innerHTML = `
        <table>
            <thead><tr><th>名稱</th><th>URL（RSS）</th><th>關鍵字</th><th>上次抓取</th><th>啟用</th><th></th></tr></thead>
            <tbody>${_sources.map(_srcRow).join('')
                || '<tr><td colspan="6" style="color:#666;text-align:center;padding:16px;">尚無來源 — 在下方新增第一個 RSS 來源</td></tr>'}</tbody>
        </table>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;">
            <input id="intel-src-name" type="text" placeholder="名稱（例：政府電子採購網）" style="width:180px;">
            <input id="intel-src-url" type="text" placeholder="https://（RSS 網址）" style="flex:1;min-width:220px;">
            <input id="intel-src-kw" type="text" placeholder="關鍵字，逗號分隔（空=全收）" style="width:220px;">
            <button id="intel-src-add" class="intel-btn">＋ 新增來源</button>
        </div>
        <div style="display:flex;gap:6px;align-items:center;margin-top:10px;">
            <span style="color:#888;font-size:12px;">排程 cron：</span>
            <input id="intel-cron" type="text" value="${esc((_settings && _settings.cron) || '30 8 * * *')}" style="width:120px;">
            <button id="intel-cron-save" class="intel-btn ghost">存</button>
            <span class="intel-note" style="margin-top:0;">預設 30 8 * * *（每日 08:30，錯開社群 09:00）</span>
        </div>
        <div class="intel-note">白名單制：只抓這裡列的來源；每源單次上限 50 項、整輪上限 200 項。
            關鍵字建議：影片、影像、宣傳、紀錄片、多媒體。只存標題+摘要+原文連結（不轉貼全文）。</div>`;

    body.querySelectorAll('tr[data-sid]').forEach(tr => {
        const sid = tr.dataset.sid;
        tr.querySelector('[data-src-enabled]').addEventListener('change', async (e) => {
            try {
                await tfetch(`${API}/sources/${sid}`, { method: 'PUT', json: { enabled: e.target.checked } });
            } catch (err) {
                alert('更新失敗：' + (err.message || err));
                e.target.checked = !e.target.checked;
            }
        });
        tr.querySelector('[data-src-del]').addEventListener('click', async () => {
            const src = _sources.find(s => s.id === sid);
            if (!confirm(`確定刪除來源「${(src && src.name) || sid}」？已入庫的情報會保留。`)) return;
            try {
                await tfetch(`${API}/sources/${sid}`, { method: 'DELETE' });
                refreshSources();
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        });
    });
    body.querySelector('#intel-src-add').addEventListener('click', async () => {
        const name = body.querySelector('#intel-src-name').value.trim();
        const url = body.querySelector('#intel-src-url').value.trim();
        const keywords = body.querySelector('#intel-src-kw').value.split(/[,，]/).map(k => k.trim()).filter(Boolean);
        if (!url) { alert('RSS 網址必填'); return; }
        try {
            await tfetch(`${API}/sources`, { method: 'POST', json: { name, url, keywords } });
            refreshSources();
        } catch (err) { alert('新增失敗：' + (err.message || err)); }
    });
    body.querySelector('#intel-cron-save').addEventListener('click', async () => {
        const cron = body.querySelector('#intel-cron').value.trim();
        if (!cron) { alert('cron 不可為空'); return; }
        try {
            await tfetch(`${API}/settings`, { method: 'PUT', json: { cron } });
            alert('已更新排程');
            refreshStatus();
        } catch (err) { alert('cron 更新失敗：' + (err.message || err)); }
    });
}
