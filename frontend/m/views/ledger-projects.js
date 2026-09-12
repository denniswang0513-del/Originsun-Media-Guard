/**
 * views/ledger-projects.js — 士源帳本「專案」分頁：可搜尋清單＋抽屜（改結案日／營收／案源、記雜支／委外費、工項唯讀、掛在本案的收支）。
 *
 * 資料＝桌機執行專案同一支 /api/v1/finance/project-ledger（顯示名／實收／應收都是它算的）。
 * 儲存只送有動的鍵（後端 PUT 是 exclude_unset）。🔴 費用鍵送的是**手填值**：畫面上的合計＝手填＋CRM 成本行
 * （project.cost_sources[key].crm），送合計會把 CRM 那半存成一份會走味的副本。
 * 連著母帳的案（locked_fields）結案日鎖住，到母帳改。
 */
import { mfetch, toast, esc, money } from '../shell.js';
import { openSheet, closeSheet, selectOpts, skeleton, emptyBox, errBox, withBusy, mountPicker, pickerHtml } from '../ui.js';

const API = '/api/v1/finance/project-ledger';
const CRM = '/api/v1/crm';                    // 推送到母帳的三條路都在 CRM 那支 router（project_links.py）
const MAIN_FEES = ['outsource', 'misc'];      // 委外費用／行政雜支：顯眼；其餘收進「更多」

let _rows = [];
let _q = '';

export async function fetchLedger() {
    const r = await mfetch(`${API}?entity=mine`);
    _rows = r.projects || [];
    return _rows;
}

