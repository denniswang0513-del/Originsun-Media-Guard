/**
 * crm-projects-cost.js — 成本估算 / 實際結算
 * 財務摘要載入、成本表格渲染、inline 編輯、計算、存取消、新增/刪除、範本
 */

import { state, callbacks, EXPENSE_CATEGORIES } from './crm-projects-state.js';
import { calcDashboard, calcDashboardParts, remainColor, profitColor, barColor, diffLabel } from './crm-projects-calc.js';
import { crmFetch as _fetch, esc as _esc, fmtNum, searchableSelect, moneyGate, today }
    from './crm-utils.js';

// ── Dirty map ──────────────────────────────────────────────────
// 儀表板基準：render 時記下「其他子表」的數字，inline 重算＝基準＋當前子表即時值
let _dashBase = null;
window._costDirtyMap = {};
window._expDirtyMap = {};  // {expId: {field: value}} 行政雜支 inline edit
// 這次 render 畫出來的雜支列（id → 列）。送請款要帶金額／類別／消費日，
// 從這裡查比塞進 onclick 字串安全（細項含單引號就會把 handler 打斷）。
let _expRowById = {};
window._projDirtyMap = {}; // {field: value} 專案資訊 cell-by-cell inline edit

window._allDirtyCount = function() {
    return Object.keys(window._costDirtyMap || {}).length
         + Object.keys(window._expDirtyMap || {}).length
         + Object.keys(window._projDirtyMap || {}).length;
};
window._clearAllDirty = function() {
    window._costDirtyMap = {};
    window._expDirtyMap = {};
    window._projDirtyMap = {};
};

// ── Auto-save state (1-sec debounce) ───────────────────────────
const AUTOSAVE_DEBOUNCE_MS = 1000;
const STATUS_REFRESH_MS = 10000;
// Fields that appear on the project-list cards (left panel). Edits to other
// fields don't need a full list refetch.
const _PROJ_LIST_FIELDS = ['name', 'client_id', 'status', 'am_username', 'start_date'];
let _autosaveTimer = null;
let _autosaveState = 'idle';   // 'idle' | 'pending' | 'saving' | 'saved' | 'error'
let _autosaveLastTs = 0;
let _autosaveStatusTimer = null;

function _setAutosaveState(s, ts) {
    if (s === _autosaveState && !ts) return;   // no-op guard avoids DOM thrash on rapid edits
    _autosaveState = s;
    if (ts) _autosaveLastTs = ts;
    _renderSaveControls();
}

function _scheduleAutoSave() {
    if (_autosaveTimer) clearTimeout(_autosaveTimer);
    _setAutosaveState('pending');
    _autosaveTimer = setTimeout(function() {
        _autosaveTimer = null;
        _flushAutoSave();
    }, AUTOSAVE_DEBOUNCE_MS);
}

async function _flushAutoSave() {
    if (_autosaveTimer) { clearTimeout(_autosaveTimer); _autosaveTimer = null; }
    if (window._allDirtyCount() === 0) return true;
    _setAutosaveState('saving');
    try {
        await _autoSaveCostExpenses();
        _setAutosaveState('saved', Date.now());
        return true;
    } catch (e) {
        _setAutosaveState('error');
        // dirty map intact (restored by _autoSaveCostExpenses catch) — user
        // can retry from the 🔴 indicator or trigger another edit.
        return false;
    }
}
window._costFlushAutoSave = _flushAutoSave;        // exposed for tab/project switch guards
window._costScheduleAutoSave = _scheduleAutoSave;  // exposed for project info inline edits

function _relativeTime(ts) {
    var sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 5) return '剛剛';
    if (sec < 60) return sec + ' 秒前';
    if (sec < 3600) return Math.floor(sec / 60) + ' 分鐘前';
    return new Date(ts).toLocaleTimeString('en-US', { hour12: false });
}

// Refresh "X 秒前" display every 10s so it doesn't feel stale.
if (!_autosaveStatusTimer) {
    _autosaveStatusTimer = setInterval(function() {
        if (_autosaveState === 'saved') _renderSaveControls();
    }, STATUS_REFRESH_MS);
}


// ── Financial Summary ──────────────────────────────────────────
async function _loadFinancialSummary(projectId) {
    const container = document.getElementById('proj-detail-finance');
    if (!container) return;
    // 這一整塊（預算/結算/毛利/成本明細/雜支）三支端點全是錢
    if (moneyGate(container)) return;
    container.innerHTML = '<div class="crm-empty" style="padding:8px;">載入中...</div>';
    try {
        // 先載子表列表 + 決定當前選中（loadCostGroups 會保證 selectedGroupId 設定）
        await callbacks.loadCostGroups?.(projectId);
        const gid = state.selectedGroupId;
        const scopeQ = gid ? ('?group_id=' + encodeURIComponent(gid)) : '';

        const [f, costData, expData] = await Promise.all([
            _fetch('/projects/' + projectId + '/financial-summary'),
            _fetch('/projects/' + projectId + '/cost-lines' + scopeQ),
            _fetch('/projects/' + projectId + '/expenses' + scopeQ),
        ]);
        const d = calcDashboard(f);
        const alert = _renderAllocationAlert(f, d);

        // 儀表板＝錨點列 + 預估/實際/差額 × 成本/雜支/剩餘/毛利 對照表。
        // 格子先留空，值由 _fillDashGrid 填 —— 初次 render 與 inline 編輯後的
        // 即時重算共用同一個填值正本，格式不會漂。
        container.innerHTML = `
            <div class="cost-dash-anchors">
              <span>合約未稅 <b>$${fmtNum(f.ex_tax)}</b></span>
              <span>目標利潤 <b>$${fmtNum(f.profit_target)}</b>（${f.profit_target_pct}%）</span>
              <span>執行預算 <b style="color:#60a5fa;">$${fmtNum(d.execBudget)}</b></span>
              <span>預估雜支 <b>$${fmtNum(d.miscEstimated)}</b>（${d.miscAuto
                    // 一張子表都沒設預算 → 數字是 0，未稅×% 只給建議（owner 2026-09-05 A 案）
                    ? `子表未設預算・建議 $${fmtNum(d.miscSuggested)}（未稅 ${d.miscPct}%）`
                    : '子表加總'}）<button class="cda-edit" onclick="window._miscPctModal()">編輯</button></span>
              <span>實際毛利 <b id="cd-anchor-pf"></b></span>
              ${f.transfer_fee ? `<span style="color:#6b7280;">帳款匯費 $${fmtNum(f.transfer_fee)}</span>` : ''}
            </div>
            <div class="cost-dash-grid">
              <span></span><span class="cdg-h">人員</span><span class="cdg-h">雜支</span><span class="cdg-h">成本</span><span class="cdg-h">剩餘預算</span><span class="cdg-h">毛利</span>
              <span class="cdg-r">預估</span><span id="cd-staff-est"></span><span id="cd-misc-est"></span><span id="cd-cost-est"></span><span id="cd-rem-est"></span><span id="cd-pf-est"></span>
              <span class="cdg-r">實際</span><span id="cd-staff-act"></span><span id="cd-misc-act"></span><span id="cd-cost-act"></span><span id="cd-rem-act"></span><span id="cd-pf-act"></span>
              <span class="cdg-r">差額</span><span id="cd-staff-diff"></span><span id="cd-misc-diff"></span><span id="cd-cost-diff"></span><span id="cd-rem-diff"></span><span id="cd-pf-diff"></span>
            </div>
            <div class="cost-progress-wrap">
              <div class="cost-progress-bar"></div>
              <div class="cost-progress-tick" title="預估合計（成本＋雜支）在預算裡的位置；藍條（實際）超過這裡＝實花超出原計畫"></div>
            </div>
            <div class="cost-progress-label"></div>
            ${alert}
            <div id="cost-groups-switcher"></div>
            ${_renderCostLines(costData.grouped || [], expData.expenses || [], f)}
        `;
        // 儀表板基準：其他子表的數字。inline 重算時「基準＋當前子表即時值」
        // 拼回專案全貌。從回應資料算（畫面只載當前子表的 cost-lines/expenses），
        // 不靠 DOM 刮字 —— 刮字的格式一變基準就整場汙染
        const curCostEst = (costData.grouped || []).reduce((s, gp) =>
            s + gp.lines.reduce((a, ln) => a + (ln.estimated_amount || 0), 0), 0);
        const curCostAct = (costData.grouped || []).reduce((s, gp) =>
            s + gp.lines.reduce((a, ln) => a + (ln.actual_amount || 0), 0), 0);
        const curMiscAct = (expData.expenses || []).reduce((s, e) => s + (e.actual || 0), 0);
        _dashBase = {
            exTax: f.ex_tax, profitTarget: f.profit_target,
            miscEstimated: d.miscEstimated, miscAuto: d.miscAuto,
            miscSuggested: d.miscSuggested, miscPct: d.miscPct,
            otherCostEst: d.costEstimated - curCostEst,
            otherCostAct: d.costActual - curCostAct,
            otherMiscAct: d.miscActual - curMiscAct,
        };
        _fillDashGrid({ ...d, miscPct: _dashBase.miscPct });
        // DO NOT clear dirty maps here. Reload may happen via enableInlineEdit's
        // post-save renderDetail() while a cost cell auto-save is still pending
        // (debounced 1s timer hasn't fired). Wiping the buffer would lose that
        // edit forever. selectProject / closeDetail / selectGroup all flush via
        // _costCheckUnsaved before triggering reload, so by the time we get
        // here in those flows the maps are already empty.
        callbacks.renderGroupSwitcher?.();
        container.querySelectorAll('.cost-staff-sel').forEach(sel => searchableSelect(sel, { placeholder: '搜尋人員...' }));
        // Inject the [🟢 已自動儲存] indicator into the detail bar.
        _renderSaveControls();
    } catch (_) {
        container.innerHTML = '<div class="crm-empty">載入失敗</div>';
    }
}

