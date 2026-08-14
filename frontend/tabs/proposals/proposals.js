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

// esc 走 js/shared —— 同目錄的共用元件都用那支，而且它零依賴
import { copyText, esc, importRetry } from '../../js/shared/utils.js';
import { createSortable, sortableTh } from '../crm/crm-utils.js';
import { openDeck, tfetch } from './prop-fetch.js';
// 動作層與欄位定義跟獨立企劃頁共用同一份（見 prop-actions.js 檔頭）
import { API, DECK_EXTS, PTYPES, STATUSES, pickableRefs, projectLabel, withCurrent }
    from './prop-const.js';
// 清單的查詢/排序/統計與獨立企劃頁共用（版面各自畫，邏輯只有一份）
import {
    fetchProposals, fillFlowCells, fillYearSelect, hasFilters, loadFlowSummary,
    paintStats, SORT_COLUMNS, SORT_GETTERS, wireFilters,
} from './prop-list.js';
import {
    addRefByUrl, changeStatus, libraryRefs, linkRef, openProjectLinker,
    removeProposal, unlinkRef, uploadDeck,
} from './prop-actions.js';
import { openProposalEditor } from './prop-editor.js';

let _content = null;
let _lastProps = [];     // 目前篩選下的清單（供點欄頭排序重繪）
let _readFilters = null;
let _rowsFiltered = false;   // 目前這批是不是篩過的（決定空狀態要說什麼）
let _escHandler = null;

// ── 點欄頭排序：預設 key '' = 不排序、維持後端順序，點了才生效 ──
const _sorter = createSortable({
    storageKey: 'proposals_list_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'prop-table',
    onChange: () => _renderRows(),
    getters: SORT_GETTERS,      // 與獨立企劃頁同一份（含「狀態照流程序」那條）
});

// 表格總欄數＝可排序的資料欄 + 尾端那個刪除鈕欄（空狀態/錯誤列的 colspan 要用）
const COLSPAN = SORT_COLUMNS.length + 1;

// 這個分頁的 section id（app.js 的 switchTab 會派 tab-changed 帶它）
const SECTION_ID = 'tab_preprod_proposals';
let _tabHookBound = false;

export async function initProposalsTab() {
    _content = document.getElementById('prop-content');
    if (!_content) return;
    _content.style.cssText = '';   // 移除「載入中…」的置中/padding inline 樣式
    _renderShell();
    _bindTabHook();
    await refreshList({ stats: true });
}

/**
 * 🔴 切回這個分頁時重抓清單。
 *
 * initProposalsTab 只在 app 啟動時跑**一次**（tab-config 的 TAB_LOADERS），
 * 之後切分頁只是 show/hide —— 清單會停在開頁那一刻。而提案可以從**別的地方**
 * 長出來：專案詳情的「提案企劃」分頁一鍵建立、同事在另一台機器新增、
 * 提案庫的資料夾連結專案。那些都不會通知這裡。
 * （2026-08-08 實例：從專案側建了一筆，切回提案庫看不到，資料其實一直都在。）
 */