export function projectCard(p, { extra = '' } = {}) {
    const recv = Number(p.receivable || 0);
    const src = (p.detail || {}).source || '';
    const parents = p.parent_names || [];
    return `<div class="m-card tap" data-id="${esc(p.id)}">
        <div class="t"><div class="name">${esc(p.name)}</div><span class="amt">${money(p.contract)}</span></div>
        <div class="sub">${esc(p.client || '')}${src ? '・' + esc(src) : ''}${p.close_date ? '・' + esc(p.close_date) : '・未結案'}${
            parents.length ? `<br>母帳：${esc(parents.join('、'))}` : ''}</div>
        <div class="row"><span>實收 <span class="amt in">${money(p.received)}</span></span>
            <span>未收 <span class="amt ${recv > 0 ? 'out' : ''}">${money(recv)}</span></span></div>${extra}
    </div>`;
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
            <input class="m-search" id="pj-q" type="search" placeholder="搜案名／客戶／年份" autocomplete="off">
            <div id="pj-list">${skeleton(4)}</div>`;
        host.querySelector('#pj-q').addEventListener('input', (ev) => { _q = ev.target.value.trim().toLowerCase(); draw(host); });
        host.querySelector('#pj-list').addEventListener('click', (ev) => {
            const c = ev.target.closest('.m-card[data-id]'); if (!c) return;
            openProjectSheet(c.dataset.id, () => load(host));
        });
    }
    await load(host);
}

async function load(host) {
    const list = host.querySelector('#pj-list');
    try { await fetchLedger(); draw(host); }
    catch (e) { list.innerHTML = errBox(e); }
}

function draw(host) {
    const hit = (p) => !_q || [p.name, p.orig_name, p.client, p.close_date, ...(p.parent_names || [])]
        .filter(Boolean).some(s => String(s).toLowerCase().includes(_q));
    const rows = _rows.filter(hit).slice(0, 60);
    host.querySelector('#pj-list').innerHTML = rows.map(p => projectCard(p)).join('')
        || emptyBox(_q ? '沒有符合的案' : '私帳還沒有案');
}

/** 案源下拉：可選的那幾個 ＋ 舊案正在用的歷史值（「自接」不再可選，但舊案選著它時要就地補一個選項，
 *  否則畫面顯示成空、存檔會把值洗掉 —— 同桌機 projects.js 那條規則）。 */
function sourceOpts(selectable, current) {
    return current && !selectable.includes(current) ? [{ value: current, label: current + '（歷史值）' }, ...selectable] : selectable;
}

/** 抽屜：讀單筆（含 locked_fields／cost_sources／entries）。 */
export async function openProjectSheet(id, onDone) {
    const body = openSheet(skeleton(3));
    let d;
    try { d = await mfetch(`${API}/${encodeURIComponent(id)}?entity=mine`); }
    catch (e) { body.innerHTML = errBox(e); return; }
    const p = d.project, det = p.detail || {}, src = p.cost_sources || {};
    const locked = (p.locked_fields || []).includes('close_date');
    const fees = d.cost_fields || [];
    const manual = (k) => Number(det[k] || 0) - Number((src[k] || {}).crm || 0);    // 畫面合計 − CRM 那半 ＝ 手填
    const feeRow = (f) => `<div class="lg-fee" data-key="${esc(f.key)}">
        <div>${esc(f.label)}${src[f.key] ? `<div class="crm">＋CRM 成本行 ${money(src[f.key].crm)}</div>` : ''}</div>
        <input type="number" inputmode="numeric" min="0" step="1" id="pf-${esc(f.key)}" value="${manual(f.key)}">
        <button type="button" class="m-btn sm" data-add="${esc(f.key)}">＋加一筆</button></div>`;
    const main = fees.filter(f => MAIN_FEES.includes(f.key)), rest = fees.filter(f => !MAIN_FEES.includes(f.key));
    const split = Object.entries(det.split || {});
    const parents = p.parent_links || [];
    body.innerHTML = `
        <div class="ttl">${esc(p.name)}</div>
        <div class="lg-sub">${esc(p.client || '')}${parents.length ? '・母帳：' + esc(parents.map(x => x.name).join('、')) : ''}</div>
        <div class="m-strip" style="margin-top:10px">
            <div class="k"><div class="n">${money(p.received)}</div><div class="l">實收</div></div>
            <div class="k"><div class="n">${money(p.receivable)}</div><div class="l">未收</div></div>
        </div>
        ${parents.length ? '' : `<button type="button" class="m-btn" id="pj-push" style="width:100%;margin-bottom:8px">推送到母帳</button><div id="pj-push-box" hidden></div>`}
        <div class="m-form">
            <label>結案日${locked ? '<span class="lg-lock" title="以母帳為準：到母帳那一案改，會自動同步過來">母帳</span>' : ''}</label>
            <input type="date" id="pj-close" value="${esc(p.close_date || '')}" ${locked ? 'disabled' : ''}>
            <label>營收（含稅）</label>
            <input type="number" inputmode="numeric" min="0" step="1" id="pj-contract" value="${esc(p.contract || 0)}">
            <label>案源</label>
            <select id="pj-source">${selectOpts(sourceOpts(d.sources || [], det.source || ''), det.source || '', '—')}</select>
        </div>
        <div class="m-h">費用（手填；CRM 掛過來的另計）</div>
        <div class="m-card">${main.map(feeRow).join('')}
            ${rest.length ? `<details class="m-fold"><summary>更多費用欄</summary>${rest.map(feeRow).join('')}</details>` : ''}
        </div>
        <button type="button" class="m-btn-primary" id="pj-save">儲存</button>
        <div class="m-h">工項拆分（唯讀）</div>
        <div class="m-card">${split.length ? split.map(([k, v]) => `<div class="lg-row"><span class="k">${esc(k)}</span><span class="v">${money(v)}</span></div>`).join('')
            : '<div class="m-empty" style="padding:8px 0">還沒拆工項</div>'}
            <div class="lg-row" style="border-top:1px solid var(--line);margin-top:4px"><span class="k">實收檢查</span><span class="v">${money(p.net)}${p.check ? ` <span class="pill warn">差 ${money(p.check)}</span>` : ''}</span></div>
        </div>
        <div class="m-h">掛在本案的收支</div>
        <div class="m-card">${(d.entries || []).length ? d.entries.map(e => `<div class="lg-row"><span class="k">${esc(e.date || '')} ${esc(e.summary || '')}</span>
            <span class="v ${e.deposit ? 'amt in' : 'amt out'}">${e.deposit ? '+' + money(e.deposit) : '−' + money(e.expense)}</span></div>`).join('')
            : '<div class="m-empty" style="padding:8px 0">還沒有收支掛在這案</div>'}</div>
        ${p.notes ? `<div class="m-h">備註</div><div class="notes">${esc(p.notes)}</div>` : ''}`;

    // 推送到母帳（owner 2026-09-12 L2）：同一個 sheet 內展開三選一，流程照桌機 projects.js 的 _fp.pushParent
    const pushBtn = body.querySelector('#pj-push');
    if (pushBtn) pushBtn.addEventListener('click', () => mountPushBox(body, p, {
        onLinked: async () => { closeSheet(); if (onDone) await onDone(); openProjectSheet(id, onDone); },   // 重開：parent_links 會出現、按鈕收掉
        onMoved: async () => { closeSheet(); if (onDone) await onDone(); },                                   // 這案已不在私帳：只重抓清單
    }));

    // 「＋加一筆」：手填值加上去（純前端加總，存的時候一起送）
    body.querySelectorAll('button[data-add]').forEach(b => b.addEventListener('click', () => {
        const key = b.dataset.add;
        const raw = window.prompt('加多少？（金額）', '');
        const add = parseInt(raw || '', 10);
        if (!Number.isFinite(add) || add <= 0) return;
        const inp = body.querySelector(`#pf-${CSS.escape(key)}`);
        inp.value = (parseInt(inp.value || '0', 10) || 0) + add;
        inp.classList.add('dirty');
    }));
    body.querySelector('#pj-save').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        const payload = {};
        const close = body.querySelector('#pj-close');
        if (!locked && close.value !== (p.close_date || '')) payload.close_date = close.value;
        const contract = parseInt(body.querySelector('#pj-contract').value || '0', 10) || 0;
        if (contract !== Number(p.contract || 0)) payload.contract_amount = contract;
        const source = body.querySelector('#pj-source').value;
        if (source !== (det.source || '')) payload.source = source;
        for (const f of fees) {
            const v = parseInt(body.querySelector(`#pf-${CSS.escape(f.key)}`).value || '0', 10) || 0;
            if (v !== manual(f.key)) payload[f.key] = v;      // 手填值，不是合計
        }
        if (!Object.keys(payload).length) { toast('沒有改動'); return; }
        try {
            await mfetch(`${API}/${encodeURIComponent(id)}?entity=mine`, { method: 'PUT', body: payload });
            toast('已儲存'); closeSheet(); if (onDone) await onDone();
        } catch (e) { toast(e.message, 'err'); }
    }));
}


