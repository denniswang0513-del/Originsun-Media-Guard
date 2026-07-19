/**
 * works.js — 作品集管理子視圖
 *
 * 網站管理員（role=website_admin）的作品全流程：列表/搜尋/篩選、新增、編輯、
 * 公開/精選切換。編輯 UI 透過 iframe 嵌入 /showcase-edit.html?token=XXX —
 * 重用既有 showcase-edit.html 避免重寫 544 行的 CRM 完稿 Tab 編輯器。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, debounce, readRowPatch, emptyRow, emptyHint } from '../website-utils.js';
import { searchableSelect } from '../../crm/crm-utils.js';   // 打字搜尋下拉 widget（.ss-* 樣式已在 index.html 全域載入）
import { openShowcaseOverlay } from '../../crm/showcase-overlay.js';   // 編輯作品共用殼（與結案收件匣同一實作）

let _works = [];
let _categories = [];
let _seoAudit = null;  // Map<project_id, auditItem>；null = audit 端點不可用
let _series = [];      // 作品系列（跨專案策展集合）
let _serOpen = null;   // 展開成員管理的系列 id

// ── 欄位自訂：勾選要顯示的欄，localStorage 記憶（存「隱藏」清單 → 未來新欄預設顯示）──
// 標題/操作鎖定不可關；隱藏走 head 裡的 style 規則（re-render 不用重套）。
const _COL_STORE = 'works-cols-hidden';
const _COLS = [
    { key: 'thumb', label: '縮圖' },
    { key: 'client', label: '客戶' },
    { key: 'title', label: '標題', locked: true },
    { key: 'slug', label: 'slug' },
    { key: 'cat', label: '分類' },
    { key: 'year', label: '年份' },
    { key: 'public', label: '公開' },
    { key: 'featured', label: '精選' },
    { key: 'noindex', label: 'noindex' },
    { key: 'seo', label: 'AI SEO' },
    { key: 'comp', label: '完成度' },
    { key: 'actions', label: '操作', locked: true },
];
let _hiddenCols = (() => {
    try {
        const keys = new Set(_COLS.map(c => c.key));
        return new Set((JSON.parse(localStorage.getItem(_COL_STORE) || '[]')).filter(k => keys.has(k)));
    } catch { return new Set(); }
})();

function _applyColVis() {
    let st = document.getElementById('works-colvis-style');
    if (!st) {
        st = document.createElement('style');
        st.id = 'works-colvis-style';
        document.head.appendChild(st);
    }
    st.textContent = [..._hiddenCols]
        .map(k => `.works-crm #works-table [data-col="${k}"]{display:none;}`).join('\n');
}

function _renderColsMenu() {
    const menu = document.getElementById('works-cols-menu');
    if (!menu) return;
    menu.innerHTML = _COLS.filter(c => !c.locked).map(c => `
        <label style="display:flex;align-items:center;gap:8px;color:#ddd;font-size:12px;padding:4px 2px;cursor:pointer;white-space:nowrap;">
            <input type="checkbox" ${_hiddenCols.has(c.key) ? '' : 'checked'}
                onchange="window._websiteToggleCol('${c.key}', this.checked)" />${esc(c.label)}
        </label>`).join('')
        + `<div style="border-top:1px solid #3a3a3a;margin:6px 0;"></div>
           <button class="crm-btn crm-btn-secondary crm-btn-sm" style="width:100%;" onclick="window._websiteShowAllCols()">全部顯示</button>`;
}

window._websiteToggleCol = (key, show) => {
    if (show) _hiddenCols.delete(key); else _hiddenCols.add(key);
    localStorage.setItem(_COL_STORE, JSON.stringify([..._hiddenCols]));
    _applyColVis();
    _renderTable();   // 空結果列的 colspan 要跟著可見欄數變
};

window._websiteShowAllCols = () => {
    _hiddenCols.clear();
    localStorage.setItem(_COL_STORE, '[]');
    _applyColVis();
    // 就地勾回、不重繪 innerHTML —— 重繪會讓冒泡中的 e.target 變成脫離節點，
    // 外側點擊判定 menu.contains() 失敗而誤關選單
    document.querySelectorAll('#works-cols-menu input[type=checkbox]').forEach(cb => { cb.checked = true; });
    _renderTable();
};

async function _reloadWorks() {
    try {
        const [res] = await Promise.all([
            websiteFetch('/api/website/admin/works?include_non_public=true'),
            _reloadSeoAudit(),
        ]);
        _works = res?.items || [];
        _renderTable();
    } catch { /* silently skip — user can reload manually */ }
}

