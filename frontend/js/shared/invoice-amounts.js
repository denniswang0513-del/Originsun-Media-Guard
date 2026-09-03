/**
 * invoice-amounts.js — 發票三個金額欄的推算。**零 import**（葉節點：手機 CRM 頁
 * 與桌機 CRM 都用，不能把 crm-utils 那整包 CRM state 拖進手機頁）。
 *
 * 兩個方向都收在這裡：來源有時記未稅、有時記含稅，若讓兩條輸入路徑各自進位，
 * 同一筆錢會產生尾差。含稅→未稅用 round(total / 1.05)（166,950 → 159,000，
 * 回推 159,000×1.05 = 166,950 ✓），稅額一律取兩者之差，保證三欄自洽。
 *
 * 稅率參數 `vatPct` 的正本在後端 `core.finance_logic.VAT_PCT`（手機頁從
 * /crm/m/options 的 invoice.vat_pct 拿到再傳進來）；沒傳就用同一個法定值 5。
 * 🔴 費率一改，這裡的預設值與後端要一起改 —— 桌機發票本是純函式層拿不到 request。
 *
 * @param {string|number} value  使用者打的那一格
 * @param {'ex'|'total'} mode    那一格是未稅還是含稅
 * @param {number} [vatPct=5]    營業稅率（%）
 */
export function invoiceAmounts(value, mode, vatPct = 5) {
    const n = parseInt(value) || 0;
    if (!n) return { amount_ex_tax: null, amount_total: null, tax_amount: null };
    const rate = 1 + (Number.isFinite(Number(vatPct)) ? Number(vatPct) : 5) / 100;
    const total = mode === 'total' ? n : Math.round(n * rate);
    const ex = mode === 'total' ? Math.round(n / rate) : n;
    return { amount_ex_tax: ex, amount_total: total, tax_amount: total - ex };
}
