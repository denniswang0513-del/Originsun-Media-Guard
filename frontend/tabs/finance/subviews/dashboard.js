/**
 * dashboard.js — 📊 財務儀表板子視圖（財務管理 Tab 階段五，預設落地子視圖）
 *
 * 版面：期間選擇列（月/季/年/自訂，預設本月）→ meta 警示條 → 指標卡列
 *       （現金水位+跑道 / 當月營收+淨利 / AR 逾期 / 前 N 大客戶佔比）
 *       → 12 月損益趨勢（折線 revenue+net）→ AR 帳齡（水平佔比條）
 *       → 客戶集中度（水平佔比條）→ 專案毛利 Top/Bottom（available 時）
 *       → 🧾 稅務包匯出（依期間匯多份 BOM CSV）。
 *
 * 後端契約（凍結）：
 *   GET /api/v1/finance/dashboard?period=...    → dashboard_summary
 *     { trend:{months[],series:[{month,revenue,cost,opex,net}]},
 *       cash:{total,by_account[],avg_monthly_net,runway_months(null=充裕)},
 *       aging:{buckets:[{key,label,amount,count}](五桶),total},
 *       concentration:{clients:[{name,amount,pct}],top_n,top_n_pct,warn},
 *       project_margins:{available,top[],bottom[]},
 *       meta:{period,as_of,baseline_month,warnings[]} }
 *   GET /api/v1/finance/tax-package?period=...   → tax_package
 *     { output_vat:{rows:[{date,number,buyer,tax_id,ex_tax,tax,total,category}],total_ex_tax,total_tax,total,count},
 *       input_vat:{...同上...}, expense_by_category:{rows:[{category,amount,count}],total},
 *       labor_fees:{rows:[{payee_name,payee_id,count,total,invoice_total}],total,count},
 *       vat:{output_tax,input_tax,paid,net}, meta:{period,months[],warnings[]} }
 *
 * ⚠ 數值格式假設（與後端對齊）：金額整數；比率為「百分比數字」（38.7 = 38.7%）。
 */
import {
    finFetch, esc, fmtNum, finToast,
    renderPeriodInputs, periodFromInputs, metricCard, fmtPct, downloadManyCsv,
} from '../fin-utils.js';
import { lineChart, hbars, CHART_COLORS } from '../../../js/shared/svg-charts.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;        // 最近一次 /dashboard 回應
let _period = '';        // 最近成功送出的 period
let _periodEnd = '';     // 期間最後一個月（baseline 比對）

const _fd = (window._finDash = window._finDash || {});

// AR 帳齡各桶色（越逾期越紅）— key 對齊後端 aging_buckets 固定五桶
const AGING_COLORS = { current: '#228b22', d1_30: '#3b82f6', d31_60: '#d48a04', d61_90: '#f97316', over90: '#fca5a5' };

// 正負色：負值紅 / 非負綠（現金水位、淨利、專案毛利等共用）
function _posNegColor(v) {
    return v < 0 ? '#fca5a5' : '#86efac';
}

// ── 入口 ────────────────────────────────────────────────────
export default async function render(container, ctx = {}) {
    _c = container;
    _isCurrent = ctx.isCurrent || (() => true);
    _renderShell();
    await _fd.load();   // 預設本月自動載入
}