// AI SEO audit：重抓所有公開作品的 SEO 完整度（completeness 0-6），給狀態欄用。
async function _reloadSeoAudit() {
    try {
        const res = await websiteFetch('/api/website/admin/seo/projects/audit');
        _seoAudit = new Map((res?.items || []).map(it => [it.project_id, it]));
    } catch (e) {
        // 端點失敗（NAS website-api 跑舊版沒此 router）→ 保持 _seoAudit 原值，_seoCell 顯示「—」
        console.warn('[website/works] SEO audit 載入失敗:', e?.message || e);
    }
}

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>作品集管理</h2><div style="color:#888;padding:20px;">載入中…</div>';
    let portfolioPdfUrl = '';
    try {
        const [worksRes, catsRes, settingsRes, auditRes, seriesRes] = await Promise.all([
            websiteFetch('/api/website/admin/works?include_non_public=true'),
            websiteFetch('/api/website/admin/categories'),
            websiteFetch('/api/website/admin/settings'),
            websiteFetch('/api/website/admin/seo/projects/audit').catch(() => null),
            websiteFetch('/api/website/admin/series').catch(() => ({ items: [] })),
        ]);
        if (!isCurrent()) return;
        _works = worksRes?.items || [];
        _categories = catsRes?.items || [];
        portfolioPdfUrl = (settingsRes?.settings?.['portfolio.pdf_url'] || '').toString();
        _seoAudit = auditRes ? new Map((auditRes.items || []).map(it => [it.project_id, it])) : null;
        _series = seriesRes?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '作品集管理', e);
        return;
    }

    // CRM 化（works-crm scoped）：crm.css 全域已載入，直接用 crm-* class；
    // 表格保留 <table>（12 欄密表），僅 th/hover 對 CRM tokens。
    container.innerHTML = `
        <style>
            .works-crm table th{color:#9ca3af;font-size:12px;font-weight:600;}
            .works-crm table tbody tr:hover td{background:#252525;}
            .works-crm .crm-toolbar{border-radius:8px;margin-bottom:12px;}
            .works-crm .wk-card{background:#252525;border:1px solid #2e2e2e;border-radius:8px;padding:14px 16px;margin-bottom:12px;}
        </style>
        <div class="works-crm">
        <h2>作品集管理 <span style="color:#888;font-size:13px;font-weight:400;">· ${_works.length} 件作品</span></h2>

        <div class="wk-card" style="border-left:3px solid #3b82f6;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <label style="color:#ddd;font-size:13px;font-weight:600;white-space:nowrap;">歷年作品 PDF</label>
                <input id="portfolio-pdf-url" type="url" class="crm-input" value="${esc(portfolioPdfUrl)}"
                    placeholder="https://drive.google.com/... 或 https://originsun-studio.com/files/portfolio.pdf"
                    style="flex:1;min-width:320px;" />
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._websiteSavePortfolioPdf()">儲存</button>
                ${portfolioPdfUrl
                    ? `<a class="crm-btn crm-btn-secondary crm-btn-sm" href="${esc(portfolioPdfUrl)}" target="_blank" rel="noopener" style="text-decoration:none;">預覽</a>`
                    : ''}
            </div>
            <div style="color:#888;font-size:11px;margin-top:6px;">
                填值後 /portfolio 頁面頂部會顯示「下載作品集 PDF」按鈕；留空則隱藏。儲存後 60 秒內對外網站重 build。
            </div>
        </div>

        <details class="wk-card" style="padding:12px 16px;" ${_serOpen !== null ? 'open' : ''}>
            <summary style="cursor:pointer;color:#ddd;font-size:13px;font-weight:600;">作品系列（<span id="series-count">${_series.length}</span>）— 跨專案綁定，作品牆摺疊成一張卡 + 系列頁 /works/series/…</summary>
            <div id="series-panel" style="padding-top:10px;"></div>
        </details>

        <div class="crm-toolbar">
            <button class="crm-btn crm-btn-primary" onclick="window._websiteNewWork()">+ 新增作品</button>
            <input id="works-filter" type="text" class="crm-search-input" placeholder="搜尋標題 / 客戶 / slug…" style="flex:1;min-width:240px;max-width:320px;" />
            <select id="works-cat-filter" class="crm-select" style="min-width:140px;">
                <option value="">所有分類</option>
                ${_categories.map(c => `<option value="${c.id}">${esc(c.name_zh)}</option>`).join('')}
            </select>
            <label style="color:#ccc;font-size:12px;display:flex;align-items:center;gap:6px;">
                <input type="checkbox" id="works-public-only" /> 只顯示已公開
            </label>
            <div style="position:relative;margin-left:auto;">
                <button id="works-cols-btn" class="crm-btn crm-btn-secondary" title="自訂列表要顯示哪些欄位（會記住偏好）">欄位</button>
                <div id="works-cols-menu" style="display:none;position:absolute;right:0;top:calc(100% + 6px);z-index:60;background:#252525;border:1px solid #3a3a3a;border-radius:8px;padding:10px 12px;min-width:150px;box-shadow:0 8px 24px rgba(0,0,0,.5);"></div>
            </div>
        </div>

        <div class="wk-card" style="padding:0;">
            <table id="works-table"></table>
        </div>
        </div>
    `;

    document.getElementById('works-filter').addEventListener('input', debounce(_renderTable, 150));
    document.getElementById('works-cat-filter').addEventListener('change', _renderTable);
    document.getElementById('works-public-only').addEventListener('change', _renderTable);
    document.getElementById('works-cols-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        const menu = document.getElementById('works-cols-menu');
        menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    });
    // 點選單外側收合（點選單內不收，讓使用者連勾多項）；document 級只綁一次（子視圖會重進）
    if (!window._websiteColsDocBound) {
        window._websiteColsDocBound = true;
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('works-cols-menu');
            if (menu && menu.style.display !== 'none' && !menu.contains(e.target)) menu.style.display = 'none';
        });
    }
    _renderColsMenu();
    _applyColVis();
    _renderTable();
    _renderSeriesPanel();
}

