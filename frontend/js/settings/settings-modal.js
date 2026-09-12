// ─── Settings Modal (extracted from app.js) ─── //

// settings.json 的 company 區塊（config.py 預設值是正本）；index.html 的欄位 id = company_<key>
const COMPANY_KEYS = ['name', 'name_en', 'tax_id', 'address', 'phone', 'email', 'bank',
    'account_name', 'account_no', 'bankbook_path', 'quote_valid_days', 'delivery_terms', 'logo_path', 'seal_path'];
const _companyEl = (k) => document.getElementById('company_' + k);

function fillCompany(company) {
    const c = company || {};
    COMPANY_KEYS.forEach(k => { const el = _companyEl(k); if (el) el.value = c[k] ?? ''; });
    ['logo', 'seal'].forEach(_loadCompanyPreview);
    _loadBankbookStatus();
}

// 存摺影本：不是圖、不預覽，只顯示「目前是哪個檔、多大」＋ 一個帶權限的下載連結。
// 存在發票根目錄（不是 company_assets/）—— 那頁由 NAS 對外容器 serve，company_assets 它看不到。
// 🔴 端點在 /api/v1/crm/ 底下：login-modal 補 token 的那份 fetch 白名單只認 /settings/ 等幾個路徑，
//    這裡要自己帶 Authorization（utils 掛在 window 的 bearerHeader）。
const _BANKBOOK_API = '/api/v1/crm/invoices/bankbook';
const _bh = () => (window.bearerHeader ? window.bearerHeader() : {});
async function _loadBankbookStatus() {
    const st = document.getElementById('company_bankbook_status'), a = document.getElementById('company_bankbook_link');
    if (!st || !a) return;
    try {
        const r = await fetch(_BANKBOOK_API, { headers: _bh() });
        if (!r.ok) throw new Error(r.status === 404 ? '尚未上傳。發票分享頁「匯款資訊」給客戶下載；存在發票資料夾底下，換檔即生效' : '無法讀取目前的存摺影本（HTTP ' + r.status + '）');
        const d = await r.json();
        const kb = Math.max(1, Math.round((d.size || 0) / 1024));
        a.textContent = `${d.file_name}（${kb} KB）`;
        a.style.display = '';
        a.onclick = async (e) => {   // 帶 token 的下載（<a href> 送不了 Authorization）
            e.preventDefault();
            const rr = await fetch(_BANKBOOK_API + '?download=1', { headers: _bh() });
            if (!rr.ok) return;
            const u = URL.createObjectURL(await rr.blob());
            const t = document.createElement('a'); t.href = u; t.download = d.file_name; document.body.appendChild(t); t.click(); t.remove();
            setTimeout(() => URL.revokeObjectURL(u), 10000);
        };
        st.textContent = '已上傳。換檔直接再按上傳';
    } catch (e) {
        a.style.display = 'none';
        st.textContent = e.message || '無法讀取目前的存摺影本';   // 別留著上一次的「已上傳」
    }
}

function _bindBankbookUpload() {
    const btn = document.getElementById('company_bankbook_upload'), file = document.getElementById('company_bankbook_file');
    if (!btn || !file || btn.dataset.bound) return;
    btn.dataset.bound = '1';
    file.accept = 'application/pdf,image/png,image/jpeg';   // 🔴 accept 在 JS 設（html 裡 image 加斜線星號會被掃碼測試當註解）
    btn.addEventListener('click', () => file.click());
    file.addEventListener('change', async () => {
        const f = file.files && file.files[0];
        if (!f) return;
        const st = document.getElementById('company_bankbook_status');
        if (st) st.textContent = '上傳中…';
        try {
            const fd = new FormData(); fd.append('file', f);
            const r = await fetch(_BANKBOOK_API, { method: 'POST', body: fd, headers: _bh() });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
            const el = _companyEl('bankbook_path'); if (el) el.value = d.path || '';
            await _loadBankbookStatus();
        } catch (e) { if (st) st.textContent = '上傳失敗：' + (e.message || e); }
        file.value = '';
    });
}

