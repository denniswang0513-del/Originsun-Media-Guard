/**
 * proposals.js — 📑 提案庫 Tab（P-b：提案智財資產化 + win/loss 學習迴圈，
 * docs/PREPROD_PLAN.md B 段）
 *
 * 同源打 /api/v1/proposals/*（帶 auth token）。統計 chips（總提案/成案率，hover 看
 * by 類型/年度細目）+ 表格列表 + 篩選（搜尋/狀態/類型/年份）+ 詳情 overlay
 * （欄位編輯、deck 上傳下載、共用參考片庫掛載、一鍵成案 /convert、
 * 未成案強制填 outcome_reason）。overlay 掛在 body 下（tab section 有 transform
 * class，fixed 定位會被困住）。
 */

import { esc } from '../website/website-utils.js';

const API = '/api/v1/proposals';
const STATUSES = ['草稿', '已提案', '入圍', '成案', '未成案', '擱置'];
const PTYPES = ['形象', '廣告', '紀錄片', '政府標案', '社群', '其他'];
const DECK_EXTS = '.pdf,.ppt,.pptx,.key,.zip';
const DECK_MAX_BYTES = 50 * 1024 * 1024;

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
let _all = [];           // 最近一次「無篩選」清單（供年份下拉選項）
let _filters = { q: '', status: '', ptype: '', year: '' };
let _clientsCache = null;
let _qTimer = null;
let _escHandler = null;

export async function initProposalsTab() {
    _content = document.getElementById('prop-content');
    if (!_content) return;
    _content.style.cssText = '';   // 移除「載入中…」的置中/padding inline 樣式
    _renderShell();
    await refreshList();
}

// ── 列表 ─────────────────────────────────────────────────

function _renderShell() {
    _content.innerHTML = `
        <div style="text-align:left;color:#ccc;">
            <h2>📑 提案庫</h2>
            <div class="prop-sub">提案智財資產化：deck / 參考片單 / win-loss 原因全入庫，成案率從此有出處</div>
            <div class="prop-toolbar">
                <span class="prop-chip" id="prop-chip-total"><span class="n">–</span><span class="l">總提案</span></span>
                <span class="prop-chip rate" id="prop-chip-rate"><span class="n">–%</span><span class="l">成案率</span></span>
                <input id="prop-q" type="text" placeholder="🔍 搜尋標題…" style="width:180px;">
                <select id="prop-f-status"><option value="">全部狀態</option>
                    ${STATUSES.map(s => `<option value="${s}">${s}</option>`).join('')}</select>
                <select id="prop-f-ptype"><option value="">全部類型</option>
                    ${PTYPES.map(t => `<option value="${t}">${t}</option>`).join('')}</select>
                <select id="prop-f-year"><option value="">全部年份</option></select>
                <button id="prop-add" class="prop-btn">＋ 新提案</button>
            </div>
            <div class="prop-table-wrap">
                <table class="prop-table">
                    <thead><tr>
                        <th>標題</th><th>客戶</th><th>類型</th><th>狀態</th>
                        <th>提案日</th><th>預算範圍</th><th>參考</th>
                    </tr></thead>
                    <tbody id="prop-rows"></tbody>
                </table>
            </div>
        </div>`;

    document.getElementById('prop-q').addEventListener('input', (e) => {
        clearTimeout(_qTimer);
        _qTimer = setTimeout(() => { _filters.q = e.target.value.trim(); refreshList(); }, 300);
    });
    for (const [id, key] of [['prop-f-status', 'status'], ['prop-f-ptype', 'ptype'], ['prop-f-year', 'year']]) {
        document.getElementById(id).addEventListener('change', (e) => {
            _filters[key] = e.target.value;
            refreshList();
        });
    }
    document.getElementById('prop-add').addEventListener('click', () => _openEditor(null));
}

function _hasFilters() {
    return Object.values(_filters).some(v => v);
}

