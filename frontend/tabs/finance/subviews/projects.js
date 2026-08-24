/**
 * projects.js — 📁 逐案損益（帳本視角的專案表；2026-08-24）。
 *
 * owner：「我希望是這種形式的，我可以自己設定每個工項的費用」「像 crm 的專案
 * 管理表那種，打開右側有詳細的表」。所以版面＝CRM 的左列右詳情（沿用
 * crm-body / crm-list-panel / crm-detail-panel，my-ledger.html 已載 crm.css），
 * 右側詳情是**可編輯**的逐案損益表：營收、費用七欄、工項拆分十項。
 *
 * 內容＝原 Sheet「結案總表」的系統版。實收與檢查**不在前端算** ——
 * 算式正本在後端 api_finance_projects.compute()，兩份算式必然漂移。
 * 存檔後端回算好的值回來，這裡只顯示。
 */
import { finFetch, esc, fmtNum, finToast } from '../fin-utils.js';
import { setupResizeHandle } from '../../crm/crm-utils.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;
let _sel = null;        // 目前選取的專案 id
let _detail = null;     // 右側載入的單案資料
let _q = '';
let _unpaidOnly = false;
let _dirty = false;
let _resizeBound = false;

// 表頭與資料列共用一份欄寬 —— 分開寫的話改一邊就整排對不齊
const _GRID = 'display:grid;grid-template-columns:92px 1.5fr 1fr 92px 92px 74px;align-items:center;gap:8px;';

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _c.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">載入專案…</div>';
    await _load();
}

/** 整份拉一次就好 —— 402 列連同工項明細約 180KB，搜尋每敲一個字重抓一次
 *  是純白工（後端還要再跑兩個 group-by 聚合）。篩選改在前端做。 */
async function _load() {
    try {
        const d = await finFetch('/project-ledger');
        if (!_isCurrent()) return;
        _data = d;
        _renderShell();
    } catch (e) {
        _c.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">專案載入失敗：${esc(e.message)}</div>`;
    }
}

/** 目前畫面上的列（前端篩選：402 列已經在手上，不必回伺服器）。 */
function _visible() {
    const q = _q.toLowerCase();
    return (_data.projects || []).filter(p =>
        (!q || p.name.toLowerCase().includes(q) || (p.client || '').toLowerCase().includes(q))
        && (!_unpaidOnly || p.payment_status !== '全額到帳'));
}

/** 合計列。存檔後就地更新走同一份，不重畫整個殼。 */
function _renderTotals() {
    const el = document.getElementById('fpl-totals');
    if (!el) return;
    const t = _data.totals || {};
    const cell = (label, key, color) =>
        `<span>${label} <b style="color:${color};">$${fmtNum(t[key])}</b></span>`;
    el.innerHTML = cell('營收', 'contract', '#eee')
        + cell('委外', 'outsource', '#fca5a5')
        + cell('代辦費', 'invoice_fee', '#fca5a5')
        + cell('實收', 'net', '#eee')
        + cell('已收', 'received', '#86efac')
        + cell('應收', 'receivable', '#fbbf24')
        + cell('未付應付', 'ap_open', '#fca5a5');
}

function _renderCount(n) {
    const el = document.getElementById('fpl-count');
    if (!el) return;
    const t = _data.totals || {};
    el.innerHTML = `${fmtNum(n)}${n === _data.count ? '' : ' / ' + fmtNum(_data.count)} 案`
        + (t.unbalanced ? `｜<span style="color:#fbbf24;">${t.unbalanced} 案檢查≠0</span>` : '');
}

/** 只切 selected class —— 重建整份 innerHTML 會重新解析約 2,800 個節點，
 *  而且把清單的捲動位置歸零（點第 300 列就跳回頂端）。 */
function _markSelected() {
    const body = document.getElementById('fpl-list-body');
    if (!body) return;
    body.querySelector('.crm-row.selected')?.classList.remove('selected');
    body.querySelector(`.crm-row[data-id="${_sel}"]`)?.classList.add('selected');
}

