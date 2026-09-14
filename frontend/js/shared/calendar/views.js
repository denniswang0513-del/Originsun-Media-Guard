// ────────────────────────────────────────────────────────────────────────────
// calendar/views.js — 月／週（時間軸、人員列）／日 三種畫法。只畫，不抓資料；按鈕全部 data-cal="…" 交給 index.js 分派。
// ────────────────────────────────────────────────────────────────────────────
import { z, DOW, addDays, colorOf, daysOf, kindLabel, md, mdw, mondayOf, monthEnd, monthStart, parse, range, timeLabel, today } from './ctx.js';

const esc = (s) => z.esc(s);

function chip(ev, full = false) {
    const c = colorOf(ev);
    const t = timeLabel(ev);
    const title = `${kindLabel(ev)}｜${ev.title}${ev.project_name && ev.kind !== 'shoot' ? '（' + ev.project_name + '）' : ''}${ev.people && ev.people.length ? '｜' + ev.people.map(p => p.name).join('、') : ''}`;
    const cls = `cal-ev${ev.mine ? ' mine' : ''}${ev.status === 'done' || ev.status === '完成' ? ' done' : ''}${ev.status === 'cancelled' || ev.status === '取消' ? ' cancelled' : ''}${ev.kind === 'plan' ? ' plan' : ''}`;
    return `<span class="${cls}" style="border-left-color:${c};" data-cal="open" data-kind="${ev.kind}" data-id="${esc(ev.id)}" title="${esc(title)}">${t ? `<b>${esc(t)}</b> ` : ''}${esc(ev.title)}${full && ev.project_name ? `<i> · ${esc(ev.project_name)}</i>` : ''}</span>`;
}

function byDay(events) {
    const m = new Map();
    for (const ev of events) for (const d of daysOf(ev)) { if (!m.has(d)) m.set(d, []); m.get(d).push(ev); }
    for (const list of m.values()) list.sort((a, b) => (a.kind === 'holiday' ? -1 : 0) - (b.kind === 'holiday' ? -1 : 0) || (a.start_time || '').localeCompare(b.start_time || ''));
    return m;
}

export function monthHtml() {
    const a = z.s.anchor, [from, to] = range();
    const m0 = monthStart(a), m1 = monthEnd(a);
    const per = byDay(z.s.events);
    const holidays = new Set(z.s.events.filter(e => e.kind === 'holiday').map(e => e.date));
    let cells = '';
    for (let d = from; d <= to; d = addDays(d, 1)) {
        const evs = (per.get(d) || []).filter(e => e.kind !== 'holiday');
        const hol = z.s.events.find(e => e.kind === 'holiday' && e.date === d);
        const pad = d < m0 || d > m1;
        const more = evs.length > 4 ? `<div class="cal-more" data-cal="day" data-day="${d}">+${evs.length - 4} 件</div>` : '';
        cells += `<div class="cal-cell${pad ? ' pad' : ''}${holidays.has(d) ? ' hol' : ''}${d === today() ? ' today' : ''}" data-cal="day" data-day="${d}">
            <div class="cal-d"><span>${+d.slice(8, 10)}</span>${hol ? `<small>${esc(hol.title)}</small>` : ''}</div>
            ${evs.slice(0, 4).map(e => chip(e)).join('')}${more}</div>`;
    }
    return `<div class="cal-month"><div class="cal-dow">${['一', '二', '三', '四', '五', '六', '日'].map(x => `<div>${x}</div>`).join('')}</div><div class="cal-grid">${cells}</div></div>`;
}

