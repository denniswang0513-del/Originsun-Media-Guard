// ────────────────────────────────────────────────────────────────────────────
// calendar/form.js — 「登記工作」彈窗（新增／修改共用）、「顏色設定」彈窗、「案子…」挑選。
// 存之前打 /conflicts：有衝突在表單裡列出來，再按一次「照排」才送（只提醒不擋，owner）。
// ────────────────────────────────────────────────────────────────────────────
import { api, loadOptions, today, z } from './ctx.js';

const esc = (s) => z.esc(s);
let _modal = null;

function overlay(html) {
    close();
    _modal = document.createElement('div');
    _modal.className = 'cal-overlay';
    _modal.innerHTML = `<div class="cal-modal">${html}</div>`;
    _modal.addEventListener('click', (e) => { if (e.target === _modal) close(); });
    (z.hooks.modalRoot ? z.hooks.modalRoot() : document.body).appendChild(_modal);
    return _modal;
}
export function close() { if (_modal) { _modal.remove(); _modal = null; } }
const $ = (id) => _modal && _modal.querySelector('#' + id);

// ── 登記工作 ──
let _att = [];        // [{staff_id, name, role, external, contact}]

function attChips(opts) {
    const me = z.s.me || {};
    const canAssign = me.can_assign;
    return _att.map((a, i) => `<span class="cal-chip on" data-i="${i}">${esc(a.name)}${a.external ? '<small>外部</small>' : ''}
        <input class="cal-role" list="cal-roles" value="${esc(a.role || '')}" placeholder="職務" data-i="${i}" title="職務（攝影／燈光／剪接…）">
        ${(canAssign || a.staff_id === me.staff_id) ? `<button type="button" class="cal-x" data-rm="${i}" aria-label="移除">×</button>` : ''}</span>`).join('')
        + `<datalist id="cal-roles">${(opts.roles || []).map(r => `<option value="${esc(r)}">`).join('')}</datalist>`;
}

function pickerHtml(opts) {
    const me = z.s.me || {};
    if (!me.can_assign) return `<div class="cal-dim">登記自己（要排別人或外部人員需要「專案管理」權限）</div>`;
    const staff = (opts.staff || []).filter(s => !_att.some(a => a.staff_id === s.id));
    return `<div class="cal-pick">
        <select id="cf-staff" class="cal-select"><option value="">＋ 內部人員…</option>${staff.map(s => `<option value="${esc(s.id)}">${esc(s.name)}${s.role ? '｜' + esc(s.role) : ''}${s.status === '兼職' ? '（兼職）' : ''}</option>`).join('')}</select>
        <input id="cf-ext" class="cal-input" list="cal-externals" placeholder="＋ 外部人員（名字）">
        <input id="cf-ext-contact" class="cal-input" placeholder="聯絡（選填）" style="max-width:130px">
        <button type="button" class="cal-btn" id="cf-ext-add">加入</button>
        <datalist id="cal-externals">${(opts.externals || []).map(x => `<option value="${esc(x.name)}">${esc(x.contact || '')}</option>`).join('')}</datalist></div>`;
}