// Logo／印章：上傳 → 後端存 company_assets/ 並直接寫進 settings；這裡只回填路徑欄與預覽。
// 預覽走 fetch（全域 fetch 有帶 token）→ blob，不用 <img src> 直打（那條不帶 Authorization，會 401）。
async function _loadCompanyPreview(kind) {
    const img = document.getElementById(`company_${kind}_preview`), st = document.getElementById(`company_${kind}_status`);
    if (!img) return;
    try {
        const r = await fetch(`/api/settings/company-image/${kind}`);
        if (!r.ok) throw new Error();
        if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);   // 每開一次設定就重抓；舊 blob 不放掉會一路留到關頁
        img.src = URL.createObjectURL(await r.blob()); img.style.display = '';
        if (st) st.textContent = '已上傳';
    } catch (_) { img.style.display = 'none'; }
}

function _bindCompanyUploads() {
    ['logo', 'seal'].forEach(kind => {
        const btn = document.getElementById(`company_${kind}_upload`), file = document.getElementById(`company_${kind}_file`);
        if (!btn || !file || btn.dataset.bound) return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', () => file.click());
        file.addEventListener('change', async () => {
            const f = file.files && file.files[0];
            if (!f) return;
            const st = document.getElementById(`company_${kind}_status`);
            if (st) st.textContent = '上傳中…';
            try {
                const fd = new FormData(); fd.append('file', f);
                const r = await fetch(`/api/settings/company-image/${kind}`, { method: 'POST', body: fd });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
                const el = _companyEl(`${kind}_path`); if (el) el.value = d.path || '';
                await _loadCompanyPreview(kind);
                if (st) st.textContent = '已上傳，下一張 PDF 起生效';
            } catch (e) { if (st) st.textContent = '上傳失敗：' + (e.message || e); }
            file.value = '';
        });
    });
}

// 🔴 欄位不在 DOM（舊 html 配新 js，CF 只快取 .js）就回 null → 整個 company 不送，
// 讓後端 merge-on-save 留住原值；照送會把公司資訊寫成一片空字串。
function readCompany() {
    if (!_companyEl('name')) return null;
    return Object.fromEntries(COMPANY_KEYS.map(k => {
        const v = (_companyEl(k)?.value ?? '').trim();
        return [k, k === 'quote_valid_days' ? (parseInt(v) || 14) : v];
    }));
}

function showInstallModal() {
    document.getElementById('install-modal').classList.remove('hidden');
}