// ── 作品系列（跨專案策展集合）────────────────────────────────
// CRUD 走 /api/website/admin/series；成員排序/加入/移除全走
// PUT /series/{id}/members 全量替換（work_ids 依序 = series_order）。

async function _reloadSeries() {
    const res = await websiteFetch('/api/website/admin/series').catch(() => null);
    if (res) _series = res.items || [];
    _renderSeriesPanel();
}

/** 成員變動/刪系列後的統一刷新：works（series_id 變了）+ series（計數/成員）一起抓 */
async function _refreshSeriesAndWorks() {
    const [worksRes, seriesRes] = await Promise.all([
        websiteFetch('/api/website/admin/works?include_non_public=true').catch(() => null),
        websiteFetch('/api/website/admin/series').catch(() => null),
    ]);
    if (worksRes) _works = worksRes.items || [];
    if (seriesRes) _series = seriesRes.items || [];
    _renderSeriesPanel();
    _renderTable();
}

function _seriesMembers(sid) {
    return _works.filter(w => w.series_id === sid)
        .sort((a, b) => (a.series_order || 0) - (b.series_order || 0));
}

function _renderSeriesPanel() {
    const el = document.getElementById('series-panel');
    if (!el) return;
    const countEl = document.getElementById('series-count');
    if (countEl) countEl.textContent = String(_series.length);
    const rows = _series.map(s => {
        const open = _serOpen === s.id;
        return `
        <tr style="border-top:1px solid #333;">
            <td style="padding:6px 8px;"><input data-id="${s.id}" data-field="title_zh" value="${esc(s.title_zh)}" style="width:180px;"></td>
            <td style="padding:6px 8px;"><input data-id="${s.id}" data-field="slug" value="${esc(s.slug)}" style="width:130px;font-family:monospace;" title="URL 永久承諾 — 改名會自動加 301 轉址，但別常改"></td>
            <td style="padding:6px 8px;"><input data-id="${s.id}" data-field="sort_order" type="number" value="${s.sort_order || 0}" style="width:60px;"></td>
            <td style="padding:6px 8px;text-align:center;"><input data-id="${s.id}" data-field="visible" type="checkbox" ${s.visible ? 'checked' : ''}></td>
            <td style="padding:6px 8px;color:#888;">${s.work_count || 0} 支</td>
            <td style="padding:6px 8px;white-space:nowrap;">
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._websiteSerSave(${s.id})">儲存</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._websiteSerMembers(${s.id})">${open ? '收合成員' : '成員'}</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._websiteSerDel(${s.id})" style="color:#f87171;">刪除</button>
            </td>
        </tr>
        ${open ? `<tr><td colspan="6" style="padding:4px 8px 12px;background:#1a1a1a;">${_serMembersHtml(s.id)}</td></tr>` : ''}`;
    }).join('');
    el.innerHTML = `
        <div style="color:#888;font-size:11px;margin-bottom:8px;">
            作品掛進系列：在該作品的編輯頁（作品系列下拉）選；這裡管系列本身 + 成員排序。
            介紹/封面等進階欄位：建立後點「儲存」旁欄位直接改（介紹欄在成員面板）。⚠ slug 發布後勿改。
        </div>
        <table style="border-collapse:collapse;font-size:12px;color:#ccc;width:100%;">
            <thead><tr style="color:#888;text-align:left;">
                <th style="padding:4px 8px;">系列名稱</th><th style="padding:4px 8px;">slug</th>
                <th style="padding:4px 8px;">排序</th><th style="padding:4px 8px;">顯示</th>
                <th style="padding:4px 8px;">成員</th><th style="padding:4px 8px;"></th>
            </tr></thead>
            <tbody>${rows || emptyRow(6, '還沒有系列 — 用下面的欄位建立第一個')}</tbody>
        </table>
        <div style="display:flex;gap:8px;align-items:flex-end;margin-top:10px;flex-wrap:wrap;">
            <div><div style="color:#888;font-size:11px;">新系列名稱</div><input id="ser-new-title" style="width:180px;"></div>
            <div><div style="color:#888;font-size:11px;">slug（小寫英數-）</div><input id="ser-new-slug" style="width:130px;font-family:monospace;" placeholder="huashan-annual"></div>
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._websiteSerCreate()">+ 建立系列</button>
        </div>`;

    // 展開中系列的「加入作品」下拉 → 套 searchableSelect（打字搜尋，與專案 AM 下拉同款）。
    // 每次重繪都是新的 <select>，searchableSelect 內部 idempotent、重複呼叫安全。
    if (_serOpen != null) {
        const addSel = document.getElementById(`ser-add-${_serOpen}`);
        if (addSel) searchableSelect(addSel, { placeholder: '搜尋作品…' });
    }
}