function _bindTabHook() {
    if (_tabHookBound) return;
    _tabHookBound = true;
    document.addEventListener('tab-changed', (e) => {
        if (!e.detail) return;
        if (e.detail.tab === SECTION_ID) { refreshList({ stats: true }); return; }
        // 🔴 切走就關詳情。overlay 是 position:fixed 掛在 body 上（不在 tab
        // section 裡），所以切了 tab 它會**留在畫面上蓋著別人的分頁**。
        // 收在這裡而不是各個離開路徑上：側欄點按、上一頁、貼網址、進度分頁的
        // 「去完成」都走 switchTab，一處就全包。
        _closeOverlay();
    });
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
                <button id="prop-folders" class="prop-btn ghost" title="NAS 上的提案資產資料夾（含過去手工整理的）">📁 資產資料夾</button>
                <button id="prop-templates" class="prop-btn ghost" title="生成企劃書時給 Claude 參考的範本">📚 企劃範本</button>
            </div>
            <div class="prop-table-wrap">
                <table class="prop-table" id="prop-table">
                    <thead><tr>
                        ${/* 欄位清單只有一份（prop-list.SORT_COLUMNS）—— 加一欄不必記得改三處 */
                          SORT_COLUMNS.map(c => sortableTh(c.key, c.label)).join('')}
                        <th></th>${/* 刪除鈕：不是資料欄，所以不進 SORT_COLUMNS（排不了） */''}
                    </tr></thead>
                    <tbody id="prop-rows"></tbody>
                </table>
            </div>
        </div>`;

    document.getElementById('prop-folders').addEventListener('click', async () => {
        try {
            const mod = await import('./proposal-folders.js');
            // 外殼由這裡給（元件自己只畫內容）—— 企劃頁給的是官網風的對話框
            mod.openFolderBrowser((inner) => _mountOverlay(`
                <div class="prop-panel" style="width:min(900px,96vw);">
                    <div class="prop-panel-head">
                        <h3>📁 提案資產資料夾</h3>
                        <button class="prop-close" title="關閉">✕</button>
                    </div>
                    <div class="prop-panel-body" style="display:block;">${inner}</div>
                </div>`));
        } catch (e) { alert('資產資料夾載入失敗：' + (e.message || e)); }
    });

    document.getElementById('prop-templates').addEventListener('click', async () => {
        try {
            const mod = await importRetry('/tabs/proposals/brief-templates.js');
            mod.openTemplateLibrary((inner) => _mountOverlay(`
                <div class="prop-panel" style="width:min(820px,96vw);">
                    <div class="prop-panel-head">
                        <h3>📚 企劃範本</h3>
                        <button class="prop-close" title="關閉">✕</button>
                    </div>
                    <div class="prop-panel-body" style="display:block;">${inner}</div>
                </div>`));
        } catch (e) { alert('範本庫載入失敗：' + (e.message || e)); }
    });

    // 篩選接線（debounce、要讀哪幾個 key）與獨立企劃頁共用
    _readFilters = wireFilters({
        q: document.getElementById('prop-q'),
        status: document.getElementById('prop-f-status'),
        ptype: document.getElementById('prop-f-ptype'),
        year: document.getElementById('prop-f-year'),
    }, () => refreshList());
    document.getElementById('prop-add').addEventListener('click', () => _openEditor(null));
}

/**
 * @param opts.stats 連統計 chips 一起更新。
 *
 * 判準不是「資料有沒有變」，是「**統計端點的輸入**有沒有變」—— 那支只讀
 * `count(*)` 與 funnel 列的 `(ptype, status, pitch_date)`（api_proposals.py
 * proposal_stats）。所以：新增/刪除提案、改狀態、改類型或提案日 → 要；
 * 掛/解參考片、上傳 deck → 不要（那些欄位它根本不看）。
 * 純篩選/搜尋當然也不要：/stats 不吃 filters，每打一個字重打一次就是拿一模
 * 一樣的數字做一次全表掃描。
 */
async function refreshList({ stats = false } = {}) {
    const tbody = document.getElementById('prop-rows');
    if (!tbody) return;
    const filters = _readFilters();
    try {
        // paintStats 自己吞錯、不回值 —— 起跑就好，不必進 Promise.all
        if (stats) paintStats(document.getElementById('prop-chip-total'),
                              document.getElementById('prop-chip-rate'));
        const props = await fetchProposals(filters);
        _rowsFiltered = hasFilters(filters);
        if (!_rowsFiltered) fillYearSelect(document.getElementById('prop-f-year'), props);
        _lastProps = props;
        _renderRows();
        // 進度摘要是**附加**資訊，所以不 await：併進上面那趟的話，一個慢
        // 查詢會讓整張表晚幾百毫秒才出現；摘要失敗也不該擋住清單。
        _paintFlow(props);
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="${COLSPAN}" style="color:#f87171;padding:30px;text-align:center;">提案載入失敗：${esc(e.message || e)}</td></tr>`;
    }
}

