/**
 * crm-projects-quotes.js — 專案內報價管理 Tab
 * 報價列表 + 明細展示 + 啟動專案
 */
import { crmFetch as _fetch, esc as _esc, fmtNum } from './crm-utils.js';
import { state, callbacks } from './crm-projects-state.js';
import { _badge } from './crm-projects-core.js';

let _selectedQuoteId = null;

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
        const canActivate = proj && proj.status !== '進行中' && proj.status !== '已結案' && quotes.length > 0;
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
                <button class="crm-btn crm-btn-danger crm-btn-sm" onclick="window._pqDelete('${q.id}')">刪除</button>
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

function initQuoteHandlers() {
    window._pqSelect = (quoteId) => _renderQuoteDetail(quoteId);

    window._pqEdit = (quoteId) => {
        if (window._openQuoteModalForEdit) {
            window._openQuoteModalForEdit(quoteId);
        }
    };

    window._pqDuplicate = async (quoteId) => {
        if (window._openQuoteModalForDuplicate) {
            window._openQuoteModalForDuplicate(quoteId);
        }
    };

    window._pqDelete = async (quoteId) => {
        if (!confirm('確定刪除此報價？')) return;
        try {
            await _fetch('/quotations/' + quoteId, { method: 'DELETE' });
            if (state.selectedId) loadProjectQuotes(state.selectedId);
        } catch (e) { alert('刪除失敗：' + e.message); }
    };

    window._projAddQuote = () => {
        if (!state.selectedId) return;
        if (window._openQuoteModalForProject) {
            window._openQuoteModalForProject(state.selectedId);
        }
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
                  <p style="font-size:13px;color:#9ca3af;margin-bottom:12px;">選擇一版報價作為合約金額，專案狀態將切為「進行中」</p>
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
                body: JSON.stringify({ status: '進行中', contract_amount: contractAmount, amount_receivable: contractAmount })
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
