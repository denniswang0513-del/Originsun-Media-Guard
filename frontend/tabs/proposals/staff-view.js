/**
 * staff-view.js — 人員配置（派工表）
 *
 * 三個呼叫端共用同一份：CRM 專案詳情的「人員配置」、客戶詳情的專案彈窗、
 * 以及專案頁（`/project.html`）的同名分頁。host 與 fetcher 由呼叫端注入
 * （CRM 走 crmFetch、專案頁走它自己的 mfetch 包裝），理由與完稿結案那組
 * 相同 —— 見 delivery-view 檔頭與 docs/PROPOSAL_PLANNER.md §15.3。
 *
 * 🔴 這支打的是 `GET /projects/{id}/staff`（`crm_project_staff`）——**人的正本**，
 * 不是 `cost-lines`。CRM 詳情面板的「執行人員」畫的是後者（項目 × 金額 × 付款
 * 狀態），那是一張掛著人事標籤的**成本表**：拿掉金額後只剩「攝影師 A 出現在
 * 拍攝階段」，是殘廢的成本表而不是人員配置。用對的端點，錢與人自然分乾淨。
 *
 * 金額欄（日費／小計／合計）跟著 `money_view` 走：沒授權時後端根本不回那些鍵
 * （core/money.py），所以這裡的判準就是**鍵在不在**，不另外問前端旗標 ——
 * 兩個掛載點的全域狀態不一樣，問旗標會在獨立頁上答錯。
 * 不畫 `***`、更不畫 0；詳見 docs/MONEY_VISIBILITY.md §4。
 *
 * 寫入（新增／修改派工）仍在 CRM，且是 Lv3。所以專案頁這一份是唯讀的：
 * 刪除鈕只有在呼叫端注入 `onRemove` 時才畫出來。
 */

import { esc, ensureStyle } from '../../js/shared/dom.js';

const STYLE_ID = 'pstaff-style';
const CSS = `
.pstaff-row{display:flex;align-items:baseline;gap:10px;padding:6px 0;
  border-bottom:1px solid #2e2e2e;font-size:13px;}
.pstaff-who{flex:1;min-width:0;color:#e0e0e0;}
.pstaff-sub{color:#9ca3af;font-size:12px;margin-left:6px;}
.pstaff-days{min-width:52px;text-align:right;color:#d1d5db;}
.pstaff-money{min-width:76px;text-align:right;color:#fbbf24;}
.pstaff-note{flex-basis:100%;color:#9ca3af;font-size:12px;padding-left:2px;}
.pstaff-total{text-align:right;font-weight:700;padding:8px 0;color:#e0e0e0;}
.pstaff-empty{color:#9ca3af;padding:8px 0;font-size:13px;}
`;

const _num = (n) => Number(n || 0).toLocaleString('zh-TW');

/**
 * @param projectId
 * @param opts.host      掛載節點（必填）
 * @param opts.fetcher   CRM 前綴的 fetcher `(path, opts) => Promise<json>`
 * @param opts.onRemove  給了才畫刪除鈕；`(rowId) => any`。不給＝唯讀。
 */
export async function loadProjectStaff(projectId, opts = {}) {
    const host = opts.host;
    if (!host) return;
    ensureStyle(STYLE_ID, CSS);
    host.innerHTML = '<div class="pstaff-empty">載入中…</div>';
    try {
        const data = await opts.fetcher(`/projects/${projectId}/staff`);
        const rows = data.staff || [];
        if (!rows.length) {
            host.innerHTML = '<div class="pstaff-empty">尚無派工</div>';
            return;
        }
        // 🔴 有沒有金額**只看後端回了什麼**（沒授權時 core/money.py 直接把鍵
        // 刪掉），不看前端旗標。`canSeeMoney()` 讀的是 SPA 的
        // `window._accessLevel/_modules`，而這支的另一個掛載點是獨立頁
        // project.html —— 那頁沒有那些全域，問了會把管理員也判成沒授權。
        // 鍵在＝後端認可，這是唯一不會漂的判準。
        const showMoney = rows.some(r => 'cost' in r);
        host.innerHTML = rows.map(r => `
            <div class="pstaff-row">
              <span class="pstaff-who">${esc(r.staff_name || '未知')}
                <span class="pstaff-sub">${esc(r.staff_role || '')}${
                    r.role_in_project ? ' · ' + esc(r.role_in_project) : ''}</span>
              </span>
              <span class="pstaff-days">${r.days ?? 0} 天</span>
              ${showMoney ? `<span class="pstaff-money">$${_num(r.rate)}</span>
              <span class="pstaff-money">$${_num(r.cost)}</span>` : ''}
              ${opts.onRemove ? `<button class="crm-btn crm-btn-danger crm-btn-sm"
                 style="padding:2px 6px;" data-rm="${esc(r.id)}">&#x2715;</button>` : ''}
              ${r.notes ? `<span class="pstaff-note">${esc(r.notes)}</span>` : ''}
            </div>`).join('')
            + (showMoney
                ? `<div class="pstaff-total">內部成本合計: $${
                    _num(rows.reduce((s, r) => s + (r.cost || 0), 0))}</div>`
                : '');
        if (opts.onRemove) {
            host.querySelectorAll('[data-rm]').forEach(btn =>
                btn.addEventListener('click', () => opts.onRemove(btn.dataset.rm)));
        }
    } catch (e) {
        host.innerHTML = `<div class="pstaff-empty" style="color:#f87171;">人員配置載入失敗：${
            esc(e && e.message ? e.message : String(e))}</div>`;
    }
}

/** `(host, opts)` 轉接口 —— 專案頁的 _LAZY_TABS 只認這個形狀。 */
export const renderStaff = (host, opts) =>
    loadProjectStaff(opts.projectId, { ...opts, host });
