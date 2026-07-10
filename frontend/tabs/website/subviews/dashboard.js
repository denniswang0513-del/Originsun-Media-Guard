/**
 * dashboard.js — 儀表板子視圖
 * 顯示月詢問統計、公開作品數、精選數、最新詢問、熱門分類
 */
import { websiteFetch, esc, fmtRelative, renderLoadError, inquiryStatusLabel, openModal, closeModal } from '../website-utils.js';

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

            <div id="watchdog-cards" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px;"></div>

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
        // 🛡️ 站點守衛 + 🔍 Google 收錄 同樣 async 載（探測 / GSC API 有延遲）
        _loadWatchdog(isCurrent);
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
        host.querySelector('[data-ga="config"]')?.addEventListener('click', _openGaConfig);
    } catch (e) {
        host.innerHTML = `<div class="card" style="border-left:3px solid #f87171;">
            <h3 style="color:#fff;margin:0 0 6px;font-size:14px;">📊 網站流量</h3>
            <div style="color:#f87171;font-size:12px;">讀取失敗：${esc(e.message || e)}</div>
            <div style="color:#888;font-size:11px;margin-top:6px;">多半是服務帳戶沒被加進 GA 資源的「檢視者」，或資源 ID 填錯。到「網站設定 › 分析追蹤」檢查。</div>
        </div>`;
    }
}

