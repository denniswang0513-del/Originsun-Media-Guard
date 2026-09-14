/**
 * crm-projects-calendar.js — 專案詳情第九個分頁「行事曆」（owner 2026-09-14 ①：只看這一案的行事曆）。
 * 同一份共用元件（js/shared/calendar/），scope 鎖 project:<id>；「登記工作」「登記拍攝」預設這一案。
 * 分頁切換由 crm-projects.js 的 detail sub-tabs 呼叫 loadCalendarTab(projectId)。
 * 行事曆端點在 /api/v1/calendar（不在 CRM 前綴下），所以不用 crmFetch，自己帶 token。
 */
import { esc as _esc, crmToast } from './crm-utils.js';
import { state } from './crm-projects-state.js';
import { mountCalendar } from '../../js/shared/calendar/index.js';

let _mountedFor = null;

async function cfetch(path, opts = {}) {
    const token = localStorage.getItem('auth_token');
    const r = await fetch(path, {
        method: opts.method || 'GET',
        headers: { Accept: 'application/json', ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: 'Bearer ' + token } : {}) },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
    return r.status === 204 ? null : r.json();
}

/** 進分頁／換專案時叫；同一案重複叫只重抓。 */
export async function loadCalendarTab(projectId) {
    const host = document.getElementById('proj-detail-calendar');
    if (!host || !projectId) return;
    const p = state.projects.find(x => x.id === projectId);
    if (_mountedFor === projectId && host.querySelector('.cal-bar')) { window._projCalendarZ?.hooks?.reload?.(); return; }
    _mountedFor = projectId;
    window._projCalendarZ = await mountCalendar({
        host, fetch: cfetch, esc: _esc,
        lockedScope: 'project:' + projectId, scopeName: p ? p.name : '',
        view: 'month',
        hooks: {
            toast: (m, k) => crmToast(m, k === 'err' ? 6000 : 3000),
            // 拍攝表單只有手機版有：開手機行事曆分頁（同一個後端）
            openShoot: () => window.open('/m/crm.html#calendar', '_blank'),
            openProject: (id) => { if (id && id !== projectId) window._projSelect?.(id); },
        },
    });
}
