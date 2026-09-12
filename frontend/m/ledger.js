/**
 * frontend/m/ledger.js — 士源帳本（私帳手機版）進入點（docs/MY_LEDGER_MOBILE_PLAN.md §4）。
 *
 * 閘門：帳號要有 finance_mine（指名制；Lv3 不 bypass，跟 /my-ledger.html 同口徑）。殼由 ./shell.js 提供。
 * 開頁：boot → GET /api/v1/finance/m/options 一次（分類樹／案源／專案／帳戶）→ 依 hash 畫分頁。
 * 分頁：#cash（預設）／#projects／#receivable／#household／#overview／#assets，畫面在 views/ledger-*.js。
 * ui.js 的 TABS／DEFAULT_TAB／currentTab 是 CRM 那份，這裡自己維護一份（不改 ui.js 的常數）。
 */
import { boot, mfetch, toast, esc } from './shell.js';
import { state, initSheet, closeSheet, errBox } from './ui.js';
import * as cashView from './views/ledger-cash.js';
import * as projectsView from './views/ledger-projects.js';
import * as receivableView from './views/ledger-receivable.js';
import * as householdView from './views/ledger-household.js';
import * as overviewView from './views/ledger-overview.js';
import * as assetsView from './views/ledger-assets.js';

export const TABS = ['cash', 'projects', 'receivable', 'household', 'overview', 'assets'];
export const DEFAULT_TAB = 'cash';
const VIEWS = { cash: cashView, projects: projectsView, receivable: receivableView,
                household: householdView, overview: overviewView, assets: assetsView };

// 指名制：直接看 modules，不走 isAdmin（後端 grant_admin_all_modules 把 finance_mine 列為「指名才有」）
const gate = (me) => ((me || {}).modules || []).includes('finance_mine');

const hosts = {};
let _depth = 0;      // 這次開頁後往前走了幾步（上一頁按到底就回首頁，不會退出這個 app）

function currentTab() {
    const h = (location.hash || '').replace(/^#/, '').split('?')[0];
    return TABS.includes(h) ? h : DEFAULT_TAB;
}

// 分頁名從底部 tabbar 的按鈕文字拿，不另外抄一份
const tabLabel = (tab) => (document.querySelector(`#m-tabbar button[data-tab="${tab}"]`) || {}).textContent || '';

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
    // 沒有／不認得的 hash → 補成落地分頁。用 replaceState：改 location.hash 會觸發 hashchange 再 render 一次
    // （每個視圖 load 兩趟），而且 _depth 會從 1 起跳，第一下「上一頁」退到沒 hash 的網址又立刻補回來
    if (location.hash.replace(/^#/, '').split('?')[0] !== tab) history.replaceState(null, '', '#' + tab);
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

/** 別的分頁要求「切過去並重畫」（應收的「收到錢」→ 收支頁帶 preset）。 */
state.gotoTab = (tab) => { if (currentTab() === tab) render(); else location.hash = tab; };

async function main() {
    const me = await boot({ gate });
    state.me = me;
    initSheet();
    // 頂欄：上一頁＝先關抽屜、再退一個分頁、退到底回首頁；首頁＝收支（落地分頁）
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
        state.options = await mfetch('/api/v1/finance/m/options');
    } catch (e) {
        document.getElementById('m-view').innerHTML =
            `<div class="m-err">字彙載入失敗（${esc(e.message)}）—— 這頁的表單需要它，請重新整理再試。</div>`;
        return;
    }
    if (!(state.options.me || {}).can_write) toast('此帳號只能檢視', 'err');
    await render();
}

main();