// ⚙️ 指標設定 modal — 勾選要顯示的指標 / 時間範圍 / 熱門頁面依標題或路徑
async function _openGaConfig() {
    let data;
    try { data = await websiteFetch('/api/v1/analytics/config'); }
    catch (e) { alert('讀取設定失敗：' + (e.message || e)); return; }
    const cfg = data.config, catalog = data.catalog, windows = data.windows;

    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
    ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
    const metricBoxes = catalog.map(m => `
        <label style="display:inline-flex;align-items:center;gap:5px;background:#232323;border:1px solid #3a3a3a;border-radius:6px;padding:5px 10px;font-size:12px;color:#ccc;cursor:pointer;">
            <input type="checkbox" data-gm="${esc(m.name)}" ${cfg.metrics.includes(m.name) ? 'checked' : ''}> ${esc(m.label)}
        </label>`).join('');
    const winRadios = windows.map(w => `
        <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#ccc;margin-right:12px;cursor:pointer;">
            <input type="radio" name="ga-win" value="${w}" ${cfg.window_days === w ? 'checked' : ''}> 近 ${w} 天
        </label>`).join('');
    const topByRadios = [['title', '標題'], ['path', '網址路徑']].map(([v, l]) => `
        <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#ccc;margin-right:12px;cursor:pointer;">
            <input type="radio" name="ga-topby" value="${v}" ${cfg.top_by === v ? 'checked' : ''}> ${l}
        </label>`).join('');
    const toggle = (k, label) => `
        <label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#ccc;margin-right:12px;cursor:pointer;">
            <input type="checkbox" data-gt="${k}" ${cfg[k] ? 'checked' : ''}> ${label}
        </label>`;

    ov.innerHTML = `
        <div class="card" style="width:520px;max-width:92%;max-height:88vh;overflow:auto;" onclick="event.stopPropagation()">
            <h3 style="color:#fff;margin:0 0 14px;font-size:15px;">⚙️ 網站流量顯示設定</h3>
            <div style="color:#888;font-size:11px;margin-bottom:5px;">顯示指標（今日 + 近 N 天各一格）</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;">${metricBoxes}</div>
            <div style="color:#888;font-size:11px;margin-bottom:5px;">時間範圍</div>
            <div style="margin-bottom:16px;">${winRadios}</div>
            <div style="color:#888;font-size:11px;margin-bottom:5px;">熱門頁面依</div>
            <div style="margin-bottom:16px;">${topByRadios}</div>
            <div style="color:#888;font-size:11px;margin-bottom:5px;">顯示區塊</div>
            <div style="margin-bottom:18px;">${toggle('show_realtime', '即時在線')}${toggle('show_trend', '趨勢圖')}${toggle('show_top_pages', '熱門頁面')}</div>
            <div style="display:flex;gap:8px;justify-content:flex-end;">
                <button class="btn btn-ghost btn-sm" data-x="cancel">取消</button>
                <button class="btn btn-sm" data-x="save">儲存</button>
            </div>
        </div>`;
    document.body.appendChild(ov);
    ov.querySelector('[data-x="cancel"]').addEventListener('click', () => ov.remove());
    ov.querySelector('[data-x="save"]').addEventListener('click', async () => {
        const metrics = [...ov.querySelectorAll('[data-gm]:checked')].map(el => el.dataset.gm);
        if (!metrics.length) { alert('至少選一個指標'); return; }
        const body = {
            metrics,
            window_days: parseInt(ov.querySelector('input[name="ga-win"]:checked').value),
            top_by: ov.querySelector('input[name="ga-topby"]:checked').value,
            show_realtime: ov.querySelector('[data-gt="show_realtime"]').checked,
            show_trend: ov.querySelector('[data-gt="show_trend"]').checked,
            show_top_pages: ov.querySelector('[data-gt="show_top_pages"]').checked,
        };
        try {
            await websiteFetch('/api/v1/analytics/config', { method: 'PUT', body });
            ov.remove();
            _loadTraffic(() => true);   // 重新載入流量卡套用新設定
        } catch (e) { alert('儲存失敗：' + (e.message || e)); }
    });
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

function _fmtMetric(v, kind) {
    if (kind === 'pct') return (v * 100).toFixed(1) + '%';
    if (kind === 'duration') {
        const s = Math.round(v); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    }
    return Math.round(v).toLocaleString();
}

function _renderTraffic(sum, rt) {
    const win = sum.window_days || 7;
    const boxes = [];
    if (sum._show_realtime !== false) {
        const rtPages = (rt.top_pages || []).slice(0, 3).map(p =>
            `<div style="color:#888;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">· ${esc(p.page)} (${p.users})</div>`).join('');
        boxes.push(`
            <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                <div style="color:#4ade80;font-size:11px;">🟢 即時在線</div>
                <div style="color:#fff;font-size:24px;font-weight:700;">${rt.active_users ?? 0}</div>
                ${rtPages}
            </div>`);
    }
    (sum.metrics || []).forEach(m => {
        boxes.push(`
            <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                <div style="color:#888;font-size:11px;">${esc(m.label)}<span style="color:#555;"> · 今日 / 近${win}天</span></div>
                <div style="color:#fff;font-size:22px;font-weight:700;">${_fmtMetric(m.today, m.kind)} <span style="color:#666;font-size:14px;">/ ${_fmtMetric(m.window, m.kind)}</span></div>
            </div>`);
    });
    if (sum._show_trend !== false) {
        boxes.push(`
            <div style="background:#232323;border-radius:8px;padding:10px 12px;">
                <div style="color:#888;font-size:11px;">近 ${win} 天趨勢（訪客）</div>
                <div style="margin-top:8px;">${_sparkline(sum.trend || [])}</div>
            </div>`);
    }

    let topBlock = '';
    if (sum._show_top_pages !== false) {
        const byTitle = (sum.top_by || 'title') === 'title';
        const topPages = (sum.top_pages || []).map(p => {
            const label = byTitle ? (p.title || p.path) : p.path;
            return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #2c2c2c;font-size:12px;">
                <span style="color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:78%;" title="${esc(p.path)}">${esc(label)}</span>
                <span style="color:#3b82f6;font-weight:600;">${p.views}</span>
            </div>`;
        }).join('') || `<div style="color:#888;font-size:12px;">近 ${win} 天無資料</div>`;
        topBlock = `<div style="color:#aaa;font-size:12px;margin-bottom:6px;">熱門頁面（近 ${win} 天）</div>${topPages}`;
    }

    return `
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h3 style="color:#fff;margin:0;font-size:14px;">📊 網站流量 <span style="color:#666;font-size:11px;font-weight:400;">· GA4</span></h3>
                <button class="btn btn-ghost btn-sm" data-ga="config" title="設定顯示指標">⚙️ 指標設定</button>
            </div>
            <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px;">
                ${boxes.join('')}
            </div>
            ${topBlock}
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


// ─────────────────────────────────────────────────────────────────────────
// 🛡️ 站點守衛 + 🔍 Google 收錄（Phase M watchdog）
// 兩張卡與 GA 流量一樣 async 載入（探測 / GSC API 有延遲，不擋主儀表板）。
// 後端契約 prefix：/api/website/admin/watchdog/{status,events,index,run,run-index,config}
// 安全：所有後端字串（checks[].got、event title、url、verdict…）皆用 esc() 逃逸，
//       因為它們可能含攻擊者控制內容（例：惡意重導的目標網址）。
// ─────────────────────────────────────────────────────────────────────────

const WD_BASE = '/api/website/admin/watchdog';
let _wdLast = null;   // 最近一次 status，給設定 modal 預填（status 未回傳的欄位留空）

async function _loadWatchdog(isCurrent) {
    const host = document.getElementById('watchdog-cards');
    if (!host) return;
    host.innerHTML =
        '<div class="card"><div style="color:#888;font-size:12px;">🛡️ 站點守衛載入中…</div></div>' +
        '<div class="card"><div style="color:#888;font-size:12px;">🔍 Google 收錄載入中…</div></div>';
    let st;
    try {
        st = await websiteFetch(`${WD_BASE}/status`);
    } catch {
        host.innerHTML = '';   // 端點不可用（舊版/離線）→ 靜默隱藏，不干擾儀表板
        return;
    }
    if (isCurrent && !isCurrent()) return;
    _wdLast = st;
    host.innerHTML = _renderWatchdogCard(st) + _renderGscCard(st);
    _loadWatchdogEvents();
}

// epoch 秒 → 相對時間（fmtRelative 吃 ms/ISO，這裡先 ×1000 轉 ms；先前踩過秒當毫秒→1970 年的坑）
function _relFromEpoch(sec) {
    if (!sec) return '尚未執行';
    return fmtRelative(sec * 1000);
}

function _wdLevelColor(level) {
    switch (level) {
        case 'error':
        case 'critical': return '#f87171';
        case 'warn':
        case 'warning': return '#f59e0b';
        case 'ok':
        case 'success': return '#4ade80';
        default: return '#3b82f6';
    }
}

// ── 卡片 1：🛡️ 站點守衛 ──
function _renderWatchdogCard(st) {
    const probe = st.last_probe;
    let lamp, lampColor, border;
    if (!probe) {
        lamp = '尚未執行'; lampColor = '#888'; border = '#555';
    } else if (probe.ok) {
        lamp = '正常'; lampColor = '#4ade80'; border = '#10b981';
    } else {
        lamp = '偵測到異常'; lampColor = '#f87171'; border = '#f87171';
    }

    let body;
    if (!probe) {
        body = '<div style="color:#888;font-size:12px;">尚未執行任何探測。點「立即檢查」開始。</div>';
    } else if (probe.ok) {
        const n = (probe.checks || []).length;
        body = `<div style="color:#4ade80;font-size:12px;">✓ ${n} 項檢查全部通過</div>`;
    } else {
        const fails = (probe.checks || []).filter(c => !c.ok);
        body = fails.map(c => `
            <div style="padding:6px 8px;margin-bottom:6px;background:#2a1414;border-left:2px solid #f87171;border-radius:4px;">
                <div style="color:#f87171;font-size:12px;font-weight:600;">${esc(c.name || '檢查')}</div>
                <div style="color:#aaa;font-size:11px;margin-top:2px;">預期：${esc(c.expect || '-')}</div>
                <div style="color:#f87171;font-size:11px;">實得：${esc(c.got || '-')}</div>
                ${c.detail ? `<div style="color:#888;font-size:11px;margin-top:2px;">${esc(c.detail)}</div>` : ''}
            </div>`).join('') || '<div style="color:#f87171;font-size:12px;">探測失敗</div>';
    }

    return `
        <div class="card" style="border-left:3px solid ${border};">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                <h3 style="color:#fff;margin:0;font-size:14px;">🛡️ 站點守衛</h3>
                <div style="display:flex;gap:6px;">
                    <button class="btn btn-ghost btn-sm" onclick="window._wdRun(this)">立即檢查</button>
                    <button class="btn btn-ghost btn-sm" onclick="window._wdConfig()" title="設定">⚙️</button>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span style="width:12px;height:12px;border-radius:50%;background:${lampColor};display:inline-block;box-shadow:0 0 8px ${lampColor};"></span>
                <span style="color:${lampColor};font-size:18px;font-weight:700;">${lamp}</span>
            </div>
            <div style="color:#888;font-size:11px;margin-bottom:12px;">上次檢查：${esc(_relFromEpoch(st.last_probe_at))}</div>
            ${body}
            <div style="color:#aaa;font-size:12px;margin:14px 0 6px;">最近事件</div>
            <div id="wd-events" style="color:#888;font-size:12px;">載入中…</div>
        </div>`;
}

async function _loadWatchdogEvents() {
    const host = document.getElementById('wd-events');
    if (!host) return;
    let data;
    try {
        data = await websiteFetch(`${WD_BASE}/events?limit=5`);
    } catch {
        host.innerHTML = '<div style="color:#888;font-size:11px;">事件載入失敗</div>';
        return;
    }
    const items = data.items || [];
    if (!items.length) { host.innerHTML = '<div style="color:#666;font-size:11px;">尚無事件</div>'; return; }
    host.innerHTML = items.map(ev => `
        <div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:11px;">
            <span style="width:7px;height:7px;border-radius:50%;background:${_wdLevelColor(ev.level)};flex-shrink:0;"></span>
            <span style="color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">${esc(ev.title || ev.kind || '事件')}</span>
            <span style="color:#666;flex-shrink:0;">${esc(fmtRelative(ev.created_at))}</span>
        </div>`).join('');
}

// ── 卡片 2：🔍 Google 收錄 ──
function _gscHeader(subtitle) {
    return `
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3 style="color:#fff;margin:0;font-size:14px;">🔍 Google 收錄 <span style="color:#888;font-size:11px;font-weight:400;">· ${subtitle}</span></h3>
            <div style="display:flex;gap:6px;">
                <button class="btn btn-ghost btn-sm" onclick="window._wdRunIndex(this)">立即檢查</button>
                <button class="btn btn-ghost btn-sm" onclick="window._gscConfig()" title="設定">⚙️</button>
            </div>
        </div>`;
}

function _gscGuide() {
    return `
        <div class="card" style="border-left:3px solid #f59e0b;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <h3 style="color:#fff;margin:0;font-size:14px;">🔍 Google 收錄 <span style="color:#888;font-size:11px;font-weight:400;">· 尚未設定</span></h3>
                <button class="btn btn-ghost btn-sm" onclick="window._gscConfig()" title="設定">⚙️</button>
            </div>
            <p style="color:#aaa;font-size:12px;margin:8px 0;line-height:1.7;">授權服務帳戶讀取 Search Console 後，這裡會顯示收錄涵蓋率與首頁檢索狀態。設定步驟：</p>
            <ol style="color:#bbb;font-size:12px;line-height:1.9;padding-left:20px;margin:0;">
                <li>Google Cloud Console → 啟用「<b>Google Search Console API</b>」</li>
                <li>Search Console → 設定 → <b>使用者和權限</b> → 新增使用者 → 填服務帳戶 email → 權限選「<b>完整</b>」</li>
                <li>點右上 ⚙️ 填入<b>資源網址</b>（例：<code>https://www.originsun-studio.com/</code>）</li>
            </ol>
        </div>`;
}

function _renderGscCard(st) {
    if (!st.gsc_configured) return _gscGuide();
    const s = st.index_summary;
    if (!s) {
        return `
            <div class="card" style="border-left:3px solid #f59e0b;">
                ${_gscHeader('Search Console')}
                <div style="color:#aaa;font-size:12px;margin-top:10px;">已授權，尚未執行收錄掃描。點「立即檢查」開始。</div>
            </div>`;
    }
    const total = s.total || 0;
    const indexed = s.indexed || 0;
    const pct = total ? Math.round(indexed / total * 100) : 0;
    const hp = s.homepage || {};
    const stale = (hp.days_since_crawl != null && s.stale_days != null && hp.days_since_crawl >= s.stale_days);
    const crawlColor = stale ? '#f87171' : '#888';
    const verdictBad = hp.verdict && hp.verdict !== 'PASS';

    return `
        <div class="card" style="border-left:3px solid ${verdictBad || stale ? '#f87171' : '#10b981'};">
            ${_gscHeader('Search Console')}
            <div style="color:#fff;font-size:24px;font-weight:700;margin:10px 0 6px;">已收錄 ${indexed} <span style="color:#666;font-size:16px;">/ ${total}</span></div>
            <div style="background:#2c2c2c;border-radius:4px;height:8px;overflow:hidden;margin-bottom:10px;">
                <div style="background:#10b981;height:100%;width:${pct}%;"></div>
            </div>
            ${hp.days_since_crawl != null ? `<div style="color:${crawlColor};font-size:12px;margin-bottom:6px;">首頁上次檢索：${esc(String(hp.days_since_crawl))} 天前${stale ? ' ⚠️ 疑似被爬蟲層攔截' : ''}</div>` : ''}
            ${verdictBad ? `<div style="padding:6px 8px;margin-bottom:8px;background:#2a1414;border-left:2px solid #f87171;border-radius:4px;color:#f87171;font-size:12px;">首頁收錄狀態：${esc(hp.verdict)}</div>` : ''}
            <button class="btn btn-ghost btn-sm" onclick="window._wdToggleIndexList(this)">未收錄清單（${s.not_indexed ?? 0}）▾</button>
            <div id="wd-index-list" style="display:none;margin-top:8px;"></div>
        </div>`;
}

// ── 按鈕行為（window.* 供 onclick 呼叫；每次 innerHTML 重繪後仍有效）──
// 兩顆「立即檢查」只差 endpoint
async function _wdTrigger(btn, path) {
    if (btn) { btn.disabled = true; btn.textContent = '執行中…'; }
    try {
        await websiteFetch(`${WD_BASE}${path}`, { method: 'POST' });
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = '立即檢查'; }
        alert('觸發失敗：' + (e.message || e));
        return;
    }
    setTimeout(() => _loadWatchdog(() => true), 2000);
}

window._wdRun = (btn) => _wdTrigger(btn, '/run');
window._wdRunIndex = (btn) => _wdTrigger(btn, '/run-index');

window._wdToggleIndexList = async (btn) => {
    const box = document.getElementById('wd-index-list');
    if (!box) return;
    if (box.style.display !== 'none') {
        box.style.display = 'none';
        if (btn) btn.textContent = btn.textContent.replace('▴', '▾');
        return;
    }
    box.style.display = 'block';
    if (btn) btn.textContent = btn.textContent.replace('▾', '▴');
    if (box.dataset.loaded) return;
    box.innerHTML = '<div style="color:#888;font-size:11px;">載入中…</div>';
    let data;
    try {
        data = await websiteFetch(`${WD_BASE}/index?state=not_indexed&limit=50`);
    } catch (e) {
        box.innerHTML = `<div style="color:#f87171;font-size:11px;">${esc(e.message || String(e))}</div>`;
        return;
    }
    const items = data.items || [];
    if (!items.length) {
        box.innerHTML = '<div style="color:#666;font-size:11px;">沒有未收錄的頁面 🎉</div>';
        box.dataset.loaded = '1';
        return;
    }
    box.innerHTML = items.map(it => `
        <div style="padding:4px 0;border-bottom:1px solid #2c2c2c;font-size:11px;">
            <div style="color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(it.url || '')}">${esc(it.url || '-')}</div>
            <div style="color:#888;">${esc(it.coverage_state || it.verdict || '-')}</div>
        </div>`).join('');
    box.dataset.loaded = '1';
};

// ── 設定 modal（兩張卡共用）──
// 兩邊的表單只差欄位、且都 PUT 同一個 /config 端點，所以做成資料驅動的建構器。
// fields: [{ key, label, type: 'text'|'number'|'checkbox', value, ph }]
// 文字欄留空 = 不變更該欄（避免開一次設定就把既有值清掉）；checkbox 一律送出。
const _cfgFields = {};

function _cfgModal(id, title, fields) {
    _cfgFields[id] = fields;
    const rows = fields.map(f => f.type === 'checkbox'
        ? `<label style="display:flex;align-items:center;gap:8px;color:#ccc;font-size:13px;margin-bottom:14px;cursor:pointer;">
               <input type="checkbox" id="${id}-${f.key}" ${f.value ? 'checked' : ''}> ${esc(f.label)}
           </label>`
        : `<div style="margin-bottom:14px;">
               <label style="color:#888;font-size:11px;display:block;margin-bottom:4px;">${esc(f.label)}</label>
               <input id="${id}-${f.key}" type="${f.type}" ${f.type === 'number' ? 'min="1"' : ''}
                      value="${esc(f.value == null ? '' : String(f.value))}"
                      placeholder="${esc(f.ph || '')}" style="width:100%;">
           </div>`).join('');
    const m = openModal(id, `
        <div style="padding:20px;">
            <h3 style="color:#fff;margin:0 0 14px;font-size:15px;">${esc(title)}</h3>
            ${rows}
            <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:18px;">
                <button class="btn btn-ghost btn-sm" onclick="document.getElementById('${id}').remove()">取消</button>
                <button class="btn btn-sm" onclick="window._cfgSave('${id}')">儲存</button>
            </div>
        </div>`, { width: '440px' });
    m.addEventListener('click', e => { if (e.target === m) closeModal(id); });
}

window._cfgSave = async (id) => {
    const body = {};
    for (const f of _cfgFields[id] || []) {
        const el = document.getElementById(`${id}-${f.key}`);
        if (!el) continue;
        if (f.type === 'checkbox') { body[f.key] = el.checked; continue; }
        const v = el.value.trim();
        if (!v) continue;                       // 留空 = 不變更
        body[f.key] = f.type === 'number' ? parseInt(v, 10) : v;
    }
    if (!Object.keys(body).length) { alert('沒有可儲存的變更'); return; }
    try {
        await websiteFetch(`${WD_BASE}/config`, { method: 'PUT', body });
        closeModal(id);
        _loadWatchdog(() => true);
    } catch (e) { alert('儲存失敗：' + (e.message || e)); }
};

window._wdConfig = () => {
    const st = _wdLast || {};
    _cfgModal('wd-cfg-modal', '⚙️ 站點守衛設定', [
        { key: 'enabled', label: '啟用定時探測', type: 'checkbox', value: st.enabled },
        { key: 'cron', label: '探測排程（cron）', type: 'text', value: st.cron, ph: '0 */6 * * *' },
        { key: 'alert_email', label: '警示信箱（留空 = 沿用官網通知信箱）', type: 'text',
          value: st.alert_email, ph: 'alert@example.com' },
    ]);
};

window._gscConfig = () => {
    const st = _wdLast || {};
    _cfgModal('gsc-cfg-modal', '⚙️ Google 收錄設定', [
        { key: 'gsc_site_url', label: '資源網址（需與 Search Console 完全一致）', type: 'text',
          value: st.gsc_site_url, ph: 'https://www.originsun-studio.com/' },
        { key: 'index_cron', label: '收錄掃描排程（cron）', type: 'text',
          value: st.index_cron, ph: '25 8 * * *' },
        { key: 'stale_days', label: '首頁檢索過期天數', type: 'number',
          value: st.stale_days, ph: '10' },
    ]);
};