// 儀表板格子的填值正本 —— 只管畫；算的事委派 calcDashboardParts（公式單一
// 來源，跟 calcDashboard 同一條），初次 render 與 inline 即時重算共用。
function _fillDashGrid(parts) {
    const d = calcDashboardParts(parts);
    const set = (id, text, color) => {
        const el = document.getElementById(id);
        if (el) { el.textContent = text; el.style.color = color || ''; }
    };
    // 欄位定義（owner 2026-09-05）：人員＝工項、雜支、成本＝人員＋雜支、剩餘預算＝執行預算−成本、毛利＝未稅−執行預算
    set('cd-staff-est', '$' + fmtNum(d.costEstimated));
    // 自動推算的雜支淡色提示；比例的說明與編輯統一在錨點列「預估雜支」的彈窗
    set('cd-misc-est', '$' + fmtNum(d.miscEstimated), d.miscAuto ? '#6b7280' : '');
    set('cd-cost-est', '$' + fmtNum(d.totalEstimated));
    set('cd-rem-est', '$' + fmtNum(d.remaining), remainColor(d.remaining));
    set('cd-pf-est', '$' + fmtNum(d.budgetProfit) + '（' + d.budgetProfitPct + '%）');
    set('cd-staff-act', '$' + fmtNum(d.costActual));
    set('cd-misc-act', '$' + fmtNum(d.miscActual));
    set('cd-cost-act', '$' + fmtNum(d.totalActual));
    set('cd-rem-act', '$' + fmtNum(d.remainingActual), remainColor(d.remainingActual));
    // 實際毛利＝未稅−實際成本（owner 2026-09-05「剩餘預算要加到實際毛利」）——
    // 預算沒花完的那段留在公司，本來就是賺到的。預估那格仍是目標毛利（未稅−執行預算），
    // 所以差額那格＝實際比目標多賺／少賺多少，不再恆為 0。
    set('cd-pf-act', '$' + fmtNum(d.actualProfit) + '（' + d.profitPct + '%）', profitColor(d.profitPct));
    // 錨點列的實際毛利跟對照表同格同源，inline 編輯後一起動
    set('cd-anchor-pf', '$' + fmtNum(d.actualProfit) + '（' + d.profitPct + '%）',
        profitColor(d.profitPct));
    // 差額列：花錢欄 剩/超（剩餘雜支就住在雜支欄這格）；推導欄 ±（比計畫好＝綠）
    const setDL = (id, dl) => set(id, dl.text, dl.color);
    setDL('cd-staff-diff', diffLabel(d.costActual - d.costEstimated,
        !d.costEstimated && !d.costActual, true, ['剩 ', '超 ']));
    setDL('cd-cost-diff', diffLabel(d.totalActual - d.totalEstimated,
        !d.totalEstimated && !d.totalActual, true, ['剩 ', '超 ']));
    setDL('cd-misc-diff', diffLabel(d.miscActual - d.miscEstimated,
        !d.miscEstimated && !d.miscActual, true, ['剩 ', '超 ']));
    const drift = (id, val) => set(id, (val >= 0 ? '+$' : '−$') + fmtNum(Math.abs(val)),
                                   remainColor(val));
    drift('cd-rem-diff', d.remainingActual - d.remaining);
    // 毛利差額＝實際毛利−目標毛利（＝剩餘預算）。正的是好事，跟剩餘同一套配色。
    drift('cd-pf-diff', d.actualProfit - d.budgetProfit);
    // 進度條：實際填充 + 預估刻度（實際追過刻度＝超出原計畫）
    const bar = document.querySelector('.cost-progress-bar');
    if (bar) { bar.style.width = Math.min(d.usagePct, 100) + '%'; bar.style.background = barColor(d.usagePct); }
    const tick = document.querySelector('.cost-progress-tick');
    if (tick) tick.style.left = Math.min(d.estPct, 100) + '%';
    const label = document.querySelector('.cost-progress-label');
    if (label) {
        label.textContent = '預算已使用 ' + d.usagePct + '%（實際 $' + fmtNum(d.totalActual)
            + ' / 執行預算 $' + fmtNum(d.execBudget) + '）｜預估合計 $' + fmtNum(d.totalEstimated)
            + ' 佔預算 ' + d.estPct + '%';
    }
}

function _renderAllocationAlert(f, d) {
    // 一切正常時安靜 —— 分配是設定期的關心事，不用每天佔一條版面
    const execBudget = d.execBudget || 0;
    const allocated = f.allocated_budget_sum || 0;
    const missing = f.groups_missing_budget_count || 0;
    const diff = allocated - execBudget;
    let msgs = [];
    if (execBudget > 0 && diff > 0) {
        msgs.push('<div class="cg-alert cg-alert-danger">子表預算加總 $' + fmtNum(allocated) + ' 超出執行預算 $' + fmtNum(execBudget) + '，超 $' + fmtNum(diff) + '</div>');
    }
    // 沒超過執行預算，但比「規劃」（人員預估＋預估雜支）多：owner 2026-09-05 要看得到——
    // 這個差就是預估階段沒分配出去、之後會被雜支或人員吃掉的那塊
    const planned = (d.costEstimated || 0) + (d.miscEstimated || 0);
    const overPlan = allocated - planned;
    if (allocated > 0 && planned > 0 && overPlan > 0 && !(execBudget > 0 && diff > 0)) {
        msgs.push('<div class="cg-alert cg-alert-hint">子表預算加總 $' + fmtNum(allocated) + ' 比規劃（人員預估 $' + fmtNum(d.costEstimated || 0) + ' ＋ 預估雜支 $' + fmtNum(d.miscEstimated || 0) + '）多 $' + fmtNum(overPlan) + '</div>');
    }
    // 🔴 執行預算沒分配到任何子表的那塊（owner 2026-09-05）。沒有它，
    // 「子表卡的剩餘加總」永遠對不上上面那格「剩餘預算」，而畫面上沒有
    // 任何地方解釋差在哪 —— 東仁社宅：2min 剩 23,381 ＋ 3min 剩 0 ＝ 23,381，
    // 但剩餘預算(實際) 是 24,063，差的 682 正是這裡。
    const unalloc = execBudget - allocated;
    if (execBudget > 0 && allocated > 0 && unalloc > 0) {
        msgs.push('<div class="cg-alert cg-alert-hint" title="子表卡上的「剩餘」只看得到分配到子表的錢；這 $'
            + fmtNum(unalloc) + ' 沒有分給任何一張子表，但它算在上面的剩餘預算裡。'
            + '所以：Σ子表剩餘 ＋ $' + fmtNum(unalloc) + ' ＝ 剩餘預算。">'
            + '執行預算還有 $' + fmtNum(unalloc) + ' 未分配到子表'
            + '<span class="cg-muted">（子表卡的剩餘加總會比上面的剩餘預算少這個數）</span></div>');
    }
    if (missing > 0) {
        msgs.push('<div class="cg-alert cg-alert-hint">還有 ' + missing + ' 張子表未設預算</div>');
    }
    return msgs.join('');
}

// ── Expense Form ───────────────────────────────────────────────
function _showExpenseForm(editId = null, cat = '', est = 0, act = 0, notes = '') {
    const form = document.getElementById('proj-expense-form');
    if (!form) return;
    form.style.display = 'block';
    form.innerHTML = `
        <div class="expense-row" style="gap:4px;flex-wrap:wrap;padding:8px;background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;margin-bottom:6px;">
            <select id="exp-f-cat" class="crm-input" style="width:80px;">${EXPENSE_CATEGORIES.map(c => `<option value="${c}">${c}</option>`).join('')}</select>
            <input id="exp-f-est" type="number" class="crm-input" placeholder="預估" style="width:80px;text-align:right;" value="${est}">
            <input id="exp-f-act" type="number" class="crm-input" placeholder="實際" style="width:80px;text-align:right;" value="${act}">
            <input id="exp-f-notes" type="text" class="crm-input" placeholder="備註" style="flex:1;min-width:60px;" value="${_esc(notes)}">
            <input id="exp-f-receipt" type="file" accept="image/*,.pdf" style="display:none;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('exp-f-receipt').click()">📷</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projSaveExpense('${editId || ''}')">確定</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="document.getElementById('proj-expense-form').style.display='none'">取消</button>
        </div>
    `;
    if (cat) document.getElementById('exp-f-cat').value = cat;
}

// ── Cost Table Rendering ───────────────────────────────────────
const _UNIT_TYPES = ['式','日','班','時','支','套','件'];
// 未使用的範本項目要不要攤開（owner 2026-09-06「版面不大」，預設收起）。
// 只影響畫面：收起的列兩側都是空的，任何加總都不動。
let _showEmpty = false;
window._costToggleEmpty = function() {
    _showEmpty = !_showEmpty;
    if (state.selectedId) _loadFinancialSummary(state.selectedId);   // 同刪工項後的重畫路徑
};

