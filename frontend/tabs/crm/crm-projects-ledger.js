/**
 * crm-projects-ledger.js — 專案管理的「母帳 ↔ 私帳」段：推送到私帳（一顆入口、依收款方式分流）、
 * 換帳本、分身（mirror-to-mine）、落後提示、彈窗。2026-09-13 從 crm-projects-core.js 原樣切出（純搬移）。
 *
 * 全部掛 window._proj*（onclick 字串呼叫），只從主檔 import `loadProjects`（函式內用；主檔不 import 本檔）。
 * 由 crm-projects.js 以副作用 import 載入，不載就是「按鈕點了沒反應」。
 */
import { crmFetch as _fetch, crmCacheInvalidate, esc as _esc, fmtNum, searchableSelect, crmToast } from './crm-utils.js';
import { state, callbacks } from './crm-projects-state.js';
import { loadProjects } from './crm-projects-core.js';

// ── 推送到私帳（一顆入口，三種情況分流）──────────────────────────
// owner 2026-09-12：「我希望私帳母帳可以連結 … 我在母帳建立專案可以推到私帳」
// ＋「有時候我會把母帳當私帳記」＋「有時候我只是拿專案費用，有時候走發票代開」。
// 三種情況是兩種病（docs/LEDGER_UNIFY_PLAN.md §8.7–8.8）：
//   1) 公司的案、公司付我一部分   → 分身（母帳留著，私帳多開一案，收入＝掛給我的成本行）
//   2) 我的案、客戶走公司代開發票 → 換帳本（案源＝代開發票；內部代開發票留在母帳掛過來）
//   3) 我的案、沒經過公司         → 換帳本（問案源）
// 使用者不必知道要按哪一顆：先問是哪一種，再分流到 _projMirrorMine／_projMoveLedger。
// 已連結的案直接進「重新同步」（同 _projMirrorMine 的 relink 分支）。
window._projPushMine = async function (id, linked) {
    if (linked) { return window._projMirrorMine(id); }
    const p = state.projects.find(x => x.id === id);
    const name = p ? p.name : '';
    // 收款方式已經回答了「是哪一種」：後期代開 → 直接建分身（案源＝代開發票），不再問
    if (p && p.billing_mode === 'passthrough') { return window._projMirrorMine(id, { source: '代開發票' }); }
    const opt = (v, title, desc, checked) => `
        <label style="display:flex;gap:8px;align-items:flex-start;padding:8px 10px;border:1px solid #333;border-radius:6px;cursor:pointer;">
            <input type="radio" name="ppm-kind" value="${v}" ${checked ? 'checked' : ''} style="margin-top:3px;">
            <span><b style="color:#eee;">${title}</b><br>
                  <span style="color:#999;font-size:12px;">${desc}</span></span></label>`;
    _mirrorModal(`推送到私帳 — ${name}`, `
        <div style="color:#bbb;font-size:12px;margin-bottom:10px;">這一案是哪一種？</div>
        <div style="display:flex;flex-direction:column;gap:6px;font-size:13px;">
            ${opt('share', '公司的案，公司付我一部分',
                  '母帳留著跟客戶的合約；在私帳開一個對應的案，收入＝人員配置裡掛給你的成本行（沒有就先開 0）', true)}
            ${opt('passthrough', '我的案，客戶走公司代開發票',
                  '案源＝代開發票，代辦費自動算；公司開的「內部代開」發票掛在這一案上')}
            <div id="ppm-pt-sub" style="margin-left:26px;display:none;flex-direction:column;gap:4px;font-size:12px;color:#bbb;">
                <label style="display:flex;gap:6px;align-items:center;cursor:pointer;">
                    <input type="radio" name="ppm-pt" value="copy" checked>
                    公司也留一份帳 —— 母帳這案留著，在私帳開對應的案（收入＝母帳合約額）</label>
                <label style="display:flex;gap:6px;align-items:center;cursor:pointer;">
                    <input type="radio" name="ppm-pt" value="move">
                    整案搬到私帳 —— 公司帳上不留這個案（換帳本）</label>
            </div>
            ${opt('own', '我的案，沒有經過公司（記錯帳本）',
                  '整案搬到私帳，會問案源。公司帳上不會留下這個案')}
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="ppm-next">下一步</button>
        </div>`);
    // owner 2026-09-12「雖然是代開發票，但是專案公司也留一份帳」：代開那一項底下再分
    // 「留一份（分身，案源＝代開發票）」／「整案搬」。子選項只在選到代開時展開。
    const kindOf = () => (document.querySelector('input[name="ppm-kind"]:checked') || {}).value || 'share';
    const syncSub = () => {
        const sub = document.getElementById('ppm-pt-sub');
        if (sub) { sub.style.display = kindOf() === 'passthrough' ? 'flex' : 'none'; }
    };
    document.getElementById('proj-mirror-body').addEventListener('change', syncSub);
    syncSub();
    document.getElementById('ppm-next').addEventListener('click', () => {
        const kind = kindOf();
        const ptCopy = (document.querySelector('input[name="ppm-pt"]:checked') || {}).value !== 'move';
        window._projMirrorClose();
        if (kind === 'share') { return window._projMirrorMine(id); }
        if (kind === 'passthrough' && ptCopy) { return window._projMirrorMine(id, { source: '代開發票' }); }
        return window._projMoveLedger(id, { source: kind === 'passthrough' ? '代開發票' : '' });
    });
};

