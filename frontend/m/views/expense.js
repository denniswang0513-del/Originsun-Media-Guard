/**
 * 記雜支（路由 #expense，不在分頁列；從專案抽屜「記雜支」進來，上一頁回專案）。
 * 取代手機上的 /expense.html —— 那頁在 /m/ 範圍外，加到主畫面後 iOS 會用內建瀏覽器彈出來
 * （owner 2026-09-03「記雜支不要用彈出頁」）。桌機分享連結（?t=token 給沒帳號的現場人員）仍走舊頁。
 *
 * 端點跟舊頁／桌機同一套：GET /projects/{id}/cost-groups、POST /projects/{id}/expenses、
 * POST /projects/{id}/receipts/{expense_id}（multipart）、GET /projects/{id}/expenses（最近紀錄，10 筆一頁＋載入更多）。
 * 類別字彙從 options.expense.categories 拿。
 * 🔴 消費日期預設今天（本地日）但要自己改：成本認列看消費日不看登記日（db/models CrmProjectExpense）。
 */
import { mfetch, toast, esc, money, fmtDate, todayLocal } from '../shell.js';
import { state, opt, skeleton, errBox, pill, withBusy, pickerHtml, mountPicker, segHtml, mountSeg, markStale, renderPaged } from '../ui.js';

const F = (id) => document.getElementById('exp-' + id);
const cats = () => ((opt().expense || {}).categories) || [];
const base = (pid) => '/api/v1/crm/projects/' + encodeURIComponent(pid);

function formHtml() {
    return `
      <div class="m-h">記雜支</div>
      <form class="m-form m-card w" id="exp-form" autocomplete="off">
        <label class="req">專案</label>${pickerHtml('exp-project_id')}
        <div id="exp-group-row" hidden><label>子表</label><select id="exp-cost_group_id"></select></div>
        <label>類別</label>${cats().length ? segHtml('exp-category', cats()) : '<input id="exp-category" placeholder="主控端還沒給類別字彙，先手打">'}
        <label>細項</label><input id="exp-sub_item" placeholder="如：高鐵來回">
        <label class="req">金額</label><input id="exp-actual" type="number" inputmode="numeric" min="0">
        <label>消費日期</label><input id="exp-expense_date" type="date">
        <div class="m-hint">成本算在消費那天，不是登記那天：隔幾天才登記要改回實際花錢的日期</div>
        <label>請款人</label><input id="exp-payee" placeholder="選填">
        <label>備註</label><input id="exp-notes" placeholder="選填">
        <label>收據</label>
        <label class="m-photo" id="exp-photo"><input id="exp-file" type="file" accept="image/*,.pdf" hidden><span>拍照或選擇圖片</span></label>
        <button type="submit" class="m-btn pri w" id="exp-submit">送出</button>
      </form>
      <div class="m-h">這個專案最近的雜支</div>
      <div id="exp-recent"></div>`;
}

function resetPhoto() {
    const ph = F('photo');
    ph.classList.remove('has-img');
    ph.querySelector('img')?.remove();
    ph.querySelector('span').hidden = false;
    F('file').value = '';
}

function showPhoto(file) {
    const ph = F('photo');
    if (!file) { resetPhoto(); return; }
    ph.querySelector('span').hidden = true;
    ph.querySelector('img')?.remove();
    if (file.type.startsWith('image/')) {
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        ph.appendChild(img);
    } else {
        ph.querySelector('span').hidden = false;
        ph.querySelector('span').textContent = file.name;
    }
    ph.classList.add('has-img');
}

async function loadProjects() {
    let items = [], placeholder = '打字找案名或客戶';
    try {
        const d = await mfetch('/api/v1/crm/m/projects?limit=100&offset=0');
        items = (d.projects || []).map(p => ({ value: p.id, label: [p.client_short_name, p.name].filter(Boolean).join('｜') }));
    } catch (e) {
        placeholder = '專案清單載入失敗：' + e.message;
    }
    mountPicker('exp-project_id', { items, placeholder, value: F('project_id').value, onPick: onProject });
}

