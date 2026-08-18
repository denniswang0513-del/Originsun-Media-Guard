/**
 * petty.js — 💵 零用金子視圖（財務管理 Tab）
 *
 * 內容**不重畫**：直接掛 `tabs/petty/petty-view.js` 的各分頁 render（見 TABS），與獨立網址
 * `/petty-cash.html` 是同一套元件、同一批端點。原本規劃寫「CRM 帳務只放一顆
 * 連過去的按鈕」—— 實際用起來那是把已經登入的人踢去另一個登入頁，所以改成
 * 就地掛載；`/petty-cash.html` 仍在（手機現場登記走那條）。
 *
 * 元件透過 `window.__petty` 取 fetch 包裝（獨立頁的殼會設）。這裡是第二個宿主，
 * 所以也要設一份 —— 用 SPA 的 authFetch，不自己造 token 讀取邏輯。
 */
import { authFetch, bearerHeader } from '../../../js/shared/utils.js';
import { hasModule, canSeeMoney } from '../../crm/crm-utils.js';

const TABS = [
    { id: 'mine', label: '我的請款', fn: 'renderMine', need: () => true },
    { id: 'claims', label: '審核', fn: 'renderClaims', need: () => canSeeMoney() && hasModule('finance_approve') },
    { id: 'accounts', label: '匯款清冊', fn: 'renderAccounts', need: () => canSeeMoney() && hasModule('finance_approve') },
    { id: 'labels', label: '未歸戶標籤', fn: 'renderLabels', need: () => canSeeMoney() && hasModule('finance_approve') },
    { id: 'all', label: '全部零用金', fn: 'renderOverview', need: () => canSeeMoney() && hasModule('finance_approve') },
];

export default async function render(container, opts = {}) {
    const isCurrent = opts.isCurrent || (() => true);

    // 元件的 fetch 出口（與 /petty-cash.html 的殼同一個合約）。
    // ⚠️ 上傳**不能**走 authFetch —— 它會補 Content-Type: application/json 並把
    // FormData 拿去 stringify。走 bearerHeader() 讓瀏覽器自己補 boundary。
    window.__petty = {
        mfetch: authFetch,
        ufetch: (path, form) => fetch(path, {
            method: 'POST', headers: bearerHeader(), body: form }),
    };

    const visible = TABS.filter(t => t.need());
    container.innerHTML = `
      <div style="display:flex;gap:4px;border-bottom:1px solid #2a2a2a;margin-bottom:16px;">
        ${visible.map((t, i) => `<button class="pt-tab" data-pt="${t.id}"
            style="background:none;border:0;border-bottom:2px solid ${i ? 'transparent' : '#3b82f6'};
                   padding:9px 14px;font-size:14px;cursor:pointer;
                   color:${i ? '#888' : '#e0e0e0'};">${t.label}</button>`).join('')}
        <a href="/petty-cash.html" target="_blank" rel="noopener"
           style="margin-left:auto;align-self:center;font-size:12px;color:#3b82f6;">
           手機版現場登記 ↗</a>
      </div>
      <div id="pt-host" style="color:#e0e0e0;"></div>`;

    // 元件的樣式用 var(--line) 等 token（來自官網風的獨立頁）；SPA 是深色，
    // 在這裡補一份對應值，免得邊框與次要文字在深色底下看不見。
    if (!document.getElementById('pt-vars')) {
        const st = document.createElement('style');
        st.id = 'pt-vars';
        st.textContent = `#pt-host { --line:#2a2a2a; --sub:#888; --ink:#e0e0e0;
            --red:#ef4444; --bg-soft:#222; --ok:#4ade80; --warn:#fbbf24; }
            #pt-host input, #pt-host select, #pt-host textarea {
                background:#1e1e1e; color:#e0e0e0; }
            #pt-host .pc-btn { background:#3b82f6; }
            #pt-host .pc-btn.ghost, #pt-host .pc-btn.danger { background:none; }`;
        document.head.appendChild(st);
    }

    if (!isCurrent()) return;          // 載入期間使用者切走了就別再畫
    const host = container.querySelector('#pt-host');

    // 收據資料夾設定（owner 2026-08-19）：探測端點，403＝非管理員 → 不顯示。
    // ⚠ 刻意不用 settings/load 整包（那條有機密遮罩，整包存回會洗掉真值），
    // 走 petty/receipts-root 這對只碰單鍵的端點。
    _mountReceiptsRoot(container, host).catch(() => {});

    const mod = await import('../../petty/petty-view.js');
    const show = async (t, btn) => {
        container.querySelectorAll('.pt-tab').forEach(b => {
            b.style.color = '#888'; b.style.borderBottomColor = 'transparent';
        });
        btn.style.color = '#e0e0e0'; btn.style.borderBottomColor = '#3b82f6';
        host.innerHTML = '<div style="color:#888;padding:24px;">載入中…</div>';
        try {
            await mod[t.fn](host);
        } catch (e) {
            host.innerHTML = `<div style="color:#ef4444;padding:24px;">載入失敗：${
                String(e && e.message || e)}</div>`;
        }
    };
    container.querySelectorAll('.pt-tab').forEach(b => {
        const t = visible.find(x => x.id === b.dataset.pt);
        b.onclick = () => show(t, b);
    });
    const first = container.querySelector('.pt-tab');
    if (first) show(visible[0], first);
}

