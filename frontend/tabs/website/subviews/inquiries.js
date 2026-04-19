/**
 * inquiries.js — 聯絡詢問收件箱
 * 列表 + 詳情面板 + 狀態切換 + 轉 CRM client + 刪除
 */
import { websiteFetch, esc, fmtDt, fmtRelative, toastOk, toastErr, renderLoadError, INQUIRY_STATUSES, inquiryStatusLabel } from '../website-utils.js';

let _inquiries = [];
let _selectedId = null;
let _filter = { status: '' };

export default async function render(container) {
    const statusOpts = INQUIRY_STATUSES.map(s => `<option value="${s.value}">${esc(s.labelZh)}</option>`).join('');
    container.innerHTML = `
        <h2>📬 聯絡詢問</h2>
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

    await _reloadList();
}

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
