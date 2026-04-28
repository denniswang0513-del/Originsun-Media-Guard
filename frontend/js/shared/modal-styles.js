// ─── Modal Shared Styles & Form Builder (extracted from app.js) ─── //
import { searchableSelect } from '../../tabs/crm/crm-utils.js';

export function _ensureModalStyles() {
    if (document.getElementById('_formModalStyles')) return;
    const style = document.createElement('style');
    style.id = '_formModalStyles';
    style.textContent = `
        @keyframes _fmFadeIn { from { opacity:0 } to { opacity:1 } }
        @keyframes _fmSlideUp { from { opacity:0; transform:translateY(12px) scale(0.98) } to { opacity:1; transform:translateY(0) scale(1) } }
        ._fm-overlay { position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:10000;display:flex;align-items:center;justify-content:center;animation:_fmFadeIn .18s ease-out }
        ._fm-modal { background:#252525;border:1px solid #3a3a3a;border-radius:10px;box-shadow:0 20px 50px rgba(0,0,0,0.55);width:460px;max-height:85vh;overflow-y:auto;animation:_fmSlideUp .22s ease-out;color:#e5e7eb }
        ._fm-header { padding:18px 24px;border-bottom:1px solid #333;display:flex;justify-content:space-between;align-items:center }
        ._fm-header h3 { margin:0;font-size:15px;font-weight:600;color:#f0f0f0;letter-spacing:0.3px }
        ._fm-close { cursor:pointer;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:6px;color:#666;font-size:18px;transition:all .15s }
        ._fm-close:hover { background:#333;color:#ccc }
        ._fm-body { padding:20px 24px }
        ._fm-section { margin-bottom:4px }
        ._fm-section-label { font-size:11px;font-weight:600;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px }
        ._fm-divider { height:1px;background:#333;margin:16px 0 }
        ._fm-field { margin-bottom:14px }
        ._fm-field:last-child { margin-bottom:0 }
        ._fm-label { display:block;font-size:12px;font-weight:500;color:#999;margin-bottom:6px }
        ._fm-label .req { color:#ef4444;margin-left:2px }
        ._fm-input, ._fm-select { width:100%;box-sizing:border-box;background:#1a1a1a;border:1px solid #3a3a3a;color:#fff;padding:9px 12px;border-radius:6px;font-size:13px;transition:border-color .15s,box-shadow .15s;outline:none }
        ._fm-input:focus, ._fm-select:focus { border-color:#7c3aed;box-shadow:0 0 0 2px rgba(124,58,237,0.15) }
        ._fm-input::placeholder { color:#555 }
        ._fm-checkgrid { display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:4px 0 }
        ._fm-chk { display:flex;align-items:center;gap:6px;font-size:12px;color:#bbb;cursor:pointer;padding:5px 8px;border-radius:5px;transition:background .12s;user-select:none }
        ._fm-chk:hover { background:#2a2a2a }
        ._fm-chk input { accent-color:#7c3aed;width:14px;height:14px;cursor:pointer }
        ._fm-hint { font-size:11px;color:#666;margin-top:4px }
        ._fm-error { display:none;color:#f87171;font-size:12px;margin-top:12px;padding:8px 12px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:6px }
        ._fm-footer { padding:14px 24px;border-top:1px solid #333;display:flex;justify-content:flex-end;gap:10px }
        ._fm-btn-cancel { background:transparent;border:1px solid #444;color:#999;padding:7px 18px;border-radius:6px;font-size:13px;cursor:pointer;transition:all .15s }
        ._fm-btn-cancel:hover { background:#2a2a2a;color:#ddd;border-color:#555 }
        ._fm-btn-submit { background:#6d28d9;border:none;color:#fff;padding:7px 22px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s }
        ._fm-btn-submit:hover { background:#5b21b6 }
        ._fm-btn-submit:disabled { opacity:0.5;cursor:not-allowed }
    `;
    document.head.appendChild(style);
}