export async function openForm(ev = null, preset = {}) {
    const opts = await loadOptions();
    const me = z.s.me || {};
    const editing = !!ev;
    _att = editing ? (ev.people || []).map(p => ({ ...p })) : (preset.attendees || [{ staff_id: me.staff_id || '', name: me.name || '', role: '', external: false, contact: '' }]).map(p => ({ ...p }));
    const slot = editing ? slotOf(ev.start_time, ev.end_time) : (preset.slot || 'all');
    const projects = opts.projects || [];
    const pid = editing ? (ev.project_id || '') : (preset.project_id || z.s.lockedScope.replace('project:', '') || '');
    const kinds = opts.kinds || [];
    overlay(`<h3>${editing ? '修改工作' : '登記工作'}</h3>
      <div class="cal-form">
        <label class="full">做什麼 <input id="cf-title" class="cal-input" value="${esc(editing ? ev.title : (preset.title || ''))}" placeholder="例：iWIN 年會 A-copy 粗剪" autocomplete="off"></label>
        <label>種類 <select id="cf-kind" class="cal-select">${kinds.map(k => `<option value="${k.id}"${(editing ? ev.sub_kind : 'work') === k.id ? ' selected' : ''}>${esc(k.label)}</option>`).join('')}</select></label>
        <label>案子（可空＝行政庶務） <select id="cf-proj" class="cal-select" ${z.s.lockedScope ? 'disabled' : ''}><option value="">— 不掛案子 —</option>${projects.map(p => `<option value="${esc(p.id)}"${p.id === pid ? ' selected' : ''}>${esc(p.client ? p.client + '｜' : '')}${esc(p.name)}</option>`).join('')}</select></label>
        <label>日期 <input id="cf-date" class="cal-input" type="date" value="${editing ? ev.date : (preset.date || z.s.anchor || today())}"></label>
        <label>結束日（多日才填） <input id="cf-end" class="cal-input" type="date" value="${editing && ev.end_date ? ev.end_date : ''}"></label>
        <div class="full"><span class="cal-lbl">時段</span><span class="cal-segs" id="cf-slots">${(opts.slots || []).map(s => `<button type="button" class="cal-seg${slot === s.id ? ' on' : ''}" data-slot="${s.id}">${esc(s.label)}${s.times && s.times[0] ? ` ${s.times[0]}–${s.times[1]}` : ''}</button>`).join('')}</span>
          <span id="cf-custom" ${slot === 'custom' ? '' : 'hidden'}><input id="cf-st" type="time" class="cal-input" value="${editing ? ev.start_time : ''}"> – <input id="cf-et" type="time" class="cal-input" value="${editing ? ev.end_time : ''}"></span></div>
        <div class="full"><span class="cal-lbl">人員</span><div id="cf-att" class="cal-chips">${attChips(opts)}</div>${pickerHtml(opts)}</div>
        <label class="full">地點（選填） <input id="cf-loc" class="cal-input" value="${esc(editing ? ev.location : '')}" placeholder="公司剪接室 / 客戶端 / 兩廳院"></label>
        <label class="full">備註 <textarea id="cf-notes" class="cal-input" rows="2">${esc(editing ? ev.notes : '')}</textarea></label>
        <div class="full cal-conflict" id="cf-conflict" hidden></div>
      </div>
      <div class="cal-bar" style="margin-top:12px">${editing ? `<button type="button" class="cal-btn danger" data-cal-form="delete">刪除</button>${ev.status !== 'cancelled' ? '<button type="button" class="cal-btn" data-cal-form="cancel">取消這件</button>' : '<button type="button" class="cal-btn" data-cal-form="reopen">改回排定</button>'}` : ''}<span class="cal-spacer"></span>
        <button type="button" class="cal-btn" data-cal-form="close">關閉</button><button type="button" class="cal-btn pri" id="cf-save" data-cal-form="save">${editing ? '儲存修改' : '登記'}</button></div>`);
    let confirmed = false;
    const redrawAtt = () => { $('cf-att').innerHTML = attChips(opts); const p = _modal.querySelector('.cal-pick'); if (p) p.outerHTML = pickerHtml(opts); wirePick(); checkConflict(); };
    const wirePick = () => {
        const sel = $('cf-staff');
        if (sel) sel.onchange = () => { const s = (opts.staff || []).find(x => x.id === sel.value); if (s) { _att.push({ staff_id: s.id, name: s.name, role: '', external: false, contact: '' }); redrawAtt(); } };
        const add = $('cf-ext-add');
        if (add) add.onclick = () => { const n = $('cf-ext').value.trim(); if (!n) return; if (!_att.some(a => a.name === n)) _att.push({ staff_id: '', name: n, role: '', external: true, contact: $('cf-ext-contact').value.trim() }); redrawAtt(); };
    };
    wirePick();
    $('cf-att').addEventListener('click', (e) => { const b = e.target.closest('[data-rm]'); if (b) { _att.splice(+b.dataset.rm, 1); redrawAtt(); } });
    $('cf-att').addEventListener('input', (e) => { const r = e.target.closest('.cal-role'); if (r) _att[+r.dataset.i].role = r.value; });
    $('cf-slots').addEventListener('click', (e) => { const b = e.target.closest('[data-slot]'); if (!b) return; $('cf-slots').querySelectorAll('.cal-seg').forEach(x => x.classList.toggle('on', x === b)); $('cf-custom').hidden = b.dataset.slot !== 'custom'; });
    $('cf-date').addEventListener('change', () => { confirmed = false; checkConflict(); });
    $('cf-end').addEventListener('change', () => { confirmed = false; checkConflict(); });
    async function checkConflict() {
        const box = $('cf-conflict'); if (!box) return;
        try {
            const qs = new URLSearchParams({ date: $('cf-date').value, end_date: $('cf-end').value || '', attendees: JSON.stringify(_att), exclude: editing ? ev.id : '' });
            const r = await api('/conflicts?' + qs);
            const hits = r.conflicts || [];
            box.hidden = !hits.length;
            box.innerHTML = hits.length ? '⚠ 衝突提醒（不擋，可以硬排）：<br>' + hits.map(h => '・' + esc(h.what)).join('<br>') : '';
            $('cf-save').textContent = hits.length && !confirmed ? '照排' : (editing ? '儲存修改' : '登記');
            box.dataset.n = hits.length;
        } catch (_) { box.hidden = true; }
    }
    checkConflict();
    _modal.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-cal-form]'); if (!b) return;
        const act = b.dataset.calForm;
        if (act === 'close') return close();
        if (act === 'save') {
            const title = $('cf-title').value.trim(); if (!title) { $('cf-title').focus(); return; }
            const n = +($('cf-conflict').dataset.n || 0);
            if (n && !confirmed) { confirmed = true; $('cf-save').textContent = editing ? '確定儲存' : '確定登記'; return; }
            const slotBtn = $('cf-slots').querySelector('.cal-seg.on');
            const body = { kind: $('cf-kind').value, title, project_id: $('cf-proj').value || null, date: $('cf-date').value, end_date: $('cf-end').value || null,
                slot: slotBtn ? slotBtn.dataset.slot : 'all', start_time: $('cf-st').value || null, end_time: $('cf-et').value || null,
                attendees: _att, location_text: $('cf-loc').value.trim(), notes: $('cf-notes').value.trim() };
            b.disabled = true;
            try { await (editing ? api(`/schedule/${encodeURIComponent(ev.id)}`, { method: 'PUT', body }) : api('/schedule', { method: 'POST', body })); close(); z.hooks.toast?.(editing ? '已修改' : '已登記'); z.hooks.reload?.(body.date); }
            catch (err) { b.disabled = false; z.hooks.toast?.(err.message || '儲存失敗', 'err'); }
            return;
        }
        if (act === 'delete') { if (!confirm('刪掉這件工作？Google 日曆上的事件會一起刪。')) return; try { await api(`/schedule/${encodeURIComponent(ev.id)}`, { method: 'DELETE' }); close(); z.hooks.reload?.(); } catch (err) { z.hooks.toast?.(err.message, 'err'); } return; }
        if (act === 'cancel' || act === 'reopen') { try { await api(`/schedule/${encodeURIComponent(ev.id)}/status`, { method: 'POST', body: { status: act === 'cancel' ? 'cancelled' : 'planned' } }); close(); z.hooks.reload?.(); } catch (err) { z.hooks.toast?.(err.message, 'err'); } }
    });
    setTimeout(() => $('cf-title')?.focus(), 30);
}