// ── 推送到母帳（L2）──────────────────────────────────────────────
// 「推送」＝在母帳建對應的案並連結，兩案並存、錢不搬（docs/LEDGER_UNIFY_PLAN.md §8）。三選一：
//   create → POST /projects/{id}/parent-create（母帳合約額先用私帳的、標待確認；客戶沒對應會一併建）
//   link   → PUT  /projects/{id}/parent-link {parent_id}
//   move   → 整案換帳本：GET ledger-move-check → confirm → POST move-ledger {entity:'parent'}
// 母帳候選與同名建議來自 GET /projects-mine-links（對應表那支端點，parents[]／mine[].suggest_id／client_state）。
async function mountPushBox(body, p, { onLinked, onMoved }) {
    const box = body.querySelector('#pj-push-box');
    const btn = body.querySelector('#pj-push');
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }        // 再按一次收起來
    box.hidden = false;
    box.innerHTML = skeleton(2);
    btn.disabled = true;
    let links;
    try { links = await mfetch(`${CRM}/projects-mine-links`); }
    catch (e) { box.innerHTML = errBox(e); btn.disabled = false; return; }
    btn.disabled = false;
    const me = (links.mine || []).find(m => m.id === p.id) || {};
    // 已對應到別的私帳案的母帳案不能再連（母帳側 1 對 1），直接不列；同名建議排最前並預選
    const cands = (links.parents || []).filter(x => !x.linked_mine_id)
        .sort((a, b) => (b.id === me.suggest_id) - (a.id === me.suggest_id))
        .map(x => ({ value: x.id, label: `${x.name}${x.client ? `（${x.client}）` : ''}${x.id === me.suggest_id ? ' — 同名建議' : ''}` }));
    const taken = (links.parents || []).length - cands.length;
    box.innerHTML = `<div class="m-card">
        <div class="m-form">
            <label class="lg-radio"><input type="radio" name="pj-push-mode" value="create" checked>
                <span><b>公司的案，我做其中一部分</b> —— 在母帳建立對應的專案並連結
                <div class="lg-sub">案名／客戶／結案日照帶，母帳的合約金額先用私帳的並標為待確認（那是你拿到的那段，不是公司跟客戶的合約額）。${
                    me.client_state === 'none' ? `<div style="color:var(--warn)">客戶「${esc(p.client || '')}」在母帳還沒有對應的一筆，會一併建立並連結。</div>` : ''}</div></span></label>
            <label class="lg-radio"><input type="radio" name="pj-push-mode" value="link">
                <span><b>連結到既有的母帳專案</b>${taken ? `<div class="lg-sub">已對應到別案的 ${taken} 案不列</div>` : ''}</span></label>
            <div id="pj-push-pick" hidden><label>母帳專案</label>${pickerHtml('pj-push-parent')}</div>
            <label class="lg-radio"><input type="radio" name="pj-push-mode" value="move">
                <span><b>整個是公司的案，記錯帳本了</b> —— 搬回公司帳
                <div class="lg-sub">一案只在一本：錢流歸屬改回母公司，之後掛在它身上的錢都算公司的；私帳這邊不再有這一案。身上已有收支的案搬不動。</div></span></label>
        </div>
        <button type="button" class="m-btn-primary" id="pj-push-go">執行</button>
    </div>`;
    mountPicker('pj-push-parent', { items: cands, placeholder: '打字找母帳專案…',
                                     value: cands.length && me.suggest_id && cands[0].value === me.suggest_id ? me.suggest_id : '' });
    const mode = () => (box.querySelector('input[name="pj-push-mode"]:checked') || {}).value || 'create';
    const sync = () => { box.querySelector('#pj-push-pick').hidden = mode() !== 'link'; };
    box.addEventListener('change', sync);
    sync();
    box.querySelector('#pj-push-go').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
        const m = mode();
        try {
            if (m === 'link') {
                const target = body.querySelector('#pj-push-parent').value || '';
                if (!target) { toast('請先選一個母帳專案', 'err'); return; }
                await mfetch(`${CRM}/projects/${encodeURIComponent(p.id)}/parent-link`, { method: 'PUT', body: { parent_id: target } });
                toast('已連結到母帳專案');
            } else if (m === 'move') {
                const chk = await mfetch(`${CRM}/projects/${encodeURIComponent(p.id)}/ledger-move-check`);
                if (!chk.can_move) { toast(chk.reason || '這個專案不能換帳本', 'err'); return; }   // 這句話的正本在後端
                if (!window.confirm(`把「${chk.name}」搬回母公司帳？\n\n錢流歸屬改回母公司，之後掛在它身上的錢都算公司的；私帳這邊不再有這一案。`)) return;
                await mfetch(`${CRM}/projects/${encodeURIComponent(p.id)}/move-ledger`, { method: 'POST', body: { entity: 'parent' } });
                toast('已搬回公司帳');
                await onMoved();
                return;
            } else {
                const r = await mfetch(`${CRM}/projects/${encodeURIComponent(p.id)}/parent-create`, { method: 'POST' });
                toast(`已在母帳建立「${r.name || p.name}」並連結`);
            }
            await onLinked();
        } catch (e) { toast(e.message, 'err'); }
    }));
}