export function weekHtml() {
    const [from] = range();
    const days = Array.from({ length: 7 }, (_, i) => addDays(from, i));
    const per = byDay(z.s.events);
    const head = days.map(d => `<div class="cal-wh${d === today() ? ' today' : ''}" data-cal="day" data-day="${d}">${md(d)}（${DOW[parse(d).getDay()]}）${per.get(d)?.some(e => e.kind === 'holiday') ? ' <small>假日</small>' : ''}</div>`).join('');
    if (z.s.weekMode === 'people') {
        // 人員列：每個人（內部；外部人員不進來）× 日；連續 4 天以上有排標「負載高」
        const people = new Map();
        for (const ev of z.s.events) for (const p of (ev.people || [])) {
            if (p.external || ev.kind === 'holiday') continue;
            const key = p.staff_id || p.name;
            if (!people.has(key)) people.set(key, { name: p.name, days: new Map() });
            for (const d of daysOf(ev)) if (days.includes(d)) { const dm = people.get(key).days; if (!dm.has(d)) dm.set(d, []); dm.get(d).push(ev); }
        }
        const rows = [...people.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh-Hant')).map(p => {
            const busy = days.filter(d => (p.days.get(d) || []).some(e => e.kind !== 'leave')).length;
            const off = days.filter(d => (p.days.get(d) || []).some(e => e.kind === 'leave')).length;
            const load = busy >= 4 ? '<span class="cal-load hot">負載高</span>' : busy === 0 ? '<span class="cal-load">閒置</span>' : '';
            return `<div class="cal-who">${esc(p.name)}<small>${busy} 天有排${off ? '・請假 ' + off + ' 天' : ''}</small>${load}</div>`
                + days.map(d => `<div class="cal-wc">${(p.days.get(d) || []).map(e => chip(e)).join('')}</div>`).join('');
        }).join('');
        return `<div class="cal-week people"><div class="cal-wh"></div>${head}${rows || '<div class="cal-empty" style="grid-column:1/-1">這週沒有排任何人</div>'}</div>`;
    }
    const cols = days.map(d => {
        const evs = (per.get(d) || []).filter(e => e.kind !== 'holiday');
        return `<div class="cal-wc tall${d === today() ? ' today' : ''}">${evs.map(e => chip(e, true)).join('') || '<span class="cal-empty">—</span>'}</div>`;
    }).join('');
    return `<div class="cal-week time">${head}${cols}</div>`;
}

export function dayHtml() {
    const d = z.s.anchor;
    const evs = z.s.events.filter(e => daysOf(e).includes(d) && e.kind !== 'holiday').sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
    const hol = z.s.events.find(e => e.kind === 'holiday' && e.date === d);
    const me = z.s.me || {};
    const item = (e) => {
        const t = timeLabel(e) || (e.kind === 'milestone' ? '到期' : '全天');
        const people = (e.people || []).map(p => esc(p.name) + (p.role ? `（${esc(p.role)}）` : '') + (p.external ? '<small>外部</small>' : '')).join('、');
        const meta = [e.project_name ? esc(e.project_name) : '', e.location ? esc(e.location) : '', people].filter(Boolean).join('｜');
        const gear = e.equipment && e.equipment.length ? `<div class="cal-meta">器材：${e.equipment.map(esc).join('、')}</div>` : '';
        const done = e.status === 'done' || e.status === '完成';
        let actions = '';
        if (e.kind === 'schedule') {
            const canTouch = me.can_assign || e.mine;
            if (e.mine && me.bound && !done && e.status !== 'cancelled') actions += `<button class="cal-btn ok" data-cal="done" data-id="${esc(e.id)}">做了 ✓</button>`;
            if (canTouch) actions += `<button class="cal-btn" data-cal="edit" data-id="${esc(e.id)}">改</button>`;
            if (e.sync && e.sync.error) actions += `<button class="cal-btn" data-cal="resync" data-id="${esc(e.id)}" title="${esc(e.sync.error)}">同步失敗・重試</button>`;
        } else if (e.kind === 'shoot') {
            actions += `<button class="cal-btn" data-cal="shoot" data-id="${esc(e.id)}">在手機行事曆改</button>`;
        } else if (e.kind === 'milestone' && e.project_id) {
            actions += `<button class="cal-btn" data-cal="project" data-pid="${esc(e.project_id)}" data-name="${esc(e.project_name)}">開專案</button>`;
        }
        return `<div class="cal-item${e.mine ? ' mine' : ''}${done ? ' done' : ''}"><div class="cal-t">${esc(t)}</div>
            <div><span class="cal-tag" style="color:${colorOf(e)};border-color:${colorOf(e)};">${kindLabel(e)}</span><b>${esc(e.title)}</b>${done ? ' <span class="cal-ok">✓</span>' : ''}${e.status === 'cancelled' ? ' <span class="cal-dim">（已取消）</span>' : ''}
            ${meta ? `<div class="cal-meta">${meta}</div>` : ''}${gear}${e.notes ? `<div class="cal-meta">備註：${esc(e.notes)}</div>` : ''}</div>
            <div class="cal-actions">${actions}</div></div>`;
    };
    return `<div class="cal-day"><div class="cal-card"><h3>${mdw(d)}${d === today() ? '　今天' : ''}${hol ? `　<small class="cal-hol">${esc(hol.title)}</small>` : ''}</h3>
        ${evs.length ? evs.map(item).join('') : '<div class="cal-empty">這天沒有事。</div>'}</div></div>`;
}

