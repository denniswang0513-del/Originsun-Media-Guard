/**
 * 報價分頁：GET /api/v1/crm/quotations 依狀態分組（順序＝options.quote_statuses），每組 10 筆一頁＋載入更多。
 * 狀態轉換由**位置**推：[0]草稿→寄出、[1]已寄出→簽回([2])／拒絕([3])；
 * 改狀態打 POST /api/v1/crm/m/quotations/{id}/status {status, activate}。
 * 按鈕文字是動作（寄出／簽回／拒絕），目標狀態字從 options 取，不寫死。
 * 每張卡都有「PDF」：GET /api/v1/crm/quotations/{id}/pdf（跟桌機同一份 PDF、同一個檔名規則）。
 */
import { mfetch, mdownload, toast, esc, money, fmtDate, quotePdfFilename } from '../shell.js';
import { list, skeleton, emptyBox, errBox, pill, withBusy, markStale, shouldLoad, renderPaged } from '../ui.js';

function transitions(status) {
    const v = list('quote_statuses');
    const [draft, sent, signed, rejected] = v;
    if (status === draft && sent) return [{ label: '寄出', to: sent }];
    if (status === sent) return [
        signed ? { label: '簽回', to: signed, ask: true } : null,
        rejected ? { label: '拒絕', to: rejected, danger: true } : null,
    ].filter(Boolean);
    return [];
}

// 動作列跟金額同一列：按鈕不吃 .m-actions 的「各半」flex，照內容寬、文字不換行（「寄出」被擠成兩行過）
const BTN = 'flex:0 0 auto;white-space:nowrap';

function cardHtml(q) {
    const btns = transitions(q.status).map(t =>
        `<button type="button" class="m-btn sm ${t.danger ? 'danger' : 'pri'}" style="${BTN}" data-id="${esc(q.id)}" data-to="${esc(t.to)}"${t.ask ? ' data-ask="1"' : ''}>${esc(t.label)}</button>`).join('')
        + `<button type="button" class="m-btn sm" style="${BTN}" data-pdf="${esc(q.id)}">PDF</button>`;
    return `
      <div class="m-card">
        <div class="t"><div class="name">${esc(q.project_name || '（未連專案）')}</div>${pill(q.version)}</div>
        <div class="sub">${esc(q.client_short_name || '')}${q.quote_date ? ' · ' + esc(fmtDate(q.quote_date)) : ''}</div>
        <div class="row">${'total' in q ? `<span class="amt">${money(q.total)}</span>` : '<span></span>'}
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
            markStale('projects');     // 簽回啟動專案：專案分頁的階段與首頁數字都變了
            await load(host);
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
        host.innerHTML = '<div id="qt-list"></div>';
        host.addEventListener('click', (ev) => {
            const b = ev.target.closest('button[data-to]');
            if (b) return change(b, host);
            const p = ev.target.closest('button[data-pdf]');
            if (p) downloadPdf(p, _rows);
        });
    }
    if (shouldLoad('quotes', { first })) await load(host);
}
