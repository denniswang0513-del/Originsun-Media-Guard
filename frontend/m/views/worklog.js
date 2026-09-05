/**
 * 每日工作紀錄（分頁 #worklog；owner 2026-09-06「手機版 CRM 把每日工作紀錄的填表放進去」）。
 *
 * 跟 /my.html「今天的專案紀錄」同一套資料與端點，只是手機上一筆一張卡、點卡片在底部抽屜改：
 *   GET  /api/v1/timesheets/mine?date=YYYY-MM-DD   當天的列（本人；沒綁人員檔案 → 409，畫提示）
 *   POST /api/v1/timesheets/mine/rows {rows:[body]} 新增；PUT /mine/{id} 改；DELETE /mine/{id} 刪
 *   GET  /api/v1/timesheets/options                 分類＋各分類的工作階段（字彙不寫死）
 *   GET  /api/v1/timesheets/project_options（沒權限退 /api/v1/me/timesheet_options）專案清單
 * body 形狀＝js/shared/ts-sheet.js rowBody：work_date、project_id／project_name、work_type、stage_id、
 * task_note、remark、start_time／end_time、hours。起訖都填了就自動算時數（同格子 applyTimeRange 的算法）。
 * 寫入守衛是「本人＋綁定人員檔案」，不看 CRM 的 can_write，所以這頁的按鈕不掛 .w。
 * 鐵則：不畫任何個人工時合計（/mine 回的 planned_total／actual_total 這裡不用）。
 */
import { mfetch, toast, esc, todayLocal } from '../shell.js';
import { skeleton, errBox, pill, withBusy, pickerHtml, mountPicker, segHtml, mountSeg, selectOpts, openSheet, closeSheet } from '../ui.js';

const F = (id) => document.getElementById('wl-' + id);
let _day = todayLocal();
let _vocab = null;        // {work_types:[], stages:{分類:[{id,name}]}}
let _projects = null;     // [{id,name,label,client,year,closed}]
let _items = [];          // 當天的列（改列時從這裡拿）

