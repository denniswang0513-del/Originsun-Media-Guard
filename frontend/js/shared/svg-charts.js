/**
 * svg-charts.js — 純函式 SVG 迷你圖模組（跨 tab 共用）
 *
 * 設計原則：
 *   - 零外部依賴、零 CDN、零 DOM 操作 —— 每個函式接資料回一段 **SVG 字串**，
 *     呼叫端自己塞進 innerHTML。
 *   - 深色主題（背景 #1a1a1a/#202020）、responsive `viewBox`（width:100% 自適應）。
 *   - 色票沿用系統主色：#3b82f6 藍 / #d48a04 橘 / #228b22 綠 / #fca5a5 紅。
 *   - 數字千分位自帶（`_fmt`）；可用 opts.formatValue 覆寫。
 *   - 空資料回友善佔位 SVG（不丟例外）。
 *
 * 匯出：
 *   lineChart(series, opts) 折線（多系列，如 revenue+net；支援負值）
 *   hbars(items, opts)      水平佔比條（帳齡／客戶集中度用，帶標籤+數值+%）
 *
 * series 形狀（line 用）：
 *   [{ name, color, values:[n,...] }, ...]   單系列也可傳單一物件（自動包成陣列）
 *   x 軸類別標籤放 opts.labels（與各 series 的 values 等長）。
 * items 形狀（hbars）：
 *   [{ label, value, pct?, color? }, ...]    pct 未給時以 Σvalue 即時算佔比。
 */

// ── 色票 / 樣式常數 ─────────────────────────────────────────
export const CHART_COLORS = { blue: '#3b82f6', orange: '#d48a04', green: '#228b22', red: '#fca5a5' };
const _PALETTE = ['#3b82f6', '#d48a04', '#228b22', '#fca5a5', '#a78bfa', '#38bdf8'];
const _AXIS = '#333';      // 格線
const _AXIS_STRONG = '#4a4a4a';
const _TXT = '#888';       // 軸標籤
const _TXT2 = '#ccc';      // 數值標籤
const _FONT = 'font-family:system-ui,-apple-system,\'Segoe UI\',sans-serif;';

// ── 內部工具 ────────────────────────────────────────────────
function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** 千分位（四捨五入為整數）*/
function _fmt(n) {
    return (Math.round(Number(n) || 0)).toLocaleString('en-US');
}

/** 軸標籤縮寫（萬／億），大額才縮，避免 y 軸過寬 */
function _abbr(n) {
    const v = Number(n) || 0;
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1).replace(/\.0$/, '') + '億';
    if (a >= 1e4) return (v / 1e4).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, '') + '萬';
    return String(Math.round(v));
}

/** 把 series 參數正規化成 [{name,color,values}]，補預設色 */
function _normSeries(series) {
    let arr = Array.isArray(series) ? series : (series ? [series] : []);
    return arr.map((s, i) => ({
        name: s.name || '',
        color: s.color || _PALETTE[i % _PALETTE.length],
        values: (s.values || []).map(v => (v == null ? null : Number(v))),
    }));
}

