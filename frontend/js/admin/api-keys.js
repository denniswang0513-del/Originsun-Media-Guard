// ─── API Key Management (extracted from app.js) ─── //
import { _ensureModalStyles, _createFormModal } from '../shared/modal-styles.js';

async function _loadApiKeyList() {
    const container = document.getElementById('apikey-list');
    if (!container) return;
    try {
        const isAdmin = window._accessLevel >= 3;
        const url = isAdmin ? '/api/v1/api_keys/all' : '/api/v1/api_keys';
        const r = await fetch(url, { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') } });
        if (!r.ok) { container.innerHTML = '<div style="color:#666;text-align:center;padding:12px;">無法載入 API Keys</div>'; return; }
        const data = await r.json();
        const keys = data.keys || [];
        if (!keys.length) {
            container.innerHTML = '<div style="color:#555;text-align:center;padding:12px;font-size:11px;">尚未建立任何 API Key</div>';
            return;
        }
        let html = keys.map(k => {
            const active = k.is_active !== false;
            const expired = k.expires_at && new Date(k.expires_at) < new Date();
            const statusBadge = !active
                ? '<span style="color:#ef4444;font-size:10px;padding:1px 6px;background:rgba(239,68,68,0.1);border-radius:3px;">已撤銷</span>'
                : expired
                ? '<span style="color:#f59e0b;font-size:10px;padding:1px 6px;background:rgba(245,158,11,0.1);border-radius:3px;">已過期</span>'
                : '<span style="color:#22c55e;font-size:10px;padding:1px 6px;background:rgba(34,197,94,0.1);border-radius:3px;">有效</span>';
            const lastUsed = k.last_used_at ? new Date(k.last_used_at).toLocaleString('zh-TW') : '從未使用';
            const created = k.created_at ? new Date(k.created_at).toLocaleDateString('zh-TW') : '';
            const ownerTag = isAdmin && k.username ? `<span style="color:#888;font-size:10px;margin-left:6px;">(${k.username})</span>` : '';
            const prefix = k.key_prefix || '****';
            const escapedName = (k.name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
            const btnStyle = 'background:transparent;border:1px solid #3a3a3a;color:#999;border-radius:5px;padding:3px 8px;cursor:pointer;font-size:10px;transition:all .15s;';
            const dangerStyle = 'background:transparent;border:1px solid rgba(239,68,68,0.3);color:#f87171;border-radius:5px;padding:3px 8px;cursor:pointer;font-size:10px;transition:all .15s;';
            return `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#1e1e1e;border:1px solid #2e2e2e;border-radius:8px;margin-bottom:4px;transition:border-color .15s;" onmouseenter="this.style.borderColor='#444'" onmouseleave="this.style.borderColor='#2e2e2e'">
                <div style="min-width:0;flex:1;">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <span style="color:#a78bfa;font-family:monospace;font-size:12px;background:#1a1a2e;padding:2px 8px;border-radius:4px;">${prefix}</span>
                        <span style="color:#e0e0e0;font-size:12px;font-weight:500;">${k.name || ''}</span>${ownerTag}
                        ${statusBadge}
                    </div>
                    <div style="font-size:10px;color:#555;margin-top:4px;">建立 ${created} ｜ 最後使用 ${lastUsed}${k.expires_at ? ' ｜ 到期 ' + new Date(k.expires_at).toLocaleDateString('zh-TW') : ''}</div>
                </div>
                <div style="display:flex;gap:4px;align-items:center;margin-left:12px;flex-shrink:0;">
                    <button onclick="window._copyApiKey('${k.raw_key||prefix}',this)" style="${btnStyle}" title="複製完整 Key">複製</button>
                    ${active ? `<button onclick="window._renameApiKey(${k.id},'${escapedName}')" style="${btnStyle}" title="修改名稱">改名</button>` : ''}
                    ${active ? `<button onclick="window._revokeApiKey(${k.id})" style="${dangerStyle}" title="停用此 Key">停用</button>` : `<button onclick="window._enableApiKey(${k.id})" style="${btnStyle}" title="重新啟用">啟用</button>`}
                    <button onclick="window._deleteApiKey(${k.id})" style="${dangerStyle}" title="永久刪除">刪除</button>
                </div>
            </div>`;
        }).join('');
        container.innerHTML = html;
    } catch (_) {
        container.innerHTML = '<div style="color:#f87171;text-align:center;padding:12px;">載入失敗</div>';
    }
}

window._createApiKey = async function() {
    _ensureModalStyles();
    const fields = [
        { key: 'ak_name', label: '名稱', placeholder: '例如：OpenClaw、CI Script', required: true, autofocus: true },
        { key: 'ak_expire', label: '過期天數（空白 = 永不過期）', placeholder: '例如：90', type: 'number', hint: '留空表示永不過期' },
    ];
    _createFormModal({
        id: 'create-apikey-modal',
        title: '產生 API Key',
        fields,
        submitLabel: '產生',
        onSubmit: async (vals) => {
            const name = (vals['ak_name'] || '').trim();
            if (!name) throw new Error('請輸入名稱');
            const days = vals['ak_expire'] ? parseInt(vals['ak_expire'], 10) : null;
            const r = await fetch('/api/v1/api_keys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') },
                body: JSON.stringify({ name, expires_days: days && days > 0 ? days : null }),
            });
            if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || '建立失敗'); }
            const data = await r.json();
            document.getElementById('create-apikey-modal')?.remove();
            _showApiKeyResult(data.key);
            _loadApiKeyList();
        },
    });
};

