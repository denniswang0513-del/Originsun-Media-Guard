/**
 * 報價分頁：GET /api/v1/crm/quotations 依狀態分組（順序＝options.quote_statuses），每組 10 筆一頁＋載入更多。
 * 狀態轉換由**位置**推：[0]草稿→建立（＝已寄送）、[1]已寄送→成案([2])／拒絕([3])；
 * 改狀態打 POST /api/v1/crm/m/quotations/{id}/status {status, activate}。
 * 按鈕文字是動作（建立／成案／拒絕；owner 2026-09-07 把「簽回」改叫「成案」、「寄出」改叫「建立」），目標狀態字從 options 取，不寫死。
 * 草稿卡多一顆「預覽」：GET /quotations/{id}/preview 回 HTML 塞 iframe，讓人先確認內容再建立（不改狀態、不存檔）。
 * 建立後的卡才有「PDF」：GET /api/v1/crm/quotations/{id}/pdf（跟桌機同一份 PDF、同一個檔名規則）。
 * 項目編輯是「大項目 → 子項目」（owner 2026-09-07），模型 js/shared/quote-amounts.js 的 groupQuoteItems／flattenQuoteGroups。
 *
 * 開報價單（owner 2026-09-06，推翻 CRM_MOBILE_PLAN §「報價項目編輯不做」）：底部抽屜開表單，
 * 新增打 POST /crm/projects/{id}/quotations、編輯打 PUT /crm/quotations/{id}——跟桌機同兩支端點。
 * 🔴 報價時案子通常還沒成立（owner 2026-09-07），所以入口是**客戶**不是專案：客戶必選（可現場建），
 *    專案自己打字；沒連結既有專案就在儲存時先建一個殼專案（階段＝options.quote_phase）再掛報價。
 * 🔴 這兩支的守衛是 `_check_auth`＝**管理員限定**，所以入口只給管理員（不是 canWrite）。
 * 🔴 金額試算用 js/shared/quote-amounts.js（跟後端 _calc_quotation 同一份規則），手機不自己算稅。
 */
import { quoteTotals, parsePaymentStages, paymentStagesToText } from '/js/shared/quote-amounts.js';
import * as _QA from '/js/shared/quote-amounts.js';
import { mfetch, mdownload, toast, esc, money, fmtDate, todayLocal, quotePdfFilename } from '../shell.js';
// 🔴 Cloudflare 給 .js 4 小時瀏覽器快取：新分頁 js 配舊 quote-amounts.js 時，named import 拿不到的 export 會讓
//    整個模組載入失敗。新 export 先用命名空間拿、缺就退回同款本地實作（reference_cloudflare_js_cache：契約要相容一輪；
//    快取過期後這兩條退路可以拿掉）。預覽刻意不在 shell.js 加新 export：走既有 mfetch＋端點 ?as=json。
const groupQuoteItems = _QA.groupQuoteItems || ((items) => {
    const groups = [], byName = new Map();
    (items || []).forEach(it => {
        const name = String(it.group_name || '').trim();
        let g = byName.get(name);
        if (!g) { g = { name, items: [] }; byName.set(name, g); groups.push(g); }
        const row = { ...it }; delete row.group_name; g.items.push(row);
    });
    return groups;
});
const flattenQuoteGroups = _QA.flattenQuoteGroups || ((groups) => (groups || []).flatMap(g => g.items.map(it => ({ ...it, group_name: g.name }))));
import { list, opt, skeleton, emptyBox, errBox, pill, withBusy, markStale, shouldLoad, renderPaged,
    isAdmin, openSheet, closeSheet, pickerHtml, mountPicker, copyText } from '../ui.js';

function transitions(status) {
    const v = list('quote_statuses');
    const [draft, sent, signed, rejected] = v;
    if (status === draft && sent) return [{ label: '建立', to: sent }];   // owner 2026-09-07：「寄出」改叫「建立」（草稿→正式建立這張報價單）
    if (status === sent) return [
        signed ? { label: '成案', to: signed, ask: true } : null,
        rejected ? { label: '拒絕', to: rejected, danger: true } : null,
    ].filter(Boolean);
    return [];
}