// ── 殼：期間列 + 結果容器 + 稅務包區塊（只畫一次，載入只換 results） ──
function _renderShell() {
    _c.innerHTML = `
        <h2 style="margin:0 0 4px;color:#eee;">📊 財務儀表板</h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">一眼看懂公司現況 — 現金水位、營收淨利、應收帳齡、客戶集中度與專案毛利。選期間按載入。</p>

        <div class="crm-toolbar" style="margin-bottom:14px;">
            <select id="findash-mode" class="crm-select">
                <option value="month" selected>月</option>
                <option value="quarter">季</option>
                <option value="year">年</option>
                <option value="custom">自訂區間</option>
            </select>
            <span id="findash-inputs" style="display:inline-flex;gap:8px;align-items:center;"></span>
            <button class="crm-btn crm-btn-primary" onclick="window._finDash.load(this)">載入</button>
        </div>

        <div id="findash-results"><div style="color:#888;padding:30px;text-align:center;">載入中…</div></div>

        <!-- 稅務包匯出（獨立於 results，隨時可用；依期間列即時取值） -->
        <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:16px;margin-top:16px;">
            <h3 style="color:#eee;margin:0 0 4px;font-size:14px;">🧾 稅務包匯出</h3>
            <p style="color:#888;font-size:12px;margin:0 0 12px;">依上方選定期間，一鍵匯出交給記帳士／報稅用的 CSV：銷項發票、進項發票、分類支出、勞報彙總，外加一張營業稅摘要。</p>
            <button class="crm-btn crm-btn-secondary" onclick="window._finDash.exportTax(this)">🧾 匯出稅務包</button>
            <div id="findash-tax-msg" style="margin-top:8px;font-size:12px;color:#888;"></div>
        </div>
    `;

    renderPeriodInputs(_c, 'findash');
    _c.querySelector('#findash-mode').addEventListener('change', () => renderPeriodInputs(_c, 'findash'));
}

// ── 載入 ────────────────────────────────────────────────────
_fd.load = async (btn) => {
    const p = periodFromInputs(_c, 'findash');
    if (!p) return;
    _period = p.period;
    _periodEnd = p.end;
    const res = _c.querySelector('#findash-results');
    res.innerHTML = '<div style="color:#888;padding:30px;text-align:center;">彙整中…</div>';
    if (btn) { btn.disabled = true; btn.textContent = '彙整中…'; }
    try {
        const data = await finFetch('/dashboard?period=' + encodeURIComponent(_period));
        if (!_isCurrent()) return;
        _data = data;
        _renderResults();
    } catch (e) {
        if (!_isCurrent()) return;
        res.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">載入失敗：${esc(e.message)}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                    onclick="window._finDash.load()">🔄 重試</button></div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '載入'; }
    }
};

function _renderResults() {
    const res = _c.querySelector('#findash-results');
    const d = _data || {};
    const meta = d.meta || {};
    const trend = d.trend || {};
    const series = trend.series || [];
    const cash = d.cash || {};
    const aging = d.aging || {};
    const conc = d.concentration || {};

    // 早於記帳基準月 → 無資料（'YYYY-MM' 字串比較）
    if (meta.baseline_month && _periodEnd && _periodEnd < meta.baseline_month) {
        res.innerHTML = _emptyBox(`此期間早於記帳基準月（${esc(meta.baseline_month)}），尚無資料`);
        return;
    }

    // 全空（尚未有任何財務資料）→ 引導文案
    const trendHas = series.some(m => (m.revenue || m.cost || m.opex || m.net));
    const hasData = (cash.total || 0) !== 0 || (aging.total || 0) !== 0
        || (conc.clients || []).length > 0 || trendHas;
    if (!hasData) {
        res.innerHTML = _emptyBox('尚未有財務資料 — 完成期初設定或匯入歷史後，這裡會自動長出圖表');
        return;
    }

    res.innerHTML = `
        ${_warningsHtml(meta.warnings)}
        ${_metricsHtml(d)}
        ${_sectionCard('📈 12 月損益趨勢', `截至 ${esc(meta.as_of || _periodEnd)}`, _trendHtml(trend, series))}
        <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:stretch;">
            <div style="flex:1 1 380px;min-width:0;">${_sectionCard('📊 應收帳齡', `合計 $${fmtNum(aging.total)}`, _agingHtml(aging))}</div>
            <div style="flex:1 1 380px;min-width:0;">${_sectionCard('👥 客戶集中度', (conc.clients || []).length ? (conc.warn ? '⚠ 集中度偏高' : '分散良好') : '', _concHtml(conc))}</div>
        </div>
        ${_sectionCard('🏆 專案毛利 Top / Bottom', '', _marginsHtml(d.project_margins || {}))}
    `;
}

