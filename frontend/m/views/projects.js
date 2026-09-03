/**
 * 專案分頁：頂端數字（/home）、階段 chips、搜尋、卡片清單（/crm/m/projects 30 筆一頁）、
 * 「＋ 新案子」抽屜、專案詳情抽屜（/crm/m/projects/{id}：快看＋推階段／加備註／記雜支／開發票）。
 * 階段／案型／客戶字彙全部來自 options；金額鍵不在＝被抹掉→不畫。
 */
import { mfetch, toast, esc, money, fmtDate } from '../shell.js';
import { state, list, lostPhase, opt, selectOpts, pickerHtml, mountPicker, skeleton, emptyBox, errBox, pill, statusPill,
         moneyCell, withBusy, openSheet, closeSheet, embedHost, unembedHost, markStale, shouldLoad } from '../ui.js';

const PAGE = 30;
const st = { phase: '', q: '', items: [], offset: 0, total: 0, loading: false };
let _debounce = null;

// 詳情抽屜的錢：前三列在 d.project（_to_project_dict 就帶著），其餘在 d.summary
// （project_financial_summary 的白名單，鍵不在＝沒 money_view 被抹掉 → 整列不畫）
const PROJECT_MONEY = [['contract_amount', '合約'], ['amount_received', '已收'], ['amount_receivable', '應收']];
const SUMMARY_LABELS = [
    ['ex_tax', '未稅'], ['expense_actual', '雜支實際'], ['staff_actual', '人力實際'],
    ['total_cost', '總成本'], ['actual_profit', '毛利'], ['profit_rate', '毛利率'],
];
const PCT_KEYS = new Set(['profit_rate']);

function layout() {
    return `
      <div class="m-strip"><div class="k"><div class="n" id="pj-active">–</div><div class="l">進行中專案</div></div>
        <div class="k"><div class="n" id="pj-pending">–</div><div class="l">待回覆報價</div></div></div>
      <button type="button" class="m-btn pri wide w" id="pj-new" style="margin-bottom:12px">＋ 新案子</button>
      <div class="m-chips" id="pj-chips">
        <button type="button" data-phase="" class="on">全部</button>
        ${list('phases').map(p => `<button type="button" data-phase="${esc(p)}">${esc(p)}</button>`).join('')}
      </div>
      <input class="m-search" id="pj-q" type="search" placeholder="搜尋案名／客戶" autocomplete="off">
      <div id="pj-list">${skeleton(4)}</div>
      <button type="button" class="m-more" id="pj-more" hidden>載入更多</button>`;
}

async function loadHome() {
    try {
        const h = await mfetch('/api/v1/crm/m/home');
        document.getElementById('pj-active').textContent = h.projects_active ?? '–';
        document.getElementById('pj-pending').textContent = h.quotes_pending ?? '–';
    } catch (_) { /* 數字只是裝飾，清單照常 */ }
}

function cardHtml(p) {
    const meta = [p.project_type, p.am_username ? 'AM ' + p.am_username : ''].filter(Boolean).join(' · ');
    const amounts = [moneyCell(p, 'contract_amount', '合約', money), moneyCell(p, 'amount_received', '已收', money)]
        .filter(Boolean).join('<span class="sub"> ／ </span>');
    return `
      <div class="m-card tap" data-id="${esc(p.id)}">
        <div class="t"><div class="name">${esc(p.name)}</div>${statusPill(p.status, list('phases'))}</div>
        <div class="sub">${esc(p.client_short_name || '')}${meta ? ' · ' + esc(meta) : ''}</div>
        <div class="row"><span>${amounts}</span><span class="sub">${p.shoot_date ? '拍攝 ' + esc(fmtDate(p.shoot_date)) : ''}</span></div>
      </div>`;
}