const dayLabel = (iso) => {
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d) ? iso : `${d.getMonth() + 1}/${d.getDate()}（${'日一二三四五六'[d.getDay()]}）`;
};
const shiftDay = (iso, n) => { const d = new Date(iso + 'T00:00:00'); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const stagesOf = (type) => ((_vocab && _vocab.stages) || {})[type] || [];

async function loadVocab() {
    if (_vocab) return _vocab;
    try { _vocab = await mfetch('/api/v1/timesheets/options'); } catch (_) { _vocab = { work_types: [], stages: {} }; }
    return _vocab;
}
async function loadProjects() {
    if (_projects) return _projects;
    try { _projects = (await mfetch('/api/v1/timesheets/project_options')).projects || []; }
    catch (_) {
        try { _projects = (await mfetch('/api/v1/me/timesheet_options')).projects || []; } catch (__) { _projects = []; }
    }
    return _projects;
}

// ── 清單 ──
function cardHtml(i) {
    const meta = [i.work_type, i.stage_name].filter(Boolean).join(' · ');
    const span = i.start_time && i.end_time ? `${i.start_time}–${i.end_time}` : (i.start_time || '');
    return `
      <div class="m-card tap" data-id="${esc(i.id)}">
        <div class="t"><div class="name">${i.project_name ? esc(i.project_name) : '<span style="color:var(--sub);font-weight:500">未填專案</span>'}</div>${pill((i.hours || 0) + ' h', 'pri')}</div>
        ${i.task_note ? `<div class="sub" style="color:var(--ink)">${esc(i.task_note)}</div>` : ''}
        <div class="sub">${[meta, span, i.remark].filter(Boolean).map(esc).join(' · ')}</div>
      </div>`;
}

async function load(host) {
    const box = host.querySelector('#wl-list');
    host.querySelector('#wl-day').textContent = dayLabel(_day);
    host.querySelector('#wl-date').value = _day;
    box.innerHTML = skeleton(3);
    try {
        const d = await mfetch('/api/v1/timesheets/mine?date=' + encodeURIComponent(_day));
        _items = d.items || [];
        box.innerHTML = _items.length ? _items.map(cardHtml).join('') : '<div class="m-empty">這天還沒有紀錄，按「新增一筆」開始填。</div>';
    } catch (e) {
        _items = [];
        box.innerHTML = e.status === 409
            ? `<div class="m-notice"><pre>${esc(e.message)}</pre><div class="m-hint" style="margin:0">工作紀錄記在人員檔案底下，請管理員在「使用者管理」把這個帳號綁到人員。</div></div>`
            : errBox(e);
    }
}

// ── 表單（底部抽屜）──
function formHtml(row) {
    const types = (_vocab && _vocab.work_types) || [];
    const type = row.work_type || '';
    return `
      <div class="ttl">${row.id ? '修改紀錄' : '新增一筆'}<span style="color:var(--sub);font-size:13px;font-weight:400;margin-left:8px">${esc(dayLabel(_day))}</span></div>
      <form class="m-form" id="wl-form" autocomplete="off">
        <label class="req">專案</label>${pickerHtml('wl-project')}
        <label>分類</label>${types.length ? segHtml('wl-type', types, type, { blank: true }) : '<input id="wl-type" placeholder="分類">'}
        <label>工作階段</label><select id="wl-stage">${selectOpts(stagesOf(type).map(s => ({ value: s.id, label: s.name })), row.stage_id || '', '—')}</select>
        <label>做了什麼</label><input id="wl-note" value="${esc(row.task_note || '')}" placeholder="例：初版剪接、與導演對戲">
        <label>備註</label><input id="wl-remark" value="${esc(row.remark || '')}" placeholder="選填">
        <div class="row2">
          <div><label>起</label><input id="wl-t0" type="time" value="${esc(row.start_time || '')}"></div>
          <div><label>訖</label><input id="wl-t1" type="time" value="${esc(row.end_time || '')}"></div>
        </div>
        <label class="req">時數 h</label><input id="wl-hours" type="number" inputmode="decimal" min="0" step="any" value="${row.hours ? row.hours : ''}">
        <div class="m-hint">起訖都填了會自動算時數；也可以直接填時數</div>
        <button type="submit" class="m-btn pri wide" id="wl-submit">儲存</button>
        ${row.id ? '<button type="button" class="m-btn danger wide" id="wl-del">刪除這一筆</button>' : ''}
      </form>`;
}

function hoursFromRange() {
    const t0 = F('t0').value, t1 = F('t1').value;
    if (!t0 || !t1) return;
    const m = (s) => { const [h, mm] = s.split(':').map(Number); return h * 60 + mm; };
    let mins = m(t1) - m(t0);
    if (mins < 0) mins += 24 * 60;
    F('hours').value = Math.round(mins / 60 * 100) / 100;
}

function bodyFromForm() {
    const raw = (F('project').value || '').trim();
    const hit = (_projects || []).find(p => p.id && (p.id === raw || p.label === raw || p.name === raw));
    const body = {
        work_date: _day,
        project_id: hit ? hit.id : null, project_name: hit ? hit.name : raw,
        work_type: F('type').value || null,
        task_note: F('note').value.trim(), remark: F('remark').value.trim(),
        start_time: F('t0').value || '', end_time: F('t1').value || '',
        hours: F('hours').value ? parseFloat(F('hours').value) : null,
    };
    if (F('stage')) body.stage_id = F('stage').value || '';
    return body;
}

async function openForm(host, row = {}) {
    await Promise.all([loadVocab(), loadProjects()]);
    const body = openSheet(formHtml(row));
    const items = (_projects || []).filter(p => p.id).map(p => ({ value: p.id, label: p.label || p.name }));
    // 專案：打字找（free：清單沒有的案名也能照打，後端再對映）；改列時先帶原本的
    const cur = row.project_id && items.some(i => i.value === row.project_id) ? row.project_id : (row.project_name || '');
    mountPicker('wl-project', { items, placeholder: '打字找案名', value: cur, free: true });
    if (!items.some(i => i.value === cur) && row.project_name) { F('project').value = row.project_name; F('project-q').value = row.project_name; }
    mountSeg('wl-type', (type) => {
        const sel = F('stage'); if (!sel) return;
        sel.innerHTML = selectOpts(stagesOf(type).map(s => ({ value: s.id, label: s.name })), '', '—');
    });
    F('t0').addEventListener('change', hoursFromRange);
    F('t1').addEventListener('change', hoursFromRange);
    body.querySelector('#wl-form').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const b = bodyFromForm();
        if (!b.project_name) { toast('請選或填專案', 'err'); return; }
        if (!(b.hours > 0)) { toast('請填時數', 'err'); return; }
        await withBusy(F('submit'), async () => {
            try {
                if (row.id) await mfetch('/api/v1/timesheets/mine/' + encodeURIComponent(row.id), { method: 'PUT', body: b });
                else {
                    const r = await mfetch('/api/v1/timesheets/mine/rows', { method: 'POST', body: { rows: [b] } });
                    if ((r.unmatched_projects || []).length) toast('已存，但「' + r.unmatched_projects.join('、') + '」對不到案，管理員會再指定');
                }
                toast(row.id ? '已更新' : '已新增');
                closeSheet();
                await load(host);
            } catch (e) { toast(e.message, 'err'); }
        });
    });
    F('del')?.addEventListener('click', async () => {
        if (!confirm('刪掉這一筆？')) return;
        await withBusy(F('del'), async () => {
            try { await mfetch('/api/v1/timesheets/mine/' + encodeURIComponent(row.id), { method: 'DELETE' }); toast('已刪除'); closeSheet(); await load(host); }
            catch (e) { toast(e.message, 'err'); }
        });
    });
}

export async function render(host, { first }) {
    if (first) {
        host.innerHTML = `
          <div class="m-h">每日工作紀錄</div>
          <div class="m-card" style="display:flex;align-items:center;gap:8px">
            <button type="button" class="m-btn sm" data-shift="-1">‹</button>
            <div style="flex:1;text-align:center"><div style="font-weight:600;color:#fff" id="wl-day"></div>
              <input type="date" id="wl-date" style="margin:6px 0 0;min-height:36px;height:36px;font-size:14px;background:var(--field);border:1px solid var(--line);border-radius:8px;color:var(--ink);padding:0 8px"></div>
            <button type="button" class="m-btn sm" data-shift="1">›</button>
          </div>
          <div class="m-actions" style="margin:0 0 12px">
            <button type="button" class="m-btn pri" data-add>新增一筆</button>
            <button type="button" class="m-btn" data-today>今天</button>
          </div>
          <div id="wl-list"></div>`;
        host.addEventListener('click', (ev) => {
            const s = ev.target.closest('button[data-shift]');
            if (s) { _day = shiftDay(_day, Number(s.dataset.shift)); load(host); return; }
            if (ev.target.closest('button[data-today]')) { _day = todayLocal(); load(host); return; }
            if (ev.target.closest('button[data-add]')) { openForm(host, {}); return; }
            const card = ev.target.closest('.m-card[data-id]');
            if (card) { const row = _items.find(i => i.id === card.dataset.id); if (row) openForm(host, row); }
        });
        host.querySelector('#wl-date').addEventListener('change', (ev) => { if (ev.target.value) { _day = ev.target.value; load(host); } });
    }
    await load(host);
}