// 動作列跟金額同一列：按鈕不吃 .m-actions 的「各半」flex，照內容寬、文字不換行（「寄出」被擠成兩行過）
const BTN = 'flex:0 0 auto;white-space:nowrap';

function cardHtml(q) {
    // PDF 與線上連結要「寄出」之後才出現（owner 2026-09-07「送出再產生連結與 pdf 按鈕」）：草稿還在改，不該流出去
    const sent = q.status !== list('quote_statuses')[0];
    const btns = transitions(q.status).map(t =>
        `<button type="button" class="m-btn sm ${t.danger ? 'danger' : 'pri'}" style="${BTN}" data-id="${esc(q.id)}" data-to="${esc(t.to)}"${t.ask ? ' data-ask="1"' : ''}>${esc(t.label)}</button>`).join('')
        + (isAdmin() ? `<button type="button" class="m-btn sm" style="${BTN}" data-edit="${esc(q.id)}">編輯</button>` : '')
        // 預覽：只把版面畫給你確認，不建立、不存檔（owner 2026-09-07「預覽點的時候讓我確認內容，不用建立報價單」）
        + (sent ? '' : `<button type="button" class="m-btn sm" style="${BTN}" data-preview="${esc(q.id)}">預覽</button>`)
        + (sent ? `<button type="button" class="m-btn sm" style="${BTN}" data-pdf="${esc(q.id)}">PDF</button>` : '')
        // 線上檢視連結：已有就誰都能複製；還沒有只有管理員能建（鑄連結是寫入）
        + (sent && (q.share_url || isAdmin()) ? `<button type="button" class="m-btn sm" style="${BTN}" data-share="${esc(q.id)}">${q.share_url ? '複製連結' : '建立連結'}</button>` : '');
    // 金額：顯示折後價（最終報價），有優惠時原價（含稅總計）畫掉放旁邊（owner 2026-09-07）
    const hasFinal = q.final_price !== null && q.final_price !== undefined;
    const discounted = hasFinal && q.final_price < q.total;
    const amount = 'total' in q
        ? `<span class="amt">${money(hasFinal ? q.final_price : q.total)}${discounted ? ` <s style="color:var(--sub);font-weight:400;font-size:12px">${money(q.total)}</s>` : ''}</span>`
        : '<span></span>';
    return `
      <div class="m-card">
        <div class="t"><div class="name">${esc(q.project_name || '（未連專案）')}</div>${pill(q.version)}</div>
        <div class="sub">${esc(q.client_short_name || '')}${q.quote_date ? ' · ' + esc(fmtDate(q.quote_date)) : ''}</div>
        <div class="row">${amount}
          <span class="m-actions w" style="margin:0">${btns}</span></div>
      </div>`;
}

async function change(btn, host) {
    const to = btn.dataset.to, id = btn.dataset.id;
    let activate = false;
    if (btn.dataset.ask) activate = window.confirm('順便啟動專案（進入製作）？');
    await withBusy(btn, async () => {
        try {
            await mfetch(`/api/v1/crm/m/quotations/${encodeURIComponent(id)}/status`, { method: 'POST', body: { status: to, activate } });
            toast('報價已改為 ' + to + (activate ? '，專案已啟動' : ''));
            markStale('projects');     // 成案啟動專案：專案分頁的階段與首頁數字都變了
            await load(host);
        } catch (e) { toast(e.message, 'err'); }
    });
}