async function loadList(reset) {
    if (st.loading) return;
    st.loading = true;
    const box = document.getElementById('pj-list'), more = document.getElementById('pj-more');
    if (reset) { st.offset = 0; st.items = []; box.innerHTML = skeleton(4); more.hidden = true; }
    try {
        const qs = new URLSearchParams({ phase: st.phase, q: st.q, limit: String(PAGE), offset: String(st.offset) });
        const d = await mfetch('/api/v1/crm/m/projects?' + qs);
        const rows = d.projects || d.items || [];
        st.items = st.items.concat(rows);
        st.total = d.total ?? st.items.length;
        st.offset += rows.length;
        box.innerHTML = st.items.length ? st.items.map(cardHtml).join('') : emptyBox('沒有符合的專案');
        more.hidden = rows.length < PAGE;
    } catch (e) { box.innerHTML = errBox(e); }
    finally { st.loading = false; }
}

// ── 專案詳情抽屜 ──
function kv(k, v) { return `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`; }
function li(l, r) { return `<div class="li"><span class="l">${l}</span><span class="r">${r}</span></div>`; }
const section = (title, rows, empty) => `<div class="m-h">${esc(title)}</div>${rows.length ? rows.join('') : `<div class="sub" style="font-size:13px;color:var(--sub)">${esc(empty)}</div>`}`;

function detailHtml(d) {
    const p = d.project || {}, s = d.summary || {}, b = d.burn || null;
    const amt = (o, k) => (k in o ? money(o[k]) : null);
    const moneyRow = (o, k, lab) => kv(lab, `<span class="amt">${PCT_KEYS.has(k) ? esc(String(o[k] ?? '–')) + '%' : money(o[k])}</span>`);
    const summaryRows = [
        ...PROJECT_MONEY.filter(([k]) => k in p).map(([k, lab]) => moneyRow(p, k, lab)),
        ...SUMMARY_LABELS.filter(([k]) => k in s).map(([k, lab]) => moneyRow(s, k, lab)),
    ];
    const burnHtml = b ? `
      <div class="m-h">工時</div>
      <div class="kv"><span class="k">已用 / 預算</span><span class="v amt">${esc(String(b.hours_used ?? '–'))} / ${esc(String(b.budget_hours ?? '–'))} 小時</span></div>
      <div class="burn"><i class="${(b.pct || 0) > 100 ? 'over' : ''}" style="width:${Math.min(100, Math.max(0, Number(b.pct) || 0))}%"></i></div>
      <div class="sub" style="font-size:12px;color:var(--sub)">${esc(String(Math.round(Number(b.pct) || 0)))}%</div>` : '';
    return `
      <div class="ttl">${esc(p.name || '')}</div>
      <div class="sub" style="color:var(--sub);font-size:13px;margin-bottom:8px">${esc(p.client_short_name || p.client_name || '')} ${statusPill(p.status, list('phases'))} ${pill(p.project_type)}</div>
      ${kv('AM', esc(p.am_username || '—'))}${kv('拍攝日', esc(fmtDate(p.shoot_date) || '—'))}
      ${summaryRows.join('')}
      ${burnHtml}
      <div class="m-actions w">
        <button type="button" class="m-btn" data-act="phase">推階段</button>
        <button type="button" class="m-btn" data-act="note">加備註</button>
        <button type="button" class="m-btn" data-act="expense">記雜支</button>
        <button type="button" class="m-btn" data-act="invoice">開發票</button>
      </div>
      <div id="pj-act-box"></div>
      ${section('報價', (d.quotes || []).map(q => li(`${esc(q.version || '')} ${pill(q.status)}`,
          `<span class="amt">${amt(q, 'total') ?? ''}</span> ${esc(fmtDate(q.quote_date))}`)), '沒有報價')}
      ${section('請款', (d.payments || []).map(x => li(`${esc(x.summary || x.payee_name || '')} ${pill(x.payment_status)}`,
          `<span class="amt">${amt(x, 'amount') ?? ''}</span> ${esc(x.planned_month || '')}`)), '沒有請款')}
      ${section('發票', (d.invoices || []).map(x => li(`${esc(x.title || x.invoice_number || '')} ${pill(x.payment_status)}`,
          `<span class="amt">${amt(x, 'amount_total') ?? ''}</span> ${esc(fmtDate(x.invoice_date))}`)), '沒有發票')}
      ${section('最近雜支', (d.expenses_recent || []).map(x => li(`${esc(x.sub_item || x.category || '')} ${esc(x.payee || '')}`,
          `<span class="amt">${amt(x, 'actual') ?? ''}</span> ${esc(fmtDate(x.created_at))}`)), '沒有雜支')}
      <div class="m-h">備註</div>
      <div class="notes" id="pj-notes">${esc(d.notes || p.notes || '') || '<span style="color:var(--sub)">（無）</span>'}</div>`;
}