/** 候選作品（未掛任何系列）的 <option> 清單 — 打字搜尋/過濾交給 searchableSelect widget */
function _serCandidateOptions() {
    const candidates = _works.filter(w => !w.series_id);
    return `<option value="">— 選擇要加入的作品 —</option>` + candidates.map(w =>
        `<option value="${esc(w.id)}">${esc(w.title || w.name || w.slug)}</option>`).join('');
}

function _serMembersHtml(sid) {
    const s = _series.find(x => x.id === sid) || {};
    const members = _seriesMembers(sid);
    const mrows = members.map((w, i) => `
        <div style="display:flex;gap:8px;align-items:center;padding:3px 0;">
            <span style="color:#666;width:20px;">${i + 1}.</span>
            <span style="flex:1;">${esc(w.title || w.name || w.slug)}</span>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._websiteEditWork('${esc(w.id)}')"
                title="開啟作品編輯頁（與作品集的編輯相同）">編輯</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" ${i === 0 ? 'disabled' : ''} onclick="window._websiteSerMove(${sid},${i},-1)">↑</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" ${i === members.length - 1 ? 'disabled' : ''} onclick="window._websiteSerMove(${sid},${i},1)">↓</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="color:#f87171;" onclick="window._websiteSerRemove(${sid},'${esc(w.id)}')">移除</button>
        </div>`).join('');
    return `
        <div style="display:flex;gap:16px;flex-wrap:wrap;">
            <div style="flex:1;min-width:280px;">
                <div style="color:#888;font-size:11px;margin-bottom:4px;">成員（依系列內順序，系列頁照此排）</div>
                ${mrows || emptyHint('尚無成員 — 用下面下拉加入，或到作品編輯頁掛', { padding: 10 })}
                <div style="display:flex;gap:6px;margin-top:8px;align-items:center;flex-wrap:wrap;">
                    <select id="ser-add-${sid}" style="min-width:220px;">
                        ${_serCandidateOptions()}
                    </select>
                    <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._websiteSerAdd(${sid})">加入</button>
                </div>
            </div>
            <div style="flex:1;min-width:280px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:8px;">
                    <div style="color:#888;font-size:11px;">系列介紹（系列頁 + SEO description 來源，建議 30 字以上）</div>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" id="ser-ai-${sid}" onclick="window._websiteSerAiDesc(${sid})"
                        title="用 AI 從成員作品的標題/介紹整理出系列介紹 — 生成後可再編修，按「儲存介紹/封面」才寫入">AI 生成</button>
                </div>
                <textarea id="ser-desc-${sid}" rows="3" style="width:100%;box-sizing:border-box;">${esc(s.description_zh || '')}</textarea>
                <div style="color:#888;font-size:11px;margin:6px 0 4px;">封面圖 URL（空 = 用第一支作品封面）</div>
                <div style="display:flex;gap:6px;">
                    <input id="ser-cover-${sid}" value="${esc(s.cover_image || '')}" style="flex:1;box-sizing:border-box;">
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('ser-cover-file-${sid}').click()"
                        title="上傳圖片 — 自動轉 WebP 並縮到網頁尺寸（長邊 1600px），上傳後直接儲存">上傳</button>
                    <input type="file" id="ser-cover-file-${sid}" accept="image/*" style="display:none;"
                        onchange="window._websiteSerCoverUpload(${sid}, this)">
                </div>
                <button class="crm-btn crm-btn-primary crm-btn-sm" style="margin-top:6px;" onclick="window._websiteSerSaveExtra(${sid})">儲存介紹/封面</button>
            </div>
        </div>`;
}

