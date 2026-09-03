/**
 * frontend/m/crm.js — CRM 手機版進入點（docs/CRM_MOBILE_PLAN.md §4）。
 *
 * 閘門：管理員（access_level ≥ 3）或模組 crm_projects；殼由 ./shell.js 提供。
 * 開頁：boot → GET /api/v1/crm/m/options 一次（所有字彙）→ 依 hash 畫分頁。
 * 分頁：#invoice（預設）／#petty／#projects／#quotes／#payments，畫面在 views/。
 */
import { boot, mfetch, toast, esc } from './shell.js';
import { state, TABS, currentTab, initSheet, closeSheet, errBox } from './ui.js';
import * as invoiceView from './views/invoice.js';
import * as pettyView from './views/petty.js';
import * as projectsView from './views/projects.js';
import * as quotesView from './views/quotes.js';
import * as paymentsView from './views/payments.js';

const VIEWS = { invoice: invoiceView, petty: pettyView, projects: projectsView,
                quotes: quotesView, payments: paymentsView };

const gate = (me) => (me.access_level || 0) >= 3 || (me.modules || []).includes('crm_projects');

let _tab = '';
const hosts = {};

function _host(tab) {
    if (!hosts[tab]) {
        const el = document.createElement('section');
        el.id = 'tab-' + tab;
        el.hidden = true;
        document.getElementById('m-view').appendChild(el);
        hosts[tab] = el;
    }
    return hosts[tab];
}

async function render() {
    const tab = currentTab();
    if (location.hash.replace(/^#/, '').split('?')[0] !== tab) location.hash = tab;
    for (const b of document.querySelectorAll('#m-tabbar button'))
        b.classList.toggle('on', b.dataset.tab === tab);
    for (const t of TABS) if (hosts[t]) hosts[t].hidden = t !== tab;
    const host = _host(tab);
    host.hidden = false;
    closeSheet();
    window.scrollTo(0, 0);
    const first = !host.dataset.ready;
    host.dataset.ready = '1';
    _tab = tab;
    try {
        await VIEWS[tab].render(host, { first });
    } catch (e) {
        host.innerHTML = errBox(e);
    }
}

async function main() {
    const me = await boot({ gate });
    state.me = me;
    document.getElementById('m-who').textContent = me.username || '';
    initSheet();
    document.getElementById('m-tabbar').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-tab]');
        if (b) location.hash = b.dataset.tab;
    });
    window.addEventListener('hashchange', render);
    try {
        state.options = await mfetch('/api/v1/crm/m/options');
    } catch (e) {
        document.getElementById('m-view').innerHTML =
            `<div class="m-err">字彙載入失敗（${esc(e.message)}）—— 這頁的表單需要它，請重新整理再試。</div>`;
        return;
    }
    const meOpt = state.options.me || {};
    state.canWrite = !!meOpt.can_write;
    document.body.classList.toggle('no-write', !state.canWrite);
    if (!state.canWrite) toast('此帳號只能檢視，寫入功能已隱藏', 'err');
    await render();
}

main();

// 讓分頁模組可以要求「重畫目前分頁」（例如新增案子後）
window.addEventListener('m-rerender', () => { if (hosts[_tab]) VIEWS[_tab].render(hosts[_tab], { first: false }); });