function actionBox(kind, p) {
    const box = document.getElementById('pj-act-box');
    if (kind === 'phase') {
        box.innerHTML = `<div class="m-form m-card" style="margin-top:10px">
            <label>推到階段</label><select id="pj-phase">${selectOpts(list('phases'), p.status, null)}</select>
            <div id="pj-reason-wrap" hidden><label>未成案原因</label><textarea id="pj-reason" placeholder="必填"></textarea></div>
            <button type="button" class="m-btn pri wide" id="pj-phase-go">確認</button></div>`;
        const sel = box.querySelector('#pj-phase'), wrap = box.querySelector('#pj-reason-wrap');
        const syncReason = () => { wrap.hidden = !(lostPhase() && sel.value === lostPhase()); };
        sel.addEventListener('change', syncReason); syncReason();
        box.querySelector('#pj-phase-go').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
            const body = { status: sel.value };
            if (!wrap.hidden) {
                body.outcome_reason = box.querySelector('#pj-reason').value.trim();
                if (!body.outcome_reason) { toast('請填未成案原因', 'err'); return; }
            }
            try {
                await mfetch(`/api/v1/crm/projects/${encodeURIComponent(p.id)}/status`, { method: 'PATCH', body });
                toast('已推到 ' + body.status);
                await openProject(p.id);
                loadList(true);
            } catch (e) {
                // options 沒給 lost_phase 時的後備：後端說「要填原因」就把原因欄打開讓人補填再送
                const code = e.data && e.data.detail && e.data.detail.code;
                if (wrap.hidden && (code === 'OUTCOME_REASON_REQUIRED' || (e.status === 422 && /原因/.test(e.message || '')))) wrap.hidden = false;
                toast(e.message, 'err');
            }
        }));
    } else if (kind === 'note') {
        box.innerHTML = `<div class="m-form m-card" style="margin-top:10px">
            <label>新增備註</label><textarea id="pj-note-text" placeholder="一句話記下來"></textarea>
            <button type="button" class="m-btn pri wide" id="pj-note-go">送出</button></div>`;
        box.querySelector('#pj-note-go').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
            const text = box.querySelector('#pj-note-text').value.trim();
            if (!text) { toast('備註是空的', 'err'); return; }
            try {
                const r = await mfetch(`/api/v1/crm/m/projects/${encodeURIComponent(p.id)}/note`, { method: 'POST', body: { text } });
                document.getElementById('pj-notes').textContent = r.notes || '';
                box.innerHTML = '';
                markStale('projects');       // updated_at 動了，清單排序跟著變
                toast('備註已加入');
            } catch (e) { toast(e.message, 'err'); }
        }));
    }
}