function _renderShell() {
    _c.innerHTML = `
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:10px;">
            <input id="fpl-q" class="crm-input" placeholder="搜尋專案 / 客戶" style="width:200px;" value="${esc(_q)}">
            <label style="font-size:12px;color:#ccc;display:flex;align-items:center;gap:5px;">
                <input type="checkbox" id="fpl-unpaid" ${_unpaidOnly ? 'checked' : ''}> 只看未收清</label>
            <div style="flex:1;"></div>
            <span id="fpl-count" style="font-size:12px;color:#888;"></span>
        </div>
        <div id="fpl-totals" style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:#ccc;
                    margin-bottom:10px;background:#202020;border:1px solid #2e2e2e;
                    border-radius:8px;padding:10px 14px;"></div>
        <div class="crm-body" id="fpl-body" style="min-height:320px;">
            <div class="crm-list-panel" id="fpl-list-panel">
                <div class="crm-list-header" style="${_GRID}">
                    <span>結案日</span><span>專案</span><span>客戶</span>
                    <span style="text-align:right;">營收</span>
                    <span style="text-align:right;">實收</span>
                    <span style="text-align:right;">檢查</span>
                </div>
                <div id="fpl-list-body"></div>
            </div>
            <div class="crm-resize-handle" id="fpl-resize"></div>
            <div class="crm-detail-panel" id="fpl-detail" style="display:none;width:46%;"></div>
        </div>`;
    _renderTotals();
    _renderList();
    const qEl = document.getElementById('fpl-q');
    let timer;
    qEl.addEventListener('input', (e) => {
        _q = e.target.value.trim();
        clearTimeout(timer);
        timer = setTimeout(_renderList, 150);   // 前端篩選，不用回伺服器
    });
    document.getElementById('fpl-unpaid').addEventListener('change', (e) => {
        _unpaidOnly = e.target.checked;
        _renderList();
    });
    setupResizeHandle('fpl-resize', 'fpl-list-panel');
    _fitBody();
    if (!_resizeBound) {
        window.addEventListener('resize', _fitBody);
        _resizeBound = true;
    }
    if (_sel) _fp.open(_sel);
}

/** 把表格框在可視高度內 —— 這一步同時修掉兩件事（2026-08-25 實測）：
 *
 *  1. 表頭捲走：`#fpl-list-body` 吃得到 crm.css 的 `[id$="-list-body"]`
 *     （flex:1 + overflow-y:auto），但外層沒有高度限制時它會長到 16,884px、
 *     內捲永遠不發生，於是整頁一起捲、表頭跟著不見。
 *  2. 🔴 更嚴重的：詳情面板是清單的 flex 兄弟，容器 16,916px 高時，捲到第
 *     300 列點開，詳情是畫在**整個表格的頂端**（往上一萬多 px）＝看不到。
 *
 *  用量的不用寫死 px：捲動容器（my-ledger 是 #finance-content、主系統是頁面）
 *  的可視底部 − 表格頂端。視窗縮放時重算。
 */
function _fitBody() {
    const body = document.getElementById('fpl-body');
    if (!body) return;
    const scroller = document.getElementById('finance-content');
    const bottom = scroller && getComputedStyle(scroller).overflowY === 'auto'
        ? scroller.getBoundingClientRect().bottom
        : window.innerHeight;
    const avail = bottom - body.getBoundingClientRect().top - 12;
    body.style.height = Math.max(320, Math.round(avail)) + 'px';
}

function _renderList() {
    const body = document.getElementById('fpl-list-body');
    const keepScroll = body.scrollTop;
    const rows = _visible();          // 篩一次就好（_renderCount 原本又篩一次）
    body.innerHTML =
        rows.map((p) => `
        <div class="crm-row${p.id === _sel ? ' selected' : ''}" data-id="${p.id}"
             style="${_GRID}"
             onclick="window._finProjLedger.open('${p.id}')">
            <span style="color:${p.close_date ? '#9ca3af' : '#6b7280'};white-space:nowrap;">${esc(p.close_date || '未結案')}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e0e0e0;"
                  title="${esc(p.name)}">${esc(p.name)}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#9ca3af;"
                  title="${esc(p.client)}">${esc(p.client)}</span>
            <span style="text-align:right;">${fmtNum(p.contract)}</span>
            <span style="text-align:right;color:#eee;">${fmtNum(p.net)}</span>
            <span style="text-align:right;color:${p.check ? '#fbbf24' : '#4b5563'};">${p.check ? fmtNum(p.check) : '0'}</span>
        </div>`).join('')
        || '<div class="crm-empty">沒有符合的專案</div>';
    body.scrollTop = keepScroll;   // 重畫不該把使用者彈回列表頂端
    _renderCount(rows.length);
}