// 線上檢視連結：沒有就先鑄一條（冪等），然後複製完整網址；手機貼給客戶或自己開都行
async function shareLink(btn, rows, host) {
    const q = rows.find(r => r.id === btn.dataset.share);
    if (!q) return;
    await withBusy(btn, async () => {
        try {
            let url = q.share_url;
            if (!url) {
                const r = await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(q.id)}/share`, { method: 'POST' });
                url = r.share_url; q.share_url = url;
            }
            const full = location.origin + url;
            toast((await copyText(full)) ? '連結已複製：' + full : '複製失敗，連結：' + full, 'ok');
        } catch (e) { toast(e.message, 'err'); }
    });
    // 第一次建完把按鈕字換掉：要在 withBusy 之後改（它的 finally 會把按鈕字還原成按下前的）；列上的 q.share_url 已更新，不必整份重抓重畫
    if (q.share_url) btn.textContent = '複製連結';
}

/** 預覽：後端把同一份報價單版面渲染成 HTML（不改狀態、不存檔），塞進整頁的 iframe；關掉就回清單。 */
async function previewQuote(btn) {
    await withBusy(btn, async () => {
        try {
            // 同一支預覽端點，?as=json 回 {html}：手機只走 shell 的 mfetch（不自己 fetch、不用新 export）
            const html = (await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(btn.dataset.preview)}/preview?as=json`)).html;
            let ov = document.getElementById('qt-preview');
            if (!ov) {
                ov = document.createElement('div'); ov.id = 'qt-preview';
                ov.style.cssText = 'position:fixed;inset:0;z-index:60;background:#fff;display:flex;flex-direction:column';
                document.body.appendChild(ov);
            }
            ov.innerHTML = `<div style="flex:0 0 auto;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 14px;background:#1a1a1a;color:#fff;font-size:14px">
                <span>預覽（還沒建立，關掉可以回去改）</span><button type="button" class="m-btn sm" id="qt-preview-close" style="${BTN}">關閉</button></div>
                <iframe style="flex:1;border:0;width:100%;background:#fff"></iframe>`;
            ov.querySelector('iframe').srcdoc = html;
            ov.querySelector('#qt-preview-close').onclick = () => ov.remove();
        } catch (e) { toast(e.message, 'err'); }
    });
}

async function downloadPdf(btn, rows) {
    const q = rows.find(r => r.id === btn.dataset.pdf);
    if (!q) return;
    await withBusy(btn, async () => {
        try { await mdownload(`/api/v1/crm/quotations/${encodeURIComponent(q.id)}/pdf`, quotePdfFilename(q)); }
        catch (e) { toast(e.message, 'err'); }
    });
}

// ── 開報價單 ─────────────────────────────────────────────────

const F = (k) => document.getElementById('qf-' + k);
const EMPTY_ROW = { description: '', unit: '式', quantity: 1, unit_price: 0 };
let _form = null;      // { id, project_id, project_label, status, groups[{name, items[]}] }；null＝抽屜沒開

// 項目的編輯模型是「大項目 → 子項目」（owner 2026-09-07：一列一列填、同名大項目被隔開就印成兩段）：
// 進來時 groupQuoteItems 收成組，存檔時 flattenQuoteGroups 攤平，同一組的列自然連在一起
const groupsOf = (items) => { const g = groupQuoteItems(items); return g.length ? g : [{ name: '', items: [{ ...EMPTY_ROW }] }]; };
const flatItems = () => flattenQuoteGroups(_form.groups);

const itemsHtml = () => _form.groups.map((g, gi) => `
  <div class="m-card" data-g="${gi}" style="padding:10px;margin-bottom:8px">
    <div class="row2" style="grid-template-columns:1fr auto;align-items:end">
      <div><label>大項目</label><input data-gk="name" value="${esc(g.name)}" placeholder="例：拍攝／後期製作／其他"></div>
      <button type="button" class="m-btn sm danger" data-delg="${gi}" style="${BTN}">刪除大項目</button>
    </div>
    ${g.items.map((it, i) => `
    <div data-i="${i}" style="border-top:1px solid var(--line);padding-top:8px;margin-top:8px">
      <input data-k="description" value="${esc(it.description || '')}" placeholder="子項目（例：剪輯、調光、動態字卡）">
      <div class="row2">
        <div><label>數量</label><input data-k="quantity" type="number" inputmode="numeric" min="0" value="${it.quantity ?? 1}"></div>
        <div><label>單價</label><input data-k="unit_price" type="number" inputmode="numeric" min="0" value="${it.unit_price ?? 0}"></div>
      </div>
      <div class="row2" style="grid-template-columns:1fr auto;align-items:end">
        <div><label>單位</label><input data-k="unit" value="${esc(it.unit || '')}" placeholder="式／支／人次"></div>
        <button type="button" class="m-btn sm danger" data-del="${i}" style="${BTN}">刪除</button>
      </div>
    </div>`).join('')}
    <button type="button" class="m-more" data-add="${gi}">＋ 子項目</button>
  </div>`).join('');

