/**
 * quote-amounts.js — 報價的金額試算與付款階段字串。**零 import**（葉節點：桌機報價分頁
 * 與手機 CRM 都用，不能把 crm-utils 那整包 CRM state 拖進手機頁）。
 *
 * 🔴 算法要跟後端 `routers/crm/quotes._calc_quotation` 逐字對齊，畫面上的數字才不會
 * 跟存進去的不一樣：小計＝Σ(數量×單價)、扣稅前折扣（不會扣成負的）、稅額**無條件捨去**
 * （後端是 `int(taxable * rate / 100)`）、含稅總計＝可稅額＋稅額。
 *
 * 「專案優惠」不在這裡：那是「含稅總計 − 最終報價」的差額，正本在
 * `core.quotation_pdf.promo_amount`（只有印 PDF 用得到）。
 */

/**
 * @param {{items?: Array<{quantity?: number, unit_price?: number}>, discount?: number, taxRate?: number}} arg
 * @returns {{subtotal: number, discount: number, taxable: number, tax: number, total: number}}
 */
export function quoteTotals({ items = [], discount = 0, taxRate = 0 } = {}) {
    const subtotal = items.reduce((s, it) => s + (parseInt(it.quantity) || 0) * (parseInt(it.unit_price) || 0), 0);
    const disc = parseInt(discount) || 0;
    const taxable = Math.max(subtotal - disc, 0);
    const tax = Math.floor(taxable * (parseInt(taxRate) || 0) / 100);
    return { subtotal, discount: disc, taxable, tax, total: taxable + tax };
}

/** `簽約 30%, 拍攝 40%, 交片 30%` → `[{label,pct}]`（逗號、全形逗號、斜線都可以分隔）。 */
export function parsePaymentStages(text) {
    if (!text) return [];
    return String(text).split(/[,，/]/).map(s => {
        const m = s.trim().match(/^(.+?)\s*(\d+)%?$/);
        return m ? { label: m[1].trim(), pct: parseInt(m[2]) } : null;
    }).filter(Boolean);
}

/** `[{label,pct}]` → 給人編輯的那一行字。 */
export function paymentStagesToText(stages) {
    return (stages || []).map(s => `${s.label} ${s.pct}%`).join(', ');
}
