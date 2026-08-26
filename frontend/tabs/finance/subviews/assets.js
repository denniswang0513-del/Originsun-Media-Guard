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
let _equip = null;     // 固定資產清單（變動少，載入抓一次即可）
let _snaps = [];

// 拍快照時，上次快照裡這些桶名**不帶入**手填欄 —— 它們已被系統自動欄取代
// （Sheet 時代的桶名 → 系統桶）：生活帳戶+公司資產(現金)→銀行現金、
// 公司資產(應收帳款)→應收帳款、財富自由(總額)→證券現值。帶入會重複計。
// 2026-08-26 帳戶/資產補齊後再收兩顆：備用金（=保險逐列+美國匯豐，已進持股）、
// 其他資產（=外幣現金 11 幣+外幣活存，已進持股）。
// ⚠ 預付帳款**留手填**：它含家用 629,897＋個人_ 各科往來 —— 家用代墊已上
// BS（自動），但儀表板沒有對應自動桶；真把它 supersede 會少掉其他科的錢。
const SUPERSEDED = new Set(['生活帳戶', '公司資產(現金)', '公司資產(應收帳款)',
                            '財富自由(總額)', '備用金(Past)', '備用金', '其他資產']);

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

// 🔴 這行必須在**任何** `_fa.xxx = ...` 之前 —— ES module 的 const 有 TDZ，
// 而那些賦值是模組求值時就跑的頂層敘述。2026-08-25 之前它待在檔案下半部，
// 整個子視圖每次點開都拋 ReferenceError、畫「子視圖載入失敗」，而三輪審查
// ＋一次瀏覽器驗收都沒抓到（驗收只開了當輪改過的子視圖）。
const _fa = (window._finAssets = window._finAssets || {});


export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    _c.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">載入資產資料…</div>';
    try {
        const [ov, sn, eq] = await Promise.all([
            finFetch('/assets/overview'),
            finFetch('/assets/snapshots'),
            finFetch('/assets/equipment'),
        ]);
        if (!_isCurrent()) return;
        _data = ov;
        _snaps = sn.snapshots || [];
        _equip = eq;
        _render();
    } catch (e) {
        _c.innerHTML = `<div style="color:#f87171;padding:40px;text-align:center;">資產資料載入失敗：${esc(e.message)}</div>`;
    }
}

/** 精準重載：只有拍快照會動到快照序列，其餘四條路只需要 overview。
 *
 *  🔴 原本五條路都呼叫 render()，那會先把容器清成「載入資產資料…」——
 *  除了白抓一份 41KB 的快照與重畫 116 點的圖，更實際的傷害是**別的持股列
 *  裡還沒按存的輸入會被一起清掉**（/simplify 2026-08-25）。
 */
_fa.reload = async ({ snaps = false } = {}) => {
    const [ov, sn] = await Promise.all([
        finFetch('/assets/overview'),
        snaps ? finFetch('/assets/snapshots') : Promise.resolve(null),
    ]);
    if (!_isCurrent()) return;
    _data = ov;
    if (sn) _snaps = sn.snapshots || [];
    _render();
};

function _card(title, inner) {
    return `<div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-bottom:16px;">
        <h3 style="color:#eee;margin:0 0 10px;font-size:14px;">${title}</h3>${inner}</div>`;
}

// 固定資產清冊（owner：「沒看到固定資產清單」）。逐件的 net 由後端走報表
// 引擎算（口徑與上面「固定資產淨值」那顆桶同一份），前端只顯示不自己算。
function _equipCard() {
    const eq = _equip;
    if (!eq || !eq.items || !eq.items.length) return '';
    const t = eq.totals;
    const rows = eq.items.map((x) => `
        <tr style="${x.counted ? '' : 'color:#555;'}">
            <td title="${esc(x.note)}">${esc(x.name)}</td>
            <td style="color:#888;">${esc(x.category)}</td>
            <td style="color:#888;white-space:nowrap;">${esc(x.purchase_date || '—')}</td>
            <td style="text-align:right;">$${fmtNum(x.cost)}</td>
            <td style="text-align:right;color:#888;">${x.months || '—'}</td>
            <td style="text-align:right;color:#888;">${x.accum != null ? '$' + fmtNum(x.accum) : '—'}</td>
            <td style="text-align:right;${x.counted ? 'color:#eee;font-weight:600;' : ''}">${x.counted ? '$' + fmtNum(x.net) : '—'}</td>
            <td>${x.counted ? esc(x.status || '在庫')
                            : `<span style="color:#777;">${esc(x.status || '除役')}</span>`}</td>
        </tr>`).join('');
    return _card(`固定資產（${t.count} 件・計入 ${t.counted} 件）`, `
        <div style="max-height:420px;overflow-y:auto;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;">
                <th>名稱</th><th>類別</th><th>建置日</th>
                <th style="text-align:right;">建構金額</th>
                <th style="text-align:right;">攤提(月)</th>
                <th style="text-align:right;">已折</th>
                <th style="text-align:right;">淨值</th><th>狀態</th></tr></thead>
            <tbody>${rows}</tbody>
        </table></div>
        <div style="display:flex;gap:20px;margin-top:8px;color:#888;font-size:12px;">
            <span>計入建構金額合計 <b style="color:#eee;">$${fmtNum(t.cost)}</b></span>
            <span>淨值合計 <b style="color:#eee;">$${fmtNum(t.net)}</b>（＝上方「固定資產淨值」）</span>
            <span style="color:#666;">截至 ${esc(eq.as_of)}；除役／未計入者灰字。編輯到側欄「器材清冊」</span>
        </div>`);
}


