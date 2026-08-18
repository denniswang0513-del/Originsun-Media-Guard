/**
 * crm-projects-calc.js — 純財務計算函式
 * 無 DOM 依賴，可被 cost / detail / 未來報表模組共用
 */

/** 執行預算 = 未稅 - 目標利潤 */
export function calcExecBudget(exTax, profitTarget) {
    return exTax - profitTarget;
}

/** 剩餘預算 = 執行預算 - 預估成本 - 預估雜支 */
export function calcRemaining(execBudget, costlineEstimated, expenseEstimated) {
    return execBudget - costlineEstimated - expenseEstimated;
}

/** 毛利 = 未稅 - 實際總成本 */
export function calcActualProfit(exTax, costlineActual, expenseActual) {
    return exTax - costlineActual - expenseActual;
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
export function calcDashboard(f) {
    const execBudget = calcExecBudget(f.ex_tax, f.profit_target);
    const miscAuto = f.misc_budget_total == null;
    const miscEstimated = miscAuto ? (f.misc_budget || 0) : f.misc_budget_total;
    const miscActual = f.expense_actual || 0;
    const costEstimated = f.costline_estimated || 0;
    const costActual = f.costline_actual || 0;
    const totalEstimated = costEstimated + miscEstimated;
    const totalActual = costActual + miscActual;
    const remaining = execBudget - totalEstimated;        // 預估剩餘（排完還能排多少）
    const remainingActual = execBudget - totalActual;     // 實際剩餘（真的還能花多少）
    const miscRemaining = miscEstimated - miscActual;     // 剩餘雜支（雜支自己的信封）
    const estProfit = f.ex_tax - totalEstimated;
    const actualProfit = f.ex_tax - totalActual;
    const estProfitPct = calcProfitPct(f.ex_tax, estProfit);
    const profitPct = calcProfitPct(f.ex_tax, actualProfit);
    const usagePct = calcUsagePct(execBudget, totalActual);
    const estPct = calcUsagePct(execBudget, totalEstimated);
    return { execBudget, miscAuto, miscEstimated, miscActual, miscRemaining,
             costEstimated, costActual, totalEstimated, totalActual,
             remaining, remainingActual, estProfit, estProfitPct,
             actualProfit, profitPct, usagePct, estPct };
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

/** 差異標籤（顏色 + 文字） */
export function diffLabel(diff, bothZero = false, showLabel = false) {
    if (bothZero) return { text: '—', color: '#9ca3af' };
    if (diff === 0) return { color: '#9ca3af', text: '—' };
    const color = diff < 0 ? '#86efac' : '#fca5a5';
    if (showLabel) {
        const prefix = diff < 0 ? '預算結餘 ' : '預算超支 ';
        return { color, text: prefix + '$' + Math.abs(diff).toLocaleString('zh-TW') };
    }
    const sign = diff > 0 ? '+' : '';
    return { color, text: sign + '$' + Math.abs(diff).toLocaleString('zh-TW') };
}
