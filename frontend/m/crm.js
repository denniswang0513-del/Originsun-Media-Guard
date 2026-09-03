/**
 * frontend/m/crm.js — CRM 手機版進入點（docs/CRM_MOBILE_PLAN.md §4）。
 *
 * 閘門：管理員（access_level ≥ 3）或模組 crm_projects；殼由 ./shell.js 提供。
 * 開頁：boot → GET /api/v1/crm/m/options 一次（所有字彙）→ 依 hash 畫分頁。
 * 分頁：#invoice（預設）／#petty／#projects／#quotes／#payments，畫面在 views/；#expense（記雜支）有畫面但不在分頁列。
 */
import { boot, mfetch, toast, esc } from './shell.js';
import { state, DEFAULT_TAB, currentTab, initSheet, closeSheet, errBox } from './ui.js';
import * as invoiceView from './views/invoice.js';
import * as pettyView from './views/petty.js';
import * as projectsView from './views/projects.js';
import * as quotesView from './views/quotes.js';
import * as paymentsView from './views/payments.js';
import * as expenseView from './views/expense.js';

const VIEWS = { invoice: invoiceView, petty: pettyView, projects: projectsView,
                quotes: quotesView, payments: paymentsView, expense: expenseView };
const ROUTE_LABELS = { expense: '記雜支' };   // 分頁列上沒有的路由，頂欄名稱在這

const gate = (me) => (me.access_level || 0) >= 3 || (me.modules || []).includes('crm_projects');

const hosts = {};
let _depth = 0;      // 這次開頁後往前走了幾步（上一頁按到底就回首頁，不會退出這個 app）

// 分頁名從底部 tabbar 的按鈕文字拿，不另外抄一份
const tabLabel = (tab) => (document.querySelector(`#m-tabbar button[data-tab="${tab}"]`) || {}).textContent || ROUTE_LABELS[tab] || '';

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
    document.getElementById('m-page').textContent = tabLabel(tab) + (state.me && state.me.username ? '｜' + state.me.username : '');
    for (const b of document.querySelectorAll('#m-tabbar button'))
        b.classList.toggle('on', b.dataset.tab === tab);
    for (const t in hosts) hosts[t].hidden = t !== tab;
    const host = _host(tab);
    host.hidden = false;
    closeSheet();
    window.scrollTo(0, 0);
    const first = !host.dataset.ready;
    host.dataset.ready = '1';
    try {
        await VIEWS[tab].render(host, { first });
    } catch (e) {
        host.innerHTML = errBox(e);
    }
}

/** 借一個畫好的分頁宿主（不切 hash）：專案抽屜把發票／記雜支表單搬進去用。preset 先設好再呼叫，render 會套。 */
state.ensureView = async (tab) => {
    const host = _host(tab);
    const first = !host.dataset.ready;
    host.dataset.ready = '1';
    await VIEWS[tab].render(host, { first });
    return host;
};

async function main() {
    const me = await boot({ gate });
    state.me = me;
    initSheet();
    // 頂欄：上一頁＝先關抽屜、再退一個分頁、退到底回首頁；首頁＝發票分頁（落地分頁）
    document.getElementById('m-back').addEventListener('click', () => {
        const s = document.getElementById('m-sheet');
        if (!s.hidden) { closeSheet(); return; }
        if (_depth > 0) { _depth -= 2; history.back(); return; }   // hashchange 會再 +1，所以先扣 2
        location.hash = DEFAULT_TAB;
    });
    document.getElementById('m-home').addEventListener('click', () => { closeSheet(); location.hash = DEFAULT_TAB; window.scrollTo(0, 0); });
    document.getElementById('m-tabbar').addEventListener('click', (ev) => {
        const b = ev.target.closest('button[data-tab]');
        if (b) location.hash = b.dataset.tab;
    });
    window.addEventListener('hashchange', () => { _depth += 1; render(); });
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
