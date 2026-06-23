/**
 * settings.js — 網站設定（website_settings key-value）
 * 依 key 前綴分群：company / social / seo / analytics / notify / turnstile
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, getApiBase } from '../website-utils.js';

// 品牌圖 src：相對 /uploads/ 要補 API base 才看得到預覽
function _brandSrc(url) {
    if (!url) return '';
    return /^https?:\/\//.test(url) ? url : `${getApiBase()}${url}`;
}
function _brandSlot(kind, label, url) {
    const src = _brandSrc(url);
    return `
        <div>
            <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">${label}</label>
            <div id="brand-prev-${kind}" style="height:48px;display:flex;align-items:center;margin-bottom:6px;background:#0f0f0f;border:1px solid #2a2a2a;border-radius:6px;padding:6px 10px;">
                ${src ? `<img src="${esc(src)}" style="max-height:36px;max-width:150px;object-fit:contain;" />` : '<span style="color:#555;font-size:11px;">尚未設定</span>'}
            </div>
            <div style="display:flex;gap:6px;">
                <button class="btn btn-sm" onclick="window._websiteUploadBrand('${kind}', this)">📤 上傳</button>
                <input id="brand-url-${kind}" data-key="brand.${kind}_url" value="${esc(url || '')}" placeholder="或貼圖片網址" style="flex:1;font-size:12px;" />
            </div>
        </div>`;
}
function _renderBrandCard() {
    return `
        <div class="card" style="border-left:3px solid #c9372c;margin-bottom:12px;">
            <h3 style="color:#fff;margin:0 0 4px;font-size:14px;">🎨 品牌 Logo</h3>
            <p style="color:#888;font-size:11px;margin:0 0 12px;">Logo 會套用到網站 header（取代預設紅色標記）；favicon 是瀏覽器分頁的小圖示。建議 Logo 用透明背景 PNG 或 SVG。上傳即套用、官網自動 rebuild。</p>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                ${_brandSlot('logo', 'Logo（網站 header）', _settings['brand.logo_url'])}
                ${_brandSlot('favicon', 'Favicon（分頁小圖示）', _settings['brand.favicon_url'])}
            </div>
        </div>`;
}

window._websiteUploadBrand = (kind, btn) => {
    const fi = document.createElement('input');
    fi.type = 'file';
    fi.accept = kind === 'favicon' ? 'image/png,image/svg+xml,.ico,.svg' : 'image/png,image/jpeg,image/svg+xml,.svg';
    fi.onchange = async () => {
        const f = fi.files && fi.files[0];
        if (!f) return;
        const orig = btn.textContent;
        btn.disabled = true; btn.textContent = '⏳';
        try {
            const fd = new FormData();
            fd.append('file', f);
            const r = await websiteFetch(`/api/website/admin/brand/upload?kind=${kind}`, { method: 'POST', body: fd });
            const input = document.getElementById(`brand-url-${kind}`);
            if (input) input.value = r.url;
            _settings[`brand.${kind}_url`] = r.url;
            const prev = document.getElementById(`brand-prev-${kind}`);
            if (prev) prev.innerHTML = `<img src="${esc(_brandSrc(r.url))}?t=${Date.now()}" style="max-height:36px;max-width:150px;object-fit:contain;" />`;
            toastOk('已上傳並套用，官網將自動 rebuild');
        } catch (e) {
            toastErr('上傳失敗：' + (e.message || e));
        } finally {
            btn.disabled = false; btn.textContent = orig;
        }
    };
    fi.click();
};

// 公司資訊(company.*) + 社群連結(social.*) 已移到「📬 聯絡」子視圖管理（屬對外聯絡頁內容）
const _CONTACT_MOVED = ['company.', 'social.'];
const _GROUPS = [
    { prefix: 'seo', label: '🔍 SEO 預設', color: '#10b981' },
    { prefix: 'analytics', label: '📊 分析追蹤', color: '#f59e0b' },
    { prefix: 'notify', label: '🔔 通知設定', color: '#dc2626' },
    { prefix: 'turnstile', label: '🛡️ Turnstile 反機器人', color: '#ec4899',
      knownKeys: ['site_key', 'secret'] },
    // knownKeys: 即使 DB 沒這個 key 也渲染空欄讓使用者填（首次設定 Notion 用）
    { prefix: 'notion', label: '📝 Notion 部落格', color: '#06b6d4',
      knownKeys: ['token', 'database_id'] },
];

let _settings = {};

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>⚙️ 網站設定</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const data = await websiteFetch('/api/website/admin/settings');
        if (!isCurrent()) return;
        _settings = data?.settings || {};
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '⚙️ 網站設定', e);
        return;
    }

    const groupKeys = (g) => {
        const existing = Object.keys(_settings).filter(k => k.startsWith(g.prefix + '.'));
        const known = (g.knownKeys || []).map(suffix => g.prefix + '.' + suffix);
        // 合併並去重，known keys 在前讓使用者一打開就看到應填欄位
        return [...new Set([...known, ...existing])].sort();
    };

    const ungrouped = Object.keys(_settings).filter(k =>
        !_GROUPS.some(g => k.startsWith(g.prefix + '.')) &&
        !_CONTACT_MOVED.some(p => k.startsWith(p))   // company.*/social.* 改在「📬 聯絡」管
    ).sort();

    container.innerHTML = `
        <h2>⚙️ 網站設定 <span style="color:#888;font-size:12px;font-weight:400;">· ${Object.keys(_settings).length} 項</span></h2>
        <div style="color:#888;font-size:11px;margin:-4px 0 12px;">📇 公司資訊 / 社群連結已移至「📬 聯絡」子視圖管理。</div>

        ${_renderBrandCard()}

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:12px;margin-bottom:16px;">
            ${_GROUPS.map(g => _renderGroup(g, groupKeys(g))).join('')}
            ${ungrouped.length ? _renderGroup({ prefix: '', label: '📦 其他', color: '#666' }, ungrouped) : ''}
        </div>

        <div class="card" style="display:flex;gap:8px;align-items:center;">
            <button class="btn" onclick="window._websiteSaveSettings()">💾 儲存全部變更</button>
            <button class="btn btn-ghost btn-sm" onclick="window._websiteReloadSettings()">↻ 重新載入</button>
            <span style="color:#888;font-size:11px;margin-left:auto;">空欄填值後存檔即建立新 key；其他 key 可透過 API 操作。</span>
        </div>
    `;
}