function _renderCostLines(grouped, expenses, financialSummary) {
    const staffOpts = '<option value="">— 未指定 —</option>' +
        // 沒填職稱就不要畫「王士源 ()」—— 空括號只是佔寬（owner 2026-09-06「版面不大」）
        state.staffList.map(s => `<option value="${s.id}">${_esc(s.name)}${s.role ? ' (' + _esc(s.role) + ')' : ''}</option>`).join('');
    const _unitOpts = function(curVal) {
        return '<option value="">—</option>' +
            _UNIT_TYPES.map(function(u) { return '<option value="' + u + '"' + (curVal === u ? ' selected' : '') + '>' + u + '</option>'; }).join('');
    };

    let grandEst = 0, grandAct = 0;
    grouped.forEach(g => g.lines.forEach(ln => {
        grandEst += ln.estimated_amount || 0;
        grandAct += ln.actual_amount || 0;
    }));

    let html = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;margin-top:12px;border-top:1px solid #2e2e2e;padding-top:10px;">
        <span style="font-size:12px;font-weight:700;color:#6b7280;">成本估算 / 實際結算</span>
        <div style="display:flex;gap:6px;align-items:center;">
          <button class="crm-btn crm-btn-secondary crm-btn-sm ${_showEmpty ? 'cost-toggle-on' : ''}" onclick="window._costToggleEmpty()"
                  title="套範本帶進來但還沒填的項目，預設收起">${_showEmpty ? '收起未使用' : '顯示未使用'}</button>
          <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projShowExpenseModal()">雜支登記</button>
          <div style="position:relative;display:inline-block;" id="cost-tpl-dropdown-wrap">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costToggleTplDropdown()">套用範本 ▾</button>
            <div id="cost-tpl-dropdown" style="display:none;position:absolute;right:0;top:100%;background:#2a2a2a;border:1px solid #3a3a3a;border-radius:6px;min-width:160px;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,0.4);margin-top:4px;"></div>
          </div>
          <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._costSaveAsTemplate()">儲存為範本</button>
        </div>
      </div>`;

    // 🔴 成本項目與行政雜支是**兩個獨立資料源** —— 沒有成本項目時雜支照樣要畫。
    // 原本整個雜支區包在 else 裡：只有零用金列的專案，錢在這頁一格都看不到
    // （2026-08-18 踩到，跟「綁了專案沒掛子表」同一類靜默失蹤）。
    html += '<div class="cost-table">';
    if (grouped.length === 0) {
        html += `<div class="crm-empty" style="padding:16px 0;text-align:center;">
            <div style="margin-bottom:10px;color:#9ca3af;">尚無項目</div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projInitCostLines()">初始化標準項目</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:8px;" onclick="window._costImportFromQuote()">從報價匯入</button>
        </div>`;
    } else {
        html += `<div class="cost-row cost-row-header">
            <span class="cost-col-item">項目</span>
            <span class="cost-col-price">單價</span>
            <span class="cost-col-qty">數量</span>
            <span class="cost-col-unit">單位</span>
            <span class="cost-col-amt">預估金額</span>
            <span class="cost-col-staff cost-divider">預估人員</span>
            <span class="cost-col-copy"></span>
            <span class="cost-col-price">單價</span>
            <span class="cost-col-qty">數量</span>
            <span class="cost-col-unit">單位</span>
            <span class="cost-col-amt">結算金額</span>
            <span class="cost-col-staff">執行人員</span>
            <span class="cost-col-diff">差異</span>
            <span class="cost-col-actions"></span>
          </div>`;

        for (const group of grouped) {
            let phaseEst = 0, phaseAct = 0;
            group.lines.forEach(ln => {
                phaseEst += ln.estimated_amount || 0;
                phaseAct += ln.actual_amount || 0;
            });
            const phaseDiff = phaseAct - phaseEst;
            const phaseDiffColor = phaseDiff < 0 ? '#86efac' : phaseDiff > 0 ? '#fca5a5' : '#9ca3af';

            html += `<div class="cost-row cost-phase-header">
              <span class="cost-col-item" style="font-weight:700;">${_esc(group.phase)}</span>
              <span class="cost-col-price"></span><span class="cost-col-qty"></span><span class="cost-col-unit"></span><span class="cost-col-amt"></span><span class="cost-col-staff cost-divider"></span><span class="cost-col-copy"></span><span class="cost-col-price"></span><span class="cost-col-qty"></span><span class="cost-col-unit"></span><span class="cost-col-amt"></span><span class="cost-col-staff"></span>
              <span class="cost-col-diff" style="display:flex;justify-content:flex-end;">
                <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:0 6px;font-size:11px;line-height:18px;" onclick="window._costAddItem('${_esc(group.phase)}')">+</button>
              </span>
              <span class="cost-col-actions">
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:1px 5px;" onclick="window._costDeletePhase('${_esc(group.phase)}')">✕</button>
              </span>
            </div>`;

            // 🔴 未使用的範本項目預設收起（owner 2026-09-06「版面不大」）：東仁 2min
            // 25 列裡 15 列是套範本帶進來的空項目，每列照樣佔一整行輸入框。
            // 「空」＝兩側都沒金額、沒單價、沒人（只有名字）。收起的列名字列在摺疊列上，
            // 一鍵展開；收起的都是 0，所以任何加總都不受影響。
            const _emptyLine = (l) => l.estimated_amount == null && l.actual_amount == null
                && l.estimated_unit_price == null && l.actual_unit_price == null
                && !l.estimated_staff_id && !l.actual_staff_id;
            const _folded = _showEmpty ? [] : group.lines.filter(_emptyLine);
            for (const ln of group.lines) {
                if (!_showEmpty && _emptyLine(ln)) continue;
                const diff = (ln.actual_amount || 0) - (ln.estimated_amount || 0);
                const bothZero = !ln.estimated_amount && !ln.actual_amount;
                const _d = diffLabel(diff, bothZero);
                const diffStr = _d.text;
                const diffColor = _d.color;

                const _selHtml = (curVal) => {
                    return staffOpts.replace(
                        new RegExp(`value="${curVal}"`),
                        `value="${curVal}" selected`
                    );
                };

                html += `
                  <div class="cost-row" data-line-id="${ln.id}">
                    <span class="cost-col-item cost-editable" onclick="window._costEditName(this,'${ln.id}','${_esc(ln.item_name)}')">${_esc(ln.item_name)}</span>
                    <span class="cost-col-price cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','estimated_unit_price',${ln.estimated_unit_price ?? "''"})">
                      ${ln.estimated_unit_price != null ? '$' + fmtNum(ln.estimated_unit_price) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-qty cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','estimated_quantity',${ln.estimated_quantity ?? "''"})">
                      ${ln.estimated_quantity != null ? ln.estimated_quantity : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-unit">
                      <select class="crm-input cost-unit-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','estimated_unit_type',this.value)">
                        ${_unitOpts(ln.estimated_unit_type)}
                      </select>
                    </span>
                    <span class="cost-col-amt">
                      ${ln.estimated_amount != null ? '$' + fmtNum(ln.estimated_amount) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-staff cost-divider">
                      <select class="crm-input cost-staff-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','estimated_staff_id',this.value)">
                        ${_selHtml(ln.estimated_staff_id)}
                      </select>
                    </span>
                    <span class="cost-col-copy" onclick="window._costCopyToActual('${ln.id}')">→</span>
                    <span class="cost-col-price cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','actual_unit_price',${ln.actual_unit_price ?? "''"})">
                      ${ln.actual_unit_price != null ? '$' + fmtNum(ln.actual_unit_price) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-qty cost-editable"
                          onclick="window._costStartEdit(this,'${ln.id}','actual_quantity',${ln.actual_quantity ?? "''"})">
                      ${ln.actual_quantity != null ? ln.actual_quantity : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-unit">
                      <select class="crm-input cost-unit-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','actual_unit_type',this.value)">
                        ${_unitOpts(ln.actual_unit_type)}
                      </select>
                    </span>
                    <span class="cost-col-amt">
                      ${ln.actual_amount != null ? '$' + fmtNum(ln.actual_amount) : '<span class="crm-muted">—</span>'}
                    </span>
                    <span class="cost-col-staff">
                      <select class="crm-input cost-staff-sel"
                              onchange="window._costMarkDirtyField('${ln.id}','actual_staff_id',this.value)">
                        ${_selHtml(ln.actual_staff_id)}
                      </select>
                    </span>
                    <span class="cost-col-diff" style="color:${diffColor};">${diffStr}</span>
                    <span class="cost-col-actions">
                      <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:1px 5px;"
                              onclick="window._projDeleteCostLine('${ln.id}')">✕</button>
                    </span>
                  </div>`;
            }

            if (_folded.length) {
                html += `<div class="cost-row cost-row-fold">
                    <span class="cost-col-item" style="flex:1;white-space:normal;">未使用 ${_folded.length} 項：${_esc(_folded.map(l => l.item_name).join('、'))}
                      <span class="cost-fold-toggle" onclick="window._costToggleEmpty()">展開</span></span>
                  </div>`;
            }
            html += `
              <div class="cost-row cost-row-subtotal">
                <span class="cost-col-item" style="color:#9ca3af;font-style:italic;">${_esc(group.phase)} 小計</span>
                <span class="cost-col-price"></span>
                <span class="cost-col-qty"></span>
                <span class="cost-col-unit"></span>
                <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(phaseEst)}</span>
                <span class="cost-col-staff cost-divider"></span>
                <span class="cost-col-copy"></span>
                <span class="cost-col-price"></span>
                <span class="cost-col-qty"></span>
                <span class="cost-col-unit"></span>
                <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(phaseAct)}</span>
                <span class="cost-col-staff"></span>
                <span class="cost-col-diff" style="color:${diffLabel(phaseDiff, !phaseEst && !phaseAct, true).color};font-weight:600;">
                  ${diffLabel(phaseDiff, !phaseEst && !phaseAct, true).text}
                </span>
                <span class="cost-col-actions"></span>
              </div>`;
        }
    }

    // ── 行政雜支 section (from crm_project_expenses) ──
    // 雜支是自己的信封：預算（本子表「雜支預算」，可就地點改）− 已用 ＝ 剩餘。
    // 🔴 不再用 financialSummary.misc_budget（那是專案層的 % 自動推算）——
    // 這一區是「本子表」範圍，預算也要跟著本子表走。
    const grp = (state.costGroups || []).find(g => g.id === state.selectedGroupId);
    const groupMisc = grp ? grp.misc_budget_amount : null;   // null＝未設，不是 0
    const miscBudget = groupMisc != null ? groupMisc : ((grp && grp.misc_budget_effective) || 0);   // owner 2026-09-05: unset = default 5% of the sub-table budget
    const expActualTotal = (expenses || []).reduce((s, e) => s + (e.actual || 0), 0);
    const miscLeft = miscBudget - expActualTotal;   // 剩餘雜支（差額欄用它的負值）
    const expDiff = -miscLeft;

    html += `<div class="cost-phase-header" style="display:flex;justify-content:space-between;align-items:center;">
      <span>行政雜支</span>
      <div style="display:flex;gap:6px;align-items:center;">
        <span class="exp-mini">
          雜支預算 <span class="cost-editable" onclick="window._miscBudgetEdit(this)"
                title="點一下直接改本子表的雜支預算">${groupMisc == null ? (grp && grp.misc_budget_default ? '預設 $' + fmtNum(grp.misc_budget_default) + '（預算 5%）' : '未設') : '$' + fmtNum(groupMisc)}</span>
          ｜ 已用 $${fmtNum(expActualTotal)}${miscBudget ? `
          ｜ 剩餘 <span style="color:${remainColor(miscLeft)};">$${fmtNum(miscLeft)}</span>` : ''}
        </span>
        <button class="crm-btn crm-btn-secondary cost-toolbar-btn" onclick="window._projShowExpenseModal()">+</button>
        <button class="crm-btn crm-btn-secondary cost-toolbar-btn" onclick="window._projBrowseReceipts()" title="瀏覽收據">&#128065;</button>
        <button class="crm-btn crm-btn-secondary cost-toolbar-btn" onclick="window._projShareExpenseLink()" title="複製當前子表的雜支登記連結">🔗</button>
      </div>
    </div>`;

    // 表頭：這段複用 cost-row 版型但欄位跟上面完全不同，沒表頭時
    // 空格子的「—」根本猜不出是哪一欄（owner 2026-08-18 回報看不懂）
    html += `
      <div class="cost-row cost-row-expense exp-head">
        <span class="exp-col-date">消費日</span>
        <span class="exp-col-cat">類別</span>
        <span class="exp-col-sub">細項</span>
        <span class="exp-col-amt">金額</span>
        <span class="exp-col-payee">收款人</span>
        <span class="exp-col-receipt">收據</span>
        <span class="exp-col-action"></span>
      </div>`;

    // 送請款（owner 2026-09-02「雜支可以送請款進請款單」）：一列一張，
    // 硬連結記在請款單的 `expense_id` 上（後端 `POST /payments` 有 409 守衛，
    // 前端換按鈕擋不住雙擊／兩個分頁）。
    // 🔴 零用金流進來的列（staff_id）**不給這顆鈕** —— 那些錢已經有自己的一條
    // 請款路（零用金批次 → 依「會計項目×月份」開應付款），兩條路都走就是同一筆
    // 錢請兩次。它們的 pill 已經標著「零用金」。
    _expRowById = Object.fromEntries((expenses || []).map((x) => [x.id, x]));
    const claimCell = (e) => {
        if (e.payment_id) {
            const paid = e.payment_status === '已付款';
            // 點開＝那張單的詳情，收回／改狀態的鈕在視窗頁尾（這一欄只有 88px，
            // 塞不下三顆；人員費用那側欄寬夠，鈕直接畫在列上）。
            // `_expClaimDone` 讓那些動作重畫**雜支這一區**，不是人員費用那區。
            return `<span class="exp-claimed" style="color:${paid ? '#86efac' : '#fb923c'};"
                       title="點開請款單（可收回、可改狀態）"
                       onclick="window._costViewPayment('${e.payment_id}','window._expClaimDone')"
                       >${paid ? '已付款' : '已請款'}</span>`;
        }
        if (e.staff_id) return '';
        return `<button class="exp-claim" title="開一張請款單（這一列一張）"
                        onclick="window._expCreatePayment('${e.id}')">請款</button>`;
    };

    if (expenses && expenses.length > 0) {
        // 照消費日排（舊→新，最新的落在底部緊鄰新增列）；同日第二筆起日期淡化
        const sorted = [...expenses].sort((a, b) =>
            String(a.expense_date || a.created_at || '').localeCompare(
                String(b.expense_date || b.created_at || '')));
        let prevDate = null;
        for (const e of sorted) {
            // 消費日優先（零用金帶真實消費日）；手動列退回登記日
            const dateStr = _esc(e.expense_date || e.created_at || '');
            const dateDisplay = dateStr && dateStr === prevDate
                ? `<span class="exp-date-rep">${dateStr}</span>` : dateStr;
            prevDate = dateStr;
            // 空值一律留白 —— 「—」三連發是雜訊，留白反而讓有料的格子跳出來
            const subDisplay = e.sub_item ? _esc(e.sub_item) : '';
            // 零用金流進來的列：收款人讀員工檔（payee 是自由文字，那些列必空）、
            // 掛「零用金」pill；已進請款單（claim_id）→ 這頁唯讀，後端同樣 409。
            const fromPetty = !!e.staff_id;
            const locked = !!e.claim_id;
            const payeeName = e.staff_name || e.payee || '';
            const pill = fromPetty
                ? `<span class="exp-pill" title="來自零用金請款${e.status ? '（狀態：' + _esc(e.status) + '）' : ''}${locked ? '，已進請款單 — 請到零用金頁處理' : ''}">零用金</span>`
                : '';
            // 可編輯性收在這一個 helper：locked 全鎖；零用金列的收款人是身分不是文字
            const edCell = (cls, field, cur, display) => {
                if (locked || (fromPetty && field === 'payee')) return `<span class="${cls}">${display}</span>`;
                const curArg = typeof cur === 'number' ? cur : `'${_esc(cur || '')}'`;
                return `<span class="${cls} cost-editable" onclick="window._expEdit(this,'${e.id}','${field}',${curArg})">${display}</span>`;
            };
            html += `
              <div class="cost-row cost-row-expense"${locked ? ' title="已進零用金請款單：這頁唯讀，請到零用金頁處理"' : ''}>
                ${edCell('exp-col-date', 'expense_date', dateStr, dateDisplay)}
                ${edCell('exp-col-cat', 'category', e.category, `<span class="exp-cat-pill">${_esc(e.category)}</span>`)}
                ${edCell('exp-col-sub', 'sub_item', e.sub_item, subDisplay)}
                ${edCell('exp-col-amt', 'actual', e.actual || 0, '$' + fmtNum(e.actual))}
                ${edCell('exp-col-payee', 'payee', e.payee, (payeeName ? _esc(payeeName) : '') + pill)}
                <span class="exp-col-receipt">${e.receipt_url ? '<a href="' + e.receipt_url + '" target="_blank" style="color:#3b82f6;">📎</a>' : ''}</span>
                <span class="exp-col-action">${locked ? '' : `${claimCell(e)}
                  <button class="exp-del" title="刪除這筆"
                          onclick="window._projDeleteExpense('${e.id}')">✕</button>`}
                </span>
              </div>`;
        }
    }

    // 就地新增列 —— 日常登記的正路（modal 留給要附收據的情況）。
    // $0 佔位列 2026-08-18 退場後，這條就是「從這裡登記」的唯一入口。
    const qaToday = today();
    const qaStaff = '<option value="">收款人…</option>' + (state.staffList || []).map(st =>
        `<option value="${_esc(st.name)}">${_esc(st.name)}</option>`).join('');
    html += `
      <div class="cost-row cost-row-expense exp-qa">
        <span class="exp-col-date"><input id="exp-qa-date" type="date" value="${qaToday}"></span>
        <span class="exp-col-cat"><select id="exp-qa-cat" data-no-search>${EXPENSE_CATEGORIES.map(c => `<option>${c}</option>`).join('')}</select></span>
        <span class="exp-col-sub"><input id="exp-qa-sub" placeholder="＋ 新增一筆：細項（Enter 儲存）"
               onkeydown="if(event.key==='Enter')window._expQuickAdd()"></span>
        <span class="exp-col-amt"><input id="exp-qa-amt" type="number" min="0" placeholder="金額"
               onkeydown="if(event.key==='Enter')window._expQuickAdd()"></span>
        <span class="exp-col-payee"><select id="exp-qa-payee" data-no-search title="收款人">${qaStaff}</select></span>
        <span class="exp-col-receipt"></span>
        <span class="exp-col-action">
          <button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:1px 6px;"
                  onclick="window._expQuickAdd()">新增</button>
        </span>
      </div>`;

    html += `
      <div class="cost-row cost-row-subtotal">
        <span class="cost-col-item" style="color:#9ca3af;font-style:italic;">行政雜支 小計</span>
        <span class="cost-col-price"></span>
        <span class="cost-col-qty"></span>
        <span class="cost-col-unit"></span>
        <span class="cost-col-amt" style="font-weight:600;${groupMisc == null ? 'color:#9ca3af;' : ''}" title="${groupMisc == null ? '預設：子表預算 5%' : ''}">${miscBudget ? '$' + fmtNum(miscBudget) : '—'}</span>
        <span class="cost-col-staff cost-divider"></span>
        <span class="cost-col-price"></span>
        <span class="cost-col-qty"></span>
        <span class="cost-col-unit"></span>
        <span class="cost-col-amt" style="font-weight:600;">$${fmtNum(expActualTotal)}</span>
        <span class="cost-col-staff"></span>
        <span class="cost-col-diff" style="color:${diffLabel(expDiff, !miscBudget && !expActualTotal, true).color};font-weight:600;">
          ${diffLabel(expDiff, !miscBudget && !expActualTotal, true).text}
        </span>
        <span class="cost-col-actions"></span>
      </div>`;

    // ── Grand total (cost lines + expenses) ──
    // 預估側計入雜支「預算」而不是實際數 —— 否則雜支對差額的貢獻恆為 0，
    // 跟上面小計那欄（實際-預算）自相矛盾。
    const totalEst = grandEst + miscBudget;
    const totalAct = grandAct + expActualTotal;
    const totalDiff = totalAct - totalEst;
    html += `
      <div class="cost-row cost-row-total">
        <span class="cost-col-item">本子表合計</span>
        <span class="cost-col-price"></span>
        <span class="cost-col-qty"></span>
        <span class="cost-col-unit"></span>
        <span class="cost-col-amt">$${fmtNum(totalEst)}</span>
        <span class="cost-col-staff cost-divider"></span>
        <span class="cost-col-price"></span>
        <span class="cost-col-qty"></span>
        <span class="cost-col-unit"></span>
        <span class="cost-col-amt">$${fmtNum(totalAct)}</span>
        <span class="cost-col-staff"></span>
        <span class="cost-col-diff" style="color:${diffLabel(totalDiff, !totalEst && !totalAct, true).color};">
          ${diffLabel(totalDiff, !totalEst && !totalAct, true).text}
        </span>
        <span class="cost-col-actions"></span>
      </div>`;

    html += '</div>';
    return html;
}

// ── Copy estimated → actual ────────────────────────────────────
window._costCopyToActual = function(lineId) {
    var row = document.querySelector('[data-line-id="' + lineId + '"]');
    if (!row) return;
    var dirty = window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});

    // Read estimated values from cells or dirty map
    var estPriceEl = row.querySelector('[onclick*="estimated_unit_price"]');
    var estQtyEl = row.querySelector('[onclick*="estimated_quantity"]');
    var estStaffEl = row.querySelectorAll('.cost-staff-sel')[0];
    var estUnitEl = row.querySelectorAll('.cost-unit-sel')[0];

    var price = dirty['estimated_unit_price'];
    if (price === undefined && estPriceEl) {
        var pt = estPriceEl.textContent.replace(/[$,]/g, '').trim();
        price = pt && pt !== '—' ? parseInt(pt) : null;
    }
    var qty = dirty['estimated_quantity'];
    if (qty === undefined && estQtyEl) {
        var qt = estQtyEl.textContent.trim();
        qty = qt && qt !== '—' ? parseInt(qt) : null;
    }
    var staffId = estStaffEl ? estStaffEl.value : '';
    var unitType = estUnitEl ? estUnitEl.value : '';

    // Write to dirty map
    if (price != null) dirty['actual_unit_price'] = price;
    if (qty != null) dirty['actual_quantity'] = qty;
    dirty['actual_staff_id'] = staffId || null;
    dirty['actual_unit_type'] = unitType || null;
    var amt = (price && qty) ? price * qty : null;
    if (amt != null) dirty['actual_amount'] = amt;

    // Update actual side cells visually
    var actPriceEl = row.querySelector('[onclick*="actual_unit_price"]');
    var actQtyEl = row.querySelector('[onclick*="actual_quantity"]');
    var actStaffEl = row.querySelectorAll('.cost-staff-sel')[1];
    var actUnitEl = row.querySelectorAll('.cost-unit-sel')[1];
    var amtCells = row.querySelectorAll('.cost-col-amt');

    if (actPriceEl && price != null) actPriceEl.innerHTML = '$' + fmtNum(price);
    if (actQtyEl && qty != null) actQtyEl.textContent = qty;
    if (actStaffEl) {
        actStaffEl.value = staffId;
        // searchableSelect wraps the <select> with a visible <input>; setting
        // .value directly skips the input, so the user sees stale "搜尋人員...".
        if (typeof actStaffEl._syncSsValue === 'function') actStaffEl._syncSsValue();
    }
    if (actUnitEl) actUnitEl.value = unitType;
    if (amtCells[1] && amt != null) amtCells[1].innerHTML = '$' + fmtNum(amt);

    _costUpdateDiff(row);
    _costUpdateDashboard();
    _scheduleAutoSave();
};

// ── Subtotal / Dashboard live update ───────────────────────────
function _costUpdateSubtotals() {
    // Recalculate all phase subtotals and grand total from individual rows
    var allRows = document.querySelectorAll('.cost-row[data-line-id]');
    var phases = {};
    allRows.forEach(function(row) {
        // Find which phase this row belongs to by scanning backwards for phase header
        var prev = row.previousElementSibling;
        var phase = '';
        while (prev) {
            if (prev.classList.contains('cost-phase-header')) {
                var itemEl = prev.querySelector('.cost-col-item');
                if (itemEl) phase = itemEl.textContent.trim();
                break;
            }
            prev = prev.previousElementSibling;
        }
        if (!phase) return;
        if (!phases[phase]) phases[phase] = { est: 0, act: 0 };
        var amts = row.querySelectorAll('.cost-col-amt');
        if (amts[0]) {
            var e = amts[0].textContent.replace(/[$,]/g, '').trim();
            if (e && e !== '—') phases[phase].est += parseInt(e);
        }
        if (amts[1]) {
            var a = amts[1].textContent.replace(/[$,]/g, '').trim();
            if (a && a !== '—') phases[phase].act += parseInt(a);
        }
    });

    // Update subtotal rows
    var subtotals = document.querySelectorAll('.cost-row-subtotal');
    var grandEst = 0, grandAct = 0, miscEstScraped = 0, miscActScraped = 0;
    subtotals.forEach(function(sub) {
        var label = sub.querySelector('.cost-col-item');
        if (!label) return;
        var text = label.textContent.trim();
        // Match phase name from "XXX 小計"
        for (var p in phases) {
            if (text.indexOf(p) >= 0 && text.indexOf('小計') >= 0) {
                var amts = sub.querySelectorAll('.cost-col-amt');
                if (amts[0]) amts[0].textContent = '$' + fmtNum(phases[p].est);
                if (amts[1]) amts[1].textContent = '$' + fmtNum(phases[p].act);
                var diff = phases[p].act - phases[p].est;
                var diffCell = sub.querySelector('.cost-col-diff');
                if (diffCell) {
                    var dl = diffLabel(diff, !phases[p].est && !phases[p].act, true);
                    diffCell.textContent = dl.text;
                    diffCell.style.color = dl.color;
                }
                grandEst += phases[p].est;
                grandAct += phases[p].act;
                break;
            }
        }
        // 行政雜支 subtotal — 實際欄從列即時重加（inline 改完金額小計才會跟著動）；
        // 預估欄是雜支預算（靜態），照畫面讀。就地新增列的格子裝的是 input，
        // textContent 為空 → 自然被跳過。
        if (text.indexOf('行政雜支') >= 0) {
            var expAct = 0;
            document.querySelectorAll('.cost-row-expense:not(.exp-head) .exp-col-amt').forEach(function(c) {
                var t = c.textContent.replace(/[$,]/g, '').trim();
                if (t && t !== '—') expAct += parseInt(t) || 0;
            });
            var eAmts = sub.querySelectorAll('.cost-col-amt');
            var expEst = 0;
            if (eAmts[0]) { var v = eAmts[0].textContent.replace(/[$,]/g, '').trim(); if (v && v !== '—') expEst = parseInt(v) || 0; }
            if (eAmts[1]) eAmts[1].textContent = '$' + fmtNum(expAct);
            var eDiffCell = sub.querySelector('.cost-col-diff');
            if (eDiffCell) {
                var edl = diffLabel(expAct - expEst, !expEst && !expAct, true);
                eDiffCell.textContent = edl.text;
                eDiffCell.style.color = edl.color;
            }
            grandEst += expEst;
            grandAct += expAct;
            miscEstScraped = expEst;
            miscActScraped = expAct;
        }
    });

    // Update grand total row
    var totalRow = document.querySelector('.cost-row-total');
    if (totalRow) {
        var tAmts = totalRow.querySelectorAll('.cost-col-amt');
        if (tAmts[0]) tAmts[0].textContent = '$' + fmtNum(grandEst);
        if (tAmts[1]) tAmts[1].textContent = '$' + fmtNum(grandAct);
        var tDiff = grandAct - grandEst;
        var tDiffCell = totalRow.querySelector('.cost-col-diff');
        if (tDiffCell) {
            var tdl = diffLabel(tDiff, !grandEst && !grandAct, true);
            tDiffCell.textContent = tdl.text;
            tDiffCell.style.color = tdl.color;
        }
    }
    return { costEst: grandEst - miscEstScraped, costAct: grandAct - miscActScraped,
             miscAct: miscActScraped };
}

function _costUpdateDashboard() {
    // inline 編輯後的即時重算：當前子表用畫面掃出來的即時值，其他子表用
    // render 時算好的 _dashBase 基準，拼回專案全貌後交給同一個填值正本。
    var totals = _costUpdateSubtotals();
    if (!_dashBase || !document.getElementById('cd-cost-est')) return;
    _fillDashGrid({
        exTax: _dashBase.exTax, profitTarget: _dashBase.profitTarget,
        miscEstimated: _dashBase.miscEstimated, miscAuto: _dashBase.miscAuto,
        miscSuggested: _dashBase.miscSuggested, miscPct: _dashBase.miscPct,
        costEstimated: _dashBase.otherCostEst + totals.costEst,
        costActual: _dashBase.otherCostAct + totals.costAct,
        miscActual: _dashBase.otherMiscAct + totals.miscAct,
    });
}

function _costUpdateDiff(row) {
    var amtCells = row.querySelectorAll('.cost-col-amt');
    var diffCell = row.querySelector('.cost-col-diff');
    if (!diffCell || amtCells.length < 2) return;
    var estText = amtCells[0].textContent.replace(/[$,]/g, '').trim();
    var actText = amtCells[1].textContent.replace(/[$,]/g, '').trim();
    var est = estText && estText !== '—' ? parseInt(estText) : 0;
    var act = actText && actText !== '—' ? parseInt(actText) : 0;
    var bothZero = !est && !act;
    var diff = act - est;
    var d = diffLabel(diff, bothZero);
    diffCell.style.color = d.color;
    diffCell.textContent = d.text;
}

// ── Inline edit: numeric fields ────────────────────────────────
window._costStartEdit = function(cell, lineId, field, currentVal) {
    if (cell.querySelector('input')) return;
    var isQty = field.indexOf('quantity') >= 0;
    var isUnitOrQty = field.indexOf('unit_price') >= 0 || isQty;
    var input = document.createElement('input');
    input.type = 'number';
    input.className = 'crm-input';
    input.style.cssText = 'width:100%;max-width:100%;box-sizing:border-box;padding:2px 4px;font-size:11px;text-align:right;';
    input.value = currentVal !== '' ? currentVal : '';
    input.min = '0';
    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    input.select();

    input.addEventListener('blur', function() {
        try {
            var val = input.value.trim();
            var parsed = val === '' ? null : parseInt(val);
            window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});
            window._costDirtyMap[lineId][field] = parsed;
            _scheduleAutoSave();
            // Display: no $ for quantity fields
            if (isQty) {
                cell.innerHTML = val === '' ? '<span class="crm-muted">—</span>' : val;
            } else {
                cell.innerHTML = val === '' ? '<span class="crm-muted">—</span>' : '$' + fmtNum(parseInt(val));
            }
            // Auto-calculate amount when unit_price or quantity changes
            if (isUnitOrQty) {
                var row = cell.closest('.cost-row');
                if (row) {
                    var side = field.indexOf('estimated') === 0 ? 'estimated' : 'actual';
                    var dirty = window._costDirtyMap[lineId] || {};
                    // Get current values from dirty map or from displayed cells
                    var priceCell = row.querySelector('[onclick*="' + side + '_unit_price"]');
                    var qtyCell = row.querySelector('[onclick*="' + side + '_quantity"]');
                    var price = dirty[side + '_unit_price'];
                    if (price === undefined && priceCell) {
                        var pt = priceCell.textContent.replace(/[$,]/g, '').trim();
                        price = pt && pt !== '—' ? parseInt(pt) : null;
                    }
                    var qty = dirty[side + '_quantity'];
                    if (qty === undefined && qtyCell) {
                        var qt = qtyCell.textContent.trim();
                        qty = qt && qt !== '—' ? parseInt(qt) : null;
                    }
                    var amt = (price && qty) ? price * qty : null;
                    var amtCells = row.querySelectorAll('.cost-col-amt');
                    var amtCell = side === 'estimated' ? amtCells[0] : amtCells[1];
                    if (amtCell) {
                        amtCell.innerHTML = amt != null ? '$' + fmtNum(amt) : '<span class="crm-muted">—</span>';
                    }
                    // Update diff cell
                    _costUpdateDiff(row);
                    _costUpdateDashboard();
                }
            } else {
                // Non unit/qty field edited (e.g. direct amount) — also update diff
                var row2 = cell.closest('.cost-row');
                if (row2) { _costUpdateDiff(row2); _costUpdateDashboard(); }
            }
        } catch(e) { alert('Error: ' + e.message); }
    });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { _loadFinancialSummary(state.selectedId); }
    });
};

// ── Mark field dirty (for select changes) ──────────────────────
window._costMarkDirtyField = function(lineId, field, value) {
    window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});
    window._costDirtyMap[lineId][field] = value || null;
    _scheduleAutoSave();
};

// ── Auto-save cost lines + expenses + project info ────────────────
// Endpoints take entry IDs (line / expense / project_id captured up-front),
// so dirty edits can be saved even after the user has navigated away.
async function _autoSaveCostExpenses() {
    // Snapshot-then-clear: subsequent edits during in-flight PUT go to the
    // fresh map and survive (the previous "wipe after PUT" pattern lost any
    // edit done during the network roundtrip).
    var savingCost = window._costDirtyMap;
    var savingExp = window._expDirtyMap;
    var savingProj = window._projDirtyMap;
    window._costDirtyMap = {};
    window._expDirtyMap = {};
    window._projDirtyMap = {};

    var costEntries = Object.entries(savingCost);
    var expEntries = Object.entries(savingExp);
    var projHasDirty = Object.keys(savingProj).length > 0;
    if (costEntries.length === 0 && expEntries.length === 0 && !projHasDirty) return;

    var projectId = state.selectedId;  // capture so a mid-flight nav can't redirect the PUT
    try {
        await Promise.all([
            ...costEntries.map(function(entry) {
                return _fetch('/project-cost-lines/' + entry[0], {
                    method: 'PUT', body: JSON.stringify(entry[1])
                });
            }),
            ...expEntries.map(function(entry) {
                return _fetch('/project-expenses/' + entry[0], {
                    method: 'PATCH', body: JSON.stringify(entry[1])
                });
            }),
            projHasDirty && projectId
                ? _fetch('/projects/' + projectId, { method: 'PUT', body: JSON.stringify(savingProj) })
                : null,
        ].filter(Boolean));
        // Refresh project list cache only if a list-displayed field changed
        // (left panel cards show name / client / status / am / start_date).
        // Skipping for description/notes/contract/etc avoids fetching all
        // projects on every keystroke burst.
        if (projHasDirty && _PROJ_LIST_FIELDS.some(f => f in savingProj)) {
            callbacks.loadProjects?.();
        }
    } catch (e) {
        // Restore so user can retry. New edits added during the failed PUT
        // take precedence over the stale saving values.
        window._costDirtyMap = Object.assign({}, savingCost, window._costDirtyMap);
        window._expDirtyMap = Object.assign({}, savingExp, window._expDirtyMap);
        window._projDirtyMap = Object.assign({}, savingProj, window._projDirtyMap);
        throw e;
    }
    // No reload — client-side updates (subtotals/dashboard/diff) already ran
    // on the edit events; reloading would destroy input focus / select dropdowns.
}


// ── Init cost lines (standard template) ────────────────────────
window._projInitCostLines = async function() {
    if (!state.selectedId) return;
    try {
        const r = await _fetch('/projects/' + state.selectedId + '/cost-lines/init', {
            method: 'POST',
            body: JSON.stringify({ cost_group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
        if (r.added === 0) alert('所有標準項目已存在，無需初始化');
    } catch (e) { alert('初始化失敗：' + e.message); }
};

// ── Import cost lines from quotation ──────────────────────────
window._costImportFromQuote = async function() {
    if (!state.selectedId) return;
    document.getElementById('cost-tpl-dropdown')?.style.setProperty('display', 'none');
    try {
        const data = await _fetch('/projects/' + state.selectedId + '/quotations');
        const quots = data.quotations || [];
        if (quots.length === 0) { alert('此專案尚無報價單'); return; }

        if (quots.length === 1) {
            await _doImportFromQuote(quots[0].id);
            return;
        }
        // Multiple versions — show modal picker (same pattern as _projActivate)
        let overlay = document.getElementById('cost-import-quote-overlay');
        if (overlay) overlay.remove();
        overlay = document.createElement('div');
        overlay.id = 'cost-import-quote-overlay';
        overlay.className = 'crm-modal-overlay';
        overlay.style.display = 'flex';
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
        overlay.innerHTML = `
          <div class="crm-modal" style="max-width:400px;">
            <div class="crm-modal-header">
              <h3>從報價匯入</h3>
              <button onclick="document.getElementById('cost-import-quote-overlay').remove()" class="crm-detail-close">✕</button>
            </div>
            <div class="crm-modal-body">
              <p style="font-size:13px;color:#9ca3af;margin-bottom:12px;">選擇報價版本匯入為成本項目（現有項目將被取代）</p>
              <div style="display:flex;flex-direction:column;gap:6px;">
                ${quots.map(q => {
                    const price = q.final_price != null ? q.final_price : q.total;
                    return `<button class="pi-activate-option" onclick="window._doImportFromQuote('${q.id}')">
                      <span>v${q.version}</span>
                      <span style="font-size:11px;color:#9ca3af;">${q.status}</span>
                      <span style="font-weight:600;color:#e0e0e0;">$${(price ?? 0).toLocaleString()}</span>
                    </button>`;
                }).join('')}
              </div>
            </div>
          </div>`;
        document.body.appendChild(overlay);
    } catch (e) { alert('匯入失敗：' + e.message); }
};

window._doImportFromQuote = async function(quotationId) {
    const overlay = document.getElementById('cost-import-quote-overlay');
    if (overlay) overlay.remove();
    const groupName = state.costGroups.find(g => g.id === state.selectedGroupId)?.name || '主表';
    if (!confirm('將從報價匯入成本項目到「' + groupName + '」，該子表現有項目將被取代。確定？')) return;
    try {
        const r = await _fetch('/projects/' + state.selectedId + '/cost-lines/import-from-quotation', {
            method: 'POST',
            body: JSON.stringify({ quotation_id: quotationId, cost_group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
        if (r.added === 0) alert('報價中沒有項目可匯入');
    } catch (e) { alert('匯入失敗：' + e.message); }
};

// ── Delete entire phase ────────────────────────────────────────
window._costDeletePhase = async function(phase) {
    if (!state.selectedId) return;
    if (!confirm('確定刪除「' + phase + '」所有項目？')) return;
    try {
        await _fetch('/projects/' + state.selectedId + '/cost-lines/phase', {
            method: 'DELETE',
            body: JSON.stringify({ phase: phase, group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert('刪除失敗：' + e.message); }
};

// ── Delete single cost line ────────────────────────────────────
window._projDeleteCostLine = async function(lineId) {
    if (!confirm('確定刪除此項目？')) return;
    try {
        await _fetch('/project-cost-lines/' + lineId, { method: 'DELETE' });
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert(e.message); }
};

// ── 送請款：一列雜支 → 一張請款單 ───────────────────────────────
// owner 2026-09-02「crm 系統裡面的雜支，可以送請款進請款單」。
// 🔴 **複用人員費用那顆的 modal**（`_costCreatePayment`），不另刻一份表單 ——
// 那支已經處理了代墊（收款人換成代墊人、費用歸屬留原人）、報支項目、預計付款月
// 與必填檢查。再刻一份的話，「代墊怎麼記」就會有兩條規則。
// 硬連結 `expense_id` 由 modal 帶進 POST /payments；重複請款由後端 409 擋。
// 請款單的動作（收回／改狀態）做完要重畫**雜支這一區** —— 人員費用那側重畫的
// 是自己那區，兩者不能共用一個寫死的重畫目標。
window._expClaimDone = function() { _loadFinancialSummary(state.selectedId); };

window._expCreatePayment = function(expenseId) {
    const e = _expRowById[expenseId];
    if (!e) return;
    // 摘要：類別＋細項（細項常是空的，只有類別也讀得懂）
    const label = [e.category, e.sub_item].filter(Boolean).join(' ');
    // 預計付款月預帶消費日那個月 —— 雜支是先花了才請，付款月通常就是當月
    const month = (e.expense_date || '').slice(0, 7);
    window._costCreatePayment(e.payee || '', e.actual || 0,
                              '雜支：' + label, '應付款', false,
                              { expenseId: e.id, plannedMonth: month,
                                onDone: window._expClaimDone });
};

// ── 就地新增一筆雜支（行政雜支區底部固定的輸入列）─────────────
window._expQuickAdd = async function() {
    if (!state.selectedId) return;
    const amt = parseInt(document.getElementById('exp-qa-amt')?.value) || 0;
    const sub = (document.getElementById('exp-qa-sub')?.value || '').trim();
    if (!amt && !sub) return;   // 空列不送
    try {
        await _fetch('/projects/' + state.selectedId + '/expenses', {
            method: 'POST',
            body: JSON.stringify({
                category: document.getElementById('exp-qa-cat').value,
                sub_item: sub,
                estimated: 0,
                actual: amt,
                payee: document.getElementById('exp-qa-payee').value,
                notes: '',
                cost_group_id: state.selectedGroupId,
                expense_date: document.getElementById('exp-qa-date').value || '',
            }),
        });
        // 重載後把焦點放回細項欄 —— 連續登記（Enter、Enter、Enter）不用重新點
        await _loadFinancialSummary(state.selectedId);
        document.getElementById('exp-qa-sub')?.focus();
    } catch (e) { alert('新增失敗：' + e.message); }
};

// ── 雜支預算就地編輯（寫回本子表的 misc_budget_amount）───────────
window._miscBudgetEdit = function(el) {
    const gid = state.selectedGroupId;
    if (!gid || el.querySelector('input')) return;
    const grp = (state.costGroups || []).find(g => g.id === gid);
    const cur = grp && grp.misc_budget_amount != null ? grp.misc_budget_amount : null;
    // 取消/沒改 → 本地還原就好，別打 3 支 API 整版重載
    const restore = () => { el.textContent = cur == null ? '未設' : '$' + fmtNum(cur); };
    const input = document.createElement('input');
    input.type = 'number'; input.min = '0'; input.value = cur == null ? '' : cur;
    input.className = 'crm-input';
    input.style.cssText = 'width:90px;font-size:11px;padding:1px 4px;';
    el.innerHTML = ''; el.appendChild(input);
    input.focus(); input.select();
    let done = false;
    const commit = async () => {
        if (done) return; done = true;
        const raw = input.value.trim();
        const val = raw === '' ? null : (parseInt(raw) || 0);   // 留空＝清回「未設」
        if (val === cur) { restore(); return; }
        try {
            await _fetch('/cost-groups/' + gid, { method: 'PUT',
                body: JSON.stringify({ misc_budget_amount: val }) });
            _loadFinancialSummary(state.selectedId);
        } catch (e) { alert('儲存失敗：' + e.message); restore(); }
    };
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
        if (ev.key === 'Escape') { done = true; restore(); }
    });
};

// ── 雜支比例編輯彈窗（寫回專案的 misc_budget_pct）────────────────
window._miscPctModal = function() {
    if (!state.selectedId || document.getElementById('misc-pct-overlay')) return;
    const cur = _dashBase ? _dashBase.miscPct : 5;
    const overlay = document.createElement('div');
    overlay.id = 'misc-pct-overlay';
    overlay.className = 'crm-modal-overlay';
    overlay.style.display = 'flex';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
      <div class="crm-modal" style="max-width:360px;">
        <div class="crm-modal-header">
          <h3>預估雜支比例</h3>
          <button onclick="document.getElementById('misc-pct-overlay').remove()" class="crm-detail-close">✕</button>
        </div>
        <div class="crm-modal-body">
          <div class="crm-field" style="margin-bottom:10px;">
            <label>比例（%，佔合約未稅）</label>
            <input id="misc-pct-input" type="number" class="crm-input" min="0" max="100" value="${cur}"
                   onkeydown="if(event.key==='Enter')window._miscPctSave()">
          </div>
          <div style="font-size:11px;color:#6b7280;line-height:1.6;">
            子表未設「雜支預算」時，預估雜支＝合約未稅 × 此比例。<br>
            子表設了雜支預算就以該手動值為準，比例不生效。
          </div>
        </div>
        <div class="crm-modal-footer">
          <button onclick="document.getElementById('misc-pct-overlay').remove()" class="crm-btn crm-btn-secondary">取消</button>
          <button onclick="window._miscPctSave()" class="crm-btn crm-btn-primary">儲存</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const inp = document.getElementById('misc-pct-input');
    inp.focus(); inp.select();
};

window._miscPctSave = async function() {
    const val = parseInt(document.getElementById('misc-pct-input')?.value);
    if (isNaN(val) || val < 0 || val > 100) { alert('比例需為 0–100 的整數'); return; }
    if (_dashBase && val === _dashBase.miscPct) {   // 沒改 → 關窗就好
        document.getElementById('misc-pct-overlay')?.remove();
        return;
    }
    try {
        await _fetch('/projects/' + state.selectedId, { method: 'PUT',
            body: JSON.stringify({ misc_budget_pct: val }) });
        document.getElementById('misc-pct-overlay')?.remove();
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert('儲存失敗：' + e.message); }
};

// ── Inline edit: 行政雜支（類別/細項/金額/請款人）─────────────
window._expEdit = function(cell, expId, field, currentVal) {
    if (cell.querySelector('input, select')) return;
    const isCategory = field === 'category';
    const isAmount = field === 'actual';
    const isDate = field === 'expense_date';
    let input;
    if (isCategory) {
        input = document.createElement('select');
        input.innerHTML = EXPENSE_CATEGORIES.map(c =>
            `<option value="${c}"${c === currentVal ? ' selected' : ''}>${c}</option>`
        ).join('');
    } else {
        input = document.createElement('input');
        input.type = isAmount ? 'number' : (isDate ? 'date' : 'text');
        if (isAmount) input.min = '0';
        input.value = currentVal !== null && currentVal !== undefined ? currentVal : '';
    }
    input.className = 'crm-input';
    input.style.cssText = 'width:100%;max-width:100%;box-sizing:border-box;padding:2px 4px;font-size:11px;' +
        (isAmount ? 'text-align:right;' : '');
    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    if (input.select) input.select();

    const commit = function() {
        let val = input.value;
        if (isAmount) val = val === '' ? 0 : parseInt(val) || 0;
        window._expDirtyMap[expId] = window._expDirtyMap[expId] || {};
        window._expDirtyMap[expId][field] = val;
        if (isAmount) {
            cell.textContent = '$' + fmtNum(val);
            // Admin expense amount changed → client-side recalc（_costUpdateDashboard
            // 內部就會跑 _costUpdateSubtotals，不用各叫一次）
            _costUpdateDashboard();
        } else if (isCategory) {
            cell.innerHTML = `<span class="exp-cat-pill">${_esc(val)}</span>`;
        } else {
            cell.innerHTML = val ? _esc(val) : '';
        }
        _scheduleAutoSave();
    };

    if (isCategory) {
        input.addEventListener('change', commit);
        input.addEventListener('blur', function() {
            // select 的 blur 也送一次 commit（避免直接點到其他地方）
            if (!cell.querySelector('select')) return;
            commit();
        });
    } else {
        input.addEventListener('blur', commit);
    }
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') {
            if (isAmount) {
                cell.textContent = '$' + fmtNum(parseInt(currentVal) || 0);
            } else if (isCategory) {
                cell.innerHTML = `<span class="exp-cat-pill">${_esc(currentVal)}</span>`;
            } else {
                cell.innerHTML = currentVal ? _esc(currentVal) : '';
            }
        }
    });
};

// ── Edit item name ─────────────────────────────────────────────
window._costEditName = function(cell, lineId, currentName) {
    if (cell.querySelector('input')) return;
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'crm-input';
    input.style.cssText = 'width:100%;padding:2px 6px;font-size:12px;';
    input.value = currentName;
    cell.innerHTML = '';
    cell.appendChild(input);
    input.focus();
    input.select();

    input.addEventListener('blur', function() {
        try {
            var val = input.value.trim();
            if (!val || val === currentName) {
                cell.textContent = currentName;
                return;
            }
            window._costDirtyMap[lineId] = Object.assign({}, window._costDirtyMap[lineId] || {});
            window._costDirtyMap[lineId]['item_name'] = val;
            cell.textContent = val;
        } catch(e) { alert('Error: ' + e.message); }
    });
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
        if (e.key === 'Escape') { cell.textContent = currentName; }
    });
};

// ── Add custom item to phase ───────────────────────────────────
window._costAddItem = function(phase) {
    if (!state.selectedId) return;
    var rows = document.querySelectorAll('.cost-row-subtotal');
    var target = null;
    for (var i = 0; i < rows.length; i++) {
        var item = rows[i].querySelector('.cost-col-item');
        if (item && item.textContent.indexOf(phase) >= 0) { target = rows[i]; break; }
    }
    if (!target) return;
    if (document.getElementById('cost-add-form')) document.getElementById('cost-add-form').remove();
    var unitOpts = '<option value="">—</option>' + _UNIT_TYPES.map(function(u) { return '<option value="' + u + '">' + u + '</option>'; }).join('');
    var staffOpts = '<option value="">— 未指定 —</option>' + state.staffList.map(function(s) { return '<option value="' + s.id + '">' + _esc(s.name) + (s.role ? ' (' + _esc(s.role) + ')' : '') + '</option>'; }).join('');
    var form = document.createElement('div');
    form.id = 'cost-add-form';
    form.className = 'cost-row';
    form.style.cssText = 'background:#1e1e1e;border-radius:6px;border:1px solid #3a3a3a;padding:6px;margin:4px 0;';
    form.innerHTML = '' +
        '<span class="cost-col-item"><input id="cost-add-name" type="text" class="crm-input" placeholder="項目名稱" style="width:100%;font-size:11px;padding:3px 6px;box-sizing:border-box;"></span>' +
        '<span class="cost-col-price"><input id="cost-add-price" type="number" class="crm-input" placeholder="單價" style="width:100%;font-size:11px;padding:2px 4px;text-align:right;box-sizing:border-box;-moz-appearance:textfield;"></span>' +
        '<span class="cost-col-qty"><input id="cost-add-qty" type="number" class="crm-input" placeholder="數量" style="width:100%;font-size:11px;padding:2px 4px;text-align:center;box-sizing:border-box;-moz-appearance:textfield;"></span>' +
        '<span class="cost-col-unit"><select id="cost-add-unit" class="crm-input cost-unit-sel">' + unitOpts + '</select></span>' +
        '<span class="cost-col-amt" id="cost-add-amt" style="color:#9ca3af;">—</span>' +
        '<span class="cost-col-staff cost-divider"><select id="cost-add-staff" class="crm-input cost-staff-sel">' + staffOpts + '</select></span>' +
        '<span class="cost-col-copy"></span>' +
        '<span class="cost-col-price"></span><span class="cost-col-qty"></span><span class="cost-col-unit"></span><span class="cost-col-amt"></span><span class="cost-col-staff"></span>' +
        '<span class="cost-col-diff" style="display:flex;gap:4px;">' +
            '<button class="crm-btn crm-btn-primary crm-btn-sm" style="padding:2px 6px;" onclick="window._costDoAddItem(\'' + phase + '\')">確定</button>' +
            '<button class="crm-btn crm-btn-secondary crm-btn-sm" style="padding:2px 6px;" onclick="document.getElementById(\'cost-add-form\').remove()">取消</button>' +
        '</span>' +
        '<span class="cost-col-actions"></span>';
    target.parentNode.insertBefore(form, target);
    document.getElementById('cost-add-name').focus();
    // Auto-calc amount preview
    var _calcPreview = function() {
        var p = parseInt(document.getElementById('cost-add-price').value) || 0;
        var q = parseInt(document.getElementById('cost-add-qty').value) || 0;
        var el = document.getElementById('cost-add-amt');
        if (el) el.textContent = (p && q) ? '$' + fmtNum(p * q) : '—';
    };
    document.getElementById('cost-add-price').addEventListener('input', _calcPreview);
    document.getElementById('cost-add-qty').addEventListener('input', _calcPreview);
    document.getElementById('cost-add-name').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') window._costDoAddItem(phase);
        if (e.key === 'Escape') form.remove();
    });
};

window._costDoAddItem = async function(phase) {
    var name = (document.getElementById('cost-add-name') || {}).value || '';
    name = name.trim();
    if (!name) { alert('請輸入項目名稱'); return; }
    var price = parseInt((document.getElementById('cost-add-price') || {}).value) || null;
    var qty = parseInt((document.getElementById('cost-add-qty') || {}).value) || null;
    var unitType = (document.getElementById('cost-add-unit') || {}).value || null;
    var staffId = (document.getElementById('cost-add-staff') || {}).value || null;
    var amt = (price && qty) ? price * qty : null;
    try {
        await _fetch('/projects/' + state.selectedId + '/cost-lines', {
            method: 'POST', body: JSON.stringify({
                phase: phase, item_name: name, sort_order: 99,
                cost_group_id: state.selectedGroupId,
                estimated_unit_price: price, estimated_quantity: qty,
                estimated_unit_type: unitType, estimated_amount: amt,
                estimated_staff_id: staffId
            })
        });
        _loadFinancialSummary(state.selectedId);
    } catch (e) { alert('新增失敗：' + e.message); }
};

// ── Template dropdown ──────────────────────────────────────────
window._costToggleTplDropdown = async function() {
    const dd = document.getElementById('cost-tpl-dropdown');
    if (!dd) return;
    if (dd.style.display !== 'none') { dd.style.display = 'none'; return; }
    dd.innerHTML = '<div style="padding:8px;color:#9ca3af;font-size:11px;">載入中...</div>';
    dd.style.display = 'block';
    try {
        const data = await _fetch('/cost-line-templates');
        const tpls = data.templates || [];
        let items = `<div class="cost-tpl-item" onclick="window._costApplyTemplate('__default__')">
            <span>標準項目</span><span style="color:#6b7280;font-size:10px;">${_COST_LINE_DEFAULT_COUNT} 項</span>
        </div>`;
        for (const t of tpls) {
            items += `<div class="cost-tpl-item" style="display:flex;justify-content:space-between;align-items:center;gap:4px;">
                <span onclick="window._costApplyTemplate('${t.id}')" style="flex:1;cursor:pointer;">${_esc(t.name)}<span style="color:#6b7280;font-size:10px;margin-left:4px;">${t.item_count} 項</span></span>
                <button style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:11px;padding:0 2px;" onclick="event.stopPropagation();window._costRenameTemplate('${t.id}','${_esc(t.name)}')">✎</button>
                <button class="crm-btn crm-btn-danger crm-btn-sm" style="padding:0 4px;font-size:10px;line-height:16px;" onclick="event.stopPropagation();window._costDeleteTemplate('${t.id}')">✕</button>
            </div>`;
        }
        if (tpls.length === 0) items += '<div style="padding:4px 12px;color:#6b7280;font-size:11px;">尚無自訂範本</div>';
        items += '<div style="border-top:1px solid #3a3a3a;margin:4px 0;"></div>';
        items += `<div class="cost-tpl-item" onclick="window._costImportFromQuote()">📄 從報價匯入...</div>`;
        dd.innerHTML = items;
    } catch (e) { dd.innerHTML = '<div style="padding:8px;color:#fca5a5;">載入失敗</div>'; }
    // Close on click outside
    const _close = (e) => { if (!document.getElementById('cost-tpl-dropdown-wrap')?.contains(e.target)) { dd.style.display = 'none'; document.removeEventListener('click', _close); } };
    setTimeout(() => document.addEventListener('click', _close), 0);
};

const _COST_LINE_DEFAULT_COUNT = 25;

window._costApplyTemplate = async function(templateId) {
    if (!state.selectedId) return;
    document.getElementById('cost-tpl-dropdown').style.display = 'none';
    const groupName = state.costGroups.find(g => g.id === state.selectedGroupId)?.name || '主表';
    if (!confirm('將範本套用到「' + groupName + '」，該子表現有項目將被取代。確定？')) return;
    try {
        const r = await _fetch('/projects/' + state.selectedId + '/cost-lines/apply-template', {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId, cost_group_id: state.selectedGroupId })
        });
        _loadFinancialSummary(state.selectedId);
        if (r.added === 0) alert('所有項目已存在，無需新增');
    } catch (e) { alert('套用失敗：' + e.message); }
};

window._costSaveAsTemplate = async function() {
    if (!state.selectedId) return;
    const name = prompt('請輸入範本名稱：');
    if (!name?.trim()) return;
    try {
        const r = await _fetch('/cost-line-templates', {
            method: 'POST', body: JSON.stringify({ name: name.trim(), project_id: state.selectedId })
        });
        alert('範本已建立（' + r.item_count + ' 個項目）');
    } catch (e) { alert('建立失敗：' + e.message); }
};

window._costRenameTemplate = async function(templateId, currentName) {
    var newName = prompt('修改範本名稱：', currentName);
    if (!newName || !newName.trim() || newName.trim() === currentName) return;
    try {
        await _fetch('/cost-line-templates/' + templateId, {
            method: 'PUT', body: JSON.stringify({ name: newName.trim() })
        });
        window._costToggleTplDropdown();
    } catch (e) { alert('修改失敗：' + e.message); }
};

window._costDeleteTemplate = async function(templateId) {
    if (!confirm('確定刪除此範本？')) return;
    try {
        await _fetch('/cost-line-templates/' + templateId, { method: 'DELETE' });
        window._costToggleTplDropdown();
    } catch (e) { alert(e.message); }
};

// ── Unsaved changes guard ──────────────────────────────────────
// ── Render persistent [auto-save status] in the detail bar ────
function _renderSaveControls() {
    var actions = document.getElementById('proj-bar-actions');
    if (!actions) return;

    var status = actions.querySelector('._cost-save-status');
    if (!status) {
        status = document.createElement('span');
        status.className = '_cost-save-status';
        status.style.cssText = 'font-size:11px;color:#9ca3af;margin:0 8px;display:inline-flex;align-items:center;gap:4px;';
        actions.insertBefore(status, actions.firstChild);
    }

    if (_autosaveState === 'pending')      status.innerHTML = '🟡 編輯中…';
    else if (_autosaveState === 'saving')  status.innerHTML = '🔵 正在儲存…';
    else if (_autosaveState === 'error')   status.innerHTML = '🔴 儲存失敗 <a href="#" style="color:#fca5a5;text-decoration:underline;">重試</a>';
    else if (_autosaveState === 'saved')   status.innerHTML = '🟢 已自動儲存 ' + _relativeTime(_autosaveLastTs);
    else status.innerHTML = '';
    if (_autosaveState === 'error') {
        status.querySelector('a').addEventListener('click', function(e) { e.preventDefault(); _flushAutoSave(); });
    }
}

// Backwards-compat: cost-groups.js calls _costCheckUnsaved(callback) when
// switching sub-tables. With auto-save we just flush pending dirty then run
// the callback — no modal needed.
window._costCheckUnsaved = function(callback) {
    if (window._allDirtyCount() === 0) { callback?.(); return true; }
    if (!callback) {
        // beforeunload sync path — flush is best-effort, browser may not wait.
        _flushAutoSave();
        return true;
    }
    // Run callback only when flush succeeds. On failure, dirty map is intact
    // (restored by _autoSaveCostExpenses) and user sees 🔴 indicator — staying
    // on the current project lets them retry. Skipping callback also blocks
    // the navigation that would have wiped the dirty map.
    _flushAutoSave().then(function(ok) { if (ok) callback(); });
    return false;
};
// detail.js calls this after renderDetail wipes the actions area, to re-inject
// the auto-save status indicator. Same function as the internal _renderSaveControls.
window._costShowSaveBtn = _renderSaveControls;

// ── Init: register all window handlers + beforeunload ──────────
function initCostHandlers() {
    window.addEventListener('beforeunload', function(e) {
        if (window._allDirtyCount() > 0) {
            // Best-effort flush; modern browsers ignore async work in beforeunload
            // but fetch with keepalive can still complete.
            _flushAutoSave();
            e.preventDefault();
            e.returnValue = '';
        }
    });
    _renderSaveControls();
}

// ── Exports ────────────────────────────────────────────────────
export { _loadFinancialSummary, _showExpenseForm, initCostHandlers };