// ── 右側詳情（可編輯）──────────────────────────────────────
const _fp = (window._finProjLedger = window._finProjLedger || {});

_fp.open = async (id) => {
    if (_dirty && id !== _sel
        && !confirm('這一案有未儲存的修改，要放棄嗎？')) return;
    const same = _sel === id;
    _sel = id;
    _dirty = false;
    _markSelected();
    if (same && _detail) { _renderDetail(); return; }   // 同一案不用再拉一次
    const panel = document.getElementById('fpl-detail');
    panel.style.display = '';
    panel.innerHTML = '<div style="color:#888;padding:30px;text-align:center;">載入中…</div>';
    try {
        _detail = await finFetch(`/project-ledger/${id}`);
        _renderDetail();
    } catch (e) {
        panel.innerHTML = `<div style="color:#f87171;padding:30px;">載入失敗：${esc(e.message)}</div>`;
    }
};

function _renderDetail() {
    const d = _detail;
    const p = d.project;
    const det = p.detail || {};
    const money = (id, val, extra = '') => `
        <input type="number" class="crm-input fpl-num" id="${id}" value="${val || ''}"
               placeholder="0" style="width:100%;text-align:right;${extra}">`;
    const costRows = (d.cost_fields || []).map((f) => `
        <tr><td style="color:#bbb;">${esc(f.label)}</td>
            <td style="width:130px;">${money('fpl-c-' + f.key, det[f.key])}</td></tr>`).join('');
    const splitRows = (d.income_items || []).map((it) => `
        <tr><td style="color:#bbb;">${esc(it)}</td>
            <td style="width:130px;">${money('fpl-s-' + encodeURIComponent(it), (det.split || {})[it])}</td></tr>`).join('');
    // 使用者自己加過、但不在預設清單裡的工項也要出現（否則存檔會靜默丟掉）
    const extra = Object.keys(det.split || {}).filter((k) => !(d.income_items || []).includes(k));
    const extraRows = extra.map((it) => `
        <tr><td style="color:#c4b5fd;">${esc(it)} <span style="font-size:10px;color:#666;">(自訂)</span></td>
            <td>${money('fpl-s-' + encodeURIComponent(it), det.split[it])}</td></tr>`).join('');

    document.getElementById('fpl-detail').innerHTML = `
        <div class="crm-detail-bar">
            <div class="crm-detail-bar-title">${esc(p.client)} / ${esc(p.name)}</div>
            <div class="crm-detail-bar-actions">
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finProjLedger.save(this)">儲存</button>
                <button class="crm-detail-close" onclick="window._finProjLedger.close()" title="關閉">✕</button>
            </div>
        </div>
        <div class="crm-detail-content" style="padding:14px 16px;">
            <div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#888;margin-bottom:12px;">
                <span>${esc(p.close_date || '未結案')}</span><span>${esc(p.status)}</span>
                <span>${esc(p.type)}</span><span>${esc(p.payment_status)}</span>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div>
                    <div style="color:#ddd;font-size:12px;font-weight:600;margin-bottom:6px;">營收與費用</div>
                    <table class="crm-table" style="width:100%;font-size:12px;">
                        <tr><td style="color:#bbb;">結案日</td>
                            <td style="width:130px;"><input type="date" class="crm-input fpl-num" id="fpl-close"
                                value="${esc(p.close_date || '')}" style="width:100%;"></td></tr>
                        <tr><td style="color:#bbb;">營收(含稅)</td><td>${money('fpl-contract', p.contract)}</td></tr>
                        ${costRows}
                    </table>
                    <table class="crm-table" style="width:100%;font-size:12px;margin-top:8px;">
                        <tr><td style="color:#ddd;font-weight:600;">實收</td>
                            <td style="text-align:right;font-weight:600;color:#eee;" id="fpl-net">$${fmtNum(p.net)}</td></tr>
                        <tr><td style="color:#ddd;font-weight:600;">檢查（實收−Σ工項）</td>
                            <td style="text-align:right;font-weight:600;color:${p.check ? '#fbbf24' : '#86efac'};" id="fpl-check">${fmtNum(p.check)}</td></tr>
                        <tr><td style="color:#888;">已收 / 應收</td>
                            <td style="text-align:right;color:#888;">${fmtNum(p.received)} / ${fmtNum(p.receivable)}</td></tr>
                    </table>
                    <div style="color:#666;font-size:11px;margin-top:6px;">
                        實收 = 營收 − 委外 − 代辦費 − 個人稅款 − 雜支 − 股東往來（後端算）。
                        檢查 0 表示工項拆分剛好等於實收。</div>
                </div>
                <div>
                    <div style="color:#ddd;font-size:12px;font-weight:600;margin-bottom:6px;">工項拆分</div>
                    <table class="crm-table" style="width:100%;font-size:12px;">
                        ${splitRows}${extraRows}
                        <tr><td style="color:#ddd;font-weight:600;">合計</td>
                            <td style="text-align:right;font-weight:600;color:#eee;" id="fpl-splitsum">$${fmtNum(Object.values(det.split || {}).reduce((a, b) => a + b, 0))}</td></tr>
                    </table>
                    <div style="display:flex;gap:6px;margin-top:8px;">
                        <input class="crm-input" id="fpl-newitem" placeholder="自訂工項名稱" style="flex:1;">
                        <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finProjLedger.addItem()">＋</button>
                    </div>
                </div>
            </div>
            <div style="color:#ddd;font-size:12px;font-weight:600;margin:16px 0 6px;">掛在本案的收支（${(d.entries || []).length}）</div>
            <table class="crm-table" style="width:100%;font-size:12px;">
                <thead><tr><th>日期</th><th>摘要</th><th style="text-align:right;">存入</th><th style="text-align:right;">支出</th></tr></thead>
                <tbody>${(d.entries || []).map((e) => `
                    <tr><td style="white-space:nowrap;color:#888;">${esc(e.date)}</td>
                        <td>${esc(e.summary)}</td>
                        <td style="text-align:right;color:#86efac;">${e.deposit ? fmtNum(e.deposit) : ''}</td>
                        <td style="text-align:right;color:#fca5a5;">${e.expense ? fmtNum(e.expense) : ''}</td></tr>`).join('')
                    || '<tr><td colspan="4" style="color:#666;padding:10px;">（無）</td></tr>'}</tbody></table>
            <div style="color:#ddd;font-size:12px;font-weight:600;margin:16px 0 6px;">應付／請款單（${(d.payments || []).length}）</div>
            <table class="crm-table" style="width:100%;font-size:12px;">
                <tbody>${(d.payments || []).map((x) => `
                    <tr><td>${esc(x.summary)}<div style="color:#666;font-size:10px;">${esc(x.category)}${x.payee ? '｜' + esc(x.payee) : ''}</div></td>
                        <td style="text-align:right;">${fmtNum(x.amount)}</td>
                        <td style="white-space:nowrap;color:${x.payment_status === '已付款' ? '#86efac' : '#fbbf24'};">${esc(x.payment_status)}</td></tr>`).join('')
                    || '<tr><td colspan="3" style="color:#666;padding:10px;">（無）</td></tr>'}</tbody></table>
            <details style="margin-top:14px;">
                <summary style="color:#888;font-size:12px;cursor:pointer;">匯入保留的原始備註（案碼／案源／税別／工項）</summary>
                <pre style="white-space:pre-wrap;color:#aaa;font-size:11px;background:#1a1a1a;
                            border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin:6px 0 0;">${esc(p.notes || '（無）')}</pre>
            </details>
        </div>`;
    document.querySelectorAll('#fpl-detail .fpl-num').forEach((el) => {
        el.addEventListener('input', () => { _dirty = true; _liveSum(); });
    });
}

