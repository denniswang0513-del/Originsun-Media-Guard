// ────────────────────────────────────────────────────────────────────────────
// crm-cashbook-petty.js —— 收支明細的一段：源日請款（＝推送到母公司零用金；owner 2026-08-27 命名）
//
// 2026-09-12 從 crm-cashbook.js（2,844 行，超過單次讀取上限）原樣切出來。主檔的狀態（_entries／_invoiceList…）
// 用 ES module 的 live binding 讀，**這裡不賦值**（賦值只在主檔）；主檔再 import 這裡的函式回去 ——
// 循環 import 只在函式內用到，模組頂層不碰對方的東西。掃原始碼的測試用 _srcscan.cashbook_src()（主檔＋五段串起來）。
// ────────────────────────────────────────────────────────────────────────────
import { esc as _esc, crmFetch as _fetch, fmtNum as _fmtNum, crmToast } from './crm-utils.js';
import { _grossOut, loadEntries } from './crm-cashbook.js';

// ── 源日請款（＝推送到母公司零用金；owner 2026-08-27 命名）──────────
//
// 「我收支表想要有個按鈕，可以推送到應收款，然後進到 crm 請款」＋「我希望接進去
// 的是零用金系統」。這裡不另造流程：把收支的那一列變成一張**零用金草稿單據**
// （crm_project_expenses），之後走既有的送出→核准→應付款→匯款。
//
// 🔴 付款方式不影響（owner：「不管是匯款或信用卡都可以直接接到 crm 的零用金
// 請款」）—— 卡片列與銀行列一視同仁，判準只有「這一列有沒有流出金額」。
// 🔴 只建草稿。送出＝即核准即產應付款，那一步要人在零用金那邊按。
// 🔴 命名：使用者看到的是**「源日請款」**（他站在私帳這一側，這動作就是「跟源日
// 請這筆錢」）；程式/資料/另一個 Tab 仍叫零用金（petty）—— 那是母公司那側的
// 系統名，別為了對齊而去改 petty_status／端點／Tab 名。

/** 這一列流出多少（卡片列與銀行列同一個式子；後端 `_cash_claim_amount` 的鏡像）。 */
const _pettyAmt = (e) => _grossOut(e) + (e.claim || 0);

/** 已推送的列在摘要後面帶一個狀態標 —— 不標的話這一列跟沒推過長得一樣。 */
export function _pettyTag(e) {
    if (!e.petty_status) { return ''; }
    return `<span class="cash-petty" title="已送出源日請款">源日·${_esc(e.petty_status)}</span>`;
}

export function _pettyMenu(e) {
    if (e.petty_status || e.expense_id) {   // expense_id 而無狀態＝單據被刪的空殼連結
        return [{ label: e.petty_status ? '撤銷源日請款' : '清除源日請款連結',
                  fn: '_cashPettyUndo' }];
    }
    return _pettyAmt(e) > 0 ? [{ label: '源日請款', fn: '_cashPettyPush' }] : [];
}

// 請款人：正本是「呼叫者綁定的人員檔案」（後端從 token 解，前端不傳）。
// 帳號還沒綁的時候才落到代管路徑 —— 那時挑一次人，之後這個 session 沿用。
// owner 2026-08-27：「登記人都是王士源」，所以代管的預設就挑他，但選單留著。
const _PETTY_DEFAULT_NAME = '王士源';
let _pettyStaffId = '';
let _pettyStaffOpts = [];

const _pettyPath = (suffix = '') => (_pettyStaffId
    ? `/petty/staff/${encodeURIComponent(_pettyStaffId)}/from-cash${suffix}`
    : `/petty/from-cash${suffix}`);

/** 先走本人路徑；帳號沒綁人員檔案（409）才挑一個人走代管。 */
async function _pettyPost(body) {
    try {
        return await _fetch(_pettyPath(), {
            method: 'POST', body: JSON.stringify(body) });
    } catch (e) {
        if (_pettyStaffId || !/綁定/.test(e.message || '')) { throw e; }
        const o = await _fetch('/petty/staff-options');
        _pettyStaffOpts = o.staff || [];
        const pick = _pettyStaffOpts.find(x => x.name === _PETTY_DEFAULT_NAME)
                     || _pettyStaffOpts[0];
        if (!pick) { throw e; }
        _pettyStaffId = pick.id;
        return await _fetch(_pettyPath(), {
            method: 'POST', body: JSON.stringify(body) });
    }
}

const _pettyClose = () => {
    const ov = document.getElementById('cash-petty-overlay');
    if (ov) { ov.remove(); }
};

