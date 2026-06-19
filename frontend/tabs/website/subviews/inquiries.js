/**
 * inquiries.js — 聯絡詢問收件箱
 * 列表 + 詳情面板 + 狀態切換 + 轉 CRM client + 刪除
 *
 * 備註：此檔使用 id-based DOM guard（document.getElementById('inq-list') 等）
 * 而非其他子視圖的 token-based isCurrent callback。原因：本檔有多個非 render
 * 的 async 函式（_reloadList / _renderDetail / window._website* handlers）會
 * 在多個時間點 await，且這些 ID 在 innerHTML 替換時真的會消失——id-check 能
 * 正確偵測 stale context。新增的子視圖優先採用 token pattern（render(container,
 * { isCurrent })）。
 */
import { websiteFetch, esc, fmtDt, fmtRelative, toastOk, toastErr, renderLoadError, INQUIRY_STATUSES, inquiryStatusLabel, renderCopyCard } from '../website-utils.js';

let _inquiries = [];
let _selectedId = null;
let _filter = { status: '' };

// /contact 頁面行銷文案（對應 contact.astro + ContactForm.astro 的 copy.contact.* fallback）
const COPY_BLOCKS = [
    { key: 'hero_eyebrow', label: 'Hero 上方小字（eyebrow）', type: 'text', placeholderZh: 'Contact' },
    { key: 'hero_title', label: 'Hero 標題', placeholderZh: '聯絡我們', placeholderEn: "Let's Talk" },
    { key: 'hero_intro', label: 'Hero 介紹段', long: true, placeholderZh: '告訴我們您的需求…' },
    { key: 'direct_heading', label: '「也可直接聯絡」標題', placeholderZh: '也可直接聯絡', placeholderEn: 'Or reach us directly' },
    // 聯絡表單欄位標籤（ContactForm.astro 的 copy.contact.form_* fallback）
    { key: 'form_name', label: '表單：姓名欄位', placeholderZh: '姓名', placeholderEn: 'Name' },
    { key: 'form_email', label: '表單：Email 欄位', placeholderZh: 'Email', placeholderEn: 'Email' },
    { key: 'form_phone', label: '表單：電話欄位', placeholderZh: '電話', placeholderEn: 'Phone' },
    { key: 'form_company', label: '表單：公司欄位', placeholderZh: '公司', placeholderEn: 'Company' },
    { key: 'form_service_type', label: '表單：服務類型欄位', placeholderZh: '服務類型', placeholderEn: 'Service Type' },
    { key: 'form_budget_range', label: '表單：預算範圍欄位', placeholderZh: '預算範圍', placeholderEn: 'Budget Range' },
    { key: 'form_message', label: '表單：訊息欄位', placeholderZh: '訊息', placeholderEn: 'Message' },
];

// 聯絡表單選項清單（forms.contact.* — JSON list of {value,label_zh,label_en}）。
// value = 穩定 token（後端 service_type / budget_range + CRM 對應存這個），唯讀不可改；
// 只 label_zh / label_en 可編。空欄不送（→ ContactForm.astro 用硬寫 fallback 選項）。
const FORM_OPTION_LISTS = [
    {
        key: 'forms.contact.service_types', label: '服務類型選項',
        defaults: [
            { value: 'commercial', label_zh: '商業廣告・形象影片', label_en: 'Commercial & Brand' },
            { value: 'documentary', label_zh: '紀實短片・紀錄片', label_en: 'Documentary' },
            { value: 'event', label_zh: '活動紀實', label_en: 'Event' },
            { value: 'animation', label_zh: '動畫製作', label_en: 'Animation' },
            { value: 'other', label_zh: '其他', label_en: 'Other' },
        ],
    },
    {
        key: 'forms.contact.budget_ranges', label: '預算範圍選項',
        defaults: [
            { value: '10-30', label_zh: '10-30 萬', label_en: '100K–300K' },
            { value: '30-80', label_zh: '30-80 萬', label_en: '300K–800K' },
            { value: '80-150', label_zh: '80-150 萬', label_en: '800K–1.5M' },
            { value: '150-300', label_zh: '150-300 萬', label_en: '1.5M–3M' },
            { value: '300+', label_zh: '300 萬以上', label_en: '3M+' },
        ],
    },
];