export async function openProject(id) {
    const body = openSheet(skeleton(3));
    let d;
    try { d = await mfetch('/api/v1/crm/m/projects/' + encodeURIComponent(id)); }
    catch (e) { body.innerHTML = errBox(e); return; }
    const p = d.project || {};
    body.innerHTML = detailHtml(d);
    // 四顆動作像分段按鈕：打開哪個功能哪顆就藍（owner 2026-09-03），再按一次收起來。
    // 開發票／記雜支也在按鈕下面原地展開（owner：不要跳到另一個畫面）：把那個分頁的宿主搬進來，不開 /expense.html。
    const acts = body.querySelectorAll('[data-act]');
    acts.forEach(b => b.addEventListener('click', async () => {
        const box = document.getElementById('pj-act-box');
        const again = b.classList.contains('pri');
        acts.forEach(x => x.classList.toggle('pri', x === b && !again));
        unembedHost();
        box.innerHTML = '';
        if (again) return;
        const kind = b.dataset.act;
        if (kind === 'invoice' || kind === 'expense') {
            if (kind === 'invoice') state.invoicePreset = p.id; else state.expensePreset = p.id;
            box.innerHTML = skeleton(2);
            try { const host = await state.ensureView(kind); box.innerHTML = ''; embedHost(box, host); }
            catch (e) { box.innerHTML = errBox(e); }
            return;
        }
        actionBox(kind, p);
    }));
}

// ── 新案子抽屜 ──
function openNewProject() {
    const phases = list('phases');
    const hint = opt().default_phase || '洽詢';
    const def = phases.includes(hint) ? hint : phases[0] || '';
    const clients = list('clients').map(c => ({ value: c.id, label: c.short_name || c.name || c.id }));
    const body = openSheet(`
      <div class="ttl">新案子</div>
      <form class="m-form" id="np-form" style="margin-top:8px">
        <label class="req">名稱</label><input id="np-name" required>
        <label class="req">客戶</label>${pickerHtml('np-client')}
        <div class="row2">
          <div><label>案型</label><select id="np-type">${selectOpts(list('project_types'), '', '未定')}</select></div>
          <div><label>階段</label><select id="np-status">${selectOpts(phases, def, null)}</select></div>
        </div>
        <button type="submit" class="m-btn-primary" id="np-go">建立</button>
      </form>`);
    mountPicker('np-client', { items: clients, placeholder: '打字找客戶' });
    body.querySelector('#np-form').addEventListener('submit', (ev) => {
        ev.preventDefault();
        withBusy(body.querySelector('#np-go'), async () => {
            const payload = {
                name: body.querySelector('#np-name').value.trim(),
                client_id: body.querySelector('#np-client').value,
                project_type: body.querySelector('#np-type').value,
                status: body.querySelector('#np-status').value,
            };
            if (!payload.name) { toast('請填名稱', 'err'); return; }
            if (!payload.client_id) { toast('請選客戶', 'err'); return; }
            try {
                const r = await mfetch('/api/v1/crm/projects', { method: 'POST', body: payload });
                toast('已建立「' + payload.name + '」');
                markStale('invoice');       // 發票分頁的專案下拉要看得到新案子
                loadList(true); loadHome();
                const id = r && r.project && r.project.id;
                if (id) await openProject(id); else closeSheet();
            } catch (e) { toast(e.message, 'err'); }
        });
    });
}

export async function render(host, { first }) {
    if (!first) {
        // 切回來：60 秒內沒人改過（markStale）就不重抓；清單空著（上次抓失敗）照樣補
        if (shouldLoad('projects', { first }) || !st.items.length) await Promise.all([loadHome(), loadList(true)]);
        return;
    }
    shouldLoad('projects', { first });
    host.innerHTML = layout();
    host.querySelector('#pj-new').addEventListener('click', openNewProject);
    host.querySelector('#pj-chips').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-phase]');
        if (!b) return;
        host.querySelectorAll('#pj-chips button').forEach(x => x.classList.toggle('on', x === b));
        st.phase = b.dataset.phase;
        loadList(true);
    });
    host.querySelector('#pj-q').addEventListener('input', (ev) => {
        clearTimeout(_debounce);
        _debounce = setTimeout(() => { st.q = ev.target.value.trim(); loadList(true); }, 300);
    });
    host.querySelector('#pj-more').addEventListener('click', () => loadList(false));
    host.querySelector('#pj-list').addEventListener('click', (ev) => {
        const c = ev.target.closest('.m-card[data-id]');
        if (c) openProject(c.dataset.id);
    });
    await Promise.all([loadHome(), loadList(true)]);
}