/** 友善空狀態佔位 SVG */
function _empty(W, H, text) {
    const msg = _esc(text || '此期間沒有資料');
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet"
        style="max-width:100%;display:block;${_FONT}" role="img" aria-label="${msg}">
        <rect x="0" y="0" width="${W}" height="${H}" fill="#1c1c1c" rx="8"/>
        <text x="${W / 2}" y="${H / 2}" fill="#666" font-size="13" text-anchor="middle" dominant-baseline="middle">${msg}</text>
    </svg>`;
}

/** 圖例（水平） */
function _legend(series, x, y) {
    let out = '';
    let cx = x;
    for (const s of series) {
        const nm = _esc(s.name || '');
        const w = 18 + Math.max(nm.length * 8, 8) + 14;
        out += `<rect x="${cx}" y="${y - 8}" width="10" height="10" rx="2" fill="${s.color}"/>`;
        out += `<text x="${cx + 15}" y="${y + 1}" fill="${_TXT2}" font-size="11">${nm}</text>`;
        cx += w;
    }
    return out;
}

/**
 * 計算 y 軸幾何：涵蓋 0 基準線 + minV..maxV，回工具函式與格線 SVG。
 */
function _yAxis(values, top, plotH, plotLeft, plotRight) {
    let maxV = 0, minV = 0;
    for (const v of values) {
        if (v == null || isNaN(v)) continue;
        if (v > maxV) maxV = v;
        if (v < minV) minV = v;
    }
    if (maxV === 0 && minV === 0) maxV = 1;   // 全 0 → 給個範圍免除以零
    const range = (maxV - minV) || 1;
    const y = (v) => top + (maxV - v) / range * plotH;

    // ~4 條格線（含 0 與極值），值域內均分
    const ticks = [];
    const N = 4;
    for (let i = 0; i <= N; i++) ticks.push(minV + (range * i) / N);
    if (minV < 0 && maxV > 0 && !ticks.some(t => Math.abs(t) < range * 1e-6)) ticks.push(0);

    let grid = '';
    for (const t of ticks) {
        const yy = y(t);
        const isZero = Math.abs(t) < range * 1e-6;
        grid += `<line x1="${plotLeft}" y1="${yy.toFixed(1)}" x2="${plotRight}" y2="${yy.toFixed(1)}"
            stroke="${isZero ? _AXIS_STRONG : _AXIS}" stroke-width="1"${isZero ? '' : ' stroke-dasharray="2,3"'}/>`;
        grid += `<text x="${plotLeft - 6}" y="${(yy + 3).toFixed(1)}" fill="${_TXT}" font-size="10" text-anchor="end">${_esc(_abbr(t))}</text>`;
    }
    return { y, zeroY: y(0), grid };
}

// ── 折線圖 ──────────────────────────────────────────────────
/**
 * @param {Array|Object} series [{name,color,values}]（多系列折線）
 * @param {Object} opts labels[] / width / height / formatValue / showLegend(預設 true) / emptyText
 */
export function lineChart(series, opts = {}) {
    const s = _normSeries(series);
    const labels = opts.labels || [];
    const W = opts.width || 640, H = opts.height || 220;
    const fmt = opts.formatValue || _fmt;
    const hasData = s.length && s.some(ser => ser.values.some(v => v != null && !isNaN(v)));
    if (!hasData) return _empty(W, H, opts.emptyText);

    const showLegend = opts.showLegend != null ? opts.showLegend : true;
    const padL = 52, padR = 14, padT = (showLegend ? 26 : 14), padB = 30;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const n = Math.max(labels.length, ...s.map(ser => ser.values.length));

    const allVals = [];
    s.forEach(ser => ser.values.forEach(v => { if (v != null && !isNaN(v)) allVals.push(v); }));
    const ax = _yAxis(allVals, padT, plotH, padL, W - padR);

    const xAt = (i) => padL + (n <= 1 ? plotW / 2 : (plotW * i) / (n - 1));

    let lines = '';
    s.forEach(ser => {
        const pts = [];
        for (let i = 0; i < ser.values.length; i++) {
            const v = ser.values[i];
            if (v == null || isNaN(v)) continue;
            pts.push([xAt(i), ax.y(v), i, v]);
        }
        if (!pts.length) return;
        const d = pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
        lines += `<polyline points="${d}" fill="none" stroke="${ser.color}" stroke-width="2"
            stroke-linejoin="round" stroke-linecap="round"/>`;
        pts.forEach(p => {
            lines += `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6" fill="${ser.color}">
                <title>${_esc((labels[p[2]] ?? '') + '｜' + (ser.name ? ser.name + ' ' : '') + fmt(p[3]))}</title></circle>`;
        });
    });

    const step = Math.ceil(n / 12);
    let xlabels = '';
    for (let i = 0; i < n; i++) {
        if (i % step !== 0 && i !== n - 1) continue;
        xlabels += `<text x="${xAt(i).toFixed(1)}" y="${H - 10}" fill="${_TXT}" font-size="10" text-anchor="middle">${_esc(labels[i] ?? '')}</text>`;
    }

    return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet"
        style="max-width:100%;display:block;${_FONT}" role="img">
        ${showLegend ? _legend(s, padL, 14) : ''}
        ${ax.grid}
        ${lines}
        ${xlabels}
    </svg>`;
}

