/**
 * securities.js — 📈 證券投資（owner 2026-08-29「開一個股票證券的側邊來整理證券」）。
 *
 * 持股的資料層與資產儀表板**是同一份**（finance_holdings、/assets/overview、
 * /assets/holdings CRUD、/assets/quotes/refresh）—— 這裡不是第二套資料，是同一
 * 批列的另一個視角：儀表板關心「淨值多少」，這頁關心「投了多少、現在值多少、
 * 賺賠多少」。所以本檔只做分組、成本、損益與編輯，一行取數邏輯都不複製。
 *
 * 🔴 成本填的是**該列自己的幣別**（跟單價同慣例）：Firstrade 的 VTI 填美金、
 *    富邦的 0050 填台幣。台幣換算在後端用同一支 _to_twd 做，市值與成本共用
 *    同一個匯率 —— 兩邊各換各的，沒交易的日子損益也會浮動。
 *
 * 圖表（owner 2026-08-29「優化視覺呈現，配色不調整，圖表形式如參考圖」）：
 *   配置    → 單色橫條（不是甜甜圈）。實帳是 47.7% / 43.1% / 7.6% / 1.6% /
 *             0.006%：前兩塊幾乎一樣大、後兩塊在圓餅上根本看不到，正是
 *             「圓餅不適合比接近的值」那一條。橫條同時給長度與數字。
 *   成本vs市值 → 一個色相＋灰的對照條（不是兩個彩色）：要比的是同一列兩根
 *             誰長，不是分辨兩個族群。
 *   報酬率  → 零線居中的正負條，紅漲綠跌（狀態色，跟全站損益同一組）。
 *   **沒有折線圖**：finance_net_snapshots 是整份淨值（含現金/不動產/器材）的
 *   快照，不是證券部位的歷史 —— 畫在這頁會是張標錯的圖。要證券的時間軸得先
 *   有證券自己的快照。
 */
import { finFetch, esc, fmtNum, finToast, metricCard } from '../fin-utils.js';
import { hbars, groupedBars, divergingBars } from '../../../js/shared/svg-charts.js';

let _c = null;
let _isCurrent = () => true;
let _data = null;
let _showAll = false;      // 預設只看「投資」那幾類；打開才看現金/保險那些列

const _fs = (window._finSecurities = window._finSecurities || {});

// 這幾個 broker 不是投資部位（保險解約金、口袋裡的外幣、銀行定存）——
// 預設收起來。它們仍在資產儀表板的淨值裡，這裡只是不混進報酬率的分母。
const NON_INVEST = new Set(['保險', '外幣現金', '外幣活存']);

const _isInvest = (h) => !NON_INVEST.has(h.broker || '');

export default async function render(container, ctx = {}) {
    _c = container;
    _isCurrent = ctx.isCurrent || (() => true);
    await _load();
}

async function _load() {
    try {
        _data = await finFetch('/assets/overview');
    } catch (e) {
        if (_isCurrent()) {
            _c.innerHTML = `<div class="crm-empty">載入失敗：${esc(e.message)}</div>`;
        }
        return;
    }
    if (_isCurrent()) { _draw(); }
}

_fs.reload = _load;

/** 一列的報酬率（沒填成本＝算不出來，不是 0%）。 */
function _roi(h) {
    return h.pnl === null || !h.cost_twd ? null : (h.pnl / h.cost_twd) * 100;
}

function _pnlCell(v, pct) {
    if (v === null || v === undefined) {
        return '<td style="text-align:right;color:#666;">—</td>'
             + '<td style="text-align:right;color:#666;">—</td>';
    }
    const col = v >= 0 ? '#f87171' : '#4ade80';   // 台股慣例：紅漲綠跌
    const sign = v >= 0 ? '+' : '';
    return `<td style="text-align:right;color:${col};">${sign}${fmtNum(v)}</td>`
         + `<td style="text-align:right;color:${col};">${
             pct === null ? '—' : sign + pct.toFixed(1) + '%'}</td>`;
}

