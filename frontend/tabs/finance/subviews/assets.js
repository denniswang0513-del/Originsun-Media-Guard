/**
 * assets.js — 💎 資產儀表板（淨值快照＋持股報價；§8 階段 5，2026-08-24）。
 *
 * owner 私帳的計分板：2021/3 起的淨值成長線（Sheet 匯入 116 列＋之後系統拍的）
 * ＋快照混合制（系統欄自動：銀行現金/應收/器材淨值/證券現值；手填欄帶上次值）
 * ＋持股表（手維護、報價自動抓 —— TWSE/Yahoo，kill switch 在 settings）。
 *
 * 母公司帳打開也能用（endpoint entity 通吃）；主要使用者是 /my-ledger.html
 * 的 mine 模式。合夥人（finance_partner）看不到本子視圖（fin-nav-mine-ok）。
 */
import { finFetch, esc, fmtNum, finToast, todayStr } from '../fin-utils.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;      // overview
let _snaps = [];

// 拍快照時，上次快照裡這些桶名**不帶入**手填欄 —— 它們已被系統自動欄取代
// （Sheet 時代的桶名 → 系統桶）：生活帳戶+公司資產(現金)→銀行現金、
// 公司資產(應收帳款)→應收帳款、財富自由(總額)→證券現值。帶入會重複計。
const SUPERSEDED = new Set(['生活帳戶', '公司資產(現金)', '公司資產(應收帳款)',
                            '財富自由(總額)', '備用金(Past)']);

/** 上次快照的手填桶（排除被系統自動桶取代者）——「哪些桶帶入下一次」只有這一份規則 */
function _manualBuckets(auto, last) {
    const manual = {};
    if (last) {
        for (const [k, v] of Object.entries(last.buckets || {})) {
            if (!SUPERSEDED.has(k) && !(k in auto)) manual[k] = v;
        }
    }
    return manual;
}

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _c.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">載入資產資料…</div>';
    try {
        const [ov, sn] = await Promise.all([
            finFetch('/assets/overview'),
            finFetch('/assets/snapshots'),
        ]);
        if (!_isCurrent()) return;
        _data = ov;
        _snaps = sn.snapshots || [];
        _render();
    } catch (e) {
        _c.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">資產資料載入失敗：${esc(e.message)}</div>`;
    }
}

function _card(title, inner) {
    return `<div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
        <h3 style="color:#eee;margin:0 0 10px;font-size:14px;">${title}</h3>${inner}</div>`;
}

