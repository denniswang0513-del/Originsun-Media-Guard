/**
 * crm-projects-quotes.js — 專案內報價管理 Tab
 * 報價列表 + 明細展示 + 啟動專案
 */
import { crmFetch as _fetch, esc as _esc, fmtNum, quotePdfFilename } from './crm-utils.js';
import * as _U from './crm-utils.js';   // permDeniedMsg 走命名空間（舊快取的 crm-utils 沒有它，named import 會炸整頁）
import { authDownload } from '../../js/shared/utils.js';
import { confirmQuoteDelete } from '../../js/shared/quote-delete.js';

// 刪除報價仍是管理員限定（RBAC 稽核第二批）—— 不是管理員就別畫那顆鈕
const _isAdmin = () => (window._accessLevel || 0) >= 3;
import { state, callbacks, PRESALE_STATUSES } from './crm-projects-state.js';
import { _badge } from './crm-projects-core.js';

let _selectedQuoteId = null;
let _detailQuote = null;   // 目前展開那版的完整報價（PDF 檔名要它的日期／客戶／專案名）

function _qBadge(status) {
    const s = status || '草稿';
    const known = ['草稿', '已寄送', '已簽核', '已拒絕'];
    const cls = known.includes(s) ? `crm-badge crm-quote-badge-${s}` : 'crm-badge';
    return `<span class="${cls}">${_esc(s)}</span>`;
}

// ── Load quotation list for current project ──────────────────

