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
.pstaff-del{background:none;border:none;cursor:pointer;color:#c4c4c4;font-size:12px;
  padding:0 4px;opacity:0;transition:opacity .12s,color .12s;}
.pstaff-row:hover .pstaff-del,.pstaff-del:focus-visible{opacity:1;}
.pstaff-del:hover{color:#e03131;}
.pstaff-add{display:flex;gap:6px;align-items:center;padding:10px 0;flex-wrap:wrap;}
.pstaff-addbtn{font:inherit;font-size:12px;padding:5px 10px;cursor:pointer;
  border:1px solid #d0d0d0;border-radius:3px;background:#fff;color:#444;}
.pstaff-addbtn:hover{border-color:#e03131;color:#e03131;}
.pstaff-f{font:inherit;font-size:13px;padding:5px 8px;border:1px solid #d0d0d0;
  border-radius:3px;background:#fff;min-width:0;}
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
        // 空狀態也要能新增 —— 「尚無派工」加一顆按鈕就沒了，那是這個分頁
        // 唯一的入口（owner 2026-08-15：內容都要可以新增可以刪除）
        if (!rows.length) {
            host.innerHTML = '<div class="pstaff-empty">尚無派工</div>';
            _wireAdd(host, projectId, opts);
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
              <button class="pstaff-del" data-rm="${esc(r.id)}"
                      title="移除這筆派工">&#x2715;</button>
              ${r.notes ? `<span class="pstaff-note">${esc(r.notes)}</span>` : ''}
            </div>`).join('')
            + (showMoney
                ? `<div class="pstaff-total">內部成本合計: $${
                    _num(rows.reduce((s, r) => s + (r.cost || 0), 0))}</div>`
                : '');
        host.querySelectorAll('[data-rm]').forEach(btn =>
            btn.addEventListener('click', () => _remove(btn.dataset.rm, projectId, opts)));
        _wireAdd(host, projectId, opts);
    } catch (e) {
        host.innerHTML = `<div class="pstaff-empty" style="color:#f87171;">人員配置載入失敗：${
            esc(e && e.message ? e.message : String(e))}</div>`;
    }
}

/** 移除一筆派工。`opts.onRemove` 給了就交給呼叫端（CRM 有自己的重載流程），
 *  沒給就自己打端點再重畫 —— 這樣兩個掛載點都刪得掉，不必各接一次線。 */
async function _remove(rowId, projectId, opts) {
    if (!confirm('確定移除此派工？')) return;
    if (opts.onRemove) return opts.onRemove(rowId);
    try {
        await opts.fetcher(`/project-staff/${rowId}`, { method: 'DELETE' });
        await loadProjectStaff(projectId, opts);
    } catch (e) { alert('移除失敗：' + (e && e.message ? e.message : e)); }
}

/** 「＋ 新增派工」：人員下拉 + 角色 + 天數。
 *
 *  🔴 表單**不問費率**：日費由後端從人員檔案帶（cost = days × rate），而沒有
 *  金額檢視權的人本來就看不到那個數字 —— 讓他填一個看不到的欄位是荒謬的。
 *  要調費率請到 CRM 的成本估算（那裡本來就是錢的工作面）。
 */
function _wireAdd(host, projectId, opts) {
    const bar = document.createElement('div');
    bar.className = 'pstaff-add';
    bar.innerHTML = '<button class="pstaff-addbtn">＋ 新增派工</button>';
    host.appendChild(bar);
    bar.querySelector('.pstaff-addbtn').addEventListener('click', async () => {
        let staff = [];
        try {
            staff = (await opts.fetcher('/staff?status=在職')).staff || [];
        } catch (e) { alert('人員清單載入失敗：' + (e && e.message ? e.message : e)); return; }
        if (!staff.length) { alert('請先在「人力資源」建立人員'); return; }
        bar.innerHTML = `
            <select class="pstaff-f" data-f="staff">${staff.map(s =>
                `<option value="${esc(s.id)}" data-role="${esc(s.role || '')}"
                 >${esc(s.name)}${s.role ? '（' + esc(s.role) + '）' : ''}</option>`).join('')}</select>
            <input class="pstaff-f" data-f="role" placeholder="本案角色（如：主攝）">
            <input class="pstaff-f" data-f="days" type="number" min="1" value="1"
                   style="width:70px;" title="天數">
            <button class="pstaff-addbtn" data-go="1">加入</button>
            <button class="pstaff-addbtn" data-cancel="1">取消</button>`;
        const f = (k) => bar.querySelector(`[data-f="${k}"]`);
        bar.querySelector('[data-cancel]').addEventListener('click',
            () => loadProjectStaff(projectId, opts));
        bar.querySelector('[data-go]').addEventListener('click', async (ev) => {
            ev.target.disabled = true;
            const sel = f('staff');
            try {
                await opts.fetcher(`/projects/${projectId}/staff`, {
                    method: 'POST',
                    body: JSON.stringify({
                        staff_id: sel.value,
                        // 沒填就用人員檔案上的職能 —— 多數情況本案角色就是他的職能
                        role_in_project: f('role').value
                            || sel.selectedOptions[0]?.dataset.role || '',
                        days: parseInt(f('days').value, 10) || 1,
                    }),
                });
                await loadProjectStaff(projectId, opts);
                opts.onChanged?.();
            } catch (e) {
                alert('新增失敗：' + (e && e.message ? e.message : e));
                ev.target.disabled = false;
            }
        });
    });
}

/** `(host, opts)` 轉接口 —— 專案頁的 _LAZY_TABS 只認這個形狀。 */
export const renderStaff = (host, opts) =>
    loadProjectStaff(opts.projectId, { ...opts, host });