/** 已連結的案：問後端「私帳落後了沒」，把「重新同步」那顆改成講實話的字。
 *  判定正本在後端（core.ledger_project.mirror_stale）：True＝母帳成本行改了、
 *  False＝一致、null＝舊連結沒記過（同步一次就會記）—— 前端不自己比 Σsplit。 */
window._projMirrorStaleHint = async function (id, btn, note) {
    if (!btn) { return; }
    let chk;
    if (note && typeof note === 'object') {
        // 詳情那一行「後期連結」同一趟就帶了三值＋差額（link_note.stale／delta／crm_total）——
        // 不再多打一支 mirror-check（owner 2026-09-13「併成一支」）
        chk = { stale: note.stale, delta: note.delta, total: note.crm_total };
    } else {
        try { chk = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-check`); }
        catch (e) { return; }                  // 只是提示，拿不到就維持原字
    }
    if (!btn.isConnected) { return; }          // 使用者已經切到別案（或這一案剛被重畫）→ 下次重畫再問
    const bar = btn.closest('#proj-bar-actions');
    if (bar) { bar.dataset.staleFor = id; }    // 畫上去了才記，之後同一案的重畫不再問
    if (chk.stale === true) {
        const d = chk.delta || 0;
        btn.textContent = `私帳落後 ${d > 0 ? '+' : ''}${fmtNum(d)} · 重新同步`;
        btn.style.color = '#fbbf24';
        btn.style.borderColor = '#7c5a12';
        btn.title = `母帳掛給你的成本行現在合計 ${fmtNum(chk.total)}，私帳上次同步時是 ${fmtNum(chk.total - d)}`;
    } else if (chk.stale === false) {
        btn.textContent = '已同步 · 重新同步';
        btn.title = `私帳跟母帳一致（掛給你的成本行合計 ${fmtNum(chk.total)}）`;
    }
};


// ── 換帳本（專案管理 ↔ 私帳）─────────────────────────────────────
// owner 2026-08-28：「可以有一個按鈕把專案推送至私帳（只有擁有私帳權限的人能用）」。
// 2026-09-12 起母帳側從 _projPushMine 的彈窗分流進來（「我的案、記錯帳本」那兩種）；
// 私帳側（已在私帳的案）仍是動作列上獨立的「搬回公司帳」。只對有 finance_mine 的帳號畫。
//
// 🔴 換帳本是全 repo「更新一律不得換帳本」的唯一例外，所以：
//   1) 先問後端「能不能搬」，把會擋住的東西講出來 —— 不要讓人按了才吃 409；
//   2) 動手前一定 confirm，並把「錢會跟著算到哪本帳」寫清楚；
//   3) 搬到私帳要問**案源**（代開發票→代辦費自動算）—— 後端拿它補齊私帳需要的欄位。
window._projMoveLedger = async function (id, opts = {}) {
    let chk;
    try {
        chk = await _fetch(`/projects/${encodeURIComponent(id)}/ledger-move-check`);
    } catch (e) {
        crmToast('查不到換帳本的狀態：' + e.message);
        return;
    }
    const toMine = chk.target === 'mine';
    if (!chk.can_move) {
        // 這句話的正本在後端（_blocked_reason）—— 前端再拼一份就會跟 409 的
        // 訊息漂成兩種說法。擋住的東西逐項列出來，人才知道要去哪裡處理。
        const rows = (chk.blockers || []).map(b => `<li>${_esc(b.what)} ${b.count} 筆</li>`).join('');
        _mirrorModal(`不能換帳本 — ${chk.name}`, `
            <div style="color:#fca5a5;font-size:13px;line-height:1.6;">${_esc(chk.reason || '這個專案不能換帳本')}</div>
            ${rows ? `<ul style="color:#ddd;font-size:12px;margin:10px 0 0 18px;">${rows}</ul>` : ''}
            <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
                ${toMine ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" id="pml-share"
                                    title="公司的案、公司付你一部分：在私帳開分身（母帳這案不動）">改用推送（分身）</button>` : ''}
                <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._projMirrorClose()">知道了</button>
            </div>`);
        document.getElementById('pml-share')?.addEventListener('click', () => {
            window._projMirrorClose();
            window._projMirrorMine(id);
        });
        return;
    }
    if (!toMine) {
        const msg = `把「${chk.name}」搬回母公司帳？\n\n`
            + '錢流歸屬改回母公司，之後掛在它身上的錢都算公司的。';
        if (!window.confirm(msg)) { return; }
        return _projMoveSubmit(id, 'parent', '', null);
    }
    // 搬到私帳：問案源（confirm() 塞不下一個下拉）
    const src = opts.source || chk.source_default || '源日';
    const sources = (chk.source_options || ['源日', '代開發票', '執行業務所得']).map(o =>
        `<option value="${_esc(o)}"${o === src ? ' selected' : ''}>${_esc(o)}</option>`).join('');
    _mirrorModal(`搬到私帳 — ${chk.name}`, `
        <div style="color:#bbb;font-size:12.5px;line-height:1.6;">
            這個專案的錢流歸屬會改成私帳：之後掛在它身上的收支／發票／請款都算私帳的，
            母公司的三表不再計入它。專案管理仍看得到（標「後期專案」）。<br>
            ${chk.has_passthrough_invoice
                ? '<span style="color:#c4b5fd;">身上有「內部代開」發票 —— 會跟著掛在這一案上，案源預設「代開發票」。</span>'
                : '目前它身上沒有任何單據，搬過去不會動到任何一筆已記的帳。'}
        </div>
        <div style="margin-top:12px;display:flex;align-items:center;gap:8px;font-size:13px;">
            <span style="color:#ddd;">案源</span>
            <select class="crm-input" id="pml-source" style="width:180px;">${sources}</select>
            <span style="color:#777;font-size:11px;">代開發票＝代辦費自動算；源日＝現金收款</span>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="pml-go">搬到私帳</button>
        </div>`);
    document.getElementById('pml-go').addEventListener('click', (ev) =>
        _projMoveSubmit(id, 'mine', document.getElementById('pml-source').value, ev.currentTarget));
};

async function _projMoveSubmit(id, entity, source, btn) {
    if (btn) { btn.disabled = true; }
    try {
        await _fetch(`/projects/${encodeURIComponent(id)}/move-ledger`, {
            method: 'POST', body: JSON.stringify({ entity, source: source || null }),
        });
        window._projMirrorClose();
        crmToast(entity === 'mine' ? '已搬到私帳' : '已搬回公司帳');
        crmCacheInvalidate('/projects');
        await loadProjects();
        // 詳情面板要重畫（按鈕文字與帳本標記都變了）
        const p = state.projects.find(x => x.id === id);
        if (p) { callbacks.renderDetail?.(p); }
    } catch (e) {
        if (btn) { btn.disabled = false; }
        crmToast('換帳本失敗：' + e.message, 6000);
    }
}


// ── 分身（母公司專案 → 私帳的收入分身）──────────────────────────
// owner 2026-08-29：「費用要給王士源的，直接在私帳建立專案、同步收入」。
//
// 跟換帳本（搬家）是兩件事：這裡母公司那案原封不動，只是在私帳多開
// 一案，收入＝人員配置裡掛給我的那幾行成本。金額判定全在後端
// （core.ledger_project.mirror_lines），前端只顯示它算出來的明細 —— 前端自己
// 再加一次總和，兩個數字遲早會不一樣。
// 2026-09-12 起沒有掛給我的成本行**不擋**（先開 0，warning 講清楚）。
window._projMirrorMine = async function (id, mirrorOpts = {}) {
    let chk;
    try {
        chk = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-check`);
    } catch (e) {
        crmToast('查不到私帳連結的狀態：' + e.message, 6000);
        return;
    }
    if (chk.can_mirror === false) {
        // 舊後端（發版空窗）才會擋；這句話的正本在後端
        crmToast(chk.reason || '這一案不能推送到私帳', 6000);
        return;
    }
    const rows = (chk.lines || []).map(l => `<tr>
        <td style="color:#888;">${_esc(l.phase)}</td>
        <td>${_esc(l.item)}</td>
        <td style="text-align:right;">${fmtNum(l.amount)}</td></tr>`).join('');
    // 已經承接過別的 CRM 案的，在名稱後標出來 —— 一個私帳案可以承接多筆
    // （owner 2026-09-01），但覆蓋會洗掉別案的錢，要先看得見
    const opts = (chk.options || []).map(o =>
        `<option value="${o.id}">${_esc(o.name)}${o.amount ? ` — ${fmtNum(o.amount)}` : ''}${
            o.linked_count ? `（已連 ${o.linked_count} 案）` : ''}</option>`).join('');
    // 已經連結過 → 這次是**重新同步**（owner 2026-09-01「我 crm 有更新費用，
    // 但是私帳沒有連結過去」）。連結是連結當下的一次性複製，CRM 後來新增的
    // 成本行不會自己流過去。這時不給「建立新專案」—— 那會多一個分身，同一筆
    // 錢在私帳算兩次；目標鎖定原本那一案，怎麼合併由下面的模式鈕決定。
    const relink = !!chk.linked;
    // 案源＝代開發票的分身：收入是那張發票的面額（母帳合約額），不是掛給你的成本行
    const src = mirrorOpts.source || '';
    const ptProj = src === '代開發票' ? state.projects.find(x => x.id === id) : null;
    const ptNote = ptProj
        ? `<div style="color:#c4b5fd;font-size:12px;margin-bottom:8px;line-height:1.5;">案源＝代開發票：私帳這案的收入＝母帳合約額 ${
            fmtNum(ptProj.contract_amount || 0)}${ptProj.contract_amount ? '' : '（母帳還沒填合約額，先用下面的成本行合計）'}，代辦費照費率自動算。</div>`
        : '';
    const warn = chk.warning && !ptProj
        ? `<div style="color:#fbbf24;font-size:12px;margin-bottom:8px;line-height:1.5;">${_esc(chk.warning)}${
            chk.staff_bound === false ? '（這個帳號還沒綁人員檔案，認不出哪幾行是你的）' : ''}</div>`
        : '';
    _mirrorModal(`${relink ? '重新同步私帳' : '推送到私帳'} — ${chk.name}`, `
        ${ptNote}${warn}
        <div style="color:#bbb;font-size:12px;margin-bottom:8px;">
            公司要付給你的（來自人員配置的成本行）</div>
        <table class="crm-table" style="width:100%;font-size:12px;">${rows
            || '<tr><td colspan="3" style="color:#666;">（沒有掛給你的成本行）</td></tr>'}
            <tr><td colspan="2" style="font-weight:600;">私帳收入合計</td>
                <td style="text-align:right;font-weight:600;color:#86efac;">
                    ${fmtNum(chk.total)}</td></tr></table>
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:8px;font-size:13px;">
            ${relink ? `
            <div style="color:#c4b5fd;">已連結到私帳的「${_esc(chk.linked.name)}」</div>
            <input type="hidden" id="pmm-target" value="${_esc(chk.linked.id)}">` : `
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="pmm-mode" value="new" checked>
                在私帳建立新專案（客戶：${_esc(chk.client || '未指定')}／案源：${_esc(src || '源日')}）</label>
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="pmm-mode" value="link"> 連結到既有私帳專案</label>
            <div id="pmm-target-wrap" style="margin-left:22px;">
            <select class="crm-input" id="pmm-target" disabled>
                <option value="">— 搜尋私帳案 —</option>${opts}</select></div>`}
            <div id="pmm-conflict" style="${relink ? '' : 'margin-left:22px;'}"></div>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    onclick="window._projMirrorClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="pmm-go"
                    >${relink ? '用 CRM 更新' : '建立並連結'}</button>
        </div>`);
    // 模式只有一個真相：目前勾中的那顆 radio。`sel.disabled` 由它推導，
    // 不另外記一份（按鈕文字曾是第三份，改一處漏一處就會自相矛盾）。
    const sel = document.getElementById('pmm-target');
    const box = document.getElementById('proj-mirror-body');
    box.dataset.source = src;          // 衝突區那幾顆模式鈕送出時也要帶同一個案源
    if (sel.tagName === 'SELECT') {
        // 400 筆私帳案塞原生 select 找不到東西 —— 升級成可搜尋（同專案對應那頁）
        searchableSelect(sel, { placeholder: '搜尋私帳案…' });
    }
    const sync = () => {
        if (sel.tagName === 'SELECT') {
            const wrap = document.getElementById('pmm-target-wrap');
            const on = _pmmLink();
            sel.disabled = !on;
            if (wrap) { wrap.style.opacity = on ? '' : '0.45'; wrap.style.pointerEvents = on ? '' : 'none'; }
        }
        _pmmDrawConflict(chk, id);
        const go = document.getElementById('pmm-go');
        const conflict = document.getElementById('pmm-conflict');
        if (go) { go.style.display = conflict && conflict.innerHTML ? 'none' : ''; }
    };
    box.addEventListener('change', sync);
    // 開啟當下就畫一次：重新同步沒有 radio 不會有 change 事件（對照與模式鈕要先出來）；
    // 新連結則要把下拉先灰掉
    sync();
    document.getElementById('pmm-go').addEventListener('click',
        (ev) => _projMirrorSubmit(ev.currentTarget, id, '', src));
};

/** 這次要連到**既有**私帳案嗎。重新同步沒有 radio（目標鎖定原本那一案），
 *  所以沒有 radio 時看有沒有目標 —— 有就是連既有，不是「建立新專案」。 */
const _pmmLink = () => {
    const r = document.querySelector('input[name="pmm-mode"]:checked');
    return r ? r.value === 'link' : !!document.getElementById('pmm-target')?.value;
};

async function _projMirrorSubmit(btn, id, mode, source) {
    source = source || document.getElementById('proj-mirror-body')?.dataset.source || '';
    const target = _pmmLink() ? (document.getElementById('pmm-target').value || '') : '';
    if (_pmmLink() && !target) { crmToast('請先選一個要連結的私帳專案'); return; }
    btn.disabled = true;
    try {
        const r = await _fetch(`/projects/${encodeURIComponent(id)}/mirror-to-mine`, {
            method: 'POST',
            body: JSON.stringify({ target_id: target, mode: mode || 'overwrite', source: source || null }),
        });
        window._projMirrorClose();
        crmToast(r.mode === 'keep' ? '已連結（私帳金額未變動）'
            : r.mode === 'import' ? `已從私帳匯入 ${r.imported} 個工項到 CRM 成本行`
                : r.mode === 'add' ? `已加進私帳：+${fmtNum(r.amount)}`
                    : `已在私帳同步收入 ${fmtNum(r.amount)}`);
        crmCacheInvalidate('/projects');
        await loadProjects();
        // 詳情面板要重畫（動作列從「推送到私帳」變成「已連結私帳 → 案名」）；
        // 同步過了，落後提示要重問（detail.js 用 staleFor 擋重複查詢）
        delete document.getElementById('proj-bar-actions')?.dataset.staleFor;
        const p = state.projects.find(x => x.id === id);
        if (p) { callbacks.renderDetail?.(p); }
    } catch (e) {
        btn.disabled = false;
        crmToast('連結私帳失敗：' + e.message, 6000);
    }
}

/** 選到的那個私帳案已經填過工項 → 把兩邊並排列出來，讓人有依據可判斷，
 *  再給三個處理方式（owner 2026-08-30「跳出幾個選擇讓我決定要怎麼做」）。
 *
 *  🔴 沒有預設哪一個是對的：私帳那份可能是他照實際請款填的（比 CRM 準），
 *  也可能是舊的估算。只給三顆按鈕不給數字，等於要他憑印象賭一把。 */
function _pmmDrawConflict(chk, projectId) {
    const box = document.getElementById('pmm-conflict');
    if (!box) { return; }
    const id = document.getElementById('pmm-target').value || '';
    // 重新同步時目標就是已連結那一案（它自己帶著現有工項回來，不必去 options 找）
    const opt = chk.linked || (_pmmLink()
        ? (chk.options || []).find(o => o.id === id) : null);
    const mineSplit = (opt && opt.split) || {};
    // 🔴 「已經承接過別的 CRM 案」也要跳選擇 —— 它可能沒有工項明細，但它的
    // 合約金額裡已經有別案鏡射進來的錢，直接覆蓋就是把那筆洗掉。
    const shared = !!(opt && opt.linked_count);
    // 🔴 重新同步一定要畫：那正是「要怎麼合併」的決定點。私帳那案剛好沒工項
    // 時直接收掉選擇，就只剩一顆預設覆蓋的按鈕，等於幫他決定了。
    if (!chk.linked && !Object.keys(mineSplit).length && !shared) {
        box.innerHTML = ''; return;
    }

    // CRM 這側的工項合計用後端算好的 `crm_split`（mirror_lines 一次算出 lines
    // 與 split 兩份）—— 使用者就是拿這個數字跟私帳現有的並排做決定。
    const crmSplit = chk.crm_split || {};
    const keys = [...new Set([...Object.keys(mineSplit),
                              ...Object.keys(crmSplit)])];
    const sum = (o) => Object.values(o).reduce((n, v) => n + (v || 0), 0);
    const cell = (v) => (v ? fmtNum(v) : '<span style="color:#3f3f46;">—</span>');
    const rows = keys.map(k => `<tr>
        <td style="color:#ddd;">${_esc(k)}</td>
        <td style="text-align:right;">${cell(mineSplit[k])}</td>
        <td style="text-align:right;">${cell(crmSplit[k])}</td></tr>`).join('');
    const btn = (mode, label, title) =>
        `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-mode="${mode}"
                 title="${_esc(title)}">${label}</button>`;
    box.innerHTML = `
        <div style="margin-top:10px;border:1px solid #4c3d78;border-radius:6px;padding:10px;">
          <div style="color:#c4b5fd;font-size:12px;margin-bottom:6px;">
            「${_esc(opt.name)}」${chk.linked
                ? '是這一案的私帳分身 —— CRM 這邊改過之後要怎麼同步'
                : shared
                    ? `已經承接 ${opt.linked_count} 個 CRM 案的收入 —— 要怎麼處理`
                    : '已經填過工項 —— 要怎麼處理'}？</div>
          <table class="crm-table" style="width:100%;font-size:12px;">
            <tr><th style="text-align:left;">工項</th>
                <th style="text-align:right;">私帳現有</th>
                <th style="text-align:right;">CRM 成本行</th></tr>
            ${rows}
            <tr style="font-weight:600;"><td>合計</td>
                <td style="text-align:right;">${fmtNum(sum(mineSplit))}</td>
                <td style="text-align:right;">${fmtNum(sum(crmSplit))}</td></tr>
          </table>
          <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;">
            ${chk.linked ? '' : btn('add', '加進去',
                  '這個 CRM 案的錢**加**到私帳案上（同名工項相加、'
                  + '合約金額累加）—— 一個私帳案承接多筆時用這個')}
            ${btn('overwrite', chk.linked ? '用 CRM 更新' : '用 CRM 覆蓋', shared
                  ? '⚠ 私帳的金額換成這個 CRM 案算出來的 —— 已經承接的別案收入會被洗掉'
                  : chk.linked
                      ? '私帳的工項換成 CRM 現在算出來的（這就是「同步過去」）'
                      : '私帳的工項換成 CRM 成本行算出來的')}
            ${!chk.linked ? '' : btn('add', '再加一次',
                  '⚠ 很少用：把 CRM 這邊的金額**再加**到私帳現有的上面。'
                  + '同一案重新同步時通常是要「用 CRM 更新」—— 加會讓同一筆錢算兩次')}
            ${btn('keep', '保留私帳', '只建立連結，私帳的金額一毛不動')}
            ${btn('import', '從私帳匯入 CRM',
                  '反過來：把私帳的工項寫成 CRM 的成本行（掛給你、階段後期製作），'
                  + '私帳不動。匯入後那些成本行在 CRM 照常可以編。')}
          </div>
        </div>`;
    box.querySelectorAll('button[data-mode]').forEach((b) => {
        b.addEventListener('click', () =>
            _projMirrorSubmit(b, projectId, b.dataset.mode));
    });
}

/** 疊在詳情面板之上的預覽視窗。重複使用同一個 overlay（每次重建會在 body
 *  裡疊出一堆孤兒節點，radio 的 name 也會互相搶）。 */
function _mirrorModal(title, bodyHtml) {
    let o = document.getElementById('proj-mirror-modal');
    if (!o) {
        o = document.createElement('div');
        o.id = 'proj-mirror-modal';
        o.className = 'crm-modal-overlay';
        o.style.zIndex = '1100';
        o.innerHTML = `<div class="crm-modal" style="max-width:min(560px,94vw);">
            <div class="crm-modal-header">
                <h3 id="proj-mirror-title"></h3>
                <button class="crm-detail-close"
                        onclick="window._projMirrorClose()">&#x2715;</button>
            </div>
            <div class="crm-modal-body" id="proj-mirror-body"></div>
        </div>`;
        document.body.appendChild(o);
    }
    o.querySelector('#proj-mirror-title').textContent = title;
    o.querySelector('#proj-mirror-body').innerHTML = bodyHtml;
    o.style.display = 'flex';
}

window._projMirrorClose = function () {
    const o = document.getElementById('proj-mirror-modal');
    if (o) { o.style.display = 'none'; o.querySelector('#proj-mirror-body').innerHTML = ''; }
};