export default async function render(container) {
    const statusOpts = INQUIRY_STATUSES.map(s => `<option value="${s.value}">${esc(s.labelZh)}</option>`).join('');
    container.innerHTML = `
        <h2>📬 聯絡</h2>

        <!-- 聯絡頁資訊（公司資訊 + 社群連結）— 對外官網「聯絡」頁顯示的內容，由此編輯 -->
        <div id="contact-info-card" class="card" style="margin-bottom:16px;border-left:3px solid #ec4899;">
            <div style="color:#888;padding:8px;">聯絡頁資訊載入中…</div>
        </div>

        <!-- 聯絡頁行銷文案（copy.contact.*）— 由 _loadContactInfo 一起填入（共用 settings fetch） -->
        <div id="contact-copy-host"></div>

        <!-- 聯絡表單選項清單（forms.contact.*）— 由 _loadContactInfo 一起填入 -->
        <div id="contact-forms-host"></div>

        <h3 style="color:#fff;font-size:14px;margin:0 0 10px;">📥 聯絡詢問收件匣</h3>
        <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;">
            <select id="inq-status-filter" style="min-width:140px;">
                <option value="">所有狀態</option>
                ${statusOpts}
            </select>
            <span id="inq-total" style="color:#888;font-size:12px;"></span>
        </div>
        <div id="inq-layout" style="display:grid;grid-template-columns:minmax(320px, 40%) 1fr;gap:16px;">
            <div class="card" style="padding:0;">
                <div id="inq-list" style="max-height:70vh;overflow:auto;"></div>
            </div>
            <div class="card" id="inq-detail">
                <div style="color:#666;padding:40px;text-align:center;">← 從左側選擇一筆詢問</div>
            </div>
        </div>
    `;

    document.getElementById('inq-status-filter').addEventListener('change', async (e) => {
        _filter.status = e.target.value;
        await _reloadList();
    });

    await _loadContactInfo();
    await _reloadList();
}

// ── 聯絡頁資訊：company.* + social.* settings（原本在「網站設定」，移來「聯絡」一起管）──
const _CONTACT_PREFIXES = ['company', 'social'];
let _contactSettings = {};