function applyPreset() {
    if (!state.expensePreset) return;
    const hidden = F('project_id');
    if (hidden._set && (hidden._items || []).some(i => String(i.value) === String(state.expensePreset))) {
        hidden._set(String(state.expensePreset));
        onProject(String(state.expensePreset));
    }
    state.expensePreset = null;
}

async function onProject(pid) {
    await Promise.all([loadGroups(pid), loadRecent(pid)]);
}

async function loadGroups(pid) {
    const row = F('group-row'), sel = F('cost_group_id');
    let groups = [];
    try { groups = (await mfetch(base(pid) + '/cost-groups')).cost_groups || []; } catch (_) { groups = []; }
    sel.innerHTML = groups.map(g => `<option value="${esc(g.id)}">${esc(g.name)}${g.shoot_date ? '（' + esc(fmtDate(g.shoot_date)) + '）' : ''}</option>`).join('');
    row.hidden = groups.length < 2;      // 只有一個子表就不用選（同舊頁）
}

function recentCardHtml(x) {
    const url = String(x.receipt_url || '').replace(/^javascript:/i, '');
    return `
      <div class="m-card">
        <div class="t"><div class="name">${esc(x.sub_item || x.category || '')}</div>${pill(x.category)}</div>
        <div class="sub">${esc(fmtDate(x.expense_date || x.created_at))}${x.payee ? ' · ' + esc(x.payee) : ''}${x.notes ? ' · ' + esc(x.notes) : ''}</div>
        <div class="row">${'actual' in x ? `<span class="amt">${money(x.actual)}</span>` : '<span></span>'}
          ${url ? `<a class="m-btn sm" href="${esc(url)}" target="_blank" rel="noopener">看收據</a>` : ''}</div>
      </div>`;
}

async function loadRecent(pid) {
    const box = F('recent');
    if (!pid) { box.innerHTML = '<div class="m-empty">先選專案</div>'; return; }
    box.innerHTML = skeleton(2);
    try {
        const d = await mfetch(base(pid) + '/expenses');
        const rows = (d.expenses || []).slice().reverse();     // 後端舊→新；最近的排前面
        renderPaged(box, rows, recentCardHtml, { empty: '這個專案還沒有雜支' });
    } catch (e) { box.innerHTML = errBox(e); }
}

async function submit(ev) {
    ev.preventDefault();
    const pid = F('project_id').value;
    const amount = parseInt(F('actual').value) || 0;
    if (!pid) { toast('請先選專案', 'err'); F('project_id-q').focus(); return; }
    if (!amount) { toast('請填金額', 'err'); F('actual').focus(); return; }
    await withBusy(F('submit'), async () => {
        try {
            const created = await mfetch(base(pid) + '/expenses', { method: 'POST', body: {
                category: F('category').value, sub_item: F('sub_item').value.trim(),
                estimated: 0, actual: amount,
                expense_date: F('expense_date').value || null,
                payee: F('payee').value.trim(), notes: F('notes').value.trim(),
                cost_group_id: F('cost_group_id').value || null,   // 只有一個子表時列雖藏著、值還是那個子表（同舊頁）
            } });
            const file = F('file').files[0];
            if (file && created && created.expense_id) {
                const form = new FormData();
                form.append('file', file);
                await mfetch(base(pid) + '/receipts/' + encodeURIComponent(created.expense_id), { method: 'POST', body: form });
            }
            toast('已送出，可繼續登記下一筆');
            // 清空這一筆的欄位；專案／子表／類別／請款人留著（通常連續登記同一人同類別）
            F('sub_item').value = ''; F('actual').value = ''; F('notes').value = '';
            F('expense_date').value = todayLocal();
            resetPhoto();
            markStale('projects');
            window.scrollTo({ top: 0, behavior: 'smooth' });
            await loadRecent(pid);
        } catch (e) { toast(e.message || '建立失敗', 'err'); }
    });
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = formHtml();
        mountSeg('exp-category');     // 沒字彙時是 input，mountSeg 找不到 -seg 就略過
        F('expense_date').value = todayLocal();
        F('form').addEventListener('submit', submit);
        F('file').addEventListener('change', (ev) => showPhoto(ev.target.files[0]));
        await loadProjects();
        if (!state.expensePreset) loadRecent('');
    }
    applyPreset();
}