async function _serPutMembers(sid, workIds) {
    try {
        await websiteFetch(`/api/website/admin/series/${sid}/members`, {
            method: 'PUT', body: { work_ids: workIds },
        });
        await _refreshSeriesAndWorks();
    } catch (e) { toastErr(e.detail || e.message); }
}

window._websiteSerCreate = async () => {
    const title = document.getElementById('ser-new-title')?.value?.trim();
    const slug = document.getElementById('ser-new-slug')?.value?.trim();
    if (!title || !slug) { toastErr('系列名稱與 slug 都要填'); return; }
    try {
        await websiteFetch('/api/website/admin/series', {
            method: 'POST', body: { title_zh: title, slug },
        });
        toastOk('系列已建立'); await _reloadSeries();
    } catch (e) { toastErr(e.detail || e.message); }
};

window._websiteSerSave = async (sid) => {
    const patch = readRowPatch('#series-panel', sid);
    try {
        await websiteFetch(`/api/website/admin/series/${sid}`, { method: 'PUT', body: patch });
        toastOk('已儲存'); await _reloadSeries();
    } catch (e) { toastErr(e.detail || e.message); }
};

window._websiteSerSaveExtra = async (sid) => {
    try {
        await websiteFetch(`/api/website/admin/series/${sid}`, {
            method: 'PUT',
            body: {
                description_zh: document.getElementById(`ser-desc-${sid}`)?.value || '',
                cover_image: document.getElementById(`ser-cover-${sid}`)?.value?.trim() || null,
            },
        });
        toastOk('已儲存'); await _reloadSeries();
    } catch (e) { toastErr(e.detail || e.message); }
};

window._websiteSerDel = async (sid) => {
    const s = _series.find(x => x.id === sid);
    if (!confirm(`刪除系列「${s?.title_zh || sid}」？成員作品會解除歸屬（作品本身不動）。`)) return;
    try {
        await websiteFetch(`/api/website/admin/series/${sid}`, { method: 'DELETE' });
        if (_serOpen === sid) _serOpen = null;
        toastOk('已刪除'); await _refreshSeriesAndWorks();
    } catch (e) { toastErr(e.detail || e.message); }
};

window._websiteSerMembers = (sid) => {
    _serOpen = _serOpen === sid ? null : sid;
    _renderSeriesPanel();
};