function _renderGroup(g, keys) {
    if (!keys.length && g.prefix) return '';
    return `
        <div class="card" style="border-left:3px solid ${g.color};">
            <h3 style="color:#fff;margin:0 0 10px 0;font-size:14px;">${g.label}</h3>
            <div style="display:grid;grid-template-columns:1fr;gap:8px;">
                ${keys.map(k => _renderField(k, _settings[k])).join('')}
            </div>
        </div>
    `;
}

function _renderField(key, val) {
    const shortKey = key.includes('.') ? key.split('.').slice(1).join('.') : key;
    const isMissing = val === undefined || val === null;
    const inputVal = isMissing ? '' : (typeof val === 'string' ? val : JSON.stringify(val));
    const isSecret = key.includes('token') || key.includes('secret') || key.includes('key');
    const isLong = typeof val === 'string' && (val.length > 80 || val.includes('\n'));
    const placeholder = isMissing ? '（尚未設定，填值後存檔即建立）' : '';
    const control = isLong
        ? `<textarea data-key="${esc(key)}" rows="3" style="width:100%;" placeholder="${esc(placeholder)}">${esc(inputVal)}</textarea>`
        : `<input data-key="${esc(key)}" type="${isSecret ? 'password' : 'text'}" value="${esc(inputVal)}" placeholder="${esc(placeholder)}" style="width:100%;" />`;
    return `
        <div>
            <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;" title="${esc(key)}">${esc(shortKey)}${isSecret ? ' 🔒' : ''}${isMissing ? ' <span style="color:#666;">（新）</span>' : ''}</label>
            ${control}
        </div>
    `;
}

window._websiteSaveSettings = async () => {
    const values = {};
    document.querySelectorAll('#website-content [data-key]').forEach(el => {
        const key = el.dataset.key;
        const orig = _settings[key];
        const raw = el.value;
        // 若原本是 JSON 物件/數字，嘗試 parse；失敗則當字串
        let parsed = raw;
        if (orig !== undefined && typeof orig !== 'string') {
            try { parsed = JSON.parse(raw); } catch { parsed = raw; }
        }
        // 新 key 但使用者沒填值 → 不建立空 row
        if (orig === undefined && (parsed === '' || parsed === null)) return;
        if (JSON.stringify(parsed) !== JSON.stringify(orig)) {
            values[key] = parsed;
        }
    });
    if (!Object.keys(values).length) {
        toastOk('沒有變更');
        return;
    }
    try {
        const result = await websiteFetch('/api/website/admin/settings', {
            method: 'PUT',
            body: { values },
        });
        toastOk(`已更新 ${result.updated} 項設定`);
        Object.assign(_settings, values);
    } catch (e) { toastErr(e.message); }
};

window._websiteReloadSettings = async () => {
    const content = document.getElementById('website-content');
    if (content) await render(content);
};
