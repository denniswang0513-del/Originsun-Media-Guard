/**
 * crm-projects-calc.js — 純財務計算函式
 * 無 DOM 依賴，可被 cost / detail / 未來報表模組共用
 */

/** 執行預算 = 未稅 - 目標利潤 */
export function calcExecBudget(exTax, profitTarget) {
    return exTax - profitTarget;
}

/** 毛利率 % */
export function calcProfitPct(exTax, actualProfit) {
    return exTax > 0 ? Math.round(actualProfit / exTax * 100) : 0;
}

/** 預算使用率 % */
export function calcUsagePct(execBudget, totalEstimated) {
    return execBudget > 0 ? Math.round(totalEstimated / execBudget * 100) : 0;
}

/** 從 financial-summary API 回應計算所有儀表板數值（2026-08-18 owner 定案）。
 *
 * 兩套並排：預估（計畫）與實際（執行），每套各有 成本/雜支/剩餘/毛利。
 * 🔴 預估雜支 ＝ 子表「雜支預算」手動加總（misc_budget_total）；全部未設才
 * 退回 misc_budget（未稅×雜支比自動推算，miscAuto=true）。不再用逐列
 * estimated 加總（expense_estimated）—— $0 佔位列退場後那個數字恆為 0。
 * usagePct 是**實際**口徑（實際結算/執行預算）；estPct 是預估刻度。 */
/** 對照表衍生鏈的**單一正本** —— 初次 render（calcDashboard 包這支）與
 * inline 編輯後的即時重算（cost.js `_fillDashGrid`）都走這裡，公式改一處生效。
 * parts = { exTax, profitTarget, costEstimated, costActual,
 *           miscEstimated, miscAuto, miscActual } */
export function calcDashboardParts(p) {
    const execBudget = calcExecBudget(p.exTax, p.profitTarget);
    const totalEstimated = p.costEstimated + p.miscEstimated;
    const totalActual = p.costActual + p.miscActual;
    const estProfit = p.exTax - totalEstimated;
    const actualProfit = p.exTax - totalActual;
    // owner 2026-09-05 的定義：成本＝人員＋雜支（totalEstimated／totalActual）、剩餘預算＝執行預算−成本、
    // 毛利（對照表那欄）＝未稅−執行預算＝目標利潤（budgetProfit，預估與實際同一個數；多省的錢住在「剩餘預算」）。
    // 錨點列的「實際毛利」仍是未稅−實際成本（actualProfit），跟財務摘要／收付款同源。
    const budgetProfit = p.exTax - execBudget;
    return {
        ...p, execBudget, totalEstimated, totalActual, estProfit, actualProfit, budgetProfit,
        budgetProfitPct: calcProfitPct(p.exTax, budgetProfit),
        remaining: execBudget - totalEstimated,       // 預估剩餘（排完還能排多少）
        remainingActual: execBudget - totalActual,    // 實際剩餘（真的還能花多少）
        miscRemaining: p.miscEstimated - p.miscActual,  // 剩餘雜支（雜支自己的信封）
        estProfitPct: calcProfitPct(p.exTax, estProfit),
        profitPct: calcProfitPct(p.exTax, actualProfit),
        usagePct: calcUsagePct(execBudget, totalActual),
        estPct: calcUsagePct(execBudget, totalEstimated),
    };
}

export function calcDashboard(f) {
    const miscAuto = f.misc_budget_total == null;
    return calcDashboardParts({
        exTax: f.ex_tax, profitTarget: f.profit_target,
        costEstimated: f.costline_estimated || 0,
        costActual: f.costline_actual || 0,
        miscEstimated: miscAuto ? (f.misc_budget || 0) : f.misc_budget_total,
        miscAuto,
        miscPct: f.misc_budget_pct != null ? f.misc_budget_pct : 5,
        miscActual: f.expense_actual || 0,
    });
}

/** 剩餘顏色 */
export function remainColor(remaining) {
    return remaining >= 0 ? '#86efac' : '#fca5a5';
}

/** 毛利顏色 */
export function profitColor(profitPct) {
    return profitPct >= 20 ? '#86efac' : profitPct >= 0 ? '#fbbf24' : '#fca5a5';
}

/** 進度條顏色 */
export function barColor(usagePct) {
    return usagePct > 100 ? '#ef4444' : usagePct > 80 ? '#f59e0b' : '#3b82f6';
}

/** 差異標籤（顏色 + 文字）。diff 慣例＝實際 − 預估（負＝比計畫省，綠）。
 * showLabel=true 時前綴可換（儀表板對照表用短版 ['剩 ', '超 ']）。 */
export function diffLabel(diff, bothZero = false, showLabel = false,
                          prefixes = ['預算結餘 ', '預算超支 ']) {
    if (bothZero) return { text: '—', color: '#9ca3af' };
    if (diff === 0) return { color: '#9ca3af', text: '—' };
    const color = diff < 0 ? '#86efac' : '#fca5a5';
    if (showLabel) {
        const prefix = diff < 0 ? prefixes[0] : prefixes[1];
        return { color, text: prefix + '$' + Math.abs(diff).toLocaleString('zh-TW') };
    }
    const sign = diff > 0 ? '+' : '';
    return { color, text: sign + '$' + Math.abs(diff).toLocaleString('zh-TW') };
}
