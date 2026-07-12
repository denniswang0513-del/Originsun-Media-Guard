/**
 * statements.js — 📑 財務三表子視圖（財務管理 Tab 階段三）
 *
 * 版面：期間選擇列（月/季/年/自訂）→ meta 提示（warnings 黃條 + 鎖帳 badge）
 *       → 指標卡列 → 白話解讀 → 三表（損益/資產負債/現金流量，各自 <details> 可收合
 *       + ⬇ CSV 匯出）。可下鑽行掛 data-drill → 事件委派開 crm-modal 明細列表。
 * 三表 HTML 與 CSV 統一走 row model（_pnlRows/_bsRows/_cfRows 產同一份 rows，
 * 兩種 render）。drill kind 一律讀後端報表行的 drill 欄位（單一來源
 * core/finance_logic.py VALID_DRILL_KINDS），前端不自拼 kind 字串。
 *
 * 後端契約：GET /api/v1/finance/statements?period=...
 *   period：'2026-06'（月）| '2026-Q2'（季）| '2026'（年）| '2025-07..2026-06'（自訂）
 * Drilldown：GET /statements/drilldown?kind=<key>&period=... → {items:[{date,label,amount}], total}
 *
 * ⚠ 數值格式假設（與後端對齊）：rate / pct / debt_ratio 為「百分比數字」
 *   （35.2 = 35.2%）；current_ratio 為倍數（1.85 = 1.85 倍）。
 */
import {
    finFetch, esc, fmtNum, finToast,
    renderPeriodInputs, periodFromInputs, metricCard, fmtPct, downloadCsv,
} from '../fin-utils.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;        // 最近一次 /statements 回應
let _period = '';        // 最近一次成功送出的 period 字串
let _periodEnd = '';     // 期間最後一個月（'YYYY-MM'，baseline 比對用）
let _drillSeq = 0;       // drilldown 競態護欄

const _fs = (window._finStmt = window._finStmt || {});

// ── 關鍵行白話 tooltip（title 屬性） ─────────────────────────
const TIP = {
    revenue: '金額為未稅——發票開 105,000（含 5% 營業稅）時這裡記 100,000；那 5,000 是代政府向客戶收的營業稅，不是公司的收入',
    collected: '這段期間認列的收入中，客戶已實際付款入帳的部分',
    receivable: '發票已開（收入已認列）但客戶還沒付的錢——賺到了、現金還沒進來，點擊可看是哪幾筆',
    cashIncome: '未開發票、直接以現金／匯款入帳的收入',
    cost: '直接歸屬到案子的成本——外包採購（料）、人力（工）、拍攝雜支（費）',
    gross: '營收扣掉直接製作成本後剩下的錢——還沒扣房租、行政人事等日常開銷',
    opex: '不直接歸屬單一案子的日常開銷（房租、行政人事、軟體訂閱等），分銷售／管理／研發三類',
    nonOp: '與本業無關的收支——利息、補助款、匯差、處分資產等',
    incomeTax: '營利事業所得稅——按稅前淨利估算或實繳金額',
    vat: '營業稅是代收代付：銷項稅（開發票時幫政府收）− 進項稅（付款拿發票時先墊）= 應繳淨額；不影響損益，只影響現金',
    payable: '已收到請款單／帳單但還沒付出去的錢（外包費、廠商款等）',
    cash: '所有銀行帳戶＋零用金的現金合計',
    openingCash: '期間開始時所有帳戶（銀行＋零用金）的現金水位合計',
    operatingCf: '本業經營帶來的現金增減——實際收到客戶的錢、實際付掉的成本費用',
    currentRatio: '流動資產 ÷ 流動負債——短期償債能力，>1 代表流動資產足以支付短期負債，>1.5 較安心',
    debtRatio: '總負債 ÷ 總資產——公司資產中借來的比例，越低越穩',
};

// ── 入口 ────────────────────────────────────────────────────
export default async function render(container, ctx = {}) {
    _c = container;
    _isCurrent = ctx.isCurrent || (() => true);
    _renderShell();
    await _fs.load();   // 預設當月自動載入
}