function _showApiKeyResult(rawKey) {
    _ensureModalStyles();
    const o = document.createElement('div');
    o.className = '_fm-overlay';
    document.addEventListener('keydown', function _esc(e) { if (e.key === 'Escape') { o.remove(); document.removeEventListener('keydown', _esc); } });
    const m = document.createElement('div');
    m.className = '_fm-modal';
    m.style.width = '520px';
    m.innerHTML = `
        <div class="_fm-header">
            <h3>API Key 已產生</h3>
            <span class="_fm-close" onclick="this.closest('._fm-overlay')?.remove()">✕</span>
        </div>
        <div class="_fm-body" style="padding:20px 24px;">
            <div style="background:#0a0a0a;border:1px solid #333;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:13px;color:#22c55e;word-break:break-all;user-select:all;cursor:text;" id="ak-result-key">${rawKey}</div>
            <div style="display:flex;gap:10px;margin-top:12px;">
                <button id="ak-copy-btn" class="_fm-btn-submit" style="padding:6px 20px;font-size:12px;">複製</button>
            </div>
            <div style="margin-top:14px;padding:10px 14px;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.2);border-radius:6px;font-size:11px;color:#f87171;line-height:1.6;">
                <strong>此金鑰只會顯示這一次</strong>，關閉後無法再查看。請立即複製並安全保存。
            </div>
        </div>
    `;
    o.appendChild(m);
    document.body.appendChild(o);
    document.getElementById('ak-copy-btn').onclick = () => {
        window._copyApiKey(rawKey, document.getElementById('ak-copy-btn'));
    };
}

window._revokeApiKey = async function(keyId) {
    if (!confirm('確定要停用此 API Key？停用後仍可在列表中看到，但無法再使用。')) return;
    try {
        const r = await fetch(`/api/v1/api_keys/${keyId}`, {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') },
        });
        if (r.ok) {
            _loadApiKeyList();
        } else {
            const e = await r.json().catch(() => ({}));
            alert(e.detail || '停用失敗');
        }
    } catch (_) { alert('停用失敗'); }
};

window._deleteApiKey = async function(keyId) {
    if (!confirm('確定要永久刪除此 API Key？此操作無法復原。')) return;
    try {
        const r = await fetch(`/api/v1/api_keys/${keyId}?permanent=true`, {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') },
        });
        if (r.ok) {
            _loadApiKeyList();
        } else {
            const e = await r.json().catch(() => ({}));
            alert(e.detail || '刪除失敗');
        }
    } catch (_) { alert('刪除失敗'); }
};

window._copyApiKey = function(key, btn) {
    // Try clipboard API (works on HTTPS / localhost)
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(key).then(() => {
            btn.textContent = 'OK'; setTimeout(() => btn.textContent = '複製', 1500);
        }).catch(() => prompt('Ctrl+C 複製此 Key：', key));
        return;
    }
    // Fallback: prompt dialog (always works, user can Ctrl+C)
    prompt('Ctrl+C 複製此 Key：', key);
};

window._enableApiKey = async function(keyId) {
    try {
        const r = await fetch(`/api/v1/api_keys/${keyId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') },
            body: JSON.stringify({ is_active: true }),
        });
        if (r.ok) {
            _loadApiKeyList();
        } else {
            const e = await r.json().catch(() => ({}));
            alert(e.detail || '啟用失敗');
        }
    } catch (_) { alert('啟用失敗'); }
};

window._renameApiKey = async function(keyId, currentName) {
    const newName = prompt('輸入新名稱：', currentName);
    if (newName === null || newName.trim() === '' || newName.trim() === currentName) return;
    try {
        const r = await fetch(`/api/v1/api_keys/${keyId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') },
            body: JSON.stringify({ name: newName.trim() }),
        });
        if (r.ok) {
            _loadApiKeyList();
        } else {
            const e = await r.json().catch(() => ({}));
            alert(e.detail || '改名失敗');
        }
    } catch (_) { alert('改名失敗'); }
};

// Expose for cross-module calls
window._loadApiKeyList = _loadApiKeyList;