async function _loadContactInfo() {
    const card = document.getElementById('contact-info-card');
    if (!card) return;
    let settings = {};
    try {
        const data = await websiteFetch('/api/website/admin/settings');
        settings = data?.settings || {};
    } catch (e) {
        if (document.getElementById('contact-info-card'))
            card.innerHTML = `<div style="color:#f87171;padding:8px;">聯絡頁資訊載入失敗：${esc(e.message)}</div>`;
        return;
    }
    if (!document.getElementById('contact-info-card')) return;  // 已切換走
    const keys = Object.keys(settings)
        .filter(k => _CONTACT_PREFIXES.some(p => k.startsWith(p + '.'))).sort();
    _contactSettings = Object.fromEntries(keys.map(k => [k, settings[k]]));
    const fields = keys.map(k => {
        const shortKey = k.split('.').slice(1).join('.');
        const v = settings[k];
        const val = (v === undefined || v === null) ? '' : (typeof v === 'string' ? v : JSON.stringify(v));
        return `<div>
            <label style="color:#888;font-size:11px;display:block;margin-bottom:3px;" title="${esc(k)}">${esc(shortKey)}</label>
            <input data-contact-key="${esc(k)}" type="text" value="${esc(val)}" style="width:100%;" />
        </div>`;
    }).join('');
    card.innerHTML = `
        <h3 style="color:#fff;margin:0 0 10px;font-size:14px;">📇 聯絡頁資訊 <span style="color:#888;font-size:11px;font-weight:400;">· 公司資訊 + 社群連結</span></h3>
        ${keys.length
            ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px;">${fields}</div>
               <div style="margin-top:10px;"><button class="btn btn-sm" onclick="window._websiteSaveContactInfo()">💾 儲存聯絡頁資訊</button></div>`
            : `<div style="color:#888;font-size:12px;">尚無 company.* / social.* 設定。</div>`}
    `;

    // 共用「📝 頁面文案」卡（重用同一份 settings，免額外 fetch）
    const copyHost = document.getElementById('contact-copy-host');
    if (copyHost) {
        copyHost.innerHTML = renderCopyCard('copy.contact', settings, COPY_BLOCKS, {
            title: '📝 聯絡頁文案',
            note: '對應 /contact 頁的標題、「也可直接聯絡」區塊與聯絡表單欄位標籤；留空則維持預設文案。',
        });
    }

    // 聯絡表單下拉選項清單（forms.contact.service_types / budget_ranges）
    const formsHost = document.getElementById('contact-forms-host');
    if (formsHost) {
        _renderFormOptionCards(formsHost, settings);
    }
}

// ── 聯絡表單選項編輯（每個 list 一張卡，rows 可改 label / 增刪；value 唯讀）──
//
// 一個 settings key = 一整個 JSON list（[{value,label_zh,label_en}, ...]）。
// 儲存時整包覆寫該 key。空 list（全刪光）→ 送 [] → ContactForm.astro 看到空就用硬寫 fallback。
// value 是穩定 token（後端 service_type/budget_range + CRM 對應存這個），唯讀不可改。

function _coerceOptionList(raw, defaults) {
    // settings 該 key 還沒設過 → 用 defaults 當初始可見內容（與對外 fallback 一致）。
    if (!Array.isArray(raw)) return defaults.map(d => ({ ...d }));
    return raw
        .filter(o => o && typeof o === 'object' && typeof o.value === 'string' && o.value.trim())
        .map(o => ({
            value: String(o.value).trim(),
            label_zh: typeof o.label_zh === 'string' ? o.label_zh : '',
            label_en: typeof o.label_en === 'string' ? o.label_en : '',
        }));
}

function _renderFormOptionCards(host, settings) {
    host.innerHTML = FORM_OPTION_LISTS.map(list => {
        const items = _coerceOptionList(settings[list.key], list.defaults);
        return `
        <div class="card" data-forms-key="${esc(list.key)}" style="margin-bottom:16px;border-left:3px solid #f59e0b;">
            <h3 style="color:#fff;margin:0 0 4px 0;font-size:14px;">⬇️ ${esc(list.label)}</h3>
            <p style="color:#888;font-size:11px;margin:0 0 10px 0;">對應聯絡表單的下拉選單。<strong>value 是後端 / CRM 儲存的穩定代碼，唯讀不可改</strong>；只 label 可編。全部刪光則對外網站沿用預設選項。</p>
            <table style="width:100%;">
                <thead><tr>
                    <th style="text-align:left;">代碼 (value)</th><th style="text-align:left;">中文標籤</th><th style="text-align:left;">English</th><th></th>
                </tr></thead>
                <tbody class="forms-rows">
                    ${items.map(it => _formOptionRow(it)).join('')}
                </tbody>
            </table>
            <div style="margin-top:10px;display:flex;gap:8px;align-items:center;">
                <button class="btn btn-sm btn-ghost" onclick="window._websiteAddFormOption('${esc(list.key)}')">+ 新增選項</button>
                <button class="btn btn-sm" onclick="window._websiteSaveFormOptions('${esc(list.key)}')">💾 儲存選項清單</button>
            </div>
        </div>`;
    }).join('');
}

function _formOptionRow(it = { value: '', label_zh: '', label_en: '' }, isNew = false) {
    // 既有項目 value 唯讀（穩定 token）；新增列的 value 可填一次（建立後也視為穩定）。
    const valCell = isNew
        ? `<input data-field="value" value="${esc(it.value)}" placeholder="代碼（英數，如 other）" style="width:100%;font-family:monospace;" />`
        : `<input data-field="value" value="${esc(it.value)}" readonly title="穩定代碼，不可變更" style="width:100%;font-family:monospace;background:#222;color:#888;cursor:not-allowed;" />`;
    return `
        <tr class="forms-row">
            <td style="padding:3px 6px 3px 0;">${valCell}</td>
            <td style="padding:3px 6px 3px 0;"><input data-field="label_zh" value="${esc(it.label_zh)}" style="width:100%;" /></td>
            <td style="padding:3px 6px 3px 0;"><input data-field="label_en" value="${esc(it.label_en)}" style="width:100%;" /></td>
            <td style="padding:3px 0;"><button class="btn btn-sm btn-danger" title="刪除此選項" onclick="this.closest('tr').remove()">🗑</button></td>
        </tr>`;
}

window._websiteAddFormOption = (key) => {
    const card = document.querySelector(`[data-forms-key="${key}"]`);
    if (!card) return;
    const tbody = card.querySelector('.forms-rows');
    if (tbody) tbody.insertAdjacentHTML('beforeend', _formOptionRow(undefined, true));
};

window._websiteSaveFormOptions = async (key) => {
    const card = document.querySelector(`[data-forms-key="${key}"]`);
    if (!card) { toastErr('找不到選項卡'); return; }
    const list = [];
    const seen = new Set();
    let bad = false;
    card.querySelectorAll('.forms-row').forEach(tr => {
        const get = (f) => (tr.querySelector(`[data-field="${f}"]`)?.value || '').trim();
        const value = get('value');
        if (!value) { bad = true; return; }       // 空 value 列丟棄並警告
        if (seen.has(value)) { bad = true; return; }
        seen.add(value);
        list.push({ value, label_zh: get('label_zh') || value, label_en: get('label_en') || get('label_zh') || value });
    });
    if (bad) { toastErr('每個選項的代碼必填且不可重複'); return; }
    try {
        const r = await websiteFetch('/api/website/admin/settings', { method: 'PUT', body: { values: { [key]: list } } });
        toastOk(`已更新 ${r.updated} 項選項清單`);
    } catch (e) { toastErr(e.message); }
};

window._websiteSaveContactInfo = async () => {
    const values = {};
    document.querySelectorAll('#contact-info-card [data-contact-key]').forEach(el => {
        const key = el.dataset.contactKey;
        const orig = _contactSettings[key];
        let parsed = el.value;
        if (orig !== undefined && typeof orig !== 'string') {
            try { parsed = JSON.parse(el.value); } catch { parsed = el.value; }
        }
        if (JSON.stringify(parsed) !== JSON.stringify(orig)) values[key] = parsed;
    });
    if (!Object.keys(values).length) { toastOk('沒有變更'); return; }
    try {
        const result = await websiteFetch('/api/website/admin/settings', { method: 'PUT', body: { values } });
        toastOk(`已更新 ${result.updated} 項聯絡頁資訊`);
        Object.assign(_contactSettings, values);
    } catch (e) { toastErr(e.message); }
};

async function _reloadList() {
    const listEl = document.getElementById('inq-list');
    if (!listEl) return;
    listEl.innerHTML = '<div style="padding:20px;color:#888;">載入中…</div>';
    try {
        const qs = _filter.status ? `?status=${_filter.status}` : '';
        const data = await websiteFetch(`/api/website/admin/inquiries${qs}`);
        if (!document.getElementById('inq-list')) return;  // 使用者切換走了
        _inquiries = data?.items || [];
        const totalEl = document.getElementById('inq-total');
        if (totalEl) totalEl.textContent = `共 ${data?.total ?? _inquiries.length} 筆`;
    } catch (e) {
        const el = document.getElementById('inq-list');
        if (el) el.innerHTML = `<div style="padding:20px;color:#f87171;">${esc(e.message)}</div>`;
        return;
    }
    _renderList();
}

function _renderList() {
    const listEl = document.getElementById('inq-list');
    if (!listEl) return;
    if (!_inquiries.length) {
        listEl.innerHTML = '<div style="padding:40px;color:#888;text-align:center;">沒有詢問</div>';
        return;
    }
    listEl.innerHTML = _inquiries.map(inq => `
        <div class="inq-row" data-id="${inq.id}"
             style="padding:12px 14px;border-bottom:1px solid #333;cursor:pointer;${_selectedId === inq.id ? 'background:#252525;' : ''}"
             onclick="window._websiteSelectInquiry(${inq.id})">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                <span style="color:#fff;font-weight:500;">${esc(inq.name || '匿名')}</span>
                <span class="website-pill status-${esc(inq.status)}">${esc(inquiryStatusLabel(inq.status))}</span>
            </div>
            <div style="color:#aaa;font-size:12px;margin-bottom:3px;">${esc(inq.company || inq.email || '-')}</div>
            <div style="color:#888;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(inq.message || '')}</div>
            <div style="color:#666;font-size:10px;margin-top:3px;">${fmtRelative(inq.created_at)}</div>
        </div>
    `).join('');
}

window._websiteSelectInquiry = async (id) => {
    _selectedId = id;
    _renderList();
    await _renderDetail(id);
};

async function _renderDetail(id) {
    const detail = document.getElementById('inq-detail');
    if (!detail) return;
    detail.innerHTML = '<div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const inq = await websiteFetch(`/api/website/admin/inquiries/${id}`);
        const detailNow = document.getElementById('inq-detail');
        if (!detailNow) return;
        detailNow.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:16px;">
                <div>
                    <h3 style="color:#fff;margin:0 0 4px 0;font-size:16px;">詢問 #${inq.id}</h3>
                    <div style="color:#888;font-size:12px;">${fmtDt(inq.created_at)} · 來源 ${esc(inq.source || '-')}</div>
                </div>
                <span class="website-pill status-${esc(inq.status)}">${esc(inquiryStatusLabel(inq.status))}</span>
            </div>

            <div style="display:grid;grid-template-columns:100px 1fr;gap:6px 12px;font-size:13px;color:#ddd;margin-bottom:16px;">
                <span style="color:#888;">姓名</span><span>${esc(inq.name || '-')}</span>
                <span style="color:#888;">Email</span><span>${esc(inq.email || '-')}</span>
                <span style="color:#888;">電話</span><span>${esc(inq.phone || '-')}</span>
                <span style="color:#888;">公司</span><span>${esc(inq.company || '-')}</span>
                <span style="color:#888;">服務類型</span><span>${esc(inq.service_type || '-')}</span>
                <span style="color:#888;">預算範圍</span><span>${esc(inq.budget_range || '-')}</span>
                <span style="color:#888;">IP</span><span style="color:#666;font-family:monospace;">${esc(inq.ip_address || '-')}</span>
                ${inq.handled_at ? `<span style="color:#888;">處理時間</span><span>${fmtDt(inq.handled_at)} by ${esc(inq.handled_by || '-')}</span>` : ''}
                ${inq.converted_client_id ? `<span style="color:#888;">已轉為客戶</span><span style="color:#4ade80;">${esc(inq.converted_client_id)}</span>` : ''}
            </div>

            <div style="margin-bottom:16px;">
                <div style="color:#888;font-size:12px;margin-bottom:6px;">訊息</div>
                <div style="background:#1a1a1a;border:1px solid #333;padding:12px;border-radius:4px;white-space:pre-wrap;color:#ddd;font-size:13px;">${esc(inq.message || '')}</div>
            </div>

            <div style="margin-bottom:16px;">
                <div style="color:#888;font-size:12px;margin-bottom:6px;">備註</div>
                <textarea id="inq-notes" rows="3" style="width:100%;resize:vertical;">${esc(inq.notes || '')}</textarea>
            </div>

            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
                <select id="inq-status-select">
                    ${INQUIRY_STATUSES.map(s => `<option value="${s.value}" ${inq.status === s.value ? 'selected' : ''}>${esc(s.labelZh)}</option>`).join('')}
                </select>
                <button class="btn" onclick="window._websiteUpdateInquiry(${inq.id})">💾 儲存狀態與備註</button>
                ${!inq.converted_client_id ? `<button class="btn btn-ghost" onclick="window._websiteConvertInquiry(${inq.id})">→ 轉為 CRM 客戶</button>` : ''}
                <button class="btn btn-danger btn-sm" onclick="window._websiteDeleteInquiry(${inq.id})" style="margin-left:auto;">🗑 刪除</button>
            </div>
        `;
    } catch (e) {
        const detailNow = document.getElementById('inq-detail');
        if (detailNow) detailNow.innerHTML = `<div style="color:#f87171;padding:20px;">${esc(e.message)}</div>`;
    }
}

