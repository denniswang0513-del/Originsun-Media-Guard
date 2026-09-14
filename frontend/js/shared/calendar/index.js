// ────────────────────────────────────────────────────────────────────────────
// calendar/index.js — 行事曆共用元件入口（docs/CALENDAR_PLAN.md §5）。
//   mountCalendar({ host, fetch, esc, scope, lockedScope, hooks })
//   hooks：toast(msg, kind)、openShoot(preset)（沒給＝開手機行事曆分頁）、openProject(id, name)、modalRoot()
// 一份元件、三個宿主：公布欄子視圖、專案詳情分頁（scope 鎖 project:<id>）、手機（自己的清單畫法，只共用 ctx）。
// ────────────────────────────────────────────────────────────────────────────
import { z, api, addDays, iso, loadEvents, loadOptions, mondayOf, today } from './ctx.js';
import { dayHtml, monthHtml, toolbarHtml, weekHtml } from './views.js';
import { close as closeModal, openColors, openForm, pickProject } from './form.js';

export { z };

const CSS = `
.cal-root{--cal-bg:#1f2124;--cal-bg2:#26282c;--cal-line:#34373c;--cal-ink:#e8e8ea;--cal-sub:#9aa0a8;--cal-dim:#6b7078;--cal-accent:#3b82f6;--cal-mine:#1d2b44;--cal-hol:#4a2426;color:var(--cal-ink);font-size:13px}
.cal-root.light{--cal-bg:#fff;--cal-bg2:#f5f5f5;--cal-line:#e2e2e2;--cal-ink:#1a1a1a;--cal-sub:#666;--cal-dim:#999;--cal-mine:#e8f0fe;--cal-hol:#fdecec}
.cal-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}.cal-spacer{flex:1}.cal-title{min-width:140px;text-align:center}
.cal-segs{display:inline-flex;border:1px solid var(--cal-line);border-radius:6px;overflow:hidden}.cal-seg{background:transparent;border:0;color:var(--cal-sub);padding:5px 11px;cursor:pointer;font:inherit}.cal-seg.on{background:var(--cal-accent);color:#fff}
.cal-btn{background:var(--cal-bg2);border:1px solid var(--cal-line);color:var(--cal-ink);padding:5px 11px;border-radius:6px;cursor:pointer;font:inherit}.cal-btn.pri{background:var(--cal-accent);border-color:var(--cal-accent);color:#fff}.cal-btn.ok{color:#86efac;border-color:#2f5d43}.cal-btn.danger{color:#fca5a5;border-color:#7f1d1d}
.cal-select,.cal-input{background:var(--cal-bg);border:1px solid var(--cal-line);color:var(--cal-ink);border-radius:6px;padding:5px 8px;font:inherit}
.cal-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:var(--cal-sub);margin-bottom:8px}.cal-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}.cal-legend i.hol{background:var(--cal-hol)}.cal-legend i.mine{background:var(--cal-mine);border:1px solid var(--cal-accent)}.cal-warn{color:#fbbf24}
.cal-month{border:1px solid var(--cal-line);border-radius:8px;overflow:hidden;background:var(--cal-bg)}
.cal-dow{display:grid;grid-template-columns:repeat(7,1fr);background:var(--cal-bg2);color:var(--cal-sub);font-size:11px}.cal-dow div{padding:5px 8px;border-right:1px solid var(--cal-line)}.cal-dow div:last-child{border-right:0}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr)}
.cal-cell{min-height:96px;border-top:1px solid var(--cal-line);border-right:1px solid var(--cal-line);padding:4px 5px;cursor:pointer;min-width:0}.cal-cell:nth-child(7n){border-right:0}.cal-cell.pad{opacity:.45}.cal-cell.hol{background:var(--cal-hol)}.cal-cell.today{outline:2px solid var(--cal-accent);outline-offset:-2px}
.cal-d{font-size:12px;color:var(--cal-sub);display:flex;justify-content:space-between;gap:4px}.cal-d small{color:var(--cal-dim);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cal-cell.today .cal-d span{color:var(--cal-accent);font-weight:700}
.cal-ev{display:block;font-size:11px;line-height:1.3;padding:2px 5px;border-radius:4px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:3px solid;background:var(--cal-bg2);cursor:pointer}.cal-ev.mine{background:var(--cal-mine)}.cal-ev.done{opacity:.6;text-decoration:line-through}.cal-ev.cancelled{opacity:.4;text-decoration:line-through}.cal-ev.plan{border-left-style:dashed}.cal-ev i{color:var(--cal-sub);font-style:normal}
.cal-more{font-size:10px;color:var(--cal-sub);margin-top:2px}
.cal-week{display:grid;grid-template-columns:repeat(7,1fr);border:1px solid var(--cal-line);border-radius:8px;overflow:hidden;background:var(--cal-bg)}.cal-week.people{grid-template-columns:120px repeat(7,1fr)}
.cal-week>div{border-right:1px solid var(--cal-line);border-bottom:1px solid var(--cal-line);padding:5px;min-width:0}.cal-wh{background:var(--cal-bg2);color:var(--cal-sub);font-size:11px;cursor:pointer}.cal-wh.today{color:var(--cal-accent);font-weight:700}
.cal-wc{min-height:56px}.cal-wc.tall{min-height:220px}.cal-wc.today{background:rgba(59,130,246,.06)}
.cal-who{color:var(--cal-ink);font-weight:500}.cal-who small{display:block;color:var(--cal-dim);font-weight:400}.cal-load{display:inline-block;font-size:10px;color:var(--cal-sub)}.cal-load.hot{color:#f87171}
.cal-day{display:grid;grid-template-columns:1fr;gap:12px}.cal-card{background:var(--cal-bg);border:1px solid var(--cal-line);border-radius:8px;padding:12px}.cal-card h3{margin:0 0 8px;font-size:13px}.cal-hol{color:#fca5a5;font-weight:400}
.cal-item{display:grid;grid-template-columns:70px 1fr auto;gap:10px;padding:10px 0;border-top:1px solid var(--cal-line);align-items:start}.cal-item:first-of-type{border-top:0}.cal-item.done b{text-decoration:line-through;opacity:.7}
.cal-t{color:var(--cal-sub);font-variant-numeric:tabular-nums}.cal-meta{color:var(--cal-sub);font-size:12px}.cal-meta small{font-size:10px;border:1px solid var(--cal-line);border-radius:3px;padding:0 3px;margin-left:2px}
.cal-tag{display:inline-block;font-size:10px;border:1px solid;border-radius:3px;padding:0 5px;margin-right:5px}.cal-ok{color:#86efac}.cal-dim{color:var(--cal-sub);font-size:12px}
.cal-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.cal-empty{color:var(--cal-dim);padding:6px 0}
.cal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:1200;padding:16px}
.cal-modal{background:var(--cal-bg,#1f2124);color:var(--cal-ink,#e8e8ea);border:1px solid var(--cal-line,#34373c);border-radius:10px;width:min(620px,100%);max-height:92vh;overflow:auto;padding:16px;font-size:13px}.cal-modal h3{margin:0 0 10px;font-size:14px}
.cal-form{display:grid;grid-template-columns:1fr 1fr;gap:10px}.cal-form label{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--cal-sub)}.cal-form .full{grid-column:span 2}.cal-form .cal-input,.cal-form .cal-select{font-size:13px;color:var(--cal-ink);width:100%;box-sizing:border-box}.cal-lbl{display:block;font-size:11px;color:var(--cal-sub);margin-bottom:4px}
@media(max-width:640px){.cal-form{grid-template-columns:1fr}.cal-form .full{grid-column:span 1}.cal-week{min-width:640px}.cal-week-scroll{overflow-x:auto}}
.cal-chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}.cal-chip{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--cal-line);border-radius:99px;padding:2px 6px 2px 10px}.cal-chip.on{border-color:var(--cal-accent);background:var(--cal-mine)}.cal-chip small{font-size:10px;color:var(--cal-sub)}.cal-role{width:64px;background:transparent;border:0;border-bottom:1px dashed var(--cal-line);color:var(--cal-ink);font:inherit;font-size:11px}.cal-x{background:none;border:0;color:var(--cal-sub);cursor:pointer;font-size:14px}
.cal-pick{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.cal-pick .cal-input{flex:1;min-width:120px}
.cal-conflict{background:#3b1d1f;border:1px solid #7f2d2d;color:#fecaca;border-radius:6px;padding:8px 10px;font-size:12px}
.cal-colors{display:flex;flex-direction:column;gap:8px}.cal-crow{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.cal-crow .cal-lbl{width:56px;margin:0}.cal-sw{width:22px;height:22px;border-radius:50%;border:2px solid transparent;cursor:pointer}.cal-sw.on{border-color:#fff;box-shadow:0 0 0 2px var(--cal-accent)}.cal-preview{border-left:3px solid;padding:2px 6px;font-size:11px;background:var(--cal-bg2);margin-left:6px}
.cal-plist{max-height:50vh;overflow:auto}.cal-pitem{padding:6px 8px;cursor:pointer;border-radius:4px}.cal-pitem:hover{background:var(--cal-bg2)}
`;