function _render() {
    const d = _data;
    const auto = d.buckets || {};
    const last = d.last_snapshot;
    // 「現在估計」= 系統自動桶 + 上次快照的手填桶（未被取代者）
    const manual = _manualBuckets(auto, last);
    const estTotal = Object.values(auto).reduce((a, b) => a + b, 0)
        + Object.values(manual).reduce((a, b) => a + b, 0);

    const compositionHtml = _compositionHtml(auto, manual, estTotal, d.bank_lines || []);

    _c.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-bottom:14px;">
            <div style="font-size:26px;color:#eee;font-weight:600;">$${fmtNum(estTotal)}</div>
            <div style="color:#888;font-size:12px;">現在估計（系統即時＋上次快照手填欄）
                ${last ? `｜上次快照 ${esc(last.date)}：$${fmtNum(last.total)}` : '｜尚無快照'}</div>
            <div style="flex:1;"></div>
            <button class="crm-btn crm-btn-secondary" onclick="window._finAssets.refreshQuotes(this)"
                    ${d.quotes_enabled === false ? 'disabled title="報價抓取已停用（settings my_ledger.quotes_enabled）"' : ''}>📈 更新報價</button>
            <button class="crm-btn crm-btn-primary" onclick="window._finAssets.snapOpen()">📸 拍快照</button>
        </div>
        ${_card('淨值成長（' + _snaps.length + ' 個快照）', _chartSvg())}
        ${_card('資產組成（現在）', `
            ${compositionHtml}
            <div style="color:#666;font-size:11px;margin-top:8px;">
                美元匯率 ${d.usd_twd ? Number(d.usd_twd).toFixed(3) : '—'}（更新報價時一併抓）；
                「上次快照」欄拍快照時可改。</div>`)}
        ${_card('持股（' + (d.holdings || []).length + '）', _holdingsHtml())}
        ${_equipCard()}
        <div id="fin-assets-modal"></div>`;
}

// ── 資產組成（owner 2026-08-26「增加一些比較好讀的視覺化設計」）────────
// 形式＝組成條（一條 100% 疊條看「佔比」）＋逐桶列（色塊＋等比小條看「量」）。
// 顏色跟著桶名走（固定槽位，不跟排名跑）—— 調色盤 8 槽已用 dataviz 驗證器
// 對 #202020 卡面跑過（CVD 相鄰 ΔE 8.4、對比全 ≥3:1 全過）；不在表裡的
// 未知桶一律中性灰（不偷用第 9 個色相，靠標籤識別）。
const BUCKET_COLORS = {
    '銀行現金': '#3987e5', '應收帳款': '#d95926', '固定資產淨值': '#199e70',
    '證券現值': '#c98500', '信用卡': '#d55181', '預付帳款': '#008300',
    '源日資本額': '#9085e9',
};
const _bucketColor = (name) => BUCKET_COLORS[name] || '#6b7280';

function _compositionHtml(auto, manual, total, bankLines) {
    const rows = [
        ...Object.entries(auto).map(([k, v]) => ({ k, v, tag: '系統即時' })),
        ...Object.entries(manual).map(([k, v]) => ({ k, v, tag: '上次快照' })),
    ];
    const pos = rows.filter((r) => r.v > 0);
    const maxV = Math.max(...rows.map((r) => Math.abs(r.v)), 1);
    // 100% 疊條：段寬=佔比、段色=桶色、2px 間隙；≥10% 的段直接標名（小段靠下方列表）
    const stack = pos.map((r) => {
        const pct = r.v / (total || 1) * 100;
        return `<div title="${esc(r.k)} $${fmtNum(r.v)}（${pct.toFixed(1)}%）"
                     style="flex:0 0 ${pct.toFixed(2)}%;background:${_bucketColor(r.k)};
                            display:flex;align-items:center;justify-content:center;overflow:hidden;">
            ${pct >= 10 ? `<span style="font-size:10px;color:#fff;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.55);">${esc(r.k)} ${pct.toFixed(0)}%</span>` : ''}
        </div>`;
    }).join('');
    const row = (r) => {
        const pct = total ? (r.v / total * 100) : 0;
        const barW = Math.abs(r.v) / maxV * 100;
        return `
        <div class="fa-comp-row" title="${esc(r.k)} $${fmtNum(r.v)}（${pct.toFixed(1)}%）">
            <span style="width:10px;height:10px;border-radius:2px;background:${_bucketColor(r.k)};flex:none;"></span>
            <span style="color:#ddd;flex:0 0 108px;">${esc(r.k)}</span>
            <span style="flex:1;height:6px;background:#2a2a2a;border-radius:3px;overflow:hidden;">
                <span style="display:block;height:100%;width:${barW.toFixed(1)}%;border-radius:3px;
                             background:${r.v < 0 ? '#e66767' : _bucketColor(r.k)};"></span></span>
            <span style="color:#888;flex:0 0 46px;text-align:right;font-variant-numeric:tabular-nums;">${pct.toFixed(1)}%</span>
            <span style="color:${r.v < 0 ? '#fca5a5' : '#eee'};flex:0 0 110px;text-align:right;font-variant-numeric:tabular-nums;">$${fmtNum(r.v)}</span>
            <span style="color:#666;font-size:10px;flex:0 0 56px;text-align:right;">${r.tag}</span>
        </div>
        ${r.k === '銀行現金' ? _bankSubRows(bankLines) : ''}`;
    };
    return `
        <style>
            .fa-comp-row{display:flex;align-items:center;gap:8px;padding:5px 2px;font-size:12px;border-radius:4px;}
            .fa-comp-row:hover{background:#262626;}
            .fa-bank-row{display:flex;align-items:center;gap:8px;padding:2px 2px 2px 20px;font-size:11px;color:#9ca3af;}
        </style>
        <div style="display:flex;gap:2px;height:18px;border-radius:5px;overflow:hidden;margin:2px 0 12px;">${stack}</div>
        ${rows.map(row).join('')}`;
}

function _bankSubRows(bankLines) {
    const lines = bankLines || [];
    const maxB = Math.max(...lines.map((b) => Math.abs(b.amount)), 1);
    return lines.map((b) => `
        <div class="fa-bank-row">
            <span style="flex:0 0 118px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">└ ${esc(b.name)}</span>
            <span style="flex:1;height:4px;background:#242424;border-radius:2px;overflow:hidden;">
                <span style="display:block;height:100%;width:${(Math.abs(b.amount) / maxB * 100).toFixed(1)}%;
                             border-radius:2px;background:#3987e5;opacity:.45;"></span></span>
            <span style="flex:0 0 110px;text-align:right;font-variant-numeric:tabular-nums;color:${b.amount < 0 ? '#fca5a5' : '#9ca3af'};">$${fmtNum(b.amount)}</span>
            <span style="flex:0 0 56px;"></span>
        </div>`).join('');
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
    // 每個點：大命中區（r=9 透明）＋懸浮即亮的標籤（:hover 顯示，不吃原生
    // title 的一秒延遲 —— owner 2026-08-25「滑鼠移動到每個點點都可以看到
    // 當時的數字」，2.4px 的點根本壓不準）。標籤位置夾在圖框內。
    const dots = _snaps.map((s, i) => {
        const cx = +x(ts[i]).toFixed(1), cy = +y(vs[i]).toFixed(1);
        const label = `${s.date}　$${fmtNum(s.total)}`;
        const lw = label.length * 6.4 + 12;
        const lx = Math.min(Math.max(cx - lw / 2, PL), W - 8 - lw);
        const ly = cy < 44 ? cy + 12 : cy - 30;   // 靠頂的點標籤放下面
        return `<g class="fa-dot">
            <circle cx="${cx}" cy="${cy}" r="9" fill="transparent"/>
            <circle cx="${cx}" cy="${cy}" r="2.6" fill="#3b82f6" class="fa-dot-c"/>
            <g class="fa-lbl" visibility="hidden" pointer-events="none">
                <rect x="${lx}" y="${ly}" width="${lw.toFixed(0)}" height="18" rx="4"
                      fill="#111" stroke="#3b82f6" stroke-width="0.6"/>
                <text x="${(lx + lw / 2).toFixed(0)}" y="${ly + 13}" text-anchor="middle"
                      fill="#dbeafe" font-size="11">${esc(label)}</text>
            </g></g>`;
    }).join('');
    return `<div style="overflow-x:auto;">
        <style>.fa-dot:hover .fa-lbl{visibility:visible;}
               .fa-dot:hover .fa-dot-c{r:4;fill:#93c5fd;}</style>
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:600px;">
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
_fa.refreshQuotes = async (btn) => {
    btn.disabled = true; btn.textContent = '抓報價中…';
    try {
        const r = await finFetch('/assets/quotes/refresh', { method: 'POST' });
        finToast(`已更新 ${r.updated.length} 檔`
            + (r.failed.length ? `；抓不到 ${r.failed.join('、')}（沿用舊價）` : '')
            + (r.usd_twd ? `；匯率 ${r.usd_twd}` : ''));
        _fa.reload();
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
        _fa.reload();
    } catch (e) { finToast('儲存失敗：' + e.message, 'error'); }
};

_fa.delHolding = async (id, name) => {
    if (!confirm(`刪除持股「${name}」？（不影響歷史快照）`)) return;
    try {
        await finFetch(`/assets/holdings/${id}`, { method: 'DELETE' });
        _fa.reload();
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
        _fa.reload();
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
        _fa.reload({ snaps: true });
    } catch (e) { finToast('存快照失敗：' + e.message, 'error'); }
    finally { btn.disabled = false; }
};