// tbody 重繪（初次載入與點欄頭排序共用）
function _renderRows() {
    const tbody = document.getElementById('prop-rows');
    if (!tbody) return;
    tbody.innerHTML = _lastProps.length ? _sorter.sorted(_lastProps).map(_row).join('')
        : `<tr><td colspan="${COLSPAN}" style="color:#666;padding:40px;text-align:center;">${
            _rowsFiltered ? '沒有符合條件的提案' : '尚無提案 — 按「＋ 新提案」建立第一筆'}</td></tr>`;
    tbody.querySelectorAll('tr[data-id]').forEach(el => {
        el.addEventListener('click', () => openDetail(el.dataset.id));
    });
    // 「專案」欄的格子本身就是換綁入口。自己吞掉點擊 —— 不然會順便開詳情。
    tbody.querySelectorAll('[data-proj]').forEach(el => {
        el.addEventListener('click', (e) => {
            e.stopPropagation();
            const p = _lastProps.find(x => x.id === el.dataset.proj);
            if (p) openProjectLinker(p, { onSaved: () => refreshList() });
        });
    });
    // 列尾的刪除：規則走共用的 removeProposal（含確認），這裡只接線。
    // 刪一筆會動到成案率 → stats 要一起更新。
    tbody.querySelectorAll('[data-del]').forEach(el => {
        el.addEventListener('click', async (e) => {
            e.stopPropagation();
            const p = _lastProps.find(x => x.id === el.dataset.del);
            if (!p) return;
            try {
                if (await removeProposal(p)) refreshList({ stats: true });
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        });
    });
    fillFlowCells(document.getElementById('prop-rows'));  // 列的樣板留空格
    _sorter.attach();
}

/**
 * 進度欄：抓摘要 → **重畫整個 tbody**。
 *
 * 🔴 只補那幾格是不夠的：「進度」是可排序欄，而它的值在這一刻才存在。
 * 排序早在摘要到之前就算完了（那時每一列都是 -1），只換格子的話順序會
 * 永遠停在後端順序、而表頭的箭頭還說著「已按進度排序」。而且 createSortable
 * 把排序選擇存進 localStorage，所以那個謊言會跟著使用者每次重開。
 * 重畫是安全的：_renderRows 自己會把三組事件委派重掛一次（點欄頭排序走的
 * 就是同一支）。
 */
async function _paintFlow(props) {
    if (await loadFlowSummary(props.map(p => p.project_id))) _renderRows();
}

/** 「專案」欄：已連結顯示專案名、未連結顯示「＋ 連結」，點下去都是換綁對話框。 */
function _projCell(p) {
    const tip = p.project_id
        ? `目前：${p.project_name || p.project_id}　（點一下換綁或解除）`
        : '把這個提案掛到某個既有專案';
    return `<button class="prop-linkproj${p.project_id ? '' : ' none'}" data-proj="${esc(p.id)}"
                    title="${esc(tip)}">${esc(projectLabel(p))}</button>`;
}

function _pill(status) {
    return `<span class="prop-pill s-${esc(status)}">${esc(status)}</span>`;
}

function _row(p) {
    return `
        <tr data-id="${esc(p.id)}">
            <td class="title">${esc(p.title)}</td>
            <td>${esc(p.client_name || '—')}</td>
            <td>${_projCell(p)}</td>
            <td>${esc(p.ptype || '—')}</td>
            <td>${_pill(p.status)}</td>
            <td>${esc(p.pitch_date || '—')}</td>
            <td>${esc(p.budget_range || '—')}</td>
            <td>🎞 ${p.refs_count || 0}</td>
            <td class="prop-flow" data-flow="${esc(p.project_id || '')}"></td>
            <td><button class="prop-rowdel" data-del="${esc(p.id)}"
                        title="刪除這個提案（參考片掛載會解除，片庫保留）">✕</button></td>
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

async function openDetail(pid) {
    let prop;
    try {
        prop = (await tfetch(`${API}/${pid}`)).proposal;
    } catch (e) {
        alert('提案載入失敗：' + (e.message || e));
        return;
    }
    const refLib = await libraryRefs();     // 拿不到就空陣列，不擋詳情

    const linked = prop.references || [];
    const pickable = pickableRefs(linked, refLib);

    const kv = (k, v) => v ? `<div class="prop-kv"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>` : '';
    const tagChips = (prop.tags || []).map(t => `<span class="prop-tag">${esc(t)}</span>`).join('');

    const refRows = linked.map(r => `
        <div class="prop-ref" data-rid="${esc(r.id)}">
            <div style="display:flex;gap:8px;align-items:center;">
                <a href="${esc(r.url)}" target="_blank" rel="noopener" style="flex:1;">${esc(r.title || r.url)}</a>
                <a href="/reference.html?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener"
                   style="font-size:11px;color:#93c5fd;white-space:nowrap;" title="研究頁：分類 + 研究四欄">研究頁 ↗</a>
                <button class="prop-btn danger prop-ref-unlink" style="padding:2px 8px;font-size:11px;">解除</button>
            </div>
            ${r.note ? `<div class="note">${esc(r.note)}</div>` : ''}
        </div>`).join('');

    const ov = _mountOverlay(`
        <div class="prop-panel">
            <div class="prop-panel-head">
                <h3>${esc(prop.title)}</h3>
                <select id="pd-status" title="狀態（成案會自動建 CRM 專案；未成案必填原因）">
                    ${/* 清單外的舊值也要留 —— 不補的話 select 會靜默把它換成第一個，
                          使用者只是點開看一眼，值就沒了（企劃頁那顆早就這樣做） */
                      withCurrent(STATUSES, prop.status).map(s =>
                        `<option value="${esc(s)}"${s === prop.status ? ' selected' : ''}>${esc(s)}</option>`).join('')}
                </select>
                <button id="pd-edit" class="prop-btn ghost">✏️ 編輯</button>
                <button id="pd-del" class="prop-btn danger">🗑 刪除</button>
                <button class="prop-close" title="關閉">✕</button>
            </div>
            <div class="prop-tabs">
                <button class="prop-tab active" data-tab="info">📋 基本資料</button>
                <button class="prop-tab" data-tab="plan">🗂 創意發想${prop.has_plan ? '' : '<span class="dot">·未開始</span>'}</button>
                <button class="prop-tab" data-tab="brief">📄 企劃書</button>
                <button class="prop-tab" data-tab="quote">報價單</button>
                <button class="prop-tab" data-tab="meeting">會議記錄</button>
                <button class="prop-tab" data-tab="flow">進度</button>
            </div>
            <div class="prop-panel-body" id="pd-tab-info">
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
                        <div id="pd-deck-body">${_deckBodyHtml(prop, [])}</div>
                        <div class="prop-note">上限 50MB，可執行檔會被擋下；更換會覆蓋 deck 連結。
                            「提案資料」裡勾選的檔要按一下才會變成簡報 — 簡報是給客戶的那一份。</div>
                    </div>
                    <div class="prop-card-sec">
                        <h4>🌐 公開頁面</h4>
                        <div id="pd-share-host"></div>
                    </div>
                    <div class="prop-card-sec">
                        <h4>📊 現況盤點</h4>
                        <div id="pd-survey-host"></div>
                    </div>
                    ${prop.outcome_reason ? `
                    <div class="prop-card-sec">
                        <h4>${prop.status === '未成案' ? '📉 未成案原因' : '📈 成案/結果原因'}（組織學習）</h4>
                        <div class="prop-outcome${prop.status === '未成案' ? ' lost' : ''}">${esc(prop.outcome_reason)}</div>
                    </div>` : ''}
                    ${prop.notes ? `
                    <div class="prop-card-sec">
                        <h4>📝 備註</h4>
                        <div style="white-space:pre-wrap;font-size:12.5px;line-height:1.8;">${esc(prop.notes)}</div>
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
            <div class="prop-panel-body" id="pd-tab-plan" style="display:none;"></div>
            <div class="prop-panel-body" id="pd-tab-brief" style="display:none;"></div>
            <div class="prop-panel-body" id="pd-tab-quote" style="display:none;"></div>
            <div class="prop-panel-body" id="pd-tab-meeting" style="display:none;"></div>
            <div class="prop-panel-body" id="pd-tab-flow" style="display:none;"></div>
        </div>`);

    // 分頁切換：企劃分頁 lazy import 元件（載入失敗不影響基本資料分頁）。
    // 重試必須帶 cache-bust query — ES module map 會永久快取 rejected import，
    // 原路徑重 import 只會拿回同一個 rejected promise（subview-loader.js 同款教訓）。
    // 容器 id 就是 `pd-tab-{data-tab}` —— 慣例寫在 markup 裡，不另外維護一張表
    // （企劃頁的側欄分頁用的是同一條慣例）。分頁內容 lazy 掛一次，之後只切顯示。
    let _planTries = 0;
    const _mounted = new Set(['info']);          // 基本資料在 markup 裡就畫好了
    const _mount = {
        plan: async (host) => {
            const mod = _planTries++ === 0 ? './plan-matrix.js' : `./plan-matrix.js?t=${Date.now()}`;
            const { renderPlan } = await import(mod);
            if (!host.isConnected) return;       // await 期間 overlay 已被關掉
            renderPlan(host, {
                proposalId: prop.id, plan: prop.plan || null, fetcher: tfetch, canShare: true,
                onPlanStarted: (p) => {
                    prop.plan = p;
                    ov.querySelector('[data-tab="plan"] .dot')?.remove();
                },
            });
        },
        brief: async (host) => {
            const { renderBriefs } = await importRetry('/tabs/proposals/brief-view.js');
            if (!host.isConnected) return;
            await renderBriefs(host, {
                proposalId: prop.id, plan: prop.plan || null,
                toast: (m) => alert(m),
            });
        },
        quote: async (host) => {
            const { renderQuotes } = await importRetry('/tabs/proposals/quote-view.js');
            if (!host.isConnected) return;
            await renderQuotes(host, { proposalId: prop.id });
        },
        meeting: async (host) => {
            const { renderMeetings } = await importRetry('/tabs/proposals/meeting-view.js');
            if (!host.isConnected) return;
            await renderMeetings(host, { proposalId: prop.id });
        },
        flow: async (host) => {
            const { renderFlow } = await importRetry('/tabs/proposals/flow-view.js');
            if (!host.isConnected) return;
            await renderFlow(host, {
                projectId: prop.project_id || '',
                // 推進會連動這筆提案本身（進「製作」＝衛星提案標成案 + 寫入
                // 成案原因），詳情標頭的狀態下拉與原因面板都會是舊值 → 重開
                onAdvanced: () => { refreshList({ stats: true }); openDetail(prop.id); },
                // 這個畫面**就是**提案工作區 —— 指回提案庫的燈不畫連結
                here: 'preprod_proposals',
            });
        },
    };
    ov.querySelectorAll('.prop-tab').forEach(btn => btn.addEventListener('click', async () => {
        const name = btn.dataset.tab;
        ov.querySelectorAll('.prop-tab').forEach(b => b.classList.toggle('active', b === btn));
        ov.querySelectorAll('.prop-tab').forEach(b => {
            ov.querySelector('#pd-tab-' + b.dataset.tab).style.display =
                b.dataset.tab === name ? '' : 'none';
        });
        if (_mounted.has(name) || !_mount[name]) return;
        _mounted.add(name);
        const host = ov.querySelector('#pd-tab-' + name);
        try {
            await _mount[name](host);
        } catch (e) {
            // 掛不起來就把旗標放回去 —— 再點一次分頁可以重試
            // （ES module map 會永久快取 rejected import，所以重試帶 cache-bust）
            _mounted.delete(name);
            if (host.isConnected) {
                host.innerHTML = `<div style="color:#888;padding:20px;">元件載入失敗：${esc(e.message || e)}（再點一次分頁重試）</div>`;
            }
        }
    }));

    _wireShareCard(ov, prop);
    _mountSurvey(ov, prop);

    // 狀態下拉：成案→建/推進專案；未成案→強制填原因；其餘直接 PUT（共用管線）
    ov.querySelector('#pd-status').addEventListener('change', async (e) => {
        try {
            const r = await changeStatus(prop, e.target.value);
            if (!r.ok) { e.target.value = prop.status; return; }   // 取消或沒過守門
            if (r.message) alert(r.message);
            refreshList({ stats: true });
            openDetail(prop.id);
        } catch (err) {
            alert('狀態更新失敗：' + (err.message || err));
            e.target.value = prop.status;
        }
    });

    ov.querySelector('#pd-edit').addEventListener('click', () => _openEditor(prop));

    ov.querySelector('#pd-del').addEventListener('click', async () => {
        try {
            if (await removeProposal(prop)) { _closeOverlay(); refreshList({ stats: true }); }
        } catch (err) { alert('刪除失敗：' + (err.message || err)); }
    });

    _wireDeckCard(ov, prop);
    // 沒有 deck 才去問「提案資料」勾了什麼 —— 那一趟要掃 NAS，不能擋詳情
    _loadDeckPicks(ov, prop);

    // 參考片：解除 / 從片庫掛上 / 快速入庫並掛上
    ov.querySelectorAll('.prop-ref-unlink').forEach(btn => {
        btn.addEventListener('click', async () => {
            try {
                await unlinkRef(prop.id, btn.closest('.prop-ref').dataset.rid);
                refreshList();          // 參考片不在 /stats 的輸入裡
                openDetail(prop.id);
            } catch (err) { alert('解除失敗：' + (err.message || err)); }
        });
    });
    ov.querySelector('#pr-link').addEventListener('click', async () => {
        const rid = ov.querySelector('#pr-pick').value;
        if (!rid) { alert('請先從片庫挑一支參考片'); return; }
        try {
            await linkRef(prop.id, rid);
            refreshList();              // 同上
            openDetail(prop.id);
        } catch (err) { alert('掛載失敗：' + (err.message || err)); }
    });
    ov.querySelector('#pr-add').addEventListener('click', async () => {
        const url = ov.querySelector('#pr-new-url').value.trim();
        const title = ov.querySelector('#pr-new-title').value.trim();
        if (!url) { alert('參考片網址必填'); return; }
        try {
            await addRefByUrl(prop.id, url, title);
            refreshList();              // 同上
            openDetail(prop.id);
        } catch (err) { alert('新增失敗：' + (err.message || err)); }
    });
}

// ── 提案簡報（deck）那一格 ────────────────────────────────
// 三種狀態，與 /proposal-plan.html 側欄同一套說法（那邊是 _deckCellHtml）：
//   有 deck                      → 檔名 + 下載 + 更換
//   沒 deck 但「提案資料」有勾選 → 已勾選的檔名 + 一顆「設為提案簡報」
//   都沒有                        → 尚未上傳 + 上傳
//
// 🔴 勾選**不會**自動變成簡報：勾選是內部策展（此刻以這份為準），簡報是要
// 給客戶的那一份 —— 中間那一步必須有人按下去。
//
// 版面兩邊各畫各的（這裡深色 SPA、那邊官網白底），共用的是**規則**。
function _deckBodyHtml(prop, picks) {
    const base = (p) => String(p || '').split(/[\\/]/).pop();
    const up = `<button id="pd-deck-upload" class="prop-btn ghost">⬆️ ${
        prop.deck_url ? '更換簡報' : '上傳簡報'}</button>
        <input id="pd-deck-file" type="file"${
            DECK_EXTS ? ` accept="${esc(DECK_EXTS)}"` : ''} style="display:none;">`;
    if (prop.deck_url) {
        return `<div style="margin-bottom:8px;">
            <a id="pd-deck-dl" href="#" style="color:#93c5fd;">⬇️ 下載簡報（${
                esc(String(prop.deck_url).split('.').pop())}）</a>
            <div style="color:#666;font-size:11px;margin-top:3px;">${esc(base(prop.deck_url))}</div>
        </div>${up}`;
    }
    if (picks.length) {
        // 一個就直接寫出來（下拉裡只有一個選項是在浪費一次點擊），
        // 多個才給下拉 —— 值一律從 #pd-deck-pick 讀，接線不必分兩種
        const ctrl = picks.length === 1
            ? `<b>${esc(base(picks[0].rel))}</b>
               <input type="hidden" id="pd-deck-pick" value="${esc(picks[0].rel)}">`
            : `<select id="pd-deck-pick" style="max-width:220px;">${picks.map(p =>
                `<option value="${esc(p.rel)}">${esc(base(p.rel))}</option>`).join('')}</select>`;
        return `<div style="color:#8b8b8b;font-size:12px;margin-bottom:8px;
                    display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                提案資料已勾選：${ctrl}
            </div>
            <button id="pd-deck-set" class="prop-btn" title="把這一份設成給客戶看的提案簡報"
                >設為提案簡報</button> ${up}`;
    }
    return `<div style="color:#666;font-size:12px;margin-bottom:8px;">尚未上傳</div>${up}`;
}

/** 這一格的三顆動作。整格重畫（補上勾選候選）之後要再叫一次 —— 元素換新了。 */
function _wireDeckCard(ov, prop) {
    // deck 下載（新落點在 NAS 資產夾 → 走帶權限端點，不是靜態連結）
    ov.querySelector('#pd-deck-dl')?.addEventListener('click', (e) => {
        e.preventDefault();
        openDeck(prop.id, prop.deck_url);
    });
    // deck 上傳（前端先擋 50MB，後端同樣把關 413）
    const deckInput = ov.querySelector('#pd-deck-file');
    ov.querySelector('#pd-deck-upload')?.addEventListener('click', () => deckInput.click());
    deckInput?.addEventListener('change', async () => {
        try {
            if (await uploadDeck(prop, deckInput.files[0])) { refreshList(); openDetail(prop.id); }
            else deckInput.value = '';
        } catch (err) { alert('上傳失敗：' + (err.message || err)); }
    });
    // 已勾選的某一份 → 升成簡報（同一支端點，企劃頁的檔案列★走的也是它）
    const setBtn = ov.querySelector('#pd-deck-set');
    setBtn?.addEventListener('click', async () => {
        const rel = ov.querySelector('#pd-deck-pick')?.value || '';
        if (!rel) return;
        setBtn.disabled = true;
        try {
            await tfetch(`${API}/${prop.id}/deck/from-asset`, { method: 'POST', json: { rel } });
            refreshList();
            openDetail(prop.id);        // 重開詳情 = 這一格換成「有 deck」那種
        } catch (err) {
            alert('設定失敗：' + (err.message || err));
            setBtn.disabled = false;
        }
    });
}

/** 沒有 deck 時才問「提案資料」勾了哪些檔。這一趟會掃 NAS（慢），所以
 *  **不擋**詳情面板：先畫「尚未上傳」，有候選再把那一格換掉。 */
async function _loadDeckPicks(ov, prop) {
    if (prop.deck_url || !prop.project_id) return;
    let d;
    try {
        // 帶 pid：候選要來自**這一筆**提案的勾選，不是專案裡最近更新的那筆
        d = await tfetch('/api/v1/crm/projects/'
            + encodeURIComponent(prop.project_id) + '/proposal-assets'
            + '?pid=' + encodeURIComponent(prop.id));
    } catch (_) { return; }            // 沒權限 / NAS 搆不到 → 維持「尚未上傳」
    // 資料夾不當候選：簡報是一個檔
    const picks = (d.pinned || []).filter(p => p && !p.is_dir);
    const body = ov.querySelector('#pd-deck-body');
    if (!picks.length || !body || !body.isConnected) return;   // await 期間 overlay 可能已關
    body.innerHTML = _deckBodyHtml(prop, picks);
    _wireDeckCard(ov, prop);
}

// ── 現況盤點表（基本資料分頁）─────────────────────────────
// 元件與客戶公開頁共用（tabs/proposals/survey-table.js）；這裡只接端點。
// 動態 import：載入失敗不能拖垮整個詳情面板。
async function _mountSurvey(ov, prop) {
    const host = ov.querySelector('#pd-survey-host');
    if (!host) return;
    const base = `${API}/${prop.id}/survey`;
    try {
        const { renderSurveyTable } = await importRetry('/tabs/proposals/survey-table.js');
        if (!host.isConnected) return;          // await 期間 overlay 已被關掉
        renderSurveyTable(host, {
            rows: prop.survey || [],
            save: (key, field, value) =>
                tfetch(base, { method: 'PATCH', json: { key, field, value } }),
            addRow: async (label) =>
                (await tfetch(`${base}/rows`, { method: 'POST', json: { label } })).survey,
            removeRow: async (key) =>
                (await tfetch(`${base}/rows/${encodeURIComponent(key)}`, { method: 'DELETE' })).survey,
            hint: '欄位改完會自動儲存。開放公開頁後，客戶在他那邊也能一起填（預算那列不會出現在客戶端）。',
        });
    } catch (e) {
        host.innerHTML = `<div style="color:#888;font-size:12px;">盤點表載入失敗：${esc(e.message || e)}</div>`;
    }
}

// ── 公開頁面卡（基本資料分頁）─────────────────────────────
// 開放＝鑄 token 連結；拿到的人**不用登入**能看企劃/基本資料/資料夾（資料夾
// 只看得到「對外分享」子夾）並共編企劃。不需要先開始企劃 —— 後端存 token 殼。
// token 存在 prop.plan.share_token（與企劃分頁的開關同一份狀態，改哪邊都同步）。
function _wireShareCard(ov, prop) {
    const host = ov.querySelector('#pd-share-host');
    if (!host) return;
    const shareUrl = () => `${location.origin}/proposal-plan.html?t=${encodeURIComponent((prop.plan || {}).share_token || '')}`;

    function paint() {
        const on = !!(prop.plan && prop.plan.share_token);
        host.innerHTML = on ? `
            <div class="prop-note" style="margin:0 0 8px;">已開放：拿到連結的人<b>不用登入</b>，可以看創意發想、基本資料與「對外分享」資料夾，並共同編輯。</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                <input id="ps-link" readonly style="flex:1;min-width:200px;font-size:12px;">
                <button id="ps-copy" class="prop-btn ghost">複製連結</button>
                <a href="#" id="ps-open" style="font-size:12px;color:#93c5fd;white-space:nowrap;">開啟 ↗</a>
                <button id="ps-off" class="prop-btn danger">停用</button>
            </div>` : `
            <div class="prop-note" style="margin:0 0 8px;">尚未開放。開放後會產生一條公開連結，可傳給客戶（不用登入）。</div>
            <button id="ps-on" class="prop-btn">🔗 開放公開頁面</button>`;
        if (on) {
            host.querySelector('#ps-link').value = shareUrl();       // DOM property，不進模板字串
            host.querySelector('#ps-open').href = shareUrl();
            host.querySelector('#ps-open').target = '_blank';
            host.querySelector('#ps-copy').addEventListener('click',
                (e) => copyText(shareUrl(), e.target));
            host.querySelector('#ps-off').addEventListener('click', async () => {
                if (!confirm('停用後，先前分享出去的連結會立即失效。確定停用？')) return;
                try {
                    await tfetch(`${API}/${prop.id}/plan/share`, { method: 'DELETE' });
                    if (prop.plan) delete prop.plan.share_token;
                    paint();
                } catch (e) { alert('停用失敗：' + (e.message || e)); }
            });
        } else {
            host.querySelector('#ps-on').addEventListener('click', async () => {
                try {
                    const d = await tfetch(`${API}/${prop.id}/plan/share`, { method: 'POST' });
                    prop.plan = prop.plan || {};
                    prop.plan.share_token = d.token;   // 企劃分頁的開關讀同一份
                    paint();
                } catch (e) { alert('開放失敗：' + (e.message || e)); }
            });
        }
    }
    paint();
}

// ── 新增 / 編輯表單 ──────────────────────────────────────
// 表單本體與獨立企劃頁共用（prop-editor.js）—— 「一筆提案有哪些欄位」只定義
// 一次。狀態刻意不在表單裡：它是有副作用的管線動作，走詳情的狀態下拉。
async function _openEditor(prop) {
    await openProposalEditor({
        proposal: prop,
        onSaved: (saved) => { refreshList({ stats: true }); openDetail(saved.id); },
    });
}