function drawItems() {
    document.getElementById('qf-items').innerHTML = _form.groups.length ? itemsHtml() : '<div class="m-empty">還沒有大項目</div>';
    recalc();
}

function recalc() {
    const t = quoteTotals({ items: flatItems(), taxRate: F('tax_rate').value, discount: _form.discount });   // 舊報價的稅前折扣要算進去，跟後端存的總計一致
    // 優惠 ↔ 最終報價 互推（同發票的未稅／含稅）：只寫「不是正在打的那一格」，免得游標被搶
    const promoEl = F('promo'), finalEl = F('final_price');
    if (_form.anchor === 'promo') {
        finalEl.value = promoEl.value === '' ? '' : Math.max(t.total - (parseInt(promoEl.value) || 0), 0);
    } else if (_form.anchor === 'final') {
        promoEl.value = finalEl.value === '' ? '' : Math.max(t.total - (parseInt(finalEl.value) || 0), 0);
    }
    // 0 是合法的最終報價（全免）：只有空字串才算「沒填」（跟 save 送出去的規則一樣）
    const finalPrice = finalEl.value.trim() === '' ? null : (parseInt(finalEl.value) || 0);
    const promo = finalPrice != null && finalPrice < t.total ? t.total - finalPrice : 0;
    document.getElementById('qf-calc').innerHTML = `
      <div class="row"><span>合計</span><span class="amt">${money(t.subtotal)}</span></div>
      <div class="row"><span>營業稅 ${parseInt(F('tax_rate').value) || 0}%</span><span class="amt">${money(t.tax)}</span></div>
      ${promo ? `<div class="row"><span>專案優惠</span><span class="amt">−${money(promo)}</span></div>` : ''}
      <div class="row"><span><b>報價總額</b>（含稅）</span><span class="amt"><b>${money(finalPrice != null ? finalPrice : t.total)}</b></span></div>`;
}

function formHtml(templates, q) {
    const editing = !!q;
    return `
      <div class="m-h">${editing ? `編輯報價 v${q.version}` : '開報價單'}</div>
      <form class="m-form" id="qf-form" autocomplete="off">
        ${editing
            ? `<label>專案</label><div class="m-card" style="padding:10px">${esc(_form.project_label || '（未連專案）')}</div>`
            : `<label class="req">客戶</label>${pickerHtml('qf-client_id')}
        <button type="button" class="m-more" id="qf-new-client">＋ 建立新客戶</button>
        <label class="req">專案</label><input id="qf-project_name" placeholder="直接打案名，例：2026 品牌形象短片">
        <label>連結既有專案</label>${pickerHtml('qf-link_project')}
        <div class="m-hint" id="qf-project-hint">選了客戶才找得到他的案子；沒連結就照上面的案名建一個新案</div>`}
        ${editing ? '' : `<label>套用範本</label>${pickerHtml('qf-template')}
        <div class="m-hint">選了範本會帶入項目、稅率、備註、付款方式，帶進來還可以改</div>`}
        <label>規格</label><input id="qf-spec" placeholder="用、分隔，例：形象短片 90 秒 1 支、含中文字幕">
        <div class="m-h">項目</div>
        <div class="m-hint">先加大項目（拍攝／後期製作／其他），再在裡面加子項目；同一個大項目的子項目在報價單上會連在一起、各自小結</div>
        <div id="qf-items"></div>
        <button type="button" class="m-more" id="qf-add">＋ 大項目</button>
        <label>稅率 %</label><input id="qf-tax_rate" type="number" inputmode="numeric" min="0" value="5">
        <div class="row2">
          <div><label>優惠</label><input id="qf-promo" type="number" inputmode="numeric" min="0" placeholder="折多少"></div>
          <div><label>最終報價（含稅）</label><input id="qf-final_price" type="number" inputmode="numeric" min="0" placeholder="空白＝照算出來的"></div>
        </div>
        <div class="m-hint">兩格互推：打優惠就算出最終報價，打最終報價就算出優惠。報價單上印成「專案優惠」</div>
        <label>付款方式</label><input id="qf-stages" placeholder="簽約 30%, 拍攝 40%, 交片 30%">
        <label>備註</label><textarea id="qf-terms" rows="3" placeholder="一行一條"></textarea>
        <div class="m-card" id="qf-calc" style="margin-top:12px"></div>
        <div id="qf-err" class="m-err" hidden></div>
        <button type="submit" class="m-btn-primary" id="qf-submit">${editing ? '儲存修改' : '建立報價'}</button>
        <button type="button" class="m-btn wide" id="qf-cancel">取消</button>
      </form>`;
}