function ensureCss() {
    if (document.getElementById('cal-shared-css')) return;
    const st = document.createElement('style');
    st.id = 'cal-shared-css';
    st.textContent = CSS;
    document.head.appendChild(st);
}

export async function render() {
    const host = z.host;
    if (!host) return;
    host.innerHTML = toolbarHtml() + `<div class="cal-body">${z.s.view === 'month' ? monthHtml() : z.s.view === 'week' ? `<div class="cal-week-scroll">${weekHtml()}</div>` : dayHtml()}</div>`;
}

export async function refresh(anchor) {
    if (anchor) z.s.anchor = anchor;
    try { await loadEvents(); } catch (e) { z.host.innerHTML = `<div class="cal-empty">行事曆載入失敗：${z.esc(e.message || e)}</div>`; return; }
    render();
}

async function onAction(btn) {
    const act = btn.dataset.cal, s = z.s;
    if (act === 'view') { s.view = btn.dataset.view; return refresh(); }
    if (act === 'weekmode') { s.weekMode = btn.dataset.mode; return render(); }
    if (act === 'nav') { const n = +btn.dataset.n; if (s.view === 'month') { const d = new Date(s.anchor.slice(0, 7) + '-01T00:00:00'); d.setMonth(d.getMonth() + n); s.anchor = iso(d); /* 不用 toISOString：UTC+8 會退成前一天 */ } else s.anchor = addDays(s.anchor, n * (s.view === 'week' ? 7 : 1)); return refresh(); }
    if (act === 'today') { s.anchor = today(); return refresh(); }
    if (act === 'day') { s.anchor = btn.dataset.day; s.view = 'day'; return refresh(); }
    if (act === 'add') return openForm(null, { date: s.view === 'day' ? s.anchor : (s.anchor >= today() ? s.anchor : today()) });
    if (act === 'edit' || act === 'open') {
        const ev = s.events.find(e => e.id === btn.dataset.id && (btn.dataset.kind ? e.kind === btn.dataset.kind : e.kind === 'schedule'));
        if (!ev) return;
        if (ev.kind === 'schedule' && (s.me.can_assign || ev.mine || act === 'edit')) return openForm(ev);
        if (ev.kind === 'shoot') return openShoot(ev);
        if (ev.kind === 'milestone' && ev.project_id) return z.hooks.openProject?.(ev.project_id, ev.project_name);
        s.anchor = ev.date; s.view = 'day'; return refresh();
    }
    if (act === 'done') {
        const ev = s.events.find(e => e.id === btn.dataset.id && e.kind === 'schedule'); if (!ev) return;
        const dflt = ev.start_time && ev.end_time ? '' : '8';
        const h = prompt(`「${ev.title}」做了幾小時？（空＝${ev.start_time && ev.end_time ? '照起訖算' : '整天 8 小時'}）`, dflt);
        if (h === null) return;
        btn.disabled = true;
        try { const r = await api(`/schedule/${encodeURIComponent(ev.id)}/done`, { method: 'POST', body: { hours: h.trim() ? +h : null } }); z.hooks.toast?.(r.created ? '已寫進當天的專案紀錄（到工作台可改時數）' : '這件已經記過工時了'); return refresh(); }
        catch (e) { btn.disabled = false; z.hooks.toast?.(e.message, 'err'); }
        return;
    }
    if (act === 'resync') { try { await api(`/schedule/${encodeURIComponent(btn.dataset.id)}/resync`, { method: 'POST', body: {} }); z.hooks.toast?.('已重新同步'); return refresh(); } catch (e) { z.hooks.toast?.(e.message, 'err'); } return; }
    if (act === 'shoot') return openShoot(s.events.find(e => e.id === btn.dataset.id && e.kind === 'shoot') || null);
    if (act === 'project') return z.hooks.openProject?.(btn.dataset.pid, btn.dataset.name);
    if (act === 'colors') return openColors();
}