async function refreshList() {
    const tbody = document.getElementById('prop-rows');
    if (!tbody) return;
    try {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(_filters)) if (v) params.set(k, v);
        const qs = params.toString();
        const [d] = await Promise.all([
            tfetch(API + (qs ? '?' + qs : '')),
            _refreshStats(),   // 統計 chips 與列表並行更新
        ]);
        const props = d.proposals || [];
        if (!_hasFilters()) { _all = props; _syncYearOptions(); }
        tbody.innerHTML = props.length ? props.map(_row).join('')
            : '<tr><td colspan="7" style="color:#666;padding:40px;text-align:center;">尚無提案 — 按「＋ 新提案」建立第一筆</td></tr>';
        tbody.querySelectorAll('tr[data-id]').forEach(el => {
            el.addEventListener('click', () => openDetail(el.dataset.id));
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" style="color:#f87171;padding:30px;text-align:center;">提案載入失敗：${esc(e.message || e)}</td></tr>`;
    }
}

// 統計 chips：總提案 + 整體成案率；hover（title）看 by 類型 / by 年度細目
async function _refreshStats() {
    try {
        const s = await tfetch(`${API}/stats`);
        const total = document.getElementById('prop-chip-total');
        const rate = document.getElementById('prop-chip-rate');
        if (!total || !rate) return;
        total.querySelector('.n').textContent = s.total_all ?? 0;
        const ov = s.overall || { total: 0, won: 0, rate: 0 };
        rate.querySelector('.n').textContent = `${ov.rate}%`;
        rate.querySelector('.l').textContent = `成案率（${ov.won}/${ov.total}）`;
        const lines = (items, head) => [head, ...(items || []).map(b => `　${b.key}：${b.won}/${b.total}（${b.rate}%）`)];
        rate.title = [...lines(s.by_type, '── by 類型 ──'), ...lines(s.by_year, '── by 年度 ──')].join('\n');
        total.title = '含草稿/擱置的全部提案數；成案率分母只算已提案/入圍/成案/未成案';
    } catch { /* 統計失敗不擋列表 */ }
}

// 年份下拉選項 = 既有資料 pitch_date 的 distinct 年（保留目前選取）
function _syncYearOptions() {
    const sel = document.getElementById('prop-f-year');
    if (!sel) return;
    const cur = sel.value;
    const years = [...new Set(_all.map(p => (p.pitch_date || '').slice(0, 4)).filter(Boolean))]
        .sort().reverse();
    sel.innerHTML = '<option value="">全部年份</option>'
        + years.map(y => `<option value="${y}">${y}</option>`).join('');
    if (years.includes(cur)) sel.value = cur;
}

function _pill(status) {
    return `<span class="prop-pill s-${esc(status)}">${esc(status)}</span>`;
}

function _row(p) {
    return `
        <tr data-id="${esc(p.id)}">
            <td class="title">${esc(p.title)}</td>
            <td>${esc(p.client_name || '—')}</td>
            <td>${esc(p.ptype || '—')}</td>
            <td>${_pill(p.status)}</td>
            <td>${esc(p.pitch_date || '—')}</td>
            <td>${esc(p.budget_range || '—')}</td>
            <td>🎞 ${p.refs_count || 0}</td>
        </tr>`;
}

// ── 詳情 overlay ──────────────────────────────────────

function _closeOverlay() {
    document.getElementById('prop-overlay')?.remove();
    if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

function _mountOverlay(innerHTML) {
    _closeOverlay();
    const ov = document.createElement('div');
    ov.id = 'prop-overlay';
    ov.innerHTML = innerHTML;
    ov.addEventListener('click', (e) => { if (e.target === ov) _closeOverlay(); });
    document.body.appendChild(ov);
    _escHandler = (e) => { if (e.key === 'Escape') _closeOverlay(); };
    document.addEventListener('keydown', _escHandler);
    ov.querySelector('.prop-close')?.addEventListener('click', _closeOverlay);
    return ov;
}

async function _loadClients() {
    if (_clientsCache) return _clientsCache;
    try {
        const d = await tfetch('/api/v1/crm/clients');
        _clientsCache = (d.clients || []).map(c => ({ id: c.id, name: c.short_name }));
    } catch {
        _clientsCache = [];
    }
    return _clientsCache;
}

async function openDetail(pid) {
    let prop;
    try {
        prop = (await tfetch(`${API}/${pid}`)).proposal;
    } catch (e) {
        alert('提案載入失敗：' + (e.message || e));
        return;
    }
    let refLib = [];
    try {
        refLib = (await tfetch(`${API}/references`)).references || [];
    } catch { /* 片庫載入失敗不擋詳情 */ }

    const linked = prop.references || [];
    const linkedIds = new Set(linked.map(r => r.id));
    const pickable = refLib.filter(r => !linkedIds.has(r.id));

    const kv = (k, v) => v ? `<div class="prop-kv"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>` : '';
    const tagChips = (prop.tags || []).map(t => `<span class="prop-tag">${esc(t)}</span>`).join('');

    const refRows = linked.map(r => `
        <div class="prop-ref" data-rid="${esc(r.id)}">
            <div style="display:flex;gap:8px;align-items:center;">
                <a href="${esc(r.url)}" target="_blank" rel="noopener" style="flex:1;">${esc(r.title || r.url)}</a>
                <button class="prop-btn danger prop-ref-unlink" style="padding:2px 8px;font-size:11px;">解除</button>
            </div>
            ${r.note ? `<div class="note">${esc(r.note)}</div>` : ''}
        </div>`).join('');

    const ov = _mountOverlay(`
        <div class="prop-panel">
            <div class="prop-panel-head">
                <h3>${esc(prop.title)}</h3>
                <select id="pd-status" title="狀態（成案會自動建 CRM 專案；未成案必填原因）">
                    ${STATUSES.map(s => `<option value="${s}"${s === prop.status ? ' selected' : ''}>${s}</option>`).join('')}
                </select>
                <button id="pd-edit" class="prop-btn ghost">✏️ 編輯</button>
                <button id="pd-del" class="prop-btn danger">🗑 刪除</button>
                <button class="prop-close" title="關閉">✕</button>
            </div>
            <div class="prop-panel-body">
                <div class="prop-info-col">
                    <div class="prop-card-sec">
                        <h4>📋 基本資訊　${_pill(prop.status)}</h4>
                        ${kv('客戶', prop.client_name || (prop.client_id ? prop.client_id : ''))}
                        ${kv('類型', prop.ptype)}
                        ${kv('提案日', prop.pitch_date)}
                        ${kv('預算範圍', prop.budget_range)}
                        ${prop.project_id ? `<div class="prop-kv"><span class="k">已建專案</span><span class="v">✅ ${esc(prop.project_name || prop.project_id)}</span></div>` : ''}
                        ${kv('報價單', prop.quotation_id)}
                        ${tagChips ? `<div class="prop-kv"><span class="k">標籤</span><span class="v">${tagChips}</span></div>` : ''}
                        ${kv('建立者', prop.created_by)}
                    </div>
                    <div class="prop-card-sec">
                        <h4>📄 提案簡報（deck）</h4>
                        ${prop.deck_url
                            ? `<div style="margin-bottom:8px;"><a href="${esc(prop.deck_url)}" target="_blank" rel="noopener" style="color:#93c5fd;">⬇️ 下載簡報（${esc(prop.deck_url.split('.').pop())}）</a></div>`
                            : '<div style="color:#666;font-size:12px;margin-bottom:8px;">尚未上傳</div>'}
                        <button id="pd-deck-upload" class="prop-btn ghost">⬆️ ${prop.deck_url ? '更換簡報' : '上傳簡報'}</button>
                        <input id="pd-deck-file" type="file" accept="${DECK_EXTS}" style="display:none;">
                        <div class="prop-note">支援 pdf / ppt / pptx / key / zip，上限 50MB；更換會覆蓋 deck 連結。</div>
                    </div>
                    ${prop.outcome_reason ? `
                    <div class="prop-card-sec">
                        <h4>${prop.status === '未成案' ? '📉 未成案原因' : '📈 成案/結果原因'}（組織學習）</h4>
                        <div class="prop-outcome${prop.status === '未成案' ? ' lost' : ''}">${esc(prop.outcome_reason)}</div>
                    </div>` : ''}
                </div>
                <div class="prop-refs-col">
                    <div class="prop-card-sec">
                        <h4>🎞 參考片單（${linked.length}）</h4>
                        <div id="pd-refs">${refRows || '<div style="color:#666;font-size:12px;">尚未掛參考片</div>'}</div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;">
                            <select id="pr-pick" style="flex:1;min-width:160px;">
                                <option value="">（從共用片庫挑選…）</option>
                                ${pickable.map(r => `<option value="${esc(r.id)}">${esc(r.title || r.url)}</option>`).join('')}
                            </select>
                            <button id="pr-link" class="prop-btn ghost">掛上</button>
                        </div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;">
                            <input id="pr-new-url" type="text" placeholder="https://（新參考片網址）" style="flex:1;min-width:160px;">
                            <input id="pr-new-title" type="text" placeholder="標題（可空）" style="width:120px;">
                            <button id="pr-add" class="prop-btn">＋ 入庫並掛上</button>
                        </div>
                        <div class="prop-note">片庫是跨提案共用資產：解除只拿掉本提案的掛載，片子仍留在庫裡。</div>
                    </div>
                </div>
            </div>
        </div>`);

    // 狀態下拉：成案→confirm+/convert；未成案→強制填原因；其餘直接 PUT
    ov.querySelector('#pd-status').addEventListener('change', async (e) => {
        const val = e.target.value;
        const revert = () => { e.target.value = prop.status; };
        try {
            if (val === '成案') {
                if (prop.project_id) { alert('此提案已建過專案'); revert(); return; }
                if (!confirm(`確定成案？將自動建立 CRM 專案「${prop.title}」`)) { revert(); return; }
                const reason = prompt('成案原因（組織學習欄，建議填寫；可留空）', prop.outcome_reason || '');
                if (reason === null) { revert(); return; }
                const d = await tfetch(`${API}/${prop.id}/convert`, { method: 'POST', json: { outcome_reason: reason.trim() } });
                alert('已成案 ✅ 已自動建立 CRM 專案（project_id: ' + d.project_id + '）');
            } else if (val === '未成案') {
                const reason = prompt('未成案原因（必填 — 組織學習欄）', prop.outcome_reason || '');
                if (reason === null) { revert(); return; }
                if (!reason.trim()) { alert('未成案原因必填'); revert(); return; }
                await tfetch(`${API}/${prop.id}`, { method: 'PUT', json: { status: val, outcome_reason: reason.trim() } });
            } else {
                await tfetch(`${API}/${prop.id}`, { method: 'PUT', json: { status: val } });
            }
            refreshList();
            openDetail(prop.id);
        } catch (err) {
            alert('狀態更新失敗：' + (err.message || err));
            revert();
        }
    });

    ov.querySelector('#pd-edit').addEventListener('click', () => _openEditor(prop));

    ov.querySelector('#pd-del').addEventListener('click', async () => {
        if (!confirm(`確定刪除提案「${prop.title}」？參考片掛載會解除（片庫保留）。`)) return;
        try {
            await tfetch(`${API}/${prop.id}`, { method: 'DELETE' });
            _closeOverlay();
            refreshList();
        } catch (err) { alert('刪除失敗：' + (err.message || err)); }
    });

    // deck 上傳（前端先擋 50MB，後端同樣把關 413）
    const deckInput = ov.querySelector('#pd-deck-file');
    ov.querySelector('#pd-deck-upload').addEventListener('click', () => deckInput.click());
    deckInput.addEventListener('change', async () => {
        const f = deckInput.files[0];
        if (!f) return;
        if (f.size > DECK_MAX_BYTES) { alert('簡報檔超過 50MB 上限'); deckInput.value = ''; return; }
        const fd = new FormData();
        fd.append('file', f);
        try {
            await tfetch(`${API}/${prop.id}/deck`, { method: 'POST', body: fd });
            refreshList();
            openDetail(prop.id);
        } catch (err) { alert('上傳失敗：' + (err.message || err)); }
    });

    // 參考片：解除 / 從片庫掛上 / 快速入庫並掛上
    ov.querySelectorAll('.prop-ref-unlink').forEach(btn => {
        btn.addEventListener('click', async () => {
            const rid = btn.closest('.prop-ref').dataset.rid;
            try {
                await tfetch(`${API}/${prop.id}/refs/${rid}`, { method: 'DELETE' });
                refreshList();
                openDetail(prop.id);
            } catch (err) { alert('解除失敗：' + (err.message || err)); }
        });
    });
    ov.querySelector('#pr-link').addEventListener('click', async () => {
        const rid = ov.querySelector('#pr-pick').value;
        if (!rid) { alert('請先從片庫挑一支參考片'); return; }
        try {
            await tfetch(`${API}/${prop.id}/refs`, { method: 'POST', json: { reference_id: rid } });
            refreshList();
            openDetail(prop.id);
        } catch (err) { alert('掛載失敗：' + (err.message || err)); }
    });
    ov.querySelector('#pr-add').addEventListener('click', async () => {
        const url = ov.querySelector('#pr-new-url').value.trim();
        const title = ov.querySelector('#pr-new-title').value.trim();
        if (!url) { alert('參考片網址必填'); return; }
        try {
            const d = await tfetch(`${API}/references`, { method: 'POST', json: { url, title } });
            await tfetch(`${API}/${prop.id}/refs`, { method: 'POST', json: { reference_id: d.reference.id } });
            refreshList();
            openDetail(prop.id);
        } catch (err) { alert('新增失敗：' + (err.message || err)); }
    });
}

// ── 新增 / 編輯表單（prop=null 為新增；狀態不在表單內 —
//    成案/未成案要走詳情的狀態下拉，才吃得到 convert/原因守門） ──

async function _openEditor(prop) {
    const isNew = !prop;
    const clients = await _loadClients();
    const v = (k) => esc((prop && prop[k]) || '');
    const row = (label, html) => `<div class="prop-form-row"><label>${label}</label>${html}</div>`;
    const ov = _mountOverlay(`
        <div class="prop-panel" style="width:min(560px,96vw);">
            <div class="prop-panel-head">
                <h3>${isNew ? '＋ 新提案' : '✏️ 編輯：' + esc(prop.title)}</h3>
                <button class="prop-close" title="關閉">✕</button>
            </div>
            <div class="prop-panel-body" style="display:block;">
                ${row('標題 *', `<input id="pe-title" value="${v('title')}" placeholder="例：某公司 2026 品牌形象片提案">`)}
                <div style="display:flex;gap:8px;">
                    <div style="flex:1;">${row('客戶', `<select id="pe-client">
                        <option value="">（未選客戶）</option>
                        ${clients.map(c => `<option value="${esc(c.id)}"${prop && prop.client_id === c.id ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}
                    </select>`)}</div>
                    <div style="flex:1;">${row('類型', `<select id="pe-ptype">
                        <option value="">（未分類）</option>
                        ${PTYPES.map(t => `<option value="${t}"${prop && prop.ptype === t ? ' selected' : ''}>${t}</option>`).join('')}
                    </select>`)}</div>
                </div>
                <div style="display:flex;gap:8px;">
                    <div style="flex:1;">${row('提案日', `<input id="pe-pitch-date" type="date" value="${v('pitch_date')}">`)}</div>
                    <div style="flex:1;">${row('預算範圍', `<input id="pe-budget" value="${v('budget_range')}" placeholder="例：80-120 萬">`)}</div>
                </div>
                ${row('報價單 ID（可空，連 CRM 報價）', `<input id="pe-quotation" value="${v('quotation_id')}">`)}
                ${row('標籤（逗號分隔）', `<input id="pe-tags" value="${esc(((prop && prop.tags) || []).join(', '))}" placeholder="政府案, 高雄, 雙語">`)}
                ${row('成案/未成案原因（組織學習欄）', `<textarea id="pe-outcome" rows="3" placeholder="轉成案或未成案時必填">${v('outcome_reason')}</textarea>`)}
                <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
                    <button id="pe-cancel" class="prop-btn ghost">取消</button>
                    <button id="pe-save" class="prop-btn">${isNew ? '建立提案' : '儲存變更'}</button>
                </div>
            </div>
        </div>`);

    ov.querySelector('#pe-cancel').addEventListener('click', () => {
        if (isNew) _closeOverlay(); else openDetail(prop.id);
    });
    ov.querySelector('#pe-save').addEventListener('click', async () => {
        const title = ov.querySelector('#pe-title').value.trim();
        if (!title) { alert('標題必填'); return; }
        const body = {
            title,
            client_id: ov.querySelector('#pe-client').value,
            ptype: ov.querySelector('#pe-ptype').value,
            pitch_date: ov.querySelector('#pe-pitch-date').value || null,
            budget_range: ov.querySelector('#pe-budget').value.trim(),
            quotation_id: ov.querySelector('#pe-quotation').value.trim(),
            tags: ov.querySelector('#pe-tags').value.split(/[,，]/).map(t => t.trim()).filter(Boolean),
            outcome_reason: ov.querySelector('#pe-outcome').value.trim(),
        };
        try {
            const d = isNew
                ? await tfetch(API, { method: 'POST', json: body })
                : await tfetch(`${API}/${prop.id}`, { method: 'PUT', json: body });
            refreshList();
            openDetail(d.proposal.id);
        } catch (err) {
            alert((isNew ? '建立' : '儲存') + '失敗：' + (err.message || err));
        }
    });
}