function _render() {
    const d = _data;
    const auto = d.buckets || {};
    const last = d.last_snapshot;
    // 「現在估計」= 系統自動桶 + 上次快照的手填桶（未被取代者）
    const manual = _manualBuckets(auto, last);
    const estTotal = Object.values(auto).reduce((a, b) => a + b, 0)
        + Object.values(manual).reduce((a, b) => a + b, 0);

    const bucketRows = (obj, tag) => Object.entries(obj).map(([k, v]) => `
        <tr><td>${esc(k)}</td>
            <td style="text-align:right;color:${v < 0 ? '#fca5a5' : '#eee'};">$${fmtNum(v)}</td>
            <td style="color:#666;font-size:11px;">${tag}</td></tr>`).join('');

    _c.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:14px;">
            <div style="font-size:26px;color:#eee;font-weight:600;">$${fmtNum(estTotal)}</div>
            <div style="color:#888;font-size:12px;">現在估計（系統即時＋上次快照手填欄）
                ${last ? `｜上次快照 ${esc(last.date)}：$${fmtNum(last.total)}` : '｜尚無快照'}</div>
            <div style="flex:1;"></div>
            <button class="crm-btn crm-btn-secondary" onclick="window._finAssets.refreshQuotes(this)">📈 更新報價</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finAssets.snapOpen()">📸 拍快照</button>
        </div>
        ${_card('淨值成長（' + _snaps.length + ' 個快照）', _chartSvg())}
        ${_card('資產組成（現在）', `
            <table class="crm-table" style="width:100%;font-size:13px;">
                <tbody>
                    ${bucketRows(auto, '系統即時')}
                    ${bucketRows(manual, '上次快照')}
                </tbody></table>
            <div style="color:#666;font-size:11px;margin-top:6px;">
                美元匯率 ${d.usd_twd ? Number(d.usd_twd).toFixed(3) : '—'}（更新報價時一併抓）；
                「上次快照」欄拍快照時可改。</div>`)}
        ${_card('持股（' + (d.holdings || []).length + '）', _holdingsHtml())}
        <div id="fin-assets-modal"></div>`;
}

// ── 淨值成長線（手刻 SVG）────────────────────────────────
// 沒用共用 js/shared/svg-charts.lineChart：那支的 x 軸是等距索引，
// 快照從 2021 週更漸疏到年 5 筆 —— 等距畫會把五年壓縮成假斜率。
// 時間比例 x 軸若日後共用層支援了，再換過去。
function _chartSvg() {
    if (_snaps.length < 2) return '<div style="color:#888;font-size:12px;">快照不足兩筆，還畫不出線。</div>';
    const W = 860, H = 220, PL = 66, PB = 22, PT = 8;
    const ts = _snaps.map((s) => new Date(s.date).getTime());
    const vs = _snaps.map((s) => s.total);
    const t0 = Math.min(...ts), t1 = Math.max(...ts);
    const vMax = Math.max(...vs), vMin = Math.min(0, ...vs);
    const x = (t) => PL + (W - PL - 8) * (t - t0) / (t1 - t0 || 1);
    const y = (v) => PT + (H - PT - PB) * (1 - (v - vMin) / (vMax - vMin || 1));
    const pts = _snaps.map((s, i) => `${x(ts[i]).toFixed(1)},${y(vs[i]).toFixed(1)}`).join(' ');
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
        const v = vMin + (vMax - vMin) * f;
        return `<text x="${PL - 6}" y="${y(v) + 4}" text-anchor="end" fill="#666" font-size="10">${(v / 1e6).toFixed(0)}M</text>
                <line x1="${PL}" x2="${W - 8}" y1="${y(v)}" y2="${y(v)}" stroke="#2a2a2a" stroke-width="1"/>`;
    }).join('');
    const years = [];
    for (let yr = new Date(t0).getFullYear() + 1; yr <= new Date(t1).getFullYear(); yr++) {
        const t = new Date(`${yr}-01-01`).getTime();
        years.push(`<text x="${x(t)}" y="${H - 6}" text-anchor="middle" fill="#666" font-size="10">${yr}</text>`);
    }
    const dots = _snaps.map((s, i) =>
        `<circle cx="${x(ts[i]).toFixed(1)}" cy="${y(vs[i]).toFixed(1)}" r="2.4" fill="#3b82f6">
            <title>${esc(s.date)}　$${fmtNum(s.total)}</title></circle>`).join('');
    return `<div style="overflow-x:auto;"><svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:600px;">
        ${yTicks}${years.join('')}
        <polyline points="${pts}" fill="none" stroke="#3b82f6" stroke-width="1.6"/>
        ${dots}</svg></div>`;
}

// ── 持股表 ─────────────────────────────────────────────────
function _holdingsHtml() {
    const rows = (_data.holdings || []).map((h) => `
        <tr data-id="${h.id}">
            <td>${esc(h.name)}<div style="color:#666;font-size:10px;">${esc(h.broker)}${h.symbol ? '｜' + esc(h.symbol) : ''}</div></td>
            <td><input class="crm-input fa-shares" style="width:90px;" value="${h.shares ?? ''}" placeholder="股數"></td>
            <td style="text-align:right;">${h.last_price ? fmtNum(h.last_price) + `<div style="color:#666;font-size:10px;">${esc(h.price_at || '')}</div>` : '<span style="color:#666;">—</span>'}</td>
            <td><input class="crm-input fa-manual" style="width:110px;" value="${h.manual_value ?? ''}" placeholder="手動現值"></td>
            <td style="text-align:right;color:#eee;">$${fmtNum(h.value_twd)}</td>
            <td style="white-space:nowrap;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finAssets.saveHolding('${h.id}', this)">存</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finAssets.delHolding('${h.id}', ${JSON.stringify(h.name).replace(/"/g, '&quot;')})">刪</button>
            </td>
        </tr>`).join('');
    return `<table class="crm-table" style="width:100%;font-size:12px;">
        <thead><tr><th>名稱</th><th>股數</th><th style="text-align:right;">單價（原幣）</th>
            <th>手動現值(TWD)</th><th style="text-align:right;">現值(TWD)</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>
        <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;">
            <input class="crm-input" id="fa-new-name" placeholder="名稱" style="width:150px;">
            <input class="crm-input" id="fa-new-broker" placeholder="券商" style="width:100px;">
            <input class="crm-input" id="fa-new-shares" placeholder="股數" style="width:80px;">
            <select class="crm-select" id="fa-new-cur"><option>TWD</option><option>USD</option></select>
            <input class="crm-input" id="fa-new-qs" placeholder="報價源 tse:0050 / yahoo:VTI（空=手動）" style="width:230px;">
            <button class="crm-btn crm-btn-secondary" onclick="window._finAssets.addHolding(this)">＋ 新增持股</button>
        </div>
        <div style="color:#666;font-size:11px;margin-top:4px;">
            報價源格式：台股 <code>tse:0050</code>、美股/ETF <code>yahoo:VTI</code>（LSE 如 <code>yahoo:VWRA.L</code>）；
            留空＝手動列（填「手動現值」，適合整戶合計/保險）。</div>`;
}

// ── 動作 ─────────────────────────────────────────────────
const _fa = (window._finAssets = window._finAssets || {});

_fa.refreshQuotes = async (btn) => {
    btn.disabled = true; btn.textContent = '抓報價中…';
    try {
        const r = await finFetch('/assets/quotes/refresh', { method: 'POST' });
        finToast(`已更新 ${r.updated.length} 檔`
            + (r.failed.length ? `；抓不到 ${r.failed.join('、')}（沿用舊價）` : '')
            + (r.usd_twd ? `；匯率 ${r.usd_twd}` : ''));
        render(_c, { isCurrent: _isCurrent });
    } catch (e) { finToast('報價更新失敗：' + e.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = '📈 更新報價'; }
};

_fa.saveHolding = async (id, btn) => {
    const tr = btn.closest('tr');
    const shares = tr.querySelector('.fa-shares').value.trim();
    const manual = tr.querySelector('.fa-manual').value.trim();
    const h = (_data.holdings || []).find((x) => x.id === id) || {};
    try {
        await finFetch(`/assets/holdings/${id}`, {
            method: 'PUT',
            body: JSON.stringify({
                name: h.name,
                shares: shares === '' ? null : Number(shares),
                manual_value: manual === '' ? null : Math.round(Number(manual)),
            }),
        });
        finToast('已儲存');
        render(_c, { isCurrent: _isCurrent });
    } catch (e) { finToast('儲存失敗：' + e.message, 'error'); }
};

_fa.delHolding = async (id, name) => {
    if (!confirm(`刪除持股「${name}」？（不影響歷史快照）`)) return;
    try {
        await finFetch(`/assets/holdings/${id}`, { method: 'DELETE' });
        render(_c, { isCurrent: _isCurrent });
    } catch (e) { finToast('刪除失敗：' + e.message, 'error'); }
};

_fa.addHolding = async (btn) => {
    const name = document.getElementById('fa-new-name').value.trim();
    if (!name) return finToast('名稱必填');
    btn.disabled = true;
    try {
        await finFetch('/assets/holdings', {
            method: 'POST',
            body: JSON.stringify({
                name,
                broker: document.getElementById('fa-new-broker').value.trim(),
                shares: Number(document.getElementById('fa-new-shares').value) || null,
                currency: document.getElementById('fa-new-cur').value,
                quote_symbol: document.getElementById('fa-new-qs').value.trim(),
            }),
        });
        render(_c, { isCurrent: _isCurrent });
    } catch (e) { finToast('新增失敗：' + e.message, 'error'); }
    finally { btn.disabled = false; }
};

// ── 拍快照 ─────────────────────────────────────────────────
_fa.snapOpen = () => {
    const auto = _data.buckets || {};
    const manual = _manualBuckets(auto, _data.last_snapshot);
    const autoRows = Object.entries(auto).map(([k, v]) => `
        <tr><td>${esc(k)}</td><td style="text-align:right;color:#86efac;">$${fmtNum(v)}</td>
            <td style="color:#666;font-size:11px;">自動</td></tr>`).join('');
    const manualRows = Object.entries(manual).map(([k, v]) => `
        <tr class="fa-snap-manual"><td><input class="crm-input fa-mk" value="${esc(k)}" style="width:150px;"></td>
            <td><input class="crm-input fa-mv" value="${v}" style="width:130px;text-align:right;"></td>
            <td><button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="this.closest('tr').remove()">✕</button></td></tr>`).join('');
    document.getElementById('fin-assets-modal').innerHTML = `
        <div style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1000;display:flex;align-items:center;justify-content:center;"
             onclick="if(event.target===this)this.remove()">
            <div style="background:#202020;border:1px solid #333;border-radius:8px;padding:20px;width:560px;max-height:85vh;overflow:auto;">
                <h3 style="color:#eee;margin:0 0 10px;font-size:15px;">📸 拍快照</h3>
                <div class="crm-field"><label>快照日期</label>
                    <input type="date" id="fa-snap-date" class="crm-input" value="${todayStr()}"></div>
                <table class="crm-table" style="width:100%;font-size:12px;">
                    <thead><tr><th>資產桶</th><th style="text-align:right;">金額</th><th></th></tr></thead>
                    <tbody id="fa-snap-rows">${autoRows}${manualRows}</tbody></table>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-top:6px;"
                        onclick="window._finAssets.snapAddRow()">＋ 加一桶</button>
                <div style="color:#666;font-size:11px;margin:8px 0;">
                    綠色＝系統即時算的（銀行/應收/器材/持股）；其餘手填、預帶上次快照的值。
                    負數（信用卡欠款）直接填負的。合計由後端加總。</div>
                <div style="display:flex;gap:8px;justify-content:flex-end;">
                    <button class="crm-btn crm-btn-secondary" onclick="document.getElementById('fin-assets-modal').innerHTML=''">取消</button>
                    <button class="crm-btn crm-btn-primary" onclick="window._finAssets.snapSave(this)">存快照</button>
                </div>
            </div></div>`;
};

_fa.snapAddRow = () => {
    const tb = document.getElementById('fa-snap-rows');
    const tr = document.createElement('tr');
    tr.className = 'fa-snap-manual';
    tr.innerHTML = `<td><input class="crm-input fa-mk" placeholder="桶名" style="width:150px;"></td>
        <td><input class="crm-input fa-mv" placeholder="金額" style="width:130px;text-align:right;"></td>
        <td><button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="this.closest('tr').remove()">✕</button></td>`;
    tb.appendChild(tr);
};

_fa.snapSave = async (btn) => {
    const buckets = { ...(_data.buckets || {}) };   // 自動桶照當下值入快照
    document.querySelectorAll('.fa-snap-manual').forEach((tr) => {
        const k = tr.querySelector('.fa-mk').value.trim();
        const v = tr.querySelector('.fa-mv').value.trim();
        if (k && v !== '' && !Number.isNaN(Number(v))) buckets[k] = Math.round(Number(v));
    });
    btn.disabled = true;
    try {
        const r = await finFetch('/assets/snapshots', {
            method: 'POST',
            body: JSON.stringify({
                snap_date: document.getElementById('fa-snap-date').value,
                buckets,
            }),
        });
        finToast(`快照已存：$${fmtNum(r.total)}`);
        document.getElementById('fin-assets-modal').innerHTML = '';
        render(_c, { isCurrent: _isCurrent });
    } catch (e) { finToast('存快照失敗：' + e.message, 'error'); }
    finally { btn.disabled = false; }
};