const _IN = 'width:100%;text-align:right;padding:2px 6px;';

function _rowHtml(h) {
    const cur = (h.currency || 'TWD').toUpperCase();
    // 手動現值只畫在**手動列**上：有報價源的列填了也不會被採用
    // （_holding_value 以 manual_value 優先，等於把報價蓋掉），
    // 每列都放一個空框只是邀請人填錯。
    const manual = h.quote_symbol
        ? '<span style="color:#444;">—</span>'
        : `<input class="crm-input fs-manual" style="${_IN}"
                  value="${h.manual_value ?? ''}" placeholder="現值">`;
    return `
        <tr data-id="${h.id}">
            <td>${esc(h.name)}${h.symbol ? ` <span style="color:#93c5fd;">${esc(h.symbol)}</span>` : ''}
                <div style="color:#666;font-size:10px;">${esc(h.broker || '—')}｜${esc(cur)}${
                    h.quote_symbol ? '' : '｜手動'}</div></td>
            <td><input class="crm-input fs-shares" style="${_IN}"
                       value="${h.shares ?? ''}" placeholder="股數"></td>
            <td style="text-align:right;">${h.last_price
                ? fmtNum(h.last_price) + `<div style="color:#666;font-size:10px;">${esc(h.price_at || '')}</div>`
                : '<span style="color:#444;">—</span>'}</td>
            <td><input class="crm-input fs-cost" style="${_IN}"
                       value="${h.cost_total ?? ''}" placeholder="成本"></td>
            <td style="text-align:right;">${manual}</td>
            <td style="text-align:right;color:#eee;">${fmtNum(h.value_twd)}</td>
            <td style="text-align:right;color:#bbb;">${h.cost_twd ? fmtNum(h.cost_twd) : '—'}</td>
            ${_pnlCell(h.pnl, _roi(h))}
            <td style="text-align:center;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm"
                        onclick="window._finSecurities.save('${h.id}', this)">存</button>
            </td>
        </tr>`;
}

/** 一個券商的小計列。沒有任何一列填了成本 → 損益欄留白，不要顯示 0。 */
function _subtotal(label, rows) {
    const val = rows.reduce((n, h) => n + (h.value_twd || 0), 0);
    const cost = rows.reduce((n, h) => n + (h.cost_twd || 0), 0);
    const known = rows.filter((h) => h.pnl !== null && h.pnl !== undefined);
    const pnl = known.length ? known.reduce((n, h) => n + h.pnl, 0) : null;
    const pct = pnl !== null && cost ? (pnl / cost) * 100 : null;
    const partial = known.length && known.length < rows.length;
    return `<tr style="background:#1b1b1b;font-weight:600;">
        <td colspan="5" style="color:#ddd;">${esc(label)}<span style="color:#666;font-weight:400;">
            ｜${rows.length} 檔${partial ? `（其中 ${known.length} 檔有填成本）` : ''}</span></td>
        <td style="text-align:right;color:#eee;">${fmtNum(val)}</td>
        <td style="text-align:right;color:#bbb;">${cost ? fmtNum(cost) : '—'}</td>
        ${_pnlCell(pnl, pct)}
        <td></td></tr>`;
}