/** 推送前先試算給人看 —— 推完那筆錢就進了公司的請款流程，不該是一鍵無聲的。 */
window._cashPettyPush = async function (id) {
    let pv;
    try {
        pv = await _pettyPost({ entry_id: id, preview: true });
    } catch (err) { crmToast('推送失敗：' + err.message); return; }
    const row = pv.row || {};
    _pettyClose();
    const ov = document.createElement('div');
    ov.id = 'cash-petty-overlay';
    ov.className = 'crm-modal-overlay';
    ov.style.display = 'flex';
    ov.addEventListener('click', ev => { if (ev.target === ov) { _pettyClose(); } });
    // 值域由後端供（_petty_item_domain）：下拉選的跟寫入時驗的是同一份清單，
    // 前端不自己濾 —— 濾法一漂，選得到的跟存得進的就不是同一批。
    const items = (pv.items || []).map(c =>
        `<option value="${_esc(c)}"${c === row.item ? ' selected' : ''}>${_esc(c)}</option>`).join('');
    ov.innerHTML = `
      <div class="crm-modal" style="max-width:460px;">
        <div class="crm-modal-header"><h3>源日請款</h3>
          <button onclick="window._cashPettyClose()" class="crm-detail-close">✕</button></div>
        <div class="crm-modal-body">
          <table class="crm-table" style="width:100%;font-size:12px;">
            <tr><td style="color:#bbb;width:80px;">日期</td><td>${_esc(row.date || '')}</td></tr>
            <tr><td style="color:#bbb;">摘要</td><td>${_esc(row.summary || '')}</td></tr>
            <tr><td style="color:#bbb;">收支類別</td><td>${_esc(row.category || '（未分類）')}</td></tr>
            <tr><td style="color:#bbb;">請款人</td><td>${_pettyStaffOpts.length
                ? `<select id="cash-petty-staff" class="crm-input" style="width:100%;">`
                  + _pettyStaffOpts.map(x => `<option value="${_esc(x.id)}"${
                        x.id === _pettyStaffId ? ' selected' : ''}>${_esc(x.name)}</option>`).join('')
                  + `</select>`
                : _esc((pv.staff || {}).name || '')}</td></tr>
            <tr><td style="color:#ddd;font-weight:600;">請款金額</td>
                <td style="font-weight:600;color:#eee;">$${_fmtNum(row.amount || 0)}</td></tr>
          </table>
          <div class="crm-form-section" style="margin-top:14px;">會計項目</div>
          <select id="cash-petty-item" class="crm-input" style="width:100%;">
            <option value="">— 請選擇 —</option>${items}</select>
          <div style="color:#666;font-size:11px;margin-top:4px;">
            ${row.item ? '由收支類別對映帶出，可以改。' :
                '這個收支類別沒有對應的會計項目，請自己挑一個。'}</div>
          <div id="cash-petty-err" style="display:none;color:#fca5a5;font-size:12px;margin-top:8px;"></div>
          <div style="color:#666;font-size:11px;margin-top:12px;">
            會建一張<b>草稿</b>單據掛在請款人名下。要真的請款，到
            「財務管理 → 零用金 → 我的請款」按送出（送出即核准並產生應付款）。</div>
        </div>
        <div class="crm-modal-footer">
          <button class="crm-btn crm-btn-secondary" onclick="window._cashPettyClose()">取消</button>
          <button class="crm-btn crm-btn-primary" onclick="window._cashPettyConfirm(this,'${_esc(id)}')"
                  ${row.blocked ? 'disabled title="' + _esc(row.blocked) + '"' : ''}>
            ${row.blocked ? _esc(row.blocked) : '建立草稿單據'}</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
};

window._cashPettyClose = _pettyClose;

window._cashPettyConfirm = async function (btn, id) {
    const err = document.getElementById('cash-petty-err');
    const item = document.getElementById('cash-petty-item').value;
    if (!item) {
        err.textContent = '請先選會計項目 —— 空著會落到「其他」，之後很難翻出來。';
        err.style.display = 'block';
        return;
    }
    const sel = document.getElementById('cash-petty-staff');
    if (sel) { _pettyStaffId = sel.value; }
    btn.disabled = true;
    try {
        const r = await _pettyPost({ entry_id: id, item });
        _pettyClose();
        crmToast(`已建立草稿單據 $${_fmtNum(r.amount || 0)}`);
        // 卡片摘要不會被推送改到（只動 expense_id/petty_status，不動金額）
        await loadEntries({ cards: false });
    } catch (e) {
        err.textContent = e.message; err.style.display = 'block';
        btn.disabled = false;
    }
};

window._cashPettyUndo = async function (id) {
    if (!confirm('撤銷推送？會刪掉那張草稿單據並解開連結。')) { return; }
    try {
        await _fetch('/petty/from-cash/' + encodeURIComponent(id), { method: 'DELETE' });
        crmToast('已撤銷');
        await loadEntries({ cards: false });
    } catch (e) { crmToast('撤銷失敗：' + e.message); }
};