/** 工項合計即時更新（實收/檢查等存檔後由後端回算 —— 前端不算第二份）。 */
function _liveSum() {
    let sum = 0;
    document.querySelectorAll('#fpl-detail [id^="fpl-s-"]').forEach((el) => {
        sum += Number(el.value) || 0;
    });
    const el = document.getElementById('fpl-splitsum');
    if (el) el.textContent = '$' + fmtNum(sum);
}

_fp.addItem = () => {
    const name = (document.getElementById('fpl-newitem').value || '').trim();
    if (!name) return finToast('請輸入工項名稱');
    if (!_detail.project.detail.split) _detail.project.detail.split = {};
    if (_detail.project.detail.split[name] !== undefined) return finToast('這個工項已經有了');
    _detail.project.detail.split[name] = 0;
    _renderDetail();
    _dirty = true;
};

_fp.close = () => {
    if (_dirty && !confirm('有未儲存的修改，要放棄嗎？')) return;
    _sel = null;
    _dirty = false;
    document.getElementById('fpl-detail').style.display = 'none';
    _markSelected();
};

_fp.save = async (btn) => {
    const body = { split: {} };
    (_detail.cost_fields || []).forEach((f) => {
        const el = document.getElementById('fpl-c-' + f.key);
        if (el) body[f.key] = Number(el.value) || 0;
    });
    document.querySelectorAll('#fpl-detail [id^="fpl-s-"]').forEach((el) => {
        const name = decodeURIComponent(el.id.slice('fpl-s-'.length));
        body.split[name] = Number(el.value) || 0;
    });
    const cEl = document.getElementById('fpl-contract');
    if (cEl) body.contract_amount = Number(cEl.value) || 0;
    const dEl = document.getElementById('fpl-close');
    if (dEl) body.close_date = dEl.value || '';     // 空＝清成未結案
    btn.disabled = true;
    btn.textContent = '儲存中…';
    try {
        const r = await finFetch(`/project-ledger/${_sel}`, {
            method: 'PUT', body: JSON.stringify(body),
        });
        _dirty = false;
        finToast(r.check ? `已儲存 —— 檢查 ${fmtNum(r.check)}（工項與實收對不上）` : '已儲存');
        // PUT 已經回了這一列重算後的值 —— 就地更新那一列與合計，不重抓 402 列
        _applySaved(r);
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '儲存';
    }
};


