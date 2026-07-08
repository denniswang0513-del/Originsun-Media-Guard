/**
 * dashboard.js — 儀表板子視圖
 * 顯示月詢問統計、公開作品數、精選數、最新詢問、熱門分類
 */
import { websiteFetch, esc, fmtRelative, renderLoadError, inquiryStatusLabel } from '../website-utils.js';

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    container.innerHTML = '<h2>📊 儀表板</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const stats = await websiteFetch('/api/website/admin/stats');
        if (!isCurrent()) return;
        container.innerHTML = `
            <h2>📊 儀表板</h2>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px;">
                ${_statCard('本月詢問', stats.month_inquiries, '📬', '#3b82f6')}
                ${_statCard('本月轉換', stats.month_converted, '✅', '#10b981')}
                ${_statCard('公開作品', stats.total_public_works, '🎬', '#8b5cf6')}
                ${_statCard('精選作品', stats.featured_count, '⭐', '#f59e0b')}
            </div>

            <div id="ga-traffic" style="margin-bottom:20px;"></div>

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
        // GA 流量另外 async 載（GA API 有延遲，不擋主儀表板）
        _loadTraffic(isCurrent);
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '📊 儀表板', e, '確認 NAS website-api 已啟動：uvicorn main_website:app --port 8001');
    }
}

async function _loadTraffic(isCurrent) {
    const host = document.getElementById('ga-traffic');
    if (!host) return;
    host.innerHTML = '<div class="card"><div style="color:#888;font-size:12px;">📊 網站流量載入中…</div></div>';
    let st;
    try {
        st = await websiteFetch('/api/v1/analytics/status');
    } catch {
        host.innerHTML = '';   // 端點不可用（舊版/離線）→ 靜默隱藏，不干擾儀表板
        return;
    }
    if (isCurrent && !isCurrent()) return;
    if (!st.configured) { host.innerHTML = _trafficGuide(st.reason); return; }
    try {
        const [sum, rt] = await Promise.all([
            websiteFetch('/api/v1/analytics/summary'),
            websiteFetch('/api/v1/analytics/realtime'),
        ]);
        if (isCurrent && !isCurrent()) return;
        host.innerHTML = _renderTraffic(sum, rt);
    } catch (e) {
        host.innerHTML = `<div class="card" style="border-left:3px solid #f87171;">
            <h3 style="color:#fff;margin:0 0 6px;font-size:14px;">📊 網站流量</h3>
            <div style="color:#f87171;font-size:12px;">讀取失敗：${esc(e.message || e)}</div>
            <div style="color:#888;font-size:11px;margin-top:6px;">多半是服務帳戶沒被加進 GA 資源的「檢視者」，或資源 ID 填錯。到「網站設定 › 分析追蹤」檢查。</div>
        </div>`;
    }
}

function _trafficGuide(reason) {
    return `
        <div class="card" style="border-left:3px solid #f59e0b;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:14px;">📊 網站流量 <span style="color:#888;font-size:11px;font-weight:400;">· 尚未設定</span></h3>
            <p style="color:#aaa;font-size:12px;margin:0 0 8px;line-height:1.7;">串接 GA4 就能在這裡看即時在線人數、今日訪客、熱門頁面。一次性設定（約 10 分鐘）：</p>
            <ol style="color:#bbb;font-size:12px;line-height:1.9;padding-left:20px;margin:0 0 8px;">
                <li>Google Cloud Console → 建/選專案 → 啟用「<b>Google Analytics Data API</b>」</li>
                <li>建<b>服務帳戶</b> → 建立 JSON 金鑰 → 下載</li>
                <li>GA4 後台 › 管理 › <b>資源存取管理</b> → 把服務帳戶 email 加為「<b>檢視者</b>」</li>
                <li>GA4 › 管理 › <b>資源設定</b> → 複製「<b>資源 ID</b>」（純數字）</li>
                <li>把「資源 ID」和「JSON 金鑰」貼到 <b>網站設定 › 📊 分析追蹤</b> 存檔</li>
            </ol>
            <div style="color:#666;font-size:11px;">${reason ? '目前狀態：' + esc(reason) : ''}</div>
        </div>`;
}

function _renderTraffic(sum, rt) {
    const t = sum.today || {}, w = sum.week || {};
    const spark = _sparkline(sum.trend || []);
    const topPages = (sum.top_pages || []).map(p => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #2c2c2c;font-size:12px;">
            <span style="color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:75%;">${esc(p.path)}</span>
            <span style="color:#3b82f6;font-weight:600;">${p.views}</span>
        </div>`).join('') || '<div style="color:#888;font-size:12px;">近 7 天無資料</div>';
    const rtPages = (rt.top_pages || []).slice(0, 3).map(p =>
        `<div style="color:#888;font-size:11px;">· ${esc(p.page)} (${p.users})</div>`).join('');
    return `
        <div class="card">
            <h3 style="color:#fff;margin:0 0 12px;font-size:14px;">📊 網站流量 <span style="color:#666;font-size:11px;font-weight:400;">· GA4</span></h3>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px;">
                <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                    <div style="color:#4ade80;font-size:11px;">🟢 即時在線</div>
                    <div style="color:#fff;font-size:24px;font-weight:700;">${rt.active_users ?? 0}</div>
                    ${rtPages}
                </div>
                <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                    <div style="color:#888;font-size:11px;">今日訪客 / 瀏覽</div>
                    <div style="color:#fff;font-size:24px;font-weight:700;">${t.users ?? 0} <span style="color:#666;font-size:14px;">/ ${t.views ?? 0}</span></div>
                </div>
                <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                    <div style="color:#888;font-size:11px;">近 7 天訪客 / 瀏覽</div>
                    <div style="color:#fff;font-size:24px;font-weight:700;">${w.users ?? 0} <span style="color:#666;font-size:14px;">/ ${w.views ?? 0}</span></div>
                </div>
                <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                    <div style="color:#888;font-size:11px;">近 7 天趨勢</div>
                    <div style="margin-top:8px;">${spark}</div>
                </div>
            </div>
            <div style="color:#aaa;font-size:12px;margin-bottom:6px;">熱門頁面（近 7 天）</div>
            ${topPages}
        </div>`;
}

function _sparkline(trend) {
    if (!trend.length) return '<span style="color:#666;font-size:11px;">無資料</span>';
    const vals = trend.map(d => d.users);
    const max = Math.max(1, ...vals);
    return `<div style="display:flex;align-items:flex-end;gap:3px;height:34px;">${
        vals.map(v => `<div title="${v}" style="flex:1;background:#3b82f6;border-radius:2px 2px 0 0;height:${Math.max(2, Math.round(v / max * 34))}px;"></div>`).join('')
    }</div>`;
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
            <span class="website-pill status-${esc(inq.status)}" style="float:right;">${esc(inquiryStatusLabel(inq.status, true))}</span>
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