// ── 殼：期間列 + 結果容器 + drill modal（只畫一次，載入只換 results） ──
function _renderShell() {
    _c.innerHTML = `
        <h2 style="margin:0 0 4px;color:#eee;">📑 財務三表</h2>
        <p style="color:#888;font-size:12px;margin:0 0 14px;">損益表（賺不賺）＋資產負債表（家底）＋現金流量表（錢的進出）——選期間按載入，可下鑽行點擊看明細。</p>

        <div class="crm-toolbar" style="margin-bottom:14px;">
            <select id="finstmt-mode" class="crm-select">
                <option value="month" selected>月</option>
                <option value="quarter">季</option>
                <option value="year">年</option>
                <option value="custom">自訂區間</option>
            </select>
            <span id="finstmt-inputs" style="display:inline-flex;gap:8px;align-items:center;"></span>
            <button class="crm-btn crm-btn-primary" onclick="window._finStmt.load(this)">載入</button>
        </div>

        <div id="finstmt-results"><div style="color:#888;padding:30px;text-align:center;">載入中…</div></div>

        <!-- Drilldown Modal -->
        <div id="finstmt-drill-modal" class="crm-modal-overlay" style="display:none;">
            <div class="crm-modal" style="max-width:640px;">
                <div class="crm-modal-header">
                    <h3 id="finstmt-drill-title">明細</h3>
                    <button class="crm-detail-close" onclick="window._finStmt.closeDrill()">&#x2715;</button>
                </div>
                <div class="crm-modal-body" id="finstmt-drill-body"></div>
                <div class="crm-modal-footer">
                    <button class="crm-btn crm-btn-secondary" onclick="window._finStmt.closeDrill()">關閉</button>
                </div>
            </div>
        </div>

        <style>
            #finstmt-results [data-drill] { cursor: pointer; }
            #finstmt-results [data-drill]:hover { background: #2f3a4d !important; }
            #finstmt-results details > summary { list-style: none; }
            #finstmt-results details > summary::-webkit-details-marker { display: none; }
        </style>
    `;

    renderPeriodInputs(_c, 'finstmt');
    _c.querySelector('#finstmt-mode').addEventListener('change', () => renderPeriodInputs(_c, 'finstmt'));

    // 可下鑽行：事件委派（results innerHTML 會整塊換掉，掛在容器上才不會掉）
    _c.querySelector('#finstmt-results').addEventListener('click', (e) => {
        const el = e.target.closest('[data-drill]');
        if (el) _openDrill(el.dataset.drill, el.dataset.drillLabel || '');
    });

    // 點 overlay 空白處關閉 modal
    const modal = _c.querySelector('#finstmt-drill-modal');
    modal.addEventListener('click', (e) => { if (e.target === modal) _fs.closeDrill(); });
}

