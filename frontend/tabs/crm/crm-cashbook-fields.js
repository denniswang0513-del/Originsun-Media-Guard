// ────────────────────────────────────────────────────────────────────────────
// crm-cashbook-fields.js —— 收支明細的一段：inline 編輯的欄位建構（發票／專案／銀行帳戶下拉、候選發票、未收餘額）
//
// 2026-09-12 從 crm-cashbook.js（2,844 行，超過單次讀取上限）原樣切出來。主檔的狀態（_entries／_invoiceList…）
// 用 ES module 的 live binding 讀，**這裡不賦值**（賦值只在主檔）；主檔再 import 這裡的函式回去 ——
// 循環 import 只在函式內用到，模組頂層不碰對方的東西。掃原始碼的測試用 _srcscan.cashbook_src()（主檔＋五段串起來）。
// ────────────────────────────────────────────────────────────────────────────
import { esc as _esc, fmtNum as _fmtNum, projectOptionsHtml } from './crm-utils.js';
import { ledgerHasInvoices } from '../finance/fin-utils.js';
import { _CATEGORIES, _bankAccounts, _invoiceList, _projectList } from './crm-cashbook.js';

// ── Edit Fields (for inline edit) ───────────────────────────

/** 下拉 option 標記的單一正本 —— 這三個下拉（詳情快速連結、編輯表單、modal）
 *  本來各自把同一串 <option> 寫一遍，改標籤要改三處。 */
export function _invOptsHtml(selectedId, placeholder = '— 不關聯 —') {
    return `<option value="">${placeholder}</option>` + _invoiceCandidates(selectedId).map(inv =>
        `<option value="${_esc(inv.id)}"${inv.id === selectedId ? ' selected' : ''}>${_esc(_invoiceLabel(inv))}</option>`).join('');
}

export const _projOptsHtml = (selectedId, placeholder = '— 不關聯 —') =>
    projectOptionsHtml(_projectList, placeholder, selectedId);

/** 這張發票還差多少錢沒收。outstanding 由後端從收支分配算（GET /invoices），
 *  沒有這欄的舊回應退回面額。 */
export function _outstanding(inv) {
    return inv.outstanding != null ? inv.outstanding : (inv.amount_total || 0);
}

/** 收齊了沒。
 *  🔴 讀後端的 settled，不要自己用 `outstanding <= 0` 判 —— 真正的規則是
 *  invoice_is_settled（含 NT$50 匯費容差，394 張歷史發票裡有 42 張靠它）。
 *  自己判的話，被匯費短收 30 元的那張會在應收帳款「收齊」、在這裡「尚欠 $30」。 */
function _settled(inv) {
    return inv.settled != null ? !!inv.settled : _outstanding(inv) <= 0;
}

/** 發票下拉的候選清單：**還沒收齊的**才列（394 張裡多數早就結案，全倒進下拉
 *  等於在已結案名單裡大海撈針）。
 *
 *  🔴 判準用 outstanding（面額 − 實收）而**不是** payment_status 字串：
 *  「已收款」只要收到第一筆就會被標上（sync_invoice_paid 與 _mark_invoice_received
 *  都只看有沒有收款、不看金額），拿它當濾網會讓「10 萬收了 4 萬」的發票從下拉裡
 *  消失 —— 而那正是要用這個下拉記第二期的時候。作廢仍排除（終態，不是欠款）。
 *
 *  🔴 `keepId` 一定要留：編輯既有收支時它掛的那張多半已經收齊，濾掉的話下拉
 *  選不到目前值，一存檔就把關聯洗掉。
 */
export function _invoiceCandidates(keepId) {
    return _invoiceList.filter(inv =>
        inv.issue_status === '已開立'
        && (inv.payment_status || '') !== '作廢'
        && (!_settled(inv) || inv.id === keepId));
}

/** 下拉顯示：內容 · 金額 · 客戶 · 發票號碼 —— 四項一起才分得出同名同額的兩張
 *  （實測「台灣歐姆龍 第五、六品 $166,950」與「第7品 $166,950」只差品名）。
 *  收過一部分的另外標出尚欠多少，分期時才知道這次該填多少。 */
function _invoiceLabel(inv) {
    const parts = [inv.title || '(無標題)', '$' + (inv.amount_total || 0).toLocaleString('zh-TW')];
    if (inv.company_name) parts.push(inv.company_name);
    if (inv.invoice_number) parts.push(inv.invoice_number);
    const left = _outstanding(inv);
    if (_settled(inv)) parts.push('已結清');
    else if (inv.collected) parts.push(`已收 $${_fmtNum(inv.collected)}，尚欠 $${_fmtNum(left)}`);
    return parts.join(' · ');
}

export function _buildEditFields(currentBankAccountId, currentInvoiceId) {
    const projectOpts = [{value:'',label:'— 不關聯 —'}].concat(
        _projectList.map(p => ({value:p.id, label:p.name + (p.client_short_name ? ' (' + p.client_short_name + ')' : '')})));
    const catOpts = [''].concat(_CATEGORIES).map(v => ({ value: v, label: v || '—' }));
    const fields = [
        {name:'entry_date', label:'日期', type:'date'},
        {name:'deposit', label:'收入', type:'number'},
        {name:'expense', label:'支出', type:'number'},
        {name:'summary', label:'內容', type:'text'},
        {name:'category', label:'類別', type:'select', options:catOpts},
        {name:'sub_item', label:'子項目', type:'text'},
        {name:'bank_memo', label:'銀行資訊', type:'text'},
        {name:'note', label:'附註', type:'text'},
        {name:'project_id', label:'專案', type:'select', options:projectOpts},
        // 私帳不開發票（owner 2026-09-01）：發票欄整個不出現，連結一律走專案。
        // 不出現＝payload 不含此鍵（exclude_unset 部分更新），不會洗掉既有值。
        // 私帳不開發票：連候選清單都不用建（走一遍全部發票只為了丟掉）
        ...(!ledgerHasInvoices() ? [] : [{name: 'invoice_id', label: '發票', type: 'select',
            options: [{ value: '', label: '— 不關聯 —' }].concat(
                _invoiceCandidates(currentInvoiceId).map(
                    (inv) => ({ value: inv.id, label: _invoiceLabel(inv) })))}]),
        {name:'bank_fee', label:'匯費（收款時入帳請填銀行實際入帳的淨額）', type:'number'},
    ];
    // 帳戶（財務模組）— 清單載入成功才提供（降級時不出現，PUT payload 不含此鍵、不洗掉既有值）
    if (_bankAccounts && _bankAccounts.length) {
        fields.push({name:'bank_account_id', label:'帳戶', type:'select',
            options: _bankAcctOpts(String(currentBankAccountId ?? ''))});
    }
    return fields;
}

/** 帳戶下拉選項（含「未指定」）：停用帳戶只在「就是現值」時保留，避免洗掉既有值 */
export function _bankAcctOpts(cur) {
    return [{ value: '', label: '— 未指定 —' }].concat(
        _bankAccounts.filter(a => a.active !== false || String(a.id) === cur)
            .map(a => ({ value: String(a.id), label: a.name })));
}