async function _mountReceiptsRoot(container, host) {
    const r = await authFetch('/api/v1/crm/petty/receipts-root');
    if (!r.ok) return;                 // 非管理員（403）或端點不在 → 靜默不顯示
    const cfg = await r.json();
    const bar = container.querySelector('a[href="/petty-cash.html"]')?.parentElement;
    if (!bar) return;
    const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    const a = document.createElement('a');
    a.href = 'javascript:void(0)';
    a.textContent = '收據資料夾';
    a.style.cssText = 'align-self:center;font-size:12px;color:#888;margin-left:14px;';
    bar.appendChild(a);

    const panel = document.createElement('div');
    panel.style.cssText = 'display:none;margin:-4px 0 14px;padding:10px 12px;'
        + 'border:1px solid #2a2a2a;border-radius:6px;font-size:12px;color:#888;line-height:1.7;';
    panel.innerHTML = `
        收據照片的儲存根目錄。要集中到 NAS 就填 NAS 路徑；留空＝主控機預設
        <span style="color:#aaa;">${esc(cfg.default)}</span>。子表自己設的收據資料夾仍優先。
        <div style="display:flex;gap:8px;margin-top:8px;">
          <input id="pt-rroot" value="${esc(cfg.receipts_root)}"
                 placeholder="例：\\\\OriginsunNAS\\Receipts 或 T:\\收據"
                 style="flex:1;padding:6px 8px;border:1px solid #2a2a2a;border-radius:4px;">
          <button class="pc-btn" id="pt-rroot-save" style="padding:6px 16px;border:0;border-radius:4px;color:#fff;cursor:pointer;">儲存</button>
          <span id="pt-rroot-msg" style="align-self:center;"></span>
        </div>
        <div style="margin-top:6px;">目前生效：<span id="pt-rroot-eff">${esc(cfg.effective)}</span></div>`;
    host.before(panel);

    a.onclick = () => { panel.style.display = panel.style.display === 'none' ? '' : 'none'; };
    panel.querySelector('#pt-rroot-save').onclick = async () => {
        const msg = panel.querySelector('#pt-rroot-msg');
        msg.textContent = '儲存中…';
        try {
            const rr = await authFetch('/api/v1/crm/petty/receipts-root', {
                method: 'POST',
                body: JSON.stringify({ receipts_root: panel.querySelector('#pt-rroot').value.trim() }),
            });
            const d = await rr.json().catch(() => ({}));
            if (!rr.ok) { msg.textContent = '失敗：' + (d.detail || rr.status); return; }
            panel.querySelector('#pt-rroot-eff').textContent = d.effective || '';
            msg.textContent = '已儲存（之後上傳的收據存到新位置；舊檔不搬）';
        } catch (e) { msg.textContent = '失敗：' + (e && e.message || e); }
    };
}