async function loadProjectQuotes(projectId) {
    const container = document.getElementById('proj-detail-quotes');
    if (!container) return;
    _selectedQuoteId = null;
    try {
        const data = await _fetch(`/projects/${projectId}/quotations`);
        const quotes = data.quotations || [];

        let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">';
        html += '<span style="font-size:12px;font-weight:700;color:#6b7280;">報價列表</span>';
        html += '<button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projAddQuote()">+ 新增報價</button>';
        html += '</div>';

        if (quotes.length === 0) {
            html += '<div class="crm-empty" style="padding:24px 0;">尚無報價</div>';
        } else {
            html += quotes.map(q => {
                const price = q.final_price != null ? q.final_price : q.total;
                return `<div class="pq-row${q.id === _selectedQuoteId ? ' selected' : ''}" onclick="window._pqSelect('${q.id}')">
                    <span class="pq-version">v${q.version}</span>
                    ${_qBadge(q.status)}
                    <span class="pq-price">$${fmtNum(price)}</span>
                    <span class="pq-date">${q.quote_date ? q.quote_date.substring(0, 10) : ''}</span>
                </div>`;
            }).join('');
        }

        // Activate button
        const proj = state.projects.find(p => p.id === projectId);
        const canActivate = proj && PRESALE_STATUSES.includes(proj.status) && quotes.length > 0;
        if (canActivate) {
            html += '<div style="padding:8px 0;"><button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projActivate()">啟動專案</button></div>';
        }

        // Detail container
        html += '<div id="pq-detail"></div>';

        container.innerHTML = html;
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Render quotation detail ──────────────────────────────────

async function _renderQuoteDetail(quoteId) {
    const el = document.getElementById('pq-detail');
    if (!el) return;
    _selectedQuoteId = quoteId;

    // Highlight selected row
    document.querySelectorAll('.pq-row').forEach(r => r.classList.remove('selected'));
    const row = document.querySelector(`.pq-row[onclick*="${quoteId}"]`);
    if (row) row.classList.add('selected');

    try {
        const q = await _fetch('/quotations/' + quoteId);
        _detailQuote = q;
        const items = q.items || [];

        // Group items
        const groups = {};
        items.forEach(it => {
            const g = it.group_name || '未分類';
            if (!groups[g]) groups[g] = [];
            groups[g].push(it);
        });

        let itemsHtml = '';
        for (const [group, groupItems] of Object.entries(groups)) {
            itemsHtml += `<div class="pq-group-header">${_esc(group)}</div>`;
            itemsHtml += groupItems.map(it => `
                <div class="pq-item-row">
                    <span class="pq-item-desc">${_esc(it.description)}</span>
                    <span class="pq-item-qty">${it.quantity} ${_esc(it.unit)}</span>
                    <span class="pq-item-price">$${fmtNum(it.unit_price)}</span>
                    <span class="pq-item-amount">$${fmtNum(it.amount)}</span>
                </div>
            `).join('');
        }

        // Cost & profit
        const costTotal = items.reduce((s, it) => s + (it.internal_cost || 0) * (it.quantity || 1), 0);
        const profitRate = q.subtotal > 0 ? Math.round((q.subtotal - costTotal) / q.subtotal * 100) : 0;
        const profitColor = profitRate >= 30 ? '#86efac' : profitRate >= 0 ? '#fbbf24' : '#fca5a5';

        // Payment stages
        const stages = q.payment_stages || [];
        const stagesHtml = stages.length > 0
            ? stages.map(s => `${_esc(s.label)} ${s.pct}%`).join(' → ')
            : '';

        el.innerHTML = `
          <div class="pq-detail-card">
            <div class="pq-detail-header">
              <span style="font-weight:700;color:#e0e0e0;">v${q.version} 報價明細</span>
              <div style="display:flex;gap:6px;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._pqEdit('${q.id}')">編輯</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._pqDuplicate('${q.id}')">複製新版</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._pqPdf('${q.id}')">PDF</button>
                ${_isAdmin() ? `<button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._pqDelete('${q.id}')">刪除</button>` : ''}
              </div>
            </div>

            <div class="pq-info-row">
              <span>狀態 ${_qBadge(q.status)}</span>
              <span>報價日 ${q.quote_date ? q.quote_date.substring(0, 10) : '—'}</span>
              <span>有效期 ${q.valid_until ? q.valid_until.substring(0, 10) : '—'}</span>
              <span>稅率 ${q.tax_rate || 5}%</span>
            </div>

            ${items.length > 0 ? `
              <div class="pq-items-table">
                ${itemsHtml}
              </div>
            ` : '<div class="crm-empty" style="padding:12px 0;">尚無項目明細</div>'}

            <div class="pq-totals">
              <div class="pq-total-row"><span>小計</span><span>$${fmtNum(q.subtotal)}</span></div>
              ${q.discount ? `<div class="pq-total-row"><span>折扣</span><span style="color:#fca5a5;">-$${fmtNum(q.discount)}</span></div>` : ''}
              <div class="pq-total-row"><span>稅額</span><span>$${fmtNum(q.tax_amount)}</span></div>
              <div class="pq-total-row pq-total-main"><span>含稅總計</span><span>$${fmtNum(q.total)}</span></div>
              ${q.final_price != null && q.final_price !== q.total ? `<div class="pq-total-row"><span>最終報價</span><span style="color:#60a5fa;font-weight:700;">$${fmtNum(q.final_price)}</span></div>` : ''}
              <div class="pq-total-row" style="border-top:1px solid #3a3a3a;margin-top:6px;padding-top:6px;">
                <span>內部成本</span><span style="color:#fbbf24;">$${fmtNum(costTotal)}</span>
              </div>
              <div class="pq-total-row"><span>毛利率</span><span style="color:${profitColor};">${profitRate}%</span></div>
            </div>

            ${stagesHtml ? `<div class="pq-stages"><span style="color:#6b7280;font-size:11px;">付款條件：</span>${stagesHtml}</div>` : ''}
            ${q.terms ? `<div class="pq-terms">${_esc(q.terms)}</div>` : ''}
          </div>
        `;
    } catch (e) {
        el.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// ── Window handlers ──────────────────────────────────────────

// 報價彈窗住在 crm-quotes.js：先把那頁載進來（embed：沒有 crm_quotes 分頁權限的人也載——報價 API 守的是
// money_view；同一支載入器記在 _loadedTabs，之後切到報價分頁不會再 init 一次），再 import 同一個模組實例
// 直接呼叫。以前經由 window 字串找函式，編輯／複製兩顆按鈕接錯名字就靜靜死了一年（2026-09-03 抓到）。
async function _withQuotes(use) {
    await window._ensureTabLoaded('tab_crm_quotes', { embed: true });
    return use(await import('./crm-quotes.js'));
}

function initQuoteHandlers() {
    window._pqSelect = (quoteId) => _renderQuoteDetail(quoteId);
    window._pqEdit = (id) => _withQuotes(m => m.quoteEdit(id));
    window._pqDuplicate = (id) => _withQuotes(m => m.quoteDup(id));

    window._pqPdf = async (id) => {
        let q;
        try {
            q = _detailQuote && _detailQuote.id === id ? _detailQuote : await _fetch('/quotations/' + id);
        } catch (e) { alert('下載 PDF 失敗：' + e.message); return; }
        const proj = state.projects.find(p => p.id === state.selectedId);
        const name = quotePdfFilename(q, q.project_name || proj?.name || '',
                                      q.client_short_name || proj?.client_short_name || '');
        await authDownload('/api/v1/crm/quotations/' + id + '/pdf', name, '下載 PDF');
    };

    window._pqDelete = async (quoteId) => {
        // 確認規則跟報價分頁／手機版同一份（js/shared/quote-delete.js）：已寄送／已簽核要
        // 打字確認案名 —— 那些一刪，客戶手上的 /q/{code} 當場變 404，而且我們不會知道。
        // 🔴 對不上就去抓 —— 用 `{ id }` 當 fallback 的話 status 是 undefined，
        //    deleteConfirmSpec 會當成草稿，已寄送的那道打字確認就被跳過了（同 :183 的作法）。
        let q = (_detailQuote && _detailQuote.id === quoteId) ? _detailQuote : null;
        if (!q) {
            try { q = await _fetch('/quotations/' + quoteId); }
            catch (e) { alert('讀不到這張報價：' + e.message); return; }
        }
        if (!confirmQuoteDelete(q)) return;
        try {
            await _fetch('/quotations/' + quoteId, { method: 'DELETE' });
            if (state.selectedId) loadProjectQuotes(state.selectedId);
        } catch (e) { alert(_U.permDeniedMsg?.('管理員', e) ?? ('刪除失敗：' + e.message)); }
    };

    window._projAddQuote = () => {
        if (state.selectedId) _withQuotes(m => m.openQuoteForProject(state.selectedId));
    };

    window._projRefreshQuotes = (projectId) => {
        if (state.selectedId === projectId) loadProjectQuotes(projectId);
    };

    window._projActivate = async () => {
        const projectId = state.selectedId;
        if (!projectId) return;
        try {
            const data = await _fetch(`/projects/${projectId}/quotations`);
            const quotes = data.quotations || [];
            if (quotes.length === 0) { alert('尚無報價單'); return; }

            let overlay = document.getElementById('proj-activate-overlay');
            if (overlay) overlay.remove();
            overlay = document.createElement('div');
            overlay.id = 'proj-activate-overlay';
            overlay.className = 'crm-modal-overlay';
            overlay.style.display = 'flex';
            overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
            overlay.innerHTML = `
              <div class="crm-modal" style="max-width:400px;">
                <div class="crm-modal-header">
                  <h3>啟動專案</h3>
                  <button onclick="document.getElementById('proj-activate-overlay').remove()" class="crm-detail-close">✕</button>
                </div>
                <div class="crm-modal-body">
                  <p style="font-size:13px;color:#9ca3af;margin-bottom:12px;">選擇一版報價作為合約金額，專案狀態將切為「製作」</p>
                  <div style="display:flex;flex-direction:column;gap:6px;">
                    ${quotes.map(q => {
                        const price = q.final_price != null ? q.final_price : q.total;
                        return `<button class="pi-activate-option" onclick="window._projDoActivate(${price})">
                          <span>v${q.version}</span>
                          ${_qBadge(q.status)}
                          <span style="font-weight:600;color:#e0e0e0;">$${fmtNum(price)}</span>
                        </button>`;
                    }).join('')}
                  </div>
                </div>
              </div>`;
            document.body.appendChild(overlay);
        } catch (e) { alert('載入報價失敗：' + e.message); }
    };

    window._projDoActivate = async (contractAmount) => {
        const projectId = state.selectedId;
        if (!projectId) return;
        try {
            await _fetch('/projects/' + projectId + '/status', {
                method: 'PATCH',
                body: JSON.stringify({ status: '製作', contract_amount: contractAmount, amount_receivable: contractAmount })
            });
            const overlay = document.getElementById('proj-activate-overlay');
            if (overlay) overlay.remove();
            const updated = await _fetch('/projects/' + projectId);
            callbacks.renderDetail?.(updated);
            loadProjectQuotes(projectId);
            await callbacks.loadProjects?.();
        } catch (e) { alert('啟動失敗：' + e.message); }
    };
}

export { loadProjectQuotes, initQuoteHandlers };