// ── 空狀態盒 ────────────────────────────────────────────────
function _emptyBox(msg) {
    return `<div style="color:#888;padding:48px 20px;text-align:center;font-size:14px;line-height:1.7;">
        <div style="font-size:34px;margin-bottom:10px;">📊</div>${esc(msg)}</div>`;
}

// ── meta 警示條 ─────────────────────────────────────────────
function _warningsHtml(warns) {
    if (!warns || !warns.length) return '';
    return `
    <div style="background:#3a2a12;border:1px solid #92600f;color:#fbbf24;border-radius:6px;padding:10px 12px;margin-bottom:14px;font-size:13px;">
        ⚠ ${fmtNum(warns.length)} 項提醒
        <ul style="margin:6px 0 0;padding-left:20px;color:#d9b36a;">${warns.map(w => `<li>${esc(w)}</li>`).join('')}</ul>
    </div>`;
}

// ── 指標卡 ──────────────────────────────────────────────────
function _metricsHtml(d) {
    const cash = d.cash || {};
    const trend = d.trend || {};
    const series = trend.series || [];
    const last = series[series.length - 1] || {};
    const meta = d.meta || {};
    const aging = d.aging || {};
    const conc = d.concentration || {};

    // 現金水位 + 跑道
    const cashTotal = cash.total || 0;
    const cashColor = _posNegColor(cashTotal);
    const rw = cash.runway_months;
    const runwayTxt = (rw == null) ? '充裕' : `${(Math.round(rw * 10) / 10).toLocaleString('zh-TW')} 個月`;
    const runwayColor = (rw == null) ? '#86efac' : (rw < 3 ? '#f87171' : rw < 6 ? '#fbbf24' : '#86efac');

    // 當月營收 + 淨利（trend 最後一月 = as_of）
    const rev = last.revenue || 0, net = last.net || 0;
    const netColor = _posNegColor(net);
    const asOf = meta.as_of || last.month || _periodEnd || '';

    // AR 逾期 = d61_90 + over90
    const byKey = Object.fromEntries((aging.buckets || []).map(b => [b.key, b]));
    const overdue = ((byKey.d61_90 || {}).amount || 0) + ((byKey.over90 || {}).amount || 0);
    const overdueCnt = ((byKey.d61_90 || {}).count || 0) + ((byKey.over90 || {}).count || 0);
    // 有逾期（overdue > 0）才紅 → 對 -overdue 取正負色（overdue>0 ⇒ -overdue<0 ⇒ 紅）
    const overdueColor = _posNegColor(-overdue);

    // 前 N 大客戶佔比
    const topN = conc.top_n || 3;
    const topPct = conc.top_n_pct || 0;
    const concColor = conc.warn ? '#fca5a5' : '#86efac';

    return `
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;">
        ${metricCard('現金水位',
            `<span style="color:${cashColor};">$${fmtNum(cashTotal)}</span>`,
            `<span style="color:#888;">可撐 </span><span style="color:${runwayColor};font-weight:600;">${runwayTxt}</span>`, '180px')}
        ${metricCard('當月營收',
            `<span style="color:#eee;">$${fmtNum(rev)}</span>`,
            `<span style="color:#888;">淨利 </span><span style="color:${netColor};font-weight:600;">$${fmtNum(net)}</span><span style="color:#888;">（${esc(asOf)}）</span>`, '180px')}
        ${metricCard('AR 逾期',
            `<span style="color:${overdueColor};">$${fmtNum(overdue)}</span>`,
            `<span style="color:#888;">61 天以上 ${fmtNum(overdueCnt)} 筆</span>`, '180px')}
        ${metricCard(`前 ${fmtNum(topN)} 大客戶佔比`,
            `<span style="color:${concColor};">${fmtPct(topPct)}</span>`,
            `<span style="color:${conc.warn ? '#fca5a5' : '#888'};">${conc.warn ? '集中度偏高，注意單一客戶風險' : '客戶分散良好'}</span>`, '180px')}
    </div>`;
}