async function openForm(host, id) {
    let templates = [], q = null;
    try {
        // 專案清單不在這裡抓：選了客戶才抓他的案子（loadClientProjects），沒選客戶前抓了也用不到
        if (id) q = await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(id)}`);
        else templates = (await mfetch('/api/v1/crm/quotation-templates').catch(() => ({ templates: [] }))).templates || [];
    } catch (e) { toast(e.message, 'err'); return; }

    _form = {
        id: id || null,
        project_id: q ? q.project_id : '',        // 有值＝掛既有案；空＝儲存時照輸入框的案名建一個（案名只住在輸入框，不另存一份）
        client_id: '',
        anchor: q && q.final_price != null ? 'final' : null,   // 優惠／最終報價 哪一格是人填的
        discount: q ? (q.discount || 0) : 0,      // 稅前折扣已退場，舊值只拿來算總計（PUT 不送＝後端不動它）
        project_label: q ? [q.client_short_name, q.project_name].filter(Boolean).join('｜') : '',
        status: q ? q.status : (list('quote_statuses')[0] || ''),   // 字彙只從 options 來
        groups: groupsOf(q && q.items),
    };

    openSheet(formHtml(templates, q));

    if (!id) {
        mountClientPicker();
        document.getElementById('qf-new-client').onclick = createClient;
        // 打字改案名＝要建新案，剛才連結的那個就不算了
        F('project_name').oninput = () => { _form.project_id = ''; };
        mountPicker('qf-template', {
            items: templates.map(t => ({ value: t.id, label: t.name })),
            placeholder: '（不套用）',
            onPick: (v) => applyTemplate(templates.find(t => t.id === v)),
        });
    }
    if (q) {
        F('spec').value = q.spec || '';
        F('tax_rate').value = q.tax_rate ?? 5;
        F('final_price').value = q.final_price ?? '';
        F('stages').value = paymentStagesToText(q.payment_stages);
        F('terms').value = q.terms || '';
    }
    drawItems();

    document.getElementById('qf-add').onclick = () => {
        _form.groups.push({ name: '', items: [{ ...EMPTY_ROW }] });
        drawItems();
        const inputs = document.querySelectorAll('#qf-items input[data-gk="name"]');
        inputs[inputs.length - 1]?.focus();      // 新的大項目先取名
    };
    document.getElementById('qf-cancel').onclick = () => { _form = null; closeSheet(); };
    document.getElementById('qf-items').oninput = (ev) => {
        const inp = ev.target.closest('input[data-k], input[data-gk]');
        if (!inp) return;
        const g = _form.groups[Number(inp.closest('[data-g]').dataset.g)];
        if (inp.dataset.gk) { g.name = inp.value; return; }      // 大項目改名：小結歸組在存檔時攤平才算
        const i = Number(inp.closest('[data-i]').dataset.i), k = inp.dataset.k;
        g.items[i][k] = (k === 'quantity' || k === 'unit_price') ? (parseInt(inp.value) || 0) : inp.value;
        recalc();                       // 只重算，不重畫（重畫會把游標踢掉）
    };
    document.getElementById('qf-items').onclick = (ev) => {
        const b = ev.target.closest('button[data-del], button[data-delg], button[data-add]');
        if (!b) return;
        const gi = Number(b.closest('[data-g]').dataset.g), g = _form.groups[gi];
        if ('add' in b.dataset) g.items.push({ ...EMPTY_ROW });
        else if ('delg' in b.dataset) {
            if (g.items.some(it => (it.description || '').trim()) && !window.confirm('這個大項目底下還有子項目，一起刪掉？')) return;
            _form.groups.splice(gi, 1);
        } else g.items.splice(Number(b.dataset.del), 1);
        drawItems();
    };
    F('tax_rate').oninput = recalc;
    F('promo').oninput = () => { _form.anchor = 'promo'; recalc(); };
    F('final_price').oninput = () => { _form.anchor = 'final'; recalc(); };
    document.getElementById('qf-form').onsubmit = (ev) => { ev.preventDefault(); save(host); };
}

// 客戶清單來自 options（跟發票分頁同一份，母帳的客戶），現場建的也 push 回去
function mountClientPicker(selected = '') {
    mountPicker('qf-client_id', {
        items: (opt().clients || []).map(c => ({ value: c.id, label: c.short_name || c.full_name || c.id })),
        placeholder: '打字找客戶', value: selected,
        onPick: (v) => { _form.client_id = v; loadClientProjects(v); },
    });
}

async function createClient() {
    const name = (prompt('客戶名稱（代稱）：') || '').trim();
    if (!name) return;
    try {
        const r = await mfetch('/api/v1/crm/clients', { method: 'POST', body: { short_name: name } });
        const c = r.client || r;
        (opt().clients || []).push({ id: c.id, short_name: c.short_name || name, full_name: c.full_name || '', tax_id: c.tax_id || '' });
        _form.client_id = c.id;
        mountClientPicker(c.id);
        loadClientProjects(c.id);
        toast('客戶已建立：' + (c.short_name || name));
    } catch (e) { toast(e.message, 'err'); }
}

// 連結既有專案：拿這個客戶的全部案子（不是最近 100 筆），免得打同名再建一個重複的案
async function loadClientProjects(clientId) {
    let rows = [];
    try {
        const d = await mfetch(`/api/v1/crm/projects?client_id=${encodeURIComponent(clientId)}`);
        rows = d.projects || [];
    } catch (_) { /* 找不到就只走「打字建新案」那條 */ }
    mountPicker('qf-link_project', {
        items: rows.map(p => ({ value: p.id, label: p.name })),
        placeholder: rows.length ? '（不連結，照上面的案名建新案）' : '這個客戶還沒有案子',
        onPick: (v) => {
            _form.project_id = v;
            const hit = rows.find(r => r.id === v);
            if (hit) F('project_name').value = hit.name;
        },
    });
}

function applyTemplate(t) {
    if (!t) return;
    if (Array.isArray(t.items) && t.items.length) _form.groups = groupsOf(t.items.map(it => ({ ...EMPTY_ROW, ...it })));
    if (t.tax_rate != null) F('tax_rate').value = t.tax_rate;
    if (t.terms) F('terms').value = t.terms;
    F('stages').value = paymentStagesToText(t.payment_stages);
    drawItems();
}

/** 案子還沒成立：照案名先開一個殼專案（階段由後端 options 的 quote_phase 給），回新案 id。 */
async function createShellProject(projectName) {
    const np = await mfetch('/api/v1/crm/projects', {
        method: 'POST',
        body: { name: projectName, client_id: _form.client_id, ...(opt().quote_phase ? { status: opt().quote_phase } : {}) },
    });
    markStale('projects');
    return (np.project || np).id;
}

async function save(host) {
    const err = document.getElementById('qf-err');
    const fail = (msg) => { err.hidden = false; err.textContent = msg; };
    const items = flatItems().filter(it => (it.description || '').trim());   // 大項目一組接一組攤平，報價單上不會被拆開
    const projectName = _form.id ? '' : F('project_name').value.trim();
    if (!_form.id && !_form.client_id) return fail('請先選客戶');
    if (!_form.id && !_form.project_id && !projectName) return fail('請填專案名稱，或連結一個既有專案');
    if (!items.length) return fail('至少要有一個有說明的項目');
    err.hidden = true;

    const body = {
        ...(_form.status ? { status: _form.status } : {}),   // 拿不到字彙就不送，讓後端給它的預設
        tax_rate: parseInt(F('tax_rate').value) || 0,
        final_price: F('final_price').value.trim() === '' ? null : (parseInt(F('final_price').value) || 0),   // 0 是合法的最終報價（全免），不是「沒填」
        spec: F('spec').value.trim(),
        terms: F('terms').value.trim(),
        payment_stages: parsePaymentStages(F('stages').value),
        items: items.map((it, i) => ({
            group_name: (it.group_name || '').trim(), description: it.description.trim(),
            unit: (it.unit || '式').trim(), quantity: parseInt(it.quantity) || 0,
            unit_price: parseInt(it.unit_price) || 0, internal_cost: it.internal_cost || 0,
            note: it.note || '', sort_order: i,
        })),
    };
    if (!_form.id) body.quote_date = todayLocal();

    const btn = document.getElementById('qf-submit');
    await withBusy(btn, async () => {
        try {
            if (_form.id) {
                await mfetch(`/api/v1/crm/quotations/${encodeURIComponent(_form.id)}`, { method: 'PUT', body });
            } else {
                const pid = _form.project_id || await createShellProject(projectName);
                await mfetch(`/api/v1/crm/projects/${encodeURIComponent(pid)}/quotations`, { method: 'POST', body });
            }
            toast(_form.id ? '報價已更新' : '報價已建立');
            _form = null;
            closeSheet();
            await load(host);
        } catch (e) { err.hidden = false; err.textContent = e.message; }
    });
}

// ── 清單 ────────────────────────────────────────────────────

let _rows = [];      // 目前畫面上的報價（PDF 鈕要拿 quote_date／專案／客戶組檔名）

async function load(host) {
    const box = host.querySelector('#qt-list');
    box.innerHTML = skeleton(3);
    try {
        const d = await mfetch('/api/v1/crm/quotations');
        const rows = d.quotations || [];
        _rows = rows;
        if (!rows.length) { box.innerHTML = emptyBox('沒有報價'); return; }
        const order = list('quote_statuses');
        const groups = new Map(order.map(s => [s, []]));
        for (const q of rows) {
            if (!groups.has(q.status)) groups.set(q.status, []);
            groups.get(q.status).push(q);
        }
        const shown = [...groups].filter(([, arr]) => arr.length);
        box.innerHTML = shown.map(([s, arr], i) => `<div class="m-h">${esc(s)}（${arr.length}）</div><div data-group="${i}"></div>`).join('');
        shown.forEach(([, arr], i) => renderPaged(box.querySelector(`[data-group="${i}"]`),
            arr.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))), cardHtml));
    } catch (e) { box.innerHTML = errBox(e); }
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `${isAdmin() ? '<button type="button" class="m-btn-primary" id="qt-new">開報價單</button>' : ''}<div id="qt-list"></div>`;
        if (isAdmin()) document.getElementById('qt-new').onclick = () => openForm(host, null);
        host.addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-to]');
            if (b) return change(b, host);
            const e = ev.target.closest('button[data-edit]');
            if (e) return openForm(host, e.dataset.edit);
            const p = ev.target.closest('button[data-pdf]');
            if (p) return downloadPdf(p, _rows);
            const v = ev.target.closest('button[data-preview]');
            if (v) return previewQuote(v);
            const s = ev.target.closest('button[data-share]');
            if (s) shareLink(s, _rows, host);
        });
    }
    if (shouldLoad('quotes', { first })) await load(host);
}