/** 存檔後就地更新一列與合計（PUT 已回傳重算的 detail/net/check）。
 *
 *  原本是 `await _load()`：重跑 402 列查詢 + 兩個聚合、重建整個殼，然後
 *  `_renderShell` 尾端又把詳情重拉一次 —— 一次存檔三個往返。
 */
// 結案日新→舊、未結案（空）排最前、同日再比 updated_at 新→舊。
// 🔴 必須與後端 ORDER BY 逐字對應（completion_date DESC NULLS FIRST,
// updated_at DESC）—— 少了次要鍵，同一天結案的案子就會排在跟重新載入不同的
// 位置，使用者存個檔就看到清單「自己動了一下」（第 4 輪瀏覽器驗收實抓：
// 還原日期後那列停在同日群組的尾端，重整才回到原位）。
function _sortProjects() {
    _data.projects.sort((a, b) => {
        if (!a.close_date !== !b.close_date) return a.close_date ? 1 : -1;
        return (b.close_date || '').localeCompare(a.close_date || '')
            || (b.updated_at || '').localeCompare(a.updated_at || '');
    });
}


function _applySaved(r) {
    const i = (_data.projects || []).findIndex(p => p.id === _sel);
    if (i < 0) { _load(); return; }
    const old = _data.projects[i];
    const next = {
        ...old,
        detail: r.detail, net: r.net, check: r.check,
        contract: r.contract != null ? r.contract : old.contract,
        close_date: r.close_date != null ? r.close_date : old.close_date,
        updated_at: r.updated_at || old.updated_at,
    };
    const t = _data.totals;
    t.contract += next.contract - old.contract;
    t.net += next.net - old.net;
    t.outsource += next.detail.outsource - old.detail.outsource;
    t.invoice_fee += next.detail.invoice_fee - old.detail.invoice_fee;
    t.unbalanced += (next.check ? 1 : 0) - (old.check ? 1 : 0);
    _data.projects[i] = next;
    // 存檔會動到排序的兩個鍵（結案日、updated_at）—— 一律重排，讓畫面上的
    // 順序與「現在重新載入會看到的順序」永遠一致。402 列排一次不值得省。
    _sortProjects();
    if (_detail) _detail.project = { ..._detail.project, ...next };
    _renderTotals();
    _renderList();          // 樣板本身就會把 selected 標在對的那列
    _renderDetail();
}