// ── 區塊卡殼 ────────────────────────────────────────────────
function _sectionCard(title, subtitle, bodyHtml) {
    return `
    <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;margin-bottom:16px;">
        <div style="padding:12px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #2a2a2a;">
            <span style="color:#eee;font-weight:700;font-size:14px;">${title}</span>
            ${subtitle ? `<span style="color:#777;font-size:11px;">${subtitle}</span>` : ''}
        </div>
        <div style="padding:14px 16px;overflow-x:auto;">${bodyHtml}</div>
    </div>`;
}

// ── 12 月損益趨勢（折線 revenue + net） ─────────────────────
function _trendHtml(trend, series) {
    if (!series.length) return '<div style="color:#888;padding:10px;">此期間無趨勢資料</div>';
    const months = (trend.months && trend.months.length) ? trend.months : series.map(s => s.month);
    const labels = months.map(m => String(m || '').slice(2));   // '2026-07' → '26-07'
    const revValues = series.map(s => s.revenue || 0);
    const netValues = series.map(s => s.net || 0);
    return lineChart(
        [
            { name: '營收', color: CHART_COLORS.blue, values: revValues },
            { name: '淨利', color: CHART_COLORS.green, values: netValues },
        ],
        { labels, width: 680, height: 240, formatValue: (v) => '$' + fmtNum(v) }
    );
}

// ── AR 帳齡（水平佔比條，五桶共用同基準） ──────────────────
function _agingHtml(aging) {
    const buckets = aging.buckets || [];
    if (!buckets.length) return '<div style="color:#888;padding:10px;">此期間無應收資料</div>';
    const items = buckets.map(b => ({
        label: b.label,
        value: b.amount || 0,
        color: AGING_COLORS[b.key] || CHART_COLORS.blue,
    }));
    return hbars(items, { width: 540, formatValue: (v) => '$' + fmtNum(v), emptyText: '此期間無應收資料' });
}

// ── 客戶集中度（水平佔比條，用後端 pct） ───────────────────
function _concHtml(conc) {
    const clients = (conc.clients || []).slice(0, 8);
    if (!clients.length) return '<div style="color:#888;padding:10px;">此期間無客戶營收資料</div>';
    const items = clients.map(c => ({ label: c.name, value: c.amount || 0, pct: c.pct }));
    return hbars(items, { width: 540, barColor: CHART_COLORS.blue, formatValue: (v) => '$' + fmtNum(v) });
}

// ── 專案毛利 Top / Bottom ───────────────────────────────────
function _marginRow(p) {
    const m = p.margin || 0;
    const color = _posNegColor(m);
    return `
    <tr style="border-top:1px solid #262626;">
        <td style="padding:5px 8px;color:#ccc;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.name || ('#' + p.project_id))}</td>
        <td style="padding:5px 8px;text-align:right;color:#aaa;white-space:nowrap;">$${fmtNum(p.revenue)}</td>
        <td style="padding:5px 8px;text-align:right;color:${color};white-space:nowrap;">$${fmtNum(m)}</td>
        <td style="padding:5px 8px;text-align:right;color:${color};white-space:nowrap;">${fmtPct(p.margin_pct)}</td>
    </tr>`;
}

function _marginTable(title, list) {
    const head = `<div style="color:#9ca3af;font-size:12px;margin-bottom:4px;">${title}</div>`;
    if (!list || !list.length) return head + '<div style="color:#666;font-size:12px;padding:6px 8px;">無資料</div>';
    return head + `
    <table style="border-collapse:collapse;font-size:12px;width:100%;">
        <thead><tr style="color:#888;text-align:left;">
            <th style="padding:4px 8px;">專案</th>
            <th style="padding:4px 8px;text-align:right;">營收</th>
            <th style="padding:4px 8px;text-align:right;">毛利</th>
            <th style="padding:4px 8px;text-align:right;">毛利率</th>
        </tr></thead>
        <tbody>${list.map(_marginRow).join('')}</tbody>
    </table>`;
}

