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
        // 非 0 卻四捨五入成 0% 的要標成 <0.1% —— 直接印「0%」會讓一筆真的有錢的
        // 部位看起來是空的（實帳：美國匯豐 24,996 / 9,369 萬 ＝ 0.027%）
        const pctR = Math.round(pct * 10) / 10;
        const pctTxt = !showPct ? ''
            : (pctR === 0 && pct !== 0 ? (pct > 0 ? '<0.1%' : '>-0.1%')
                : `${pctR.toLocaleString('en-US')}%`);
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


/**
 * groupedBars(items, opts) — 每個類別兩根橫條的對照圖（成本 vs 市值、預算 vs 實際）。
 *
 * 形式選擇：兩個**同單位**的量放同一個 x 軸比長度（絕不畫雙軸）。配色是
 * 「一個色相 + 灰」的強調式，不是分類色 —— 讀者要比的是同一列的兩根誰長，
 * 不是分辨兩個族群，用兩個彩色反而讓每一列都在跟旁邊那列搶注意力。
 *
 * items: [{ label, a, b }]；opts.aName/bName 圖例名稱、opts.aColor/bColor 覆寫。
 */
export function groupedBars(items, opts = {}) {
    const rows = (items || []).filter(Boolean);
    const W = opts.width || 560;
    const fmt = opts.formatValue || _fmt;
    const labelW = opts.labelWidth || 88;
    const valueW = opts.valueWidth || 96;
    const aColor = opts.aColor || '#4a4a4a';               // 對照組：退到灰
    const bColor = opts.bColor || CHART_COLORS.blue;       // 主角
    const barH = 11, gap = 2, rowGap = 16;                 // gap=2：兩根之間留底色縫
    const rowH = barH * 2 + gap + rowGap;
    const padT = 26, padB = 6;                             // padT 讓出圖例

    if (!rows.length || !rows.some(r => (Number(r.a) || 0) || (Number(r.b) || 0))) {
        return _empty(W, 120, opts.emptyText);
    }
    const maxV = Math.max(...rows.map(r => Math.max(Math.abs(r.a || 0), Math.abs(r.b || 0))), 1);
    const barLeft = labelW + 8;
    // 條的最大長度要**扣掉數值欄**：不扣的話最長那列的條會一路頂到右邊，
    // 跟靠右的數字疊在一起（2026-08-29 截圖上富邦／盈透兩列就是這樣）。
    const barMaxW = W - barLeft - valueW - 12;
    const H = rows.length * rowH + padT + padB;

    let body = '';
    rows.forEach((r, i) => {
        const top = padT + i * rowH;
        [['a', aColor, opts.aName || 'A'], ['b', bColor, opts.bName || 'B']]
            .forEach(([k, color, name], j) => {
                const v = Number(r[k]) || 0;
                const y = top + j * (barH + gap);
                const w = Math.max(2, Math.abs(v) / maxV * barMaxW);
                body += `
        <rect x="${barLeft}" y="${y}" width="${w.toFixed(1)}" height="${barH}" rx="4" fill="${color}">
            <title>${_esc(r.label)}｜${_esc(name)} ${_esc(fmt(v))}</title></rect>
        <text x="${W - 4}" y="${y + barH - 1}" fill="${j ? _TXT2 : _TXT}" font-size="10.5"
              text-anchor="end">${_esc(fmt(v))}</text>`;
            });
        body += `
        <text x="${labelW}" y="${top + barH + 4}" fill="${_TXT2}" font-size="11.5"
              text-anchor="end">${_esc(r.label)}</text>`;
    });

    return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet"
        style="max-width:100%;display:block;${_FONT}" role="img">
        ${_legend([{ name: opts.aName || 'A', color: aColor },
                   { name: opts.bName || 'B', color: bColor }], barLeft, 12)}
        ${body}
    </svg>`;
}


/**
 * divergingBars(items, opts) — 有正負的橫條（報酬率、損益貢獻）。
 *
 * 零線在中間、正負各往一邊長 —— 「賺還是賠」用**方向**回答，不必先讀數字。
 * 顏色是狀態色不是分類色（預設台股慣例：紅漲綠跌），所以不進分類色的檢核。
 *
 * items: [{ label, value, note? }]；opts.posColor/negColor 覆寫。
 */
export function divergingBars(items, opts = {}) {
    const rows = (items || []).filter(Boolean);
    const W = opts.width || 560;
    const fmt = opts.formatValue || _fmt;
    const labelW = opts.labelWidth || 132;
    const valueW = opts.valueWidth || 74;
    const pos = opts.posColor || '#f87171';
    const neg = opts.negColor || '#4ade80';
    const rowH = opts.rowHeight || 26;
    const padT = 6, padB = 6;

    if (!rows.length) { return _empty(W, 100, opts.emptyText); }

    const maxV = Math.max(...rows.map(r => Math.abs(Number(r.value) || 0)), 1);
    const left = labelW + 8;
    const span = W - left - valueW - 8;
    // 🔴 零線只在**真的有正也有負**的時候才置中。全是正的還把零線放中間，等於
    // 把一半的寬度讓給永遠不會有東西的那側 —— 條就只剩一半長，小額那幾筆
    // 直接細到看不見。實帳的報酬率就是全正（2026-08-29 截圖上一眼可見）。
    const hasPos = rows.some(r => (Number(r.value) || 0) > 0);
    const hasNeg = rows.some(r => (Number(r.value) || 0) < 0);
    const both = hasPos && hasNeg;
    const half = both ? span / 2 : span;
    const zero = both ? left + span / 2 : (hasNeg ? left + span : left);
    const H = rows.length * rowH + padT + padB;

    let body = '';
    rows.forEach((r, i) => {
        const v = Number(r.value) || 0;
        const cy = padT + i * rowH;
        const y = cy + rowH / 2 - 6;
        const w = Math.max(2, Math.abs(v) / maxV * half);
        const x = v >= 0 ? zero : zero - w;
        body += `
        <text x="${labelW}" y="${(cy + rowH / 2 + 4).toFixed(1)}" fill="${_TXT2}" font-size="11.5"
              text-anchor="end">${_esc(r.label)}</text>
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="12" rx="4"
              fill="${v >= 0 ? pos : neg}"><title>${_esc(r.label)}｜${_esc(fmt(v))}${
            r.note ? '｜' + _esc(r.note) : ''}</title></rect>
        <text x="${W - 4}" y="${(cy + rowH / 2 + 4).toFixed(1)}" fill="${v >= 0 ? pos : neg}"
              font-size="11.5" text-anchor="end">${_esc(fmt(v))}</text>`;
    });

    return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet"
        style="max-width:100%;display:block;${_FONT}" role="img">
        <line x1="${zero}" y1="${padT}" x2="${zero}" y2="${H - padB}" stroke="${_AXIS_STRONG}" stroke-width="1"/>
        ${body}
    </svg>`;
}