window._websiteSerMove = (sid, idx, dir) => {
    const ids = _seriesMembers(sid).map(w => w.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    _serPutMembers(sid, ids);
};

window._websiteSerRemove = (sid, workId) => {
    _serPutMembers(sid, _seriesMembers(sid).map(w => w.id).filter(id => id !== workId));
};

window._websiteSerAdd = (sid) => {
    const sel = document.getElementById(`ser-add-${sid}`);
    if (!sel || !sel.value) return;
    _serPutMembers(sid, [..._seriesMembers(sid).map(w => w.id), sel.value]);
};

// AI 生成系列介紹：後端拿成員作品的標題/介紹組 prompt 丟 claude（最長 3 分鐘）。
// 只填進 textarea 不落庫 — owner 看過（可編修）按「儲存介紹/封面」才寫入。
window._websiteSerAiDesc = async (sid) => {
    const ta = document.getElementById(`ser-desc-${sid}`);
    const btn = document.getElementById(`ser-ai-${sid}`);
    if (!ta) return;
    const orig = btn?.textContent;
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    try {
        const r = await websiteFetch(`/api/website/admin/series/${sid}/generate-description`, { method: 'POST' });
        ta.value = r.description || '';
        toastOk('已生成 — 可先編修，按「儲存介紹/封面」才會寫入');
    } catch (e) { toastErr(e.detail || e.message); }
    finally { if (btn) { btn.disabled = false; btn.textContent = orig; } }
};

// 封面上傳：轉 WebP + 長邊縮 1600px → 填 URL 欄並直接儲存（沿用 SaveExtra，
// 介紹欄現值一併存 — 兩欄本來就共用同一顆儲存鈕）。
window._websiteSerCoverUpload = async (sid, input) => {
    const f = input.files && input.files[0];
    input.value = '';   // 清掉才能重選同一張再觸發 onchange
    if (!f) return;
    try {
        const fd = new FormData();
        fd.append('file', f);
        const r = await websiteFetch('/api/website/admin/series/upload-cover', { method: 'POST', body: fd });
        const el = document.getElementById(`ser-cover-${sid}`);
        if (el) el.value = r.url;
        await window._websiteSerSaveExtra(sid);
    } catch (e) { toastErr('上傳失敗：' + (e.detail || e.message || e)); }
};


window._websiteSavePortfolioPdf = async () => {
    const url = (document.getElementById('portfolio-pdf-url')?.value || '').trim();
    if (url && !/^https?:\/\//i.test(url)) {
        toastErr('PDF 連結必須是 http:// 或 https:// 開頭');
        return;
    }
    try {
        await websiteFetch('/api/website/admin/settings', {
            method: 'PUT',
            body: { values: { 'portfolio.pdf_url': url } },
        });
        toastOk(url ? '已儲存 PDF 連結（60 秒後對外網站重 build）' : '已清除 PDF 連結');
    } catch (e) { toastErr(e.message || '儲存失敗'); }
};

function _renderTable() {
    const table = document.getElementById('works-table');
    if (!table) return;
    const q = (document.getElementById('works-filter')?.value || '').toLowerCase().trim();
    const catId = document.getElementById('works-cat-filter')?.value || '';
    const publicOnly = document.getElementById('works-public-only')?.checked;

    const rows = _works.filter(w => {
        if (publicOnly && !w.public) return false;
        if (q && !`${w.public_title || ''}${w.name || ''}${w.client || ''}${w.slug || ''}`.toLowerCase().includes(q)) return false;
        if (catId) {
            const catSlug = _categories.find(c => c.id === Number(catId))?.slug;
            if (!catSlug || !w.categories?.includes(catSlug)) return false;
        }
        return true;
    });

    if (!rows.length) {
        table.innerHTML = `<tr><td colspan="${_COLS.length - _hiddenCols.size}" style="color:#888;text-align:center;padding:30px;">沒有符合條件的作品</td></tr>`;
        return;
    }

    // 唯讀狀態徽章 — 比 disabled checkbox 更顯眼，使用者能一眼看出 on/off
    // 修改入口在右側「編輯」按鈕，不靠 inline toggle。on = 實心藍方塊、off = 空心方塊
    const _roBadge = (on) => on
        ? '<span style="display:inline-flex;width:22px;height:22px;background:#3b82f6;border-radius:4px;cursor:default;"></span>'
        : '<span style="display:inline-flex;width:22px;height:22px;background:transparent;border:1px solid #4b5563;border-radius:4px;cursor:default;"></span>';

    // 1 專案 : N 作品 — w.id !== w.project_id 代表同專案下的子作品
    const _subBadge = (w) => (w.project_id && w.id !== w.project_id)
        ? '<span title="同專案下的子作品" style="display:inline-block;margin-left:5px;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:#1e3a5f;color:#93c5fd;vertical-align:middle;white-space:nowrap;">子作品</span>'
        : '';

    table.innerHTML = `
        <thead>
            <tr>
                <th data-col="thumb">縮圖</th>
                <th data-col="client">客戶</th>
                <th data-col="title">標題</th>
                <th data-col="slug">slug</th>
                <th data-col="cat">分類</th>
                <th data-col="year">年份</th>
                <th data-col="public">公開</th>
                <th data-col="featured">精選</th>
                <th data-col="noindex" title="個別作品強制 noindex（站級允許索引仍會被擋）">noindex</th>
                <th data-col="seo" title="AI 自動生成的作品 SEO 內容（標題／描述／關鍵字／長文／FAQ）">AI SEO</th>
                <th data-col="comp" title="作品內容填寫狀況：影片／圖／說明（專案描述）／credits">完成度</th>
                <th data-col="actions">操作</th>
            </tr>
        </thead>
        <tbody>
            ${rows.map(w => {
                // 縮圖優先序：真自訂封面（後端 has_custom_cover 旗標；cover_url 本身是
                // fallback 鏈「自訂封面→YT 縮圖」，不能直接當自訂用 — 否則整表 YT 作品
                // 都改抓 maxres 大圖）→ YouTube default 小圖 → 灰色佔位
                //（FB/Vimeo 等非 YouTube 影片作品沒封面時不出破圖）
                const _thumbSrc = (w.has_custom_cover ? w.cover_url : null)
                    || (w.youtube_id ? `https://img.youtube.com/vi/${w.youtube_id}/default.jpg` : null);
                const thumb = _thumbSrc
                    ? `<img src="${esc(_thumbSrc)}" style="width:80px;height:45px;object-fit:cover;border-radius:3px;background:#000;" onerror="this.style.visibility='hidden'" />`
                    : '<div style="width:80px;height:45px;background:#333;border-radius:3px;"></div>';
                return `
                <tr data-id="${esc(w.id)}">
                    <td data-col="thumb">${thumb}</td>
                    <td data-col="client" style="color:#fff;">${esc(w.client || '-')}</td>
                    <td data-col="title" style="color:#fff;">${esc(w.title || w.name)}${_subBadge(w)}</td>
                    <td data-col="slug" style="color:#888;font-size:12px;">
                        ${esc(w.slug || '(未設)')}
                        ${w.redirect_count > 0
                            ? `<span title="此作品有 ${w.redirect_count} 條舊 slug 被 301 轉址到目前 slug" style="color:#3b82f6;margin-left:6px;">轉址 ${w.redirect_count}</span>`
                            : ''}
                    </td>
                    <td data-col="cat">${(w.categories || []).map(s => `<span class="crm-badge">${esc(s)}</span>`).join(' ') || '<span style="color:#666;">-</span>'}</td>
                    <td data-col="year">${w.year ?? '-'}</td>
                    <td data-col="public" title="${w.public ? '已公開' : '未公開'}（僅顯示，請進編輯頁修改）">${_roBadge(w.public)}</td>
                    <td data-col="featured" title="${w.featured ? '已設精選' : '未設精選'}（僅顯示，請進編輯頁修改）">${_roBadge(w.featured)}</td>
                    <td data-col="noindex" title="${w.noindex ? '已設 noindex' : '允許索引'}（僅顯示，請進編輯頁修改）">${_roBadge(w.noindex)}</td>
                    <td data-col="seo">${_seoCell(w)}</td>
                    <td data-col="comp">${_compCell(w.completeness)}</td>
                    <td data-col="actions" style="white-space:nowrap;">
                        <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._websiteEditWork('${esc(w.id)}')">編輯</button>
                        <button class="crm-btn crm-btn-secondary crm-btn-sm" title="在同一專案下新增子作品（開啟編輯器，關閉時未填內容會自動清掉空殼）" onclick="window._websiteAddSubWork('${esc(w.project_id || w.id)}')">+子作品</button>
                    </td>
                </tr>
                `;
            }).join('')}
        </tbody>
    `;
}



// 完成度欄：影片／圖／說明（專案描述）／credits 填寫狀況（與結案看板同邏輯）
function _compChip(label, ok) {
    const on = !!ok;
    return `<span title="${esc(label)}${on ? '：已完成' : '：未完成'}" style="display:inline-flex;align-items:center;gap:1px;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:600;white-space:nowrap;background:${on ? '#14532d' : '#2a2a2a'};color:${on ? '#86efac' : '#6b7280'};">${on ? '✓' : '✗'}${esc(label)}</span>`;
}
function _compCell(c) {
    if (!c) return '<span style="color:#666;">-</span>';
    return `<div style="display:flex;gap:3px;flex-wrap:wrap;">${_compChip('影片', c.video)}${_compChip('圖', c.images)}${_compChip('說明', c.description)}${_compChip('credits', c.credits)}</div>`;
}

// ══════════════════════════════════════════════════════════
// AI SEO 狀態欄
// ══════════════════════════════════════════════════════════
// 後端 AI SEO（services/website/seo_runner.py）用 `claude --print` 自動補
// 作品 SEO 內容（標題／描述／關鍵字／長文／key_facts／FAQ）。audit 端點
// 回每件公開作品的 completeness（0-6）：
//   completeness 0  → 待生成（給「生成」按鈕）
//   completeness >0 → 已生成（顯示 n/6）
// 非公開作品不適用（後端 get_project_seo_draft_context 會擋未公開作品）。

function _seoCell(w) {
    if (_seoAudit === null) {
        // audit 端點不可用（NAS website-api 跑舊版沒 admin_seo router）
        return '<span style="color:#666;font-size:11px;" title="AI SEO audit 端點不可用，請在 master 跑 /publish 同步後端到 NAS">—</span>';
    }
    if (!w.public) {
        return '<span style="color:#666;font-size:11px;" title="作品未公開，AI SEO 不適用（需先公開作品才能生成）">未公開</span>';
    }
    const score = _seoAudit.get(w.id)?.completeness || 0;
    if (score > 0) {
        return `<span style="color:#4ade80;font-size:12px;white-space:nowrap;" title="AI SEO 已生成（內容完整度 ${score}/6）">已生成 <span style="color:#888;">${score}/6</span></span>`;
    }
    return `
        <div style="display:flex;align-items:center;gap:6px;white-space:nowrap;">
            <span style="color:#f59e0b;font-size:12px;">待生成</span>
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                title="用 AI 自動生成此作品的 SEO 內容（約 30-60 秒）"
                onclick="window._websiteGenSeo('${esc(w.id)}', this)">生成</button>
        </div>`;
}

// 單筆 AI SEO 生成 — 呼叫既有 /seo/projects/{id}/run（claude --print，約 30-60 秒/筆）。
// 完成後重抓 audit 並重畫表格，狀態欄即時翻成「已生成」。
window._websiteGenSeo = async (pid, btn) => {
    if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
    try {
        const r = await websiteFetch(`/api/website/admin/seo/projects/${pid}/run`, { method: 'POST' });
        if (r?.status === 'busy') {
            toastErr('已有 AI SEO 任務在執行，請稍後再試');
        } else if ((r?.errors || 0) > 0) {
            const detail = (r.works || [])[0]?.detail || r.error || '未知錯誤';
            toastErr('生成失敗：' + detail);
        } else if ((r?.processed || 0) > 0) {
            toastOk('AI SEO 已生成（60 秒後對外網站重 build）');
        } else {
            toastErr('生成未完成 — 後端未回報任何處理結果');
        }
    } catch (e) {
        toastErr('生成失敗：' + (e.message || e));
    } finally {
        await _reloadSeoAudit();
        _renderTable();   // 重畫 → 該列狀態翻新、loading 按鈕一併被取代
    }
};


// ══════════════════════════════════════════════════════════
// Edit panel — 共用殼 showcase-overlay.js（與結案收件匣同一實作）
// ══════════════════════════════════════════════════════════
// skeleton 語意保留：跳過小表單流程建出來的 skeleton project_id 走 onClose
// 閉包 → 關閉時呼叫 if-skeleton 讓後端決定刪不刪（sanity check name +
// 各 public_* 是否仍空）。編輯既有作品不帶 skeletonProjectId，不會誤刪。
function _openEditPanel(url, title, { skeletonProjectId = null } = {}) {
    openShowcaseOverlay(url, title || '編輯作品', {
        onSaved: _reloadWorks,
        onClose: async () => {
            if (skeletonProjectId) {
                try {
                    await websiteFetch(`/api/website/admin/works/${skeletonProjectId}/if-skeleton`, { method: 'DELETE' });
                } catch (e) {
                    // 失敗就留著（DB 中保留 skeleton，下次列表會看到，使用者可手動處理）
                    console.warn('[website/works] if-skeleton 清理失敗:', e.message || e);
                }
            }
            _reloadWorks();
        },
    });
}

window._websiteEditWork = async (pid) => {
    try {
        const r = await websiteFetch(`/api/website/admin/works/${pid}/edit-url`, { method: 'POST' });
        const w = _works.find(x => x.id === pid);
        _openEditPanel(r.edit_url, `編輯：${w?.public_title || w?.name || pid}`);
    } catch (e) {
        toastErr(e.message);
    }
};


// ══════════════════════════════════════════════════════════
// 新增作品：直接建 skeleton 後跳編輯 overlay（跳過小表單）
// ══════════════════════════════════════════════════════════
// 客戶/年份/分類都改在編輯頁裡填。Overlay 關閉時 onClose 閉包會呼叫
// /works/{id}/if-skeleton；後端確認 name 仍是 sentinel 且各 public_*
// 都沒填內容才刪掉，避免使用者開了又馬上關留下空殼紀錄。

window._websiteNewWork = async () => {
    try {
        // POST 不帶 body — 後端塞 sentinel name「（未命名作品）」、status 預設「已結案」
        const r = await websiteFetch('/api/website/admin/works/create', {
            method: 'POST', body: {},
        });
        toastOk('已建立空作品 — 在編輯頁填寫資訊後按儲存');
        _openEditPanel(r.edit_url, `編輯：${r.name}`, { skeletonProjectId: r.id });
    } catch (e) {
        toastErr(e.message || '建立失敗');
    }
};

// ══════════════════════════════════════════════════════════
// 新增子作品：同一 CRM 專案下再掛一個官網作品（1 專案 : N 作品）
// ══════════════════════════════════════════════════════════
// title 留空讓後端補預設；關閉 overlay 時 if-skeleton 已支援子作品 id，
// 未填內容的空殼會被清掉（不動專案本身）。

window._websiteAddSubWork = async (projectId) => {
    try {
        const r = await websiteFetch(`/api/website/admin/projects/${projectId}/works`, {
            method: 'POST', body: {},
        });
        toastOk('已建立子作品 — 在編輯頁填寫內容後按儲存');
        _openEditPanel(r.edit_url, `編輯：${r.title || '新子作品'}`, { skeletonProjectId: r.id });
    } catch (e) {
        toastErr(e.message || '建立子作品失敗');
    }
};