// ─── Styled Form Modal Builder ─── //
export function _createFormModal({ id, title, fields, onSubmit, submitLabel = '建立' }) {
    document.getElementById(id)?.remove();
    _ensureModalStyles();

    const overlay = document.createElement('div');
    overlay.id = id;
    overlay.className = '_fm-overlay';

    // Build sections / fields HTML
    let bodyHtml = '';
    let inSection = false;
    for (const f of fields) {
        // Section divider support
        if (f.type === 'divider') {
            if (inSection) bodyHtml += `</div>`;
            bodyHtml += `<div class="_fm-divider"></div>`;
            inSection = false;
            continue;
        }
        if (f.type === 'section') {
            if (inSection) bodyHtml += `</div>`;
            bodyHtml += `<div class="_fm-section"><div class="_fm-section-label">${f.label}</div>`;
            inSection = true;
            continue;
        }

        bodyHtml += `<div class="_fm-field">`;
        if (f.label && f.type !== 'checkboxes') {
            bodyHtml += `<label class="_fm-label">${f.label}${f.required ? '<span class="req">*</span>' : ''}</label>`;
        }
        if (f.type === 'select') {
            const opts = (f.options || []).map(o =>
                `<option value="${o.value}" ${o.value === f.defaultValue ? 'selected' : ''}>${o.label}</option>`
            ).join('');
            // f.searchable=true → 套 searchableSelect 包裝（同 CRM 專案客戶欄）。
            // data-ss-placeholder 給 init 時讀取 placeholder 文字。
            const ssAttr = f.searchable ? ` data-ss="1" data-ss-placeholder="${f.placeholder || '搜尋...'}"` : '';
            bodyHtml += `<select data-field="${f.key}" class="_fm-select"${ssAttr}>${opts}</select>`;
        } else if (f.type === 'checkboxes') {
            if (f.label) bodyHtml += `<label class="_fm-label">${f.label}</label>`;
            bodyHtml += `<div class="_fm-checkgrid">`;
            for (const o of (f.options || [])) {
                bodyHtml += `<label class="_fm-chk">
                    <input type="checkbox" data-field="${f.key}" value="${o.value}" ${o.checked ? 'checked' : ''}> ${o.label}
                </label>`;
            }
            bodyHtml += `</div>`;
        } else {
            bodyHtml += `<input data-field="${f.key}" type="${f.type || 'text'}" placeholder="${f.placeholder || ''}" ${f.autofocus ? 'autofocus' : ''} class="_fm-input">`;
        }
        if (f.hint) bodyHtml += `<div class="_fm-hint">${f.hint}</div>`;
        bodyHtml += `</div>`;
    }
    if (inSection) bodyHtml += `</div>`;

    const modal = document.createElement('div');
    modal.className = '_fm-modal';
    modal.innerHTML = `
        <div class="_fm-header">
            <h3>${title}</h3>
            <span class="_fm-close">✕</span>
        </div>
        <div class="_fm-body">${bodyHtml}
            <div class="_fm-error" data-error></div>
        </div>
        <div class="_fm-footer">
            <button class="_fm-btn-cancel" data-action="cancel">取消</button>
            <button class="_fm-btn-submit" data-action="submit">${submitLabel}</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // 掛上後才能 wrap searchable select（searchableSelect 用 insertBefore，
    // 需要 select 已在 DOM）。CSS 來自全域載入的 crm.css 的 .ss-* class。
    modal.querySelectorAll('select[data-ss="1"]').forEach(sel => {
        searchableSelect(sel, { placeholder: sel.dataset.ssPlaceholder });
    });

    const close = () => { overlay.style.animation = 'none'; overlay.remove(); };
    const errEl = modal.querySelector('[data-error]');
    const setError = (msg) => { if (msg) { errEl.textContent = msg; errEl.style.display = ''; } else { errEl.style.display = 'none'; } };
    const getValues = () => {
        const vals = {};
        for (const f of fields) {
            if (f.type === 'divider' || f.type === 'section') continue;
            if (f.type === 'checkboxes') {
                vals[f.key] = [...modal.querySelectorAll(`input[data-field="${f.key}"]:checked`)].map(cb => cb.value);
            } else {
                const el = modal.querySelector(`[data-field="${f.key}"]`);
                vals[f.key] = el ? el.value.trim() : '';
            }
        }
        return vals;
    };

    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    modal.querySelector('._fm-close').onclick = close;
    modal.querySelector('[data-action="cancel"]').onclick = close;
    modal.querySelector('[data-action="submit"]').onclick = async () => {
        setError('');
        const btn = modal.querySelector('[data-action="submit"]');
        btn.disabled = true; btn.textContent = '處理中...';
        try {
            await onSubmit(getValues(), setError, close);
        } finally {
            btn.disabled = false; btn.textContent = submitLabel;
        }
    };
    document.addEventListener('keydown', function _esc(e) {
        if (e.key === 'Escape') { close(); document.removeEventListener('keydown', _esc); }
    });
    modal.querySelectorAll('input._fm-input').forEach(inp => {
        inp.addEventListener('keydown', e => {
            if (e.key === 'Enter') modal.querySelector('[data-action="submit"]').click();
        });
    });
    const firstInput = modal.querySelector('input[autofocus], input._fm-input');
    if (firstInput) setTimeout(() => firstInput.focus(), 80);

    return { overlay, getValues, close, setError };
}

// Also expose on window for inline onclick handlers
window._ensureModalStyles = _ensureModalStyles;
window._createFormModal = _createFormModal;