// ── 載入 ────────────────────────────────────────────────────
_fs.load = async (btn) => {
    const p = periodFromInputs(_c, 'finstmt');
    if (!p) return;
    _period = p.period;
    _periodEnd = p.end;
    const res = _c.querySelector('#finstmt-results');
    res.innerHTML = '<div style="color:#888;padding:30px;text-align:center;">產表中…</div>';
    if (btn) { btn.disabled = true; btn.textContent = '產表中…'; }
    try {
        const data = await finFetch('/statements?period=' + encodeURIComponent(_period));
        if (!_isCurrent()) return;
        _data = data;
        _renderResults();
    } catch (e) {
        if (!_isCurrent()) return;
        res.innerHTML = `<div style="color:#f87171;padding:30px;text-align:center;">產表失敗：${esc(e.message)}
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;"
                    onclick="window._finStmt.load()">🔄 重試</button></div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '載入'; }
    }
};

function _renderResults() {
    const res = _c.querySelector('#finstmt-results');
    const meta = _data.meta || {};

    // 期間早於記帳基準月 → 無資料（'YYYY-MM' 字串比較即可）
    if (meta.baseline_month && _periodEnd && _periodEnd < meta.baseline_month) {
        res.innerHTML = `<div style="color:#888;padding:40px;text-align:center;">
            此期間早於記帳基準月（${esc(meta.baseline_month)}），無資料</div>`;
        return;
    }
    if (!_data.pnl || !_data.bs || !_data.cf) {
        res.innerHTML = '<div style="color:#888;padding:40px;text-align:center;">此期間無資料</div>';
        return;
    }

    res.innerHTML = `
        ${_metaBarsHtml(meta)}
        ${_metricsHtml(_data)}
        ${_interpHtml(_data.interpretation)}
        ${_stmtCard('pnl', '📃 損益表', `期間：${esc(_period)}`, _pnlHtml(_data.pnl))}
        ${_stmtCard('bs', '🏛️ 資產負債表', `截至 ${esc(_data.bs.as_of || _periodEnd)}`, _bsHtml(_data.bs))}
        ${_stmtCard('cf', '💵 現金流量表', `期間：${esc(_period)}`, _cfHtml(_data.cf))}
    `;
}

// ── meta 提示（warnings 黃條 + 鎖帳 badge） ─────────────────
function _metaBarsHtml(meta) {
    let html = '';
    const warns = meta.warnings || [];
    if (warns.length) {
        html += `
        <div style="background:#3a2a12;border:1px solid #92600f;color:#fbbf24;border-radius:6px;padding:10px 12px;margin-bottom:12px;font-size:13px;">
            ⚠ 有 ${fmtNum(warns.length)} 筆未歸類／提醒 — 去「⚙️ 科目與設定」處理，數字才完整
            <ul style="margin:6px 0 0;padding-left:20px;color:#d9b36a;">${warns.map(w => `<li>${esc(w)}</li>`).join('')}</ul>
        </div>`;
    }
    const locked = meta.locked_months || [];
    if (locked.length) {
        html += `
        <div style="margin-bottom:12px;">
            <span style="font-size:12px;background:#1e3a5f;color:#93c5fd;border-radius:10px;padding:3px 10px;"
                  title="鎖帳月份的收支已月結凍結，不會再被新增／修改影響">🔒 含已鎖帳月份（${locked.map(esc).join('、')}），數字已凍結</span>
        </div>`;
    }
    return html;
}

// ── 指標卡列 ────────────────────────────────────────────────
function _metricsHtml(d) {
    const p = d.pnl;
    const rev = p.revenue || {};
    const r = (d.bs && d.bs.ratios) || {};
    const labels = r.labels || {};
    const cr = r.current_ratio, dr = r.debt_ratio;
    const ma = p.monthly_avg || {};
    const spend = (ma.cost || 0) + (ma.opex || 0);
    const net = (p.net && p.net.amount) || 0;
    const netColor = net < 0 ? '#fca5a5' : '#86efac';
    return `
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;">
        ${metricCard('營業收入', `<span style="color:#eee;" title="${esc(TIP.revenue)}">$${fmtNum(rev.total)}</span>`, '<span style="color:#888;">未稅</span>')}
        ${metricCard('稅後淨利', `<span style="color:${netColor};">$${fmtNum(net)}</span>`, `<span style="color:#888;">淨利率 ${fmtPct(p.net && p.net.rate)}</span>`)}
        ${metricCard('流動比率', `<span style="color:${_crColor(cr)};" title="${esc(TIP.currentRatio)}">${_fmtRatio(cr)}</span>`, `<span style="color:${_crColor(cr)};">${esc(labels.current_ratio || '')}</span>`)}
        ${metricCard('負債比率', `<span style="color:${_drColor(dr)};" title="${esc(TIP.debtRatio)}">${fmtPct(dr)}</span>`, `<span style="color:${_drColor(dr)};">${esc(labels.debt_ratio || '')}</span>`)}
        ${metricCard('月均收入', `<span style="color:#eee;">$${fmtNum(ma.revenue)}</span>`, '<span style="color:#888;">期間平均</span>')}
        ${metricCard('月均開銷', `<span style="color:#eee;">$${fmtNum(spend)}</span>`, `<span style="color:#888;">成本 $${fmtNum(ma.cost)}＋費用 $${fmtNum(ma.opex)}</span>`)}
    </div>`;
}

// ── 白話解讀 ────────────────────────────────────────────────
function _interpHtml(list) {
    if (!list || !list.length) return '';
    return `
    <div style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;padding:12px 16px;margin-bottom:16px;">
        <div style="color:#9ca3af;font-size:12px;margin-bottom:6px;">白話解讀</div>
        ${list.map(s => `<div style="color:#ccc;font-size:13px;padding:3px 0;">💡 ${esc(s)}</div>`).join('')}
    </div>`;
}

// ── 三表卡殼（<details> 可收合 + CSV 鈕） ───────────────────
function _stmtCard(id, title, subtitle, bodyHtml) {
    return `
    <details open style="background:#202020;border:1px solid #2e2e2e;border-radius:8px;margin-bottom:16px;">
        <summary style="cursor:pointer;padding:12px 16px;display:flex;align-items:center;gap:10px;">
            <span style="color:#eee;font-weight:700;font-size:14px;">${title}</span>
            <span style="color:#777;font-size:11px;">${subtitle}</span>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:auto;"
                    onclick="event.preventDefault();event.stopPropagation();window._finStmt.exportCsv('${id}')">⬇ CSV</button>
        </summary>
        <div style="padding:0 16px 16px;overflow-x:auto;">${bodyHtml}</div>
    </details>`;
}

// ── 表格小工具 ──────────────────────────────────────────────
/** current_ratio 為倍數 */
function _fmtRatio(v) {
    if (v == null || isNaN(v)) return '—';
    return (Math.round(v * 100) / 100).toLocaleString('zh-TW');
}

/**
 * 比率顏色分級 — 門檻對齊後端 labels 文案生成處（core/finance_logic.py
 * build_balance_sheet）：流動比率 ≥2 充足／1–2 尚可／<1 不足；負債比率
 * <65 綠／65–80 橘（後端再細分 65–75 良好、75–80 偏高）／>80 紅（需要增資）。
 * 用數字門檻而非解析 label 字串——文案措辭可改，門檻改動時兩邊要一起動。
 */
function _crColor(cr) {
    if (cr == null || isNaN(cr)) return '#888';
    return cr >= 2 ? '#86efac' : cr >= 1 ? '#fbbf24' : '#f87171';
}

function _drColor(dr) {
    if (dr == null || isNaN(dr)) return '#888';
    return dr < 65 ? '#86efac' : dr <= 80 ? '#fbbf24' : '#f87171';
}

function _amt(n) {
    const v = n || 0;
    return `<span style="color:${v < 0 ? '#fca5a5' : '#ddd'};">$${fmtNum(v)}</span>`;
}

function _sum(lines) {
    return (lines || []).reduce((s, l) => s + (l.amount || 0), 0);
}

/**
 * 通用表列：label + 右對齊金額。
 * row：indent 縮排層級、bold 加粗、groupBg 深底（group 行）、
 *      drill 下鑽 kind（來自後端 drill 欄位）、tip title 白話說明、
 *      note label 後灰色備註。可直接吃 row model 物件（多的 key 不理）。
 */
function _trow(label, amountHtml, { indent = 0, bold = false, groupBg = false, drill = null, tip = null, note = '' } = {}) {
    const drillAttr = drill ? ` data-drill="${esc(drill)}" data-drill-label="${esc(label)}"` : '';
    const tipHtml = tip ? ` <span title="${esc(tip)}" style="color:#667;cursor:help;font-size:11px;">ⓘ</span>` : '';
    const noteHtml = note ? `<span style="color:#777;font-weight:400;font-size:11px;margin-left:8px;">${esc(note)}</span>` : '';
    return `
    <tr style="border-top:1px solid #262626;${groupBg ? 'background:#26282e;' : ''}${bold ? 'font-weight:700;color:#eee;' : 'color:#ccc;'}"${drillAttr}>
        <td style="padding:6px 10px 6px ${10 + indent * 18}px;">${esc(label)}${tipHtml}${noteHtml}</td>
        <td style="padding:6px 10px;text-align:right;white-space:nowrap;">${amountHtml}</td>
    </tr>`;
}

// ── 損益表 row model（HTML 與 CSV 共用同一份 rows） ─────────
// row：{label, amount, indent, bold, groupBg, note, drill, tip, noAmount}
// drill 只在後端行帶 drill 欄位時掛（by_collection 小列/明細行後端不給 → 不掛）。
function _pnlRows(p) {
    const rows = [];
    const rev = p.revenue || {};
    const bc = rev.by_collection || {};
    rows.push({ label: '營業收入', amount: rev.total, bold: true, groupBg: true, drill: rev.drill, tip: TIP.revenue, note: '未稅' });
    (rev.lines || []).forEach(l => rows.push({ label: l.label, amount: l.amount, indent: 1, drill: l.drill }));
    rows.push({ label: '收款拆分｜已收', amount: bc.collected, indent: 1, tip: TIP.collected });
    rows.push({ label: '收款拆分｜應收', amount: bc.receivable, indent: 1, tip: TIP.receivable });
    rows.push({ label: '收款拆分｜現金收款', amount: bc.cash, indent: 1, tip: TIP.cashIncome });

    const cost = p.cost || {};
    rows.push({ label: '營業成本', amount: cost.total, bold: true, groupBg: true, tip: TIP.cost });
    (cost.groups || []).forEach(g => {
        rows.push({ label: `${g.label}（小計）`, amount: g.total, indent: 1, groupBg: true, drill: g.drill });
        (g.lines || []).forEach(l => rows.push({ label: l.label, amount: l.amount, indent: 2 }));
    });

    const gross = p.gross || {};
    rows.push({
        label: '營業毛利', amount: gross.amount, bold: true, tip: TIP.gross,
        note: `毛利率 ${fmtPct(gross.rate)}（(營收-成本)/營收，>10% 佳）`,
    });

    const opex = p.opex || {};
    rows.push({ label: '營業費用', amount: opex.total, bold: true, groupBg: true, tip: TIP.opex });
    (opex.groups || []).forEach(g => {
        rows.push({ label: `${g.label}（小計）`, amount: g.total, indent: 1, groupBg: true, drill: g.drill });
        (g.lines || []).forEach(l => rows.push({ label: l.label, amount: l.amount, indent: 2 }));
    });

    const op = p.operating || {};
    rows.push({
        label: '營業淨利', amount: op.amount, bold: true,
        note: `營利率 ${fmtPct(op.rate)}｜費用率 ${fmtPct(op.expense_rate)}`,
    });

    const no = p.non_operating || {};
    rows.push({ label: '業外收支', amount: no.total, bold: true, groupBg: true, tip: TIP.nonOp });
    (no.income || []).forEach(l => rows.push({ label: `（收）${l.label}`, amount: l.amount, indent: 1 }));
    (no.expense || []).forEach(l => rows.push({ label: `（支）${l.label}`, amount: l.amount, indent: 1 }));

    rows.push({ label: '稅前淨利', amount: p.pretax, bold: true });

    const tax = p.tax || {};
    rows.push({ label: '營所稅', amount: tax.income_tax, indent: 1, tip: TIP.incomeTax });
    const v = tax.vat_info || {};
    rows.push({
        label: '營業稅資訊', noAmount: true, tip: TIP.vat,
        note: `銷項 $${fmtNum(v.output)}｜進項 $${fmtNum(v.input)}｜已繳 $${fmtNum(v.paid)}｜淨額 $${fmtNum(v.net)}（營業稅不計入損益，此為代收代付資訊）`,
    });

    const net = p.net || {};
    rows.push({ label: '稅後淨利', amount: net.amount, bold: true, groupBg: true, note: `淨利率 ${fmtPct(net.rate)}` });
    return rows;
}

function _pnlHtml(p) {
    const rows = _pnlRows(p).map(r => _trow(r.label, r.noAmount ? '' : _amt(r.amount), r));
    return `
    <table style="border-collapse:collapse;font-size:13px;width:100%;min-width:420px;">
        <tbody>${rows.join('')}</tbody>
    </table>`;
}

// ── 資產負債表 ──────────────────────────────────────────────
function _bsTipFor(key) {
    if (key === 'receivable') return TIP.receivable;
    if (key === 'payable') return TIP.payable;
    if (key === 'cash') return TIP.cash;
    return null;
}

// BS row model：{side:'a'|'l', block, label, amount, pct, drill, tip} 明細列、
// {header} 區塊標題列、{total, strong} 小計/總計列。HTML 依 side 拆左右兩表，
// CSV 走同一份 rows（header 列跳過，block 欄承接分組）。
function _bsRows(b) {
    const a = b.assets || {}, li = b.liabilities || {}, eq = b.equity || {};
    const rows = [];
    const lines = (side, block, list) => (list || []).forEach(l => rows.push({
        side, block, label: l.label, amount: l.amount, pct: l.pct,
        drill: l.drill, tip: _bsTipFor(l.key),
    }));
    rows.push({ side: 'a', header: '資產' });
    rows.push({ side: 'a', header: '流動資產' });
    lines('a', '資產-流動', a.current);
    rows.push({ side: 'a', block: '資產-流動', label: '流動資產小計', amount: _sum(a.current), total: true });
    rows.push({ side: 'a', header: '非流動資產' });
    lines('a', '資產-非流動', a.noncurrent);
    rows.push({ side: 'a', block: '資產-非流動', label: '非流動資產小計', amount: _sum(a.noncurrent), total: true });
    rows.push({ side: 'a', block: '資產', label: '資產總計', amount: a.total, total: true, strong: true });
    rows.push({ side: 'l', header: '負債' });
    rows.push({ side: 'l', header: '流動負債' });
    lines('l', '負債-流動', li.current);
    rows.push({ side: 'l', block: '負債-流動', label: '流動負債小計', amount: _sum(li.current), total: true });
    rows.push({ side: 'l', header: '非流動負債' });
    lines('l', '負債-非流動', li.noncurrent);
    rows.push({ side: 'l', block: '負債-非流動', label: '非流動負債小計', amount: _sum(li.noncurrent), total: true });
    rows.push({ side: 'l', block: '負債', label: '負債總計', amount: li.total, total: true, strong: true });
    rows.push({ side: 'l', header: '權益' });
    lines('l', '權益', eq.lines);
    rows.push({ side: 'l', block: '權益', label: '權益總計', amount: eq.total, total: true, strong: true });
    rows.push({ side: 'l', block: '合計', label: '負債及權益總計', amount: (li.total || 0) + (eq.total || 0), total: true, strong: true });
    return rows;
}

function _bsRowHtml(r) {
    if (r.header) {
        return `<tr style="background:#26282e;"><td colspan="3" style="padding:6px 10px;color:#9ca3af;font-weight:600;font-size:12px;">${esc(r.header)}</td></tr>`;
    }
    if (r.total) {
        return `
        <tr style="border-top:1px solid #333;${r.strong ? 'background:#26282e;' : ''}font-weight:700;color:#eee;">
            <td style="padding:6px 10px;">${esc(r.label)}</td>
            <td style="padding:6px 10px;text-align:right;white-space:nowrap;">${_amt(r.amount)}</td>
            <td></td>
        </tr>`;
    }
    const drillAttr = r.drill ? ` data-drill="${esc(r.drill)}" data-drill-label="${esc(r.label)}"` : '';
    return `
    <tr style="border-top:1px solid #262626;color:#ccc;"${drillAttr}>
        <td style="padding:5px 10px 5px 28px;">${esc(r.label)}${r.tip ? ` <span title="${esc(r.tip)}" style="color:#667;cursor:help;font-size:11px;">ⓘ</span>` : ''}</td>
        <td style="padding:5px 10px;text-align:right;white-space:nowrap;">${_amt(r.amount)}</td>
        <td style="padding:5px 10px;text-align:right;color:#777;font-size:11px;">${r.pct != null ? fmtPct(r.pct) : ''}</td>
    </tr>`;
}

function _bsHtml(b) {
    const rows = _bsRows(b);
    const table = (side) => `
    <table style="border-collapse:collapse;font-size:13px;width:100%;">
        <tbody>${rows.filter(r => r.side === side).map(_bsRowHtml).join('')}</tbody>
    </table>`;

    const chk = b.check || {};
    const diff = chk.diff || 0;
    const checkHtml = diff !== 0 ? `
        <div style="color:#f87171;font-size:13px;margin-top:12px;">
            ⚠ 未對平差額 $${fmtNum(diff)}
            ${(chk.notes || []).length ? `<ul style="margin:4px 0 0;padding-left:20px;color:#fca5a5;font-size:12px;">${chk.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
        </div>` : '';

    const r = b.ratios || {};
    const labels = r.labels || {};
    const cr = r.current_ratio, dr = r.debt_ratio;
    const ratiosHtml = `
        <div style="margin-top:12px;font-size:13px;color:#ccc;display:flex;gap:24px;flex-wrap:wrap;">
            <div>流動比率 <b style="color:${_crColor(cr)};">${_fmtRatio(cr)}</b> — ${esc(labels.current_ratio || '')}
                <span title="${esc(TIP.currentRatio)}" style="color:#667;cursor:help;font-size:11px;">ⓘ</span></div>
            <div>負債比率 <b style="color:${_drColor(dr)};">${fmtPct(dr)}</b> — ${esc(labels.debt_ratio || '')}
                <span title="${esc(TIP.debtRatio)}" style="color:#667;cursor:help;font-size:11px;">ⓘ</span></div>
        </div>`;

    return `
    <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start;">
        <div style="flex:1;min-width:300px;">${table('a')}</div>
        <div style="flex:1;min-width:300px;">${table('l')}</div>
    </div>
    ${checkHtml}
    ${ratiosHtml}`;
}

// ── 現金流量表 row model ────────────────────────────────────
function _cfRows(cf) {
    const opening = cf.opening || {};
    const closing = cf.closing || {};
    const drills = cf.drills || {};   // 後端三活動 drill kind（cash.*）
    const rows = [];
    rows.push({ label: '期初現金', amount: opening.total, bold: true, groupBg: true, tip: TIP.openingCash });
    (opening.by_account || []).forEach(x => rows.push({ label: x.name, amount: x.amount, indent: 1 }));
    rows.push({ label: '營運活動現金流', amount: cf.operating, drill: drills.operating, tip: TIP.operatingCf });
    rows.push({ label: '投資活動現金流', amount: cf.investing, drill: drills.investing, tip: '買賣設備、器材等投資的現金進出' });
    rows.push({ label: '籌資活動現金流', amount: cf.financing, drill: drills.financing, tip: '借款／還款、業主投入或提領的現金進出' });
    rows.push({ label: '本期淨流', amount: cf.net, bold: true });
    rows.push({ label: '期末現金', amount: closing.total, bold: true, groupBg: true });
    (closing.by_account || []).forEach(x => rows.push({ label: x.name, amount: x.amount, indent: 1 }));
    return rows;
}

function _cfHtml(cf) {
    const rows = _cfRows(cf).map(r => _trow(r.label, _amt(r.amount), r));
    const chk = cf.check || {};
    const diff = chk.diff || 0;
    const checkHtml = diff !== 0
        ? `<div style="color:#f87171;font-size:13px;margin-top:10px;">⚠ 現金流勾稽差額 $${fmtNum(diff)}（期初＋淨流 ≠ 期末，可能有收支未掛帳戶）</div>`
        : '';

    return `
    <table style="border-collapse:collapse;font-size:13px;width:100%;min-width:420px;">
        <tbody>${rows.join('')}</tbody>
    </table>
    ${checkHtml}`;
}

// ── Drilldown Modal ─────────────────────────────────────────
async function _openDrill(kind, label) {
    const modal = _c.querySelector('#finstmt-drill-modal');
    const body = _c.querySelector('#finstmt-drill-body');
    const title = _c.querySelector('#finstmt-drill-title');
    if (!modal || !body) return;
    const seq = ++_drillSeq;
    title.textContent = `🔍 ${label || kind} 明細（${_period}）`;
    body.innerHTML = '<div style="color:#888;padding:20px;">載入明細…</div>';
    modal.style.display = 'flex';
    let r;
    try {
        r = await finFetch(`/statements/drilldown?kind=${encodeURIComponent(kind)}&period=${encodeURIComponent(_period)}`);
    } catch (e) {
        if (seq !== _drillSeq || !_isCurrent()) return;
        body.innerHTML = `<div style="color:#f87171;padding:20px;">明細載入失敗：${esc(e.message)}</div>`;
        return;
    }
    if (seq !== _drillSeq || !_isCurrent()) return;   // 已開別的 drill 或已切走
    const items = r.items || [];
    if (!items.length) {
        body.innerHTML = '<div style="color:#666;padding:20px;">此期間沒有明細</div>';
        return;
    }
    body.innerHTML = `
    <table style="border-collapse:collapse;font-size:12px;color:#ccc;width:100%;">
        <thead>
            <tr style="color:#888;text-align:left;">
                <th style="padding:5px 10px;">日期</th>
                <th style="padding:5px 10px;">摘要</th>
                <th style="padding:5px 10px;text-align:right;">金額</th>
            </tr>
        </thead>
        <tbody>${items.map(it => `
            <tr style="border-top:1px solid #2a2a2a;">
                <td style="padding:5px 10px;white-space:nowrap;">${esc(it.date ? String(it.date).substring(0, 10) : '')}</td>
                <td style="padding:5px 10px;">${esc(it.label || '')}</td>
                <td style="padding:5px 10px;text-align:right;white-space:nowrap;color:${(it.amount || 0) < 0 ? '#fca5a5' : '#ddd'};">$${fmtNum(it.amount)}</td>
            </tr>`).join('')}
        </tbody>
        <tfoot>
            <tr style="border-top:2px solid #444;font-weight:700;color:#eee;">
                <td colspan="2" style="padding:6px 10px;">合計（${fmtNum(items.length)} 筆）</td>
                <td style="padding:6px 10px;text-align:right;">$${fmtNum(r.total)}</td>
            </tr>
        </tfoot>
    </table>`;
}

_fs.closeDrill = () => {
    const modal = _c && _c.querySelector('#finstmt-drill-modal');
    if (modal) modal.style.display = 'none';
};

// ── CSV 匯出 ────────────────────────────────────────────────
_fs.exportCsv = (which) => {
    if (!_data) { finToast('請先載入報表', true); return; }
    let rows, name;
    if (which === 'pnl') { rows = _pnlCsv(_data.pnl || {}); name = '損益表'; }
    else if (which === 'bs') { rows = _bsCsv(_data.bs || {}); name = '資產負債表'; }
    else { rows = _cfCsv(_data.cf || {}); name = '現金流量表'; }
    downloadCsv(rows, `${name}_${_period}.csv`);
    finToast(`已匯出${name}`);
};

/** row model → CSV 的縮排項目名（全形空白 × indent） */
function _csvLabel(r) {
    return '　'.repeat(r.indent || 0) + r.label;
}

function _pnlCsv(p) {
    const rows = [['項目', '金額', '備註']];
    _pnlRows(p).forEach(r =>
        rows.push([_csvLabel(r), r.noAmount ? '' : (r.amount || 0), r.note || '']));
    return rows;
}

function _bsCsv(b) {
    const rows = [['區塊', '項目', '金額', '占比']];
    _bsRows(b).forEach(r => {
        if (r.header) return;   // 分組資訊由「區塊」欄承接
        rows.push([r.block, r.label, r.amount || 0, r.pct != null ? fmtPct(r.pct) : '']);
    });
    const r = b.ratios || {}, labels = r.labels || {};
    rows.push(['比率', '流動比率', _fmtRatio(r.current_ratio), labels.current_ratio || '']);
    rows.push(['比率', '負債比率', fmtPct(r.debt_ratio), labels.debt_ratio || '']);
    const chk = b.check || {};
    if ((chk.diff || 0) !== 0) rows.push(['勾稽', '未對平差額', chk.diff, (chk.notes || []).join('；')]);
    return rows;
}

function _cfCsv(cf) {
    const rows = [['項目', '金額']];
    _cfRows(cf).forEach(r => rows.push([_csvLabel(r), r.amount || 0]));
    const chk = cf.check || {};
    if ((chk.diff || 0) !== 0) rows.push(['勾稽差額', chk.diff]);
    return rows;
}
