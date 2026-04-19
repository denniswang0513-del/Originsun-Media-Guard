/**
 * dashboard.js — 儀表板子視圖
 * 顯示月詢問統計、公開作品數、精選數、最新詢問、熱門分類
 */
import { websiteFetch, esc, fmtRelative } from '../website-utils.js';

export default async function render(container) {
    container.innerHTML = '<h2>📊 儀表板</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const stats = await websiteFetch('/api/website/admin/stats');
        container.innerHTML = `
            <h2>📊 儀表板</h2>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px;">
                ${_statCard('本月詢問', stats.month_inquiries, '📬', '#3b82f6')}
                ${_statCard('本月轉換', stats.month_converted, '✅', '#10b981')}
                ${_statCard('公開作品', stats.total_public_works, '🎬', '#8b5cf6')}
                ${_statCard('精選作品', stats.featured_count, '⭐', '#f59e0b')}
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div class="card">
                    <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">最新詢問</h3>
                    ${_renderInquiryList(stats.latest_inquiries)}
                </div>
                <div class="card">
                    <h3 style="color:#fff;margin:0 0 12px 0;font-size:14px;">熱門分類（按作品數）</h3>
                    ${_renderCategoryList(stats.top_categories)}
                </div>
            </div>
        `;
    } catch (e) {
        container.innerHTML = `
            <h2>📊 儀表板</h2>
            <div class="card" style="color:#f87171;">
                <strong>無法載入：</strong> ${esc(e.message)}
                <div style="color:#888;margin-top:8px;font-size:12px;">
                    確認 NAS website-api 服務已啟動：<code>uvicorn main_website:app --port 8001</code>
                </div>
            </div>
        `;
    }
}

function _statCard(label, val, icon, color) {
    return `
        <div class="card" style="border-left:3px solid ${color};padding:14px 16px;">
            <div style="color:#888;font-size:11px;letter-spacing:0.05em;">${icon} ${label}</div>
            <div style="color:#fff;font-size:28px;font-weight:700;margin-top:4px;">${val ?? 0}</div>
        </div>
    `;
}

function _renderInquiryList(list) {
    if (!list?.length) return '<div style="color:#888;font-size:12px;">尚無詢問</div>';
    return list.map(inq => `
        <div style="padding:8px 0;border-bottom:1px solid #333;font-size:12px;">
            <span class="website-pill status-${inq.status}" style="float:right;">${_statusLabel(inq.status)}</span>
            <div style="color:#fff;">${esc(inq.name || '匿名')} · ${esc(inq.company || '-')}</div>
            <div style="color:#888;margin-top:2px;">${esc((inq.message || '').slice(0, 50))}${inq.message?.length > 50 ? '…' : ''}</div>
            <div style="color:#666;font-size:11px;margin-top:2px;">${fmtRelative(inq.created_at)}</div>
        </div>
    `).join('');
}

function _renderCategoryList(list) {
    if (!list?.length) return '<div style="color:#888;font-size:12px;">尚無分類</div>';
    return list.map(c => `
        <div style="padding:6px 0;border-bottom:1px solid #333;font-size:12px;display:flex;justify-content:space-between;">
            <span style="color:#fff;">${esc(c.name_zh)}</span>
            <span style="color:#3b82f6;font-weight:600;">${c.project_count ?? 0}</span>
        </div>
    `).join('');
}

function _statusLabel(s) {
    return { new: '新', in_progress: '處理中', converted: '已轉換', spam: '垃圾' }[s] || s;
}