function openShoot(ev) {
    if (z.hooks.openShoot) return z.hooks.openShoot(ev ? { id: ev.id, project_id: ev.project_id } : { project_id: z.s.lockedScope.replace('project:', '') });
    // 桌機沒有拍攝表單：開手機版行事曆分頁（同一個後端、同一份場次）
    window.open('/m/crm.html#calendar', '_blank');
}

/** 掛載。opts：host、fetch(path, {method, body})→json、esc、scope／lockedScope、light（白底宿主）、hooks。 */
export async function mountCalendar(opts) {
    ensureCss();
    z.host = opts.host;
    z.fetch = opts.fetch;
    if (opts.esc) z.esc = opts.esc;
    z.hooks = { ...(opts.hooks || {}) };
    z.hooks.reload = (day) => refresh(day && z.s.view === 'day' ? day : undefined);
    z.hooks.setScope = (scope, name) => { z.s.scope = scope; z.s.scopeName = name || ''; refresh(); };
    z.s.lockedScope = opts.lockedScope || '';
    z.s.scope = opts.scope || 'all';
    z.s.scopeName = opts.scopeName || '';
    z.s.view = opts.view || 'month';
    z.s.anchor = opts.anchor || today();
    z.s.options = null;
    z.host.classList.add('cal-root');
    z.host.classList.toggle('light', !!opts.light);
    if (!z.host._calWired) {
        z.host._calWired = true;
        z.host.addEventListener('click', (e) => { const b = e.target.closest('[data-cal]'); if (b && b.dataset.cal !== 'scope') { e.stopPropagation(); onAction(b); } });
        z.host.addEventListener('change', (e) => { const sel = e.target.closest('[data-cal="scope"]'); if (!sel) return; if (sel.value === '__pick') { sel.value = z.s.scope; pickProject(); return; } z.s.scope = sel.value; refresh(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
    }
    z.host.innerHTML = '<div class="cal-empty">載入中…</div>';
    try { await loadOptions(true); } catch (_) { /* 字彙抓不到：表單開的時候再試 */ }
    await refresh();
    return z;
}

export { openForm, openColors, mondayOf };