// Global tab-switch function (called from inline onclick on tab buttons)
function switchSettingsTab(tabId, event) {
    document.querySelectorAll('#settingsModal .tab-content').forEach(el => el.style.display = 'none');
    document.querySelectorAll('#settingsModal .tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('tab_' + tabId).style.display = 'block';
    (event || window.event).currentTarget.classList.add('active');
    if (tabId === 'user_mgmt' && typeof window._loadUserList === 'function') window._loadUserList();
}

// Wrapped in DOMContentLoaded so modal HTML (placed after this script)
// is fully parsed before we try to bind event listeners.
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('settingsModal');
    _bindCompanyUploads();
    _bindBankbookUpload();

    // ── Load settings when modal opens ───────────────────────
    document.getElementById('btnOpenSettings').addEventListener('click', async () => {
        // 預設是完整的系統設定；報價頁的「公司資訊」鈕會在這之後加上 company-only（只留那一個分頁）
        modal.classList.remove('company-only');
        const h3 = modal.querySelector('.modal-header h3'); if (h3) h3.textContent = h3.dataset.full || h3.textContent;
        modal.style.display = 'flex';
        try {
            const res = await fetch('/api/settings/load');
            if (res.ok) {
                const data = await res.json();
                const n = data.notifications || {};
                const t = data.message_templates || {};
                document.getElementById('gchat_webhook').value = n.google_chat_webhook || '';
                document.getElementById('alert_webhook').value = n.alert_webhook || '';
                document.getElementById('custom_webhook').value = n.custom_webhook_url || '';
                document.getElementById('tpl_backup_success').value = t.backup_success || '';
                document.getElementById('tpl_report_success').value = t.report_success || '';
                document.getElementById('tpl_transcode_success').value = t.transcode_success || '';
                document.getElementById('tpl_concat_success').value = t.concat_success || '';
                document.getElementById('tpl_verify_success').value = t.verify_success || '';
                document.getElementById('tpl_transcribe_success').value = t.transcribe_success || '';
                fillCompany(data.company);
                // ── Load channel toggles ──────────────────────────────────────
                const ch = data.notification_channels || {};
                const tabs = ['backup', 'report', 'transcode', 'concat', 'verify', 'transcribe'];
                tabs.forEach(tab => {
                    const cfg = ch[tab] || { gchat: true };
                    document.querySelectorAll(`.ch-toggle[data-tab="${tab}"]`).forEach(el => {
                        const channel = el.dataset.ch;
                        const isOn = cfg[channel] !== undefined ? cfg[channel] : (channel === 'gchat');
                        el.classList.toggle('on', isOn);
                        el.classList.toggle('off', !isOn);
                    });
                });
            }
        } catch (e) { /* silent fail */ }
    });

    document.getElementById('btnCloseSettings').onclick = () => modal.style.display = 'none';
    document.getElementById('btnCancelSettings').onclick = () => modal.style.display = 'none';

    // ── Save settings ────────────────────────────────────────
    document.getElementById('btnSaveSettings').addEventListener('click', async () => {
        // 從報價頁「公司資訊」開的（company-only）只送 company 這一個頂層鍵：
        // 後端 /api/settings/save 依頂層鍵分流，單獨的 company 給報價／帳務模組寫，夾了通知設定進去就變成要管理員
        const companyOnly = modal.classList.contains('company-only');
        if (companyOnly && !readCompany()) { alert('公司資訊欄位不在畫面上，請重新整理後再試。'); return; }
        // LINE Notify 服務已終止（2025-03-31），通道與 token 欄位已移除
        const settingsData = companyOnly ? { company: readCompany() } : {
            notifications: {
                google_chat_webhook: document.getElementById('gchat_webhook').value,
                alert_webhook: document.getElementById('alert_webhook').value,
                custom_webhook_url: document.getElementById('custom_webhook').value,
            },
            message_templates: {
                backup_success: document.getElementById('tpl_backup_success').value,
                report_success: document.getElementById('tpl_report_success').value,
                transcode_success: document.getElementById('tpl_transcode_success').value,
                concat_success: document.getElementById('tpl_concat_success').value,
                verify_success: document.getElementById('tpl_verify_success').value,
                transcribe_success: document.getElementById('tpl_transcribe_success').value,
            },
            // 後端 save_settings 是 merge-on-save（頂層鍵逐一覆蓋、dict 淺合併），沒送的區塊不會被洗掉
            ...(readCompany() ? { company: readCompany() } : {}),
            // ── Channel toggles ──────────────────────────────
            notification_channels: Object.fromEntries(
                ['backup', 'report', 'transcode', 'concat', 'verify', 'transcribe'].map(tab => [
                    tab,
                    {
                        gchat: document.querySelector(`.ch-toggle.gchat[data-tab="${tab}"]`)?.classList.contains('on') ?? true,
                    }
                ])
            ),
        };
        try {
            const response = await fetch('/api/settings/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settingsData)
            });
            if (response.ok) {
                alert('✅ 設定已成功儲存！');
                modal.style.display = 'none';
            } else if (response.status === 401 || response.status === 403) {
                // 分流後的 403 是「缺哪把鑰匙」，不是連線問題
                alert(companyOnly ? '儲存公司資訊需要管理員、報價管理或帳務管理權限。' : '儲存系統設定需要管理員權限。');
            } else {
                const d = await response.json().catch(() => ({}));
                alert('儲存失敗：' + (d.message || d.detail || ('HTTP ' + response.status)));
            }
        } catch (error) {
            console.error('儲存設定發生錯誤:', error);
            alert('儲存失敗：' + (error.message || '網路錯誤'));
        }
    });
    // ── Restart Agent（已移至下拉選單 window._restartAgent）──
    // ── Channel toggle pills ────────────────────────────────
    document.getElementById('settingsModal').addEventListener('click', e => {
        const t = e.target.closest('.ch-toggle');
        if (!t) return;
        t.classList.toggle('on');
        t.classList.toggle('off');
    });
});

// Expose on window
window.showInstallModal = showInstallModal;
window.switchSettingsTab = switchSettingsTab;