export function toolbarHtml() {
    const s = z.s, a = s.anchor;
    const title = s.view === 'month' ? `${a.slice(0, 4)} 年 ${+a.slice(5, 7)} 月` : s.view === 'week' ? `${md(mondayOf(a))} – ${md(addDays(mondayOf(a), 6))}` : mdw(a);
    const seg = (k, l) => `<button type="button" class="cal-seg${s.view === k ? ' on' : ''}" data-cal="view" data-view="${k}">${l}</button>`;
    const scopeSel = s.lockedScope ? '' : `<select class="cal-select" data-cal="scope">
        <option value="all"${s.scope === 'all' ? ' selected' : ''}>全公司</option>
        <option value="me"${s.scope === 'me' ? ' selected' : ''}>我的</option>
        ${s.scope.startsWith('project:') ? `<option value="${esc(s.scope)}" selected>案子：${esc(s.scopeName || '')}</option>` : ''}
        <option value="__pick">案子…</option></select>`;
    const wk = s.view === 'week' ? `<span class="cal-segs"><button type="button" class="cal-seg${s.weekMode === 'time' ? ' on' : ''}" data-cal="weekmode" data-mode="time">時間軸</button><button type="button" class="cal-seg${s.weekMode === 'people' ? ' on' : ''}" data-cal="weekmode" data-mode="people">人員列</button></span>` : '';
    const admin = (z.s.me && z.s.me.is_admin) || (z.s.options && z.s.options.me && z.s.options.me.is_admin);
    return `<div class="cal-bar">
        <span class="cal-segs">${seg('month', '月')}${seg('week', '週')}${seg('day', '日')}</span>
        <button type="button" class="cal-btn" data-cal="nav" data-n="-1">‹</button><b class="cal-title">${title}</b><button type="button" class="cal-btn" data-cal="nav" data-n="1">›</button>
        <button type="button" class="cal-btn" data-cal="today">今天</button>
        ${wk}${scopeSel}<span class="cal-spacer"></span>
        ${z.hooks.openShoot ? '<button type="button" class="cal-btn" data-cal="shoot">登記拍攝</button>' : ''}
        <button type="button" class="cal-btn pri" data-cal="add">＋ 登記工作</button>
        ${admin ? '<button type="button" class="cal-btn" data-cal="colors" title="六種事件各一個顏色（Google 日曆同一張表）">顏色設定</button>' : ''}
    </div>
    <div class="cal-legend">${['shoot', 'work', 'meeting', 'out', 'milestone', 'leave'].map(k => `<span><i style="background:${(z.s.colors && z.s.colors.hex && z.s.colors.hex[k]) || '#888'}"></i>${(z.s.colors && z.s.colors.labels && z.s.colors.labels[k]) || k}</span>`).join('')}<span><i class="hol"></i>假日</span><span><i class="mine"></i>跟我有關</span>${z.s.options && z.s.options.calendar_configured === false ? '<span class="cal-warn">還沒接 Google 日曆（管理員在手機行事曆分頁設定）</span>' : ''}</div>`;
}