function _marginsHtml(pm) {
    if (!pm.available) {
        return '<div style="color:#888;font-size:13px;padding:6px 2px;">專案毛利分析暫無資料 — 專案成本與營收齊備後這裡會顯示賺最多／最少的案子。</div>';
    }
    return `
    <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start;">
        <div style="flex:1;min-width:280px;">${_marginTable('🟢 毛利最高', pm.top)}</div>
        <div style="flex:1;min-width:280px;">${_marginTable('🔴 毛利最低', pm.bottom)}</div>
    </div>`;
}

// ── 稅務包匯出 ──────────────────────────────────────────────
_fd.exportTax = async (btn) => {
    const p = periodFromInputs(_c, 'findash');
    if (!p) return;
    const msg = _c.querySelector('#findash-tax-msg');
    if (btn) { btn.disabled = true; btn.textContent = '產生中…'; }
    if (msg) { msg.style.color = '#888'; msg.textContent = '向後端彙整稅務資料…'; }
    try {
        const t = await finFetch('/tax-package?period=' + encodeURIComponent(p.period));
        const files = _taxCsvFiles(t, p.period);
        await downloadManyCsv(files);
        if (msg) { msg.style.color = '#86efac'; msg.textContent = `已匯出 ${files.length} 份 CSV（若瀏覽器阻擋多重下載，請允許）`; }
        finToast(`已匯出 ${files.length} 份稅務 CSV`);
    } catch (e) {
        if (msg) { msg.style.color = '#fca5a5'; msg.textContent = '匯出失敗：' + (e.message || e); }
        finToast(e.message || '匯出失敗', true);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🧾 匯出稅務包'; }
    }
};

/** 稅務包回應 → [{name, rows}]（銷項/進項/分類支出/勞報彙總 + 營業稅摘要） */
function _taxCsvFiles(t, period) {
    t = t || {};
    return [
        { name: `銷項發票_${period}.csv`, rows: _vatCsv(t.output_vat || {}, '買受人') },
        { name: `進項發票_${period}.csv`, rows: _vatCsv(t.input_vat || {}, '賣方／廠商') },
        { name: `分類支出_${period}.csv`, rows: _expenseCsv(t.expense_by_category || {}) },
        { name: `勞報彙總_${period}.csv`, rows: _laborCsv(t.labor_fees || {}) },
        { name: `營業稅摘要_${period}.csv`, rows: _vatSummaryCsv(t.vat || {}) },
    ];
}

function _vatCsv(sec, partyLabel) {
    const rows = [['日期', '發票號碼', partyLabel, '統一編號', '未稅', '稅額', '含稅', '分類']];
    (sec.rows || []).forEach(r => rows.push([
        r.date ? String(r.date).substring(0, 10) : '', r.number || '', r.buyer || '', r.tax_id || '',
        r.ex_tax || 0, r.tax || 0, r.total || 0, r.category || '',
    ]));
    rows.push(['合計', '', '', '', sec.total_ex_tax || 0, sec.total_tax || 0, sec.total || 0, `${sec.count || 0} 筆`]);
    return rows;
}

function _expenseCsv(sec) {
    const rows = [['分類', '金額', '筆數']];
    (sec.rows || []).forEach(r => rows.push([r.category || '（未分類）', r.amount || 0, r.count || 0]));
    rows.push(['合計', sec.total || 0, '']);
    return rows;
}

function _laborCsv(sec) {
    const rows = [['受款人', '身分證字號', '筆數', '勞報金額', '發票金額']];
    (sec.rows || []).forEach(r => rows.push([
        r.payee_name || '', r.payee_id || '', r.count || 0, r.total || 0, r.invoice_total || 0,
    ]));
    rows.push(['合計', '', sec.count || 0, sec.total || 0, '']);
    return rows;
}

function _vatSummaryCsv(sec) {
    return [
        ['項目', '金額'],
        ['銷項稅額', sec.output_tax || 0],
        ['進項稅額', sec.input_tax || 0],
        ['已繳營業稅', sec.paid || 0],
        ['應繳淨額', sec.net || 0],
    ];
}