function _draw() {
    const all = _data.holdings || [];
    const rows = _showAll ? all : all.filter(_isInvest);
    const byBroker = new Map();
    rows.forEach((h) => {
        const k = h.broker || '（未指定券商）';
        if (!byBroker.has(k)) { byBroker.set(k, []); }
        byBroker.get(k).push(h);
    });
    // 市值大的券商排前面 —— 一打開就看到主要部位
    const groups = [...byBroker.entries()].sort(
        (a, b) => b[1].reduce((n, h) => n + h.value_twd, 0)
                - a[1].reduce((n, h) => n + h.value_twd, 0));

    const body = groups.map(([k, list]) =>
        _subtotal(k, list) + list.map(_rowHtml).join('')).join('');

    const val = rows.reduce((n, h) => n + (h.value_twd || 0), 0);
    const cost = rows.reduce((n, h) => n + (h.cost_twd || 0), 0);
    const known = rows.filter((h) => h.pnl !== null && h.pnl !== undefined);
    const pnl = known.length ? known.reduce((n, h) => n + h.pnl, 0) : null;
    const missing = rows.filter((h) => h.pnl === null || h.pnl === undefined);

    // ── 圖表資料（都從同一份 rows 推導，沒有第二個取數來源）──
    const alloc = groups.map(([k, list]) => ({
        label: k, value: list.reduce((n, h) => n + (h.value_twd || 0), 0),
    })).filter((r) => r.value > 0);
    const costVsValue = groups.map(([k, list]) => ({
        label: k,
        a: list.reduce((n, h) => n + (h.cost_twd || 0), 0),
        b: list.reduce((n, h) => n + (h.value_twd || 0), 0),
    })).filter((r) => r.a > 0);
    // 報酬率與損益各排各的：報酬率高的常常是小部位（0050 +266% 只有 76 萬），
    // 兩張圖回答的是不同問題（哪一檔賺得兇 vs 哪一檔真的把錢賺回來）
    // 兩張圖都濾掉 0：活存那幾列的報酬率恆為 0%，畫成一根長度 0 的條只是佔位，
    // 把真正有動的那幾檔擠窄。它們在下面的表格裡照樣列得到。
    const roiRows = known.map((h) => ({
        label: h.name, value: _roi(h), note: h.broker || '',
    })).filter((r) => r.value).sort((a, b) => b.value - a.value);
    const pnlRows = known.map((h) => ({
        label: h.name, value: h.pnl, note: h.broker || '',
    })).filter((r) => r.value).sort((a, b) => b.value - a.value);

    _c.innerHTML = `
    <div style="padding:16px;max-width:1400px;">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
        <h2 style="margin:0;font-size:16px;color:#eee;">證券投資</h2>
        <span style="color:#666;font-size:12px;">匯率 USD/TWD ${
            _data.usd_twd ? fmtNum(_data.usd_twd) : '未設定'}</span>
        <span style="flex:1;"></span>
        <label style="color:#9ca3af;font-size:12px;display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="fs-all" ${_showAll ? 'checked' : ''}>
          連現金／保險一起看</label>
        <button class="crm-btn crm-btn-secondary crm-btn-sm"
                onclick="window._finSecurities.refresh(this)">更新報價</button>
      </div>

      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
        ${metricCard('市值合計', `<span style="color:#eee;">${fmtNum(val)}</span>`,
            `<span style="color:#666;">${rows.length} 檔</span>`)}
        ${metricCard('投入成本', `<span style="color:#bbb;">${cost ? fmtNum(cost) : '—'}</span>`,
            known.length ? `<span style="color:#666;">${known.length} 檔有填成本</span>` : '')}
        ${metricCard('未實現損益', `<span style="color:${_pnlColor(pnl)};">${
            pnl === null ? '—' : (pnl >= 0 ? '+' : '') + fmtNum(pnl)}</span>`,
            pnl === null ? '' : `<span style="color:#666;">市值 − 成本</span>`)}
        ${metricCard('報酬率', `<span style="color:${_pnlColor(pnl)};">${
            pnl === null || !cost ? '—'
                : (pnl >= 0 ? '+' : '') + ((pnl / cost) * 100).toFixed(1) + '%'}</span>`,
            cost ? `<span style="color:#666;">對投入成本</span>` : '')}
      </div>

      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));
                  gap:10px;margin-bottom:14px;">
        ${_panel('配置（按券商）', hbars(alloc, {
            width: 520, labelWidth: 96, valueWidth: 132,
            emptyText: '尚無持股',
        }), `合計 ${fmtNum(val)}`)}
        ${_panel('成本 vs 市值', groupedBars(costVsValue, {
            width: 520, labelWidth: 96, valueWidth: 104,
            aName: '成本', bName: '市值', emptyText: '還沒有填成本的券商',
        }), '只列有填成本的券商')}
        ${_panel('個股報酬率', divergingBars(roiRows, {
            width: 520, labelWidth: 150, valueWidth: 68,
            formatValue: (v) => (v >= 0 ? '+' : '') + v.toFixed(1) + '%',
            emptyText: '還沒有填成本的持股',
        }), '紅漲綠跌；0% 的不列')}
        ${_panel('未實現損益貢獻', divergingBars(pnlRows, {
            width: 520, labelWidth: 150, valueWidth: 92,
            emptyText: '還沒有填成本的持股',
        }), '誰把整體報酬撐起來')}
      </div>

      ${missing.length ? `<div style="background:#2a2416;border:1px solid #6b5a1e;border-radius:6px;
            padding:8px 12px;margin-bottom:12px;color:#fbbf24;font-size:12px;">
          ⚠ ${missing.length} 檔還沒填投資成本，損益與報酬率算的只有填了的那
          ${known.length} 檔：${missing.map((h) => esc(h.name)).join('、')}</div>` : ''}

      <div style="overflow-x:auto;">
      <table class="crm-table" style="width:100%;min-width:1020px;font-size:12px;
             table-layout:fixed;font-variant-numeric:tabular-nums;">
        <colgroup>
          <col><col style="width:96px;"><col style="width:96px;"><col style="width:112px;">
          <col style="width:104px;"><col style="width:124px;"><col style="width:120px;">
          <col style="width:120px;"><col style="width:80px;"><col style="width:52px;">
        </colgroup>
        <thead><tr>
          <th>標的</th><th style="text-align:right;">股數</th>
          <th style="text-align:right;">現價<span style="color:#666;">(原幣)</span></th>
          <th style="text-align:right;">投資成本<span style="color:#666;">(原幣)</span></th>
          <th style="text-align:right;">手動現值</th>
          <th style="text-align:right;">市值(TWD)</th>
          <th style="text-align:right;">成本(TWD)</th>
          <th style="text-align:right;">損益</th><th style="text-align:right;">報酬率</th>
          <th></th>
        </tr></thead>
        <tbody>${body || '<tr><td colspan="10" class="crm-empty">尚無持股</td></tr>'}</tbody>
      </table>
      </div>

      <div style="display:flex;gap:6px;margin-top:14px;flex-wrap:wrap;align-items:center;
                  background:#1b1b1b;border:1px solid #2e2e2e;border-radius:6px;padding:10px;">
        <span style="color:#9ca3af;font-size:12px;">新增持股</span>
        <input class="crm-input" id="fs-new-name" placeholder="名稱" style="width:160px;">
        <input class="crm-input" id="fs-new-symbol" placeholder="代號" style="width:76px;">
        <input class="crm-input" id="fs-new-broker" placeholder="券商" style="width:104px;">
        <input class="crm-input" id="fs-new-shares" placeholder="股數" style="width:88px;text-align:right;">
        <input class="crm-input" id="fs-new-cost" placeholder="投資成本" style="width:96px;text-align:right;">
        <select class="crm-select" id="fs-new-cur" style="width:78px;"><option>TWD</option><option>USD</option></select>
        <input class="crm-input" id="fs-new-qs" placeholder="報價源（空=手動）" style="width:170px;">
        <button class="crm-btn crm-btn-secondary" onclick="window._finSecurities.add(this)">＋ 新增</button>
      </div>
      <div style="color:#666;font-size:11px;margin-top:6px;line-height:1.6;">
        報價源：台股 <code>tse:0050</code>、美股／ETF <code>yahoo:VTI</code>（LSE 如
        <code>yahoo:VWRA.L</code>）；留空＝手動列，現值填「手動現值」（適合複委託整戶、定存）。<br>
        投資成本填**該列幣別**的金額（Firstrade 的 VTI 填美金、富邦的 0050 填台幣），
        台幣換算與市值用同一個匯率。持股與資產儀表板是同一份資料，兩邊改都會同步。
      </div>
    </div>`;
    document.getElementById('fs-all').addEventListener('change', (e) => {
        _showAll = e.target.checked;
        _draw();
    });
}