window._websiteUpdateInquiry = async (id) => {
    const status = document.getElementById('inq-status-select').value;
    const notes = document.getElementById('inq-notes').value;
    try {
        await websiteFetch(`/api/website/admin/inquiries/${id}`, {
            method: 'PUT',
            body: { status, notes },
        });
        toastOk('已儲存');
        await _reloadList();
        await _renderDetail(id);
    } catch (e) { toastErr(e.message); }
};

window._websiteConvertInquiry = async (id) => {
    if (!confirm('確定要轉為 CRM 客戶嗎？')) return;
    try {
        const result = await websiteFetch(`/api/website/admin/inquiries/${id}/convert`, {
            method: 'POST',
            body: {},
        });
        toastOk(`已建立 CRM 客戶：${result.client_id}`);
        await _reloadList();
        await _renderDetail(id);
    } catch (e) { toastErr(e.message); }
};

window._websiteDeleteInquiry = async (id) => {
    if (!confirm('確定要刪除此詢問？此動作無法復原。')) return;
    try {
        await websiteFetch(`/api/website/admin/inquiries/${id}`, { method: 'DELETE' });
        toastOk('已刪除');
        _selectedId = null;
        const detail = document.getElementById('inq-detail');
        if (detail) detail.innerHTML = '<div style="color:#666;padding:40px;text-align:center;">← 從左側選擇一筆詢問</div>';
        await _reloadList();
    } catch (e) { toastErr(e.message); }
};