// ── 水平佔比條 ──────────────────────────────────────────────
/**
 * @param {Array} items [{label,value,pct?,color?}]
 * @param {Object} opts
 *   width=560            寬（高由列數自動算）
 *   barColor='#3b82f6'   預設條色（item.color 可各別覆寫）
 *   formatValue          數值格式；預設 _fmt
 *   showPct=true         顯示佔比 %（item.pct 或 value/Σvalue）
 *   rowHeight=30
 *   labelWidth=112       左側標籤欄寬
 *   valueWidth=118       右側數值欄寬
 *   maxValue             條長基準（預設 max(value)）；帳齡各桶共用同基準時可指定
 *   emptyText
 */
export function hbars(items, opts = {}) {
    const rows = (items || []).filter(Boolean);
    const W = opts.width || 560;
    const fmt = opts.formatValue || _fmt;
    const showPct = opts.showPct !== false;
    const rowH = opts.rowHeight || 30;
    const labelW = opts.labelWidth || 112;
    const valueW = opts.valueWidth || 118;
    const padT = 8, padB = 8;

    if (!rows.length || !rows.some(r => (Number(r.value) || 0) !== 0)) {
        return _empty(W, Math.max(80, rows.length * rowH + 16), opts.emptyText);
    }

    const total = rows.reduce((s, r) => s + (Number(r.value) || 0), 0);
    const maxV = opts.maxValue != null ? opts.maxValue
        : Math.max(...rows.map(r => Math.abs(Number(r.value) || 0)), 1);
    const barLeft = labelW + 8;
    const barMaxW = W - barLeft - valueW - 8;
    const H = rows.length * rowH + padT + padB;

    let body = '';
    rows.forEach((r, i) => {
        const v = Number(r.value) || 0;
        const cy = padT + i * rowH;
        const barY = cy + rowH / 2 - 8;
        const w = Math.max(2, Math.abs(v) / maxV * barMaxW);
        const color = r.color || (v < 0 ? CHART_COLORS.red : (opts.barColor || CHART_COLORS.blue));
        const pct = r.pct != null ? Number(r.pct) : (total ? (v / total * 100) : 0);
        const pctTxt = showPct ? `${(Math.round(pct * 10) / 10).toLocaleString('en-US')}%` : '';
        const lbl = _esc(r.label ?? '');
        body += `
        <text x="${labelW}" y="${(cy + rowH / 2 + 4).toFixed(1)}" fill="${_TXT2}" font-size="12" text-anchor="end">${lbl}</text>
        <rect x="${barLeft}" y="${barY.toFixed(1)}" width="${barMaxW}" height="16" rx="3" fill="#2a2a2a"/>
        <rect x="${barLeft}" y="${barY.toFixed(1)}" width="${w.toFixed(1)}" height="16" rx="3" fill="${color}">
            <title>${lbl}｜${_esc(fmt(v))}${showPct ? '（' + pctTxt + '）' : ''}</title></rect>
        <text x="${W - 4}" y="${(cy + rowH / 2 + 4).toFixed(1)}" fill="${_TXT2}" font-size="11.5" text-anchor="end">${_esc(fmt(v))}${pctTxt ? `  <tspan fill="${_TXT}">${pctTxt}</tspan>` : ''}</text>`;
    });

    return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet"
        style="max-width:100%;display:block;${_FONT}" role="img">
        ${body}
    </svg>`;
}