/** 損益色：紅漲綠跌（台股慣例，全站同一組狀態色）；算不出來是灰的不是 0。 */
function _pnlColor(v) {
    return v === null || v === undefined ? '#666' : (v >= 0 ? '#f87171' : '#4ade80');
}

/** 圖表卡：標題 + 右上角一句註解 + 圖。四張共用一個殼，版面才會齊。 */
function _panel(title, svg, note) {
    return `<div style="background:#222;border:1px solid #333;border-radius:8px;padding:12px 14px;">
        <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px;">
            <span style="color:#ddd;font-size:13px;font-weight:600;">${esc(title)}</span>
            <span style="flex:1;"></span>
            <span style="color:#666;font-size:11px;">${esc(note || '')}</span>
        </div>
        ${svg}
    </div>`;
}

// ── 動作（端點與資產儀表板共用）────────────────────────────────
_fs.refresh = async (btn) => {
    btn.disabled = true;
    btn.textContent = '抓報價中…';
    try {
        const r = await finFetch('/assets/quotes/refresh', { method: 'POST' });
        finToast(`已更新 ${r.updated.length} 檔`
            + (r.failed.length ? `；抓不到 ${r.failed.join('、')}（沿用舊價）` : '')
            + (r.usd_twd ? `；匯率 ${r.usd_twd}` : ''));
        await _load();
    } catch (e) {
        finToast('報價更新失敗：' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '更新報價';
    }
};

// 🔴 收 null：手動現值那格只畫在手動列上（有報價的列沒有這個 input），
// 直接 el.value 會 TypeError —— 整個「存」就啞掉。
const _num = (el) => (!el || el.value.trim() === '' ? null : Number(el.value.trim()));

_fs.save = async (id, btn) => {
    const tr = btn.closest('tr');
    const h = (_data.holdings || []).find((x) => x.id === id) || {};
    btn.disabled = true;
    try {
        await finFetch(`/assets/holdings/${id}`, {
            method: 'PUT',
            body: JSON.stringify({
                name: h.name,               // PUT 是 exclude_unset，name 是必填欄
                shares: _num(tr.querySelector('.fs-shares')),
                cost_total: _num(tr.querySelector('.fs-cost')),   // 碎股成本有小數，不取整
                // 有報價的列沒有這一格 → 不送這個欄位（PUT 是 exclude_unset，
                // 送 null 會把它清成 null；那對手動列是「清掉」的語意，
                // 對有報價的列則是白寫一次）
                ...(tr.querySelector('.fs-manual')
                    ? { manual_value: _num(tr.querySelector('.fs-manual')) === null
                        ? null : Math.round(_num(tr.querySelector('.fs-manual'))) }
                    : {}),
            }),
        });
        finToast('已儲存');
        await _load();
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
        btn.disabled = false;
    }
};

_fs.add = async (btn) => {
    const v = (id) => document.getElementById(id).value.trim();
    if (!v('fs-new-name')) { finToast('名稱必填', 'error'); return; }
    btn.disabled = true;
    try {
        await finFetch('/assets/holdings', {
            method: 'POST',
            body: JSON.stringify({
                name: v('fs-new-name'), symbol: v('fs-new-symbol'),
                broker: v('fs-new-broker'),
                shares: v('fs-new-shares') === '' ? null : Number(v('fs-new-shares')),
                cost_total: v('fs-new-cost') === '' ? null : Number(v('fs-new-cost')),
                currency: v('fs-new-cur'), quote_symbol: v('fs-new-qs'),
            }),
        });
        finToast('已新增');
        await _load();
    } catch (e) {
        finToast('新增失敗：' + e.message, 'error');
    } finally {
        btn.disabled = false;
    }
};