function slotOf(st, et) {
    if (!st && !et) return 'all';
    const slots = (z.s.options && z.s.options.slots) || [];
    const hit = slots.find(s => s.times && s.times[0] === st && s.times[1] === et);
    return hit ? hit.id : 'custom';
}

// ── 顏色設定（admin）──
export async function openColors() {
    const c = await api('/colors');
    const kinds = Object.keys(c.labels || {});
    overlay(`<h3>顏色設定</h3><div class="cal-dim" style="margin-bottom:10px">六種事件各一個顏色；系統畫面和 Google 日曆用同一張表（Google 只有這 11 色）。</div>
      <div class="cal-colors">${kinds.map(k => `<div class="cal-crow" data-kind="${k}"><span class="cal-lbl">${esc(c.labels[k])}</span>
        ${(c.palette || []).map(p => `<button type="button" class="cal-sw${c.map[k] === p.id ? ' on' : ''}" data-color="${p.id}" style="background:${p.hex}" title="${esc(p.name)}"></button>`).join('')}
        <span class="cal-preview" style="border-left-color:${esc(c.hex[k])}">${esc(c.labels[k])}｜範例</span></div>`).join('')}</div>
      <div class="cal-bar" style="margin-top:12px"><span class="cal-spacer"></span><button type="button" class="cal-btn" data-c="close">關閉</button>
        <button type="button" class="cal-btn" data-c="save">儲存</button><button type="button" class="cal-btn pri" data-c="apply" title="未來 180 天已經在日曆上的事件也一起換色">儲存並套用到既有事件</button></div>`);
    const chosen = { ...c.map };
    _modal.addEventListener('click', async (e) => {
        const sw = e.target.closest('.cal-sw');
        if (sw) { const row = sw.closest('.cal-crow'); chosen[row.dataset.kind] = sw.dataset.color; row.querySelectorAll('.cal-sw').forEach(x => x.classList.toggle('on', x === sw)); row.querySelector('.cal-preview').style.borderLeftColor = sw.style.background; return; }
        const b = e.target.closest('[data-c]'); if (!b) return;
        if (b.dataset.c === 'close') return close();
        b.disabled = true;
        try {
            const r = await api('/colors' + (b.dataset.c === 'apply' ? '?apply=1' : ''), { method: 'PUT', body: { colors: chosen } });
            z.s.colors = r.colors; if (z.s.options) z.s.options.colors = r.colors;
            close();
            const ap = r.applied; z.hooks.toast?.(ap ? `已儲存並重新同步：工作 ${ap.schedule.total}、拍攝 ${ap.shoot.total}、里程碑 ${ap.milestone.total}、休假 ${ap.leave.total}` : '已儲存顏色');
            z.hooks.reload?.();
        } catch (err) { b.disabled = false; z.hooks.toast?.(err.message, 'err'); }
    });
}

// ── 案子… ──
export async function pickProject() {
    const opts = await loadOptions();
    overlay(`<h3>看哪一案</h3><input id="cp-q" class="cal-input" placeholder="打字找案名或客戶" autocomplete="off" style="width:100%;margin-bottom:8px">
      <div id="cp-list" class="cal-plist">${(opts.projects || []).map(p => `<div class="cal-pitem" data-pid="${esc(p.id)}" data-name="${esc(p.name)}">${esc(p.client ? p.client + '｜' : '')}${esc(p.name)}</div>`).join('')}</div>`);
    $('cp-q').addEventListener('input', () => { const q = $('cp-q').value.trim().toLowerCase(); _modal.querySelectorAll('.cal-pitem').forEach(el => { el.hidden = q && !el.textContent.toLowerCase().includes(q); }); });
    _modal.addEventListener('click', (e) => { const it = e.target.closest('.cal-pitem'); if (!it) return; close(); z.hooks.setScope?.('project:' + it.dataset.pid, it.dataset.name); });
    setTimeout(() => $('cp-q')?.focus(), 30);
}
