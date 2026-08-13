/**
 * flow-view.js — 專案工作流：階段（單線）× 五軌進度（多方前進）+ 手動里程碑。
 *
 * 規格正本 docs/PROPOSAL_PLANNER.md §14。判定邏輯全在後端
 * （core/project_flow.py + routers/crm/flow.py）—— 這裡只畫。
 *
 * 🔴 import closure 鐵則：只准 `tabs/proposals/` 與 `js/shared/`。NAS 對外容器
 * 只 serve 這兩處（core/public_assets.py MODULE_DIRS），碰到 tabs/crm/ 會壞掉。
 * tests/unit/test_public_surface.py 守著。
 *
 * 🔴 UI 無 emoji（owner 2026-07-17 鐵則）：新 UI 一律純文字。
 */
import { ensureStyle, esc } from '../../js/shared/utils.js';
import { TAB_MAP } from '../../js/shared/tab-config.js';
import { tfetch } from './prop-fetch.js';

// 軌色沿用四段進度條的既有色彙（備份藍/轉檔橘/串接綠/報表紫）—— 同事已經
// 認得這套顏色語言，不另外發明一組。收割用灰藍（PARA 的 Resources，非主線）。
const TRACK_COLOR = {
    plan: '#1f538d', prod: '#d48a04', biz: '#228b22',
    deliver: '#7c3aed', harvest: '#546e7a',
};

// 主題化＝元件自帶 --pf-* 變數、深色為預設、白底靠 html.plan-theme-light 覆寫
// （比照 plan-matrix / meeting-view / folder-view 的既有慣例）。這頁在兩個地方
// 掛：後台 SPA（深色）與 /proposal-plan.html（官網白底）—— 寫死顏色的話，
// 企劃人員最常用的那個頁面會是一片黑底貼在白紙上。
const CSS = `
.pflow { --pf-ink:#ddd; --pf-sub:#888; --pf-line:#2e2e2e; --pf-card:#161616;
    --pf-panel:#181818; --pf-on-bg:#1c1c1c; --pf-on-line:#3a3a3a;
    --pf-done:#9ccc65; --pf-done-bg:#1d2a16; --pf-done-line:#3d5c2a;
    --pf-cur:#1f538d; --pf-cur-ink:#fff;
    --pf-lost:#e88; --pf-lost-bg:#2a1618; --pf-lost-line:#6e2b2b;
    --pf-warn:#d9b45a; --pf-warn-b:#f0c96a; --pf-warn-bg:#1e1a12; --pf-warn-line:#4a3c1a;
    --pf-hover-bg:#202020; --pf-hover-line:#4a4a4a;
    padding:4px 2px 18px; color:var(--pf-ink); }
html.plan-theme-light .pflow { --pf-ink:#262626; --pf-sub:#737373; --pf-line:#e5e5e5;
    --pf-card:#fafafa; --pf-panel:#fafafa; --pf-on-bg:#fff; --pf-on-line:#d4d4d4;
    --pf-done:#3f7a1f; --pf-done-bg:#eef7e6; --pf-done-line:#cfe4bd;
    --pf-cur:#1f538d; --pf-cur-ink:#fff;
    --pf-lost:#b3261e; --pf-lost-bg:#fdecea; --pf-lost-line:#f3c2bd;
    --pf-warn:#8a6d1f; --pf-warn-b:#6b5416; --pf-warn-bg:#fdf6e3; --pf-warn-line:#ecdcb0;
    --pf-hover-bg:#f0f0f0; --pf-hover-line:#c4c4c4; }
.pflow * { box-sizing:border-box; }
.pflow-stage { display:flex; align-items:center; gap:6px; flex-wrap:wrap;
    padding:12px 14px; background:var(--pf-panel); border:1px solid var(--pf-line);
    border-radius:8px; margin-bottom:14px; }
.pflow-step { font-size:12px; padding:5px 12px; border-radius:999px;
    border:1px solid var(--pf-line); color:var(--pf-sub); white-space:nowrap; }
.pflow-step.done { color:var(--pf-done); border-color:var(--pf-done-line);
    background:var(--pf-done-bg); }
.pflow-step.cur { color:var(--pf-cur-ink); border-color:var(--pf-cur);
    background:var(--pf-cur); font-weight:600; }
.pflow-arrow { color:var(--pf-line); font-size:11px; }
.pflow-lost { padding:12px 14px; border-radius:8px; margin-bottom:14px;
    background:var(--pf-lost-bg); border:1px solid var(--pf-lost-line);
    color:var(--pf-lost); font-size:12.5px; }
.pflow-track { display:flex; align-items:flex-start; gap:10px; padding:9px 0;
    border-bottom:1px solid var(--pf-line); }
.pflow-track:last-child { border-bottom:0; }
.pflow-tname { flex:0 0 52px; font-size:12px; font-weight:600; padding-top:4px; }
.pflow-items { flex:1; display:flex; flex-wrap:wrap; gap:6px; }
.pflow-item { display:inline-flex; align-items:center; gap:5px; font-size:11.5px;
    padding:4px 9px; border-radius:6px; border:1px solid var(--pf-line);
    background:var(--pf-card); color:var(--pf-sub); cursor:default;
    text-decoration:none; }
/* 「去完成」的記號。純文字箭頭 —— UI 無 emoji 鐵則（既有的「研究頁 ↗」同款）。
   沒權限的還是畫，只是更淡：不藏功能，讓人知道有這條路、缺什麼權限。
   走 ::after 而不是塞一個 <span>：它是裝飾不是內容 —— 混進 textContent 的話
   連結的可及性名稱會多一個箭頭念出來，讀燈號名的測試也要各自去剝它。 */
.pflow-item.go::after, .pflow-item.nogo::after { content:'↗'; font-size:10px;
    margin-left:1px; }
.pflow-item.go::after { opacity:.7; }
.pflow-item.nogo::after { opacity:.3; }
.pflow-golink { color:inherit; text-decoration:underline dotted; }
.pflow-golink:hover { text-decoration-style:solid; }
.pflow-item.on { color:var(--pf-ink); border-color:var(--pf-on-line);
    background:var(--pf-on-bg); }
.pflow-item.skip { opacity:.45; border-style:dashed; }
.pflow-dot { width:8px; height:8px; border-radius:50%; flex:0 0 8px;
    border:1px solid currentColor; }
/* 亮起來＝填軌色。用 CSS 變數而不是在 JS 裡按 state 決定要不要寫 inline
   background —— 那樣的話樂觀更新只加 class、點會變成無邊框又無底色（消失）。 */
.pflow-item.on .pflow-dot { border:0; background:var(--dot, currentColor); }
.pflow-item.manual .pflow-dot { border-radius:2px; }
.pflow-count { flex:0 0 auto; font-size:11px; color:var(--pf-sub); padding-top:5px;
    min-width:34px; text-align:right; }
.pflow-miss { margin-top:14px; padding:10px 12px; border-radius:8px;
    background:var(--pf-warn-bg); border:1px solid var(--pf-warn-line);
    font-size:12px; color:var(--pf-warn); }
.pflow-miss b { color:var(--pf-warn-b); font-weight:600; }
.pflow-miss .adv { color:var(--pf-sub); }
.pflow-empty { padding:22px; text-align:center; color:var(--pf-sub); font-size:12.5px; }
.pflow-note { margin-top:12px; font-size:11.5px; color:var(--pf-sub); }
.pflow-item.clickable { cursor:pointer; }
.pflow-item.clickable:hover { border-color:var(--pf-hover-line);
    background:var(--pf-hover-bg); color:var(--pf-ink); }
.pflow-item.clickable:focus-visible { outline:2px solid var(--pf-cur); outline-offset:1px; }
.pflow-item[data-busy] { opacity:.5; pointer-events:none; }
/* 推進鈕靠右：用 margin-left:auto 而不是一顆空的 spacer span */
.pflow-adv { margin-left:auto; border:1px solid var(--pf-cur); background:var(--pf-cur);
    color:var(--pf-cur-ink); font:inherit; font-size:12px; font-weight:600;
    padding:6px 14px; border-radius:6px; cursor:pointer; white-space:nowrap; }
.pflow-adv:hover:not(:disabled) { filter:brightness(1.12); }
.pflow-adv:disabled { opacity:.42; cursor:not-allowed; }
/* 推進對話框：殼與按鈕吃 prop-dialog 的 .pdlg-*，這裡只留它沒有的兩個 */
.pflow-dlg-p { margin:0 0 8px; font-size:13px; line-height:1.7; }
.pflow-dlg-p.sub { margin-top:14px; color:var(--pdlg-sub); font-size:12px; }
.pflow-mlist { margin:0 0 4px; padding-left:20px; font-size:12.5px; line-height:1.9; }
.pflow-mlist.adv { color:var(--pdlg-sub); }
@media (max-width: 720px) {
    .pflow-track { flex-direction:column; gap:4px; }
    .pflow-tname { flex:none; padding-top:0; }
    .pflow-count { text-align:left; padding-top:0; }
}
`;

function _stageHtml(st, advanceHtml = '') {
    if (st.is_lost) {
        return `<div class="pflow-lost">未成案 — 這個案子沒有拿到。原因記在提案的組織學習欄。</div>`;
    }
    const steps = st.pipeline.map((s, i) => {
        const cls = s === st.status ? 'cur' : (st.index >= 0 && i < st.index ? 'done' : '');
        return `<span class="pflow-step ${cls}">${esc(s)}</span>`;
    }).join('<span class="pflow-arrow">—</span>');
    return `<div class="pflow-stage">${steps}${advanceHtml}</div>`;
}

/** 燈要能自己解釋為什麼亮 —— 沒有這個，燈號系統會變成沒人信任的裝飾。 */
function _why(it, clickable) {
    if (it.detail) return `${it.label}：${it.detail}`;
    if (it.kind !== 'manual') return it.hint || it.label;
    if (it.state === 'on') {
        return `${it.label}：${it.by || '有人'} 於 ${it.at || '—'} 標記`
            + (it.note ? `（${it.note}）` : '');
    }
    return clickable ? `${it.hint || it.label}（點一下標記）`
                     : `${it.label}：需要專案管理權限才能標記`;
}

/**
 * 「去完成」的去處。`dest` 只在該畫連結時才由後端給（未亮的自動燈 ——
 * 見 core.project_flow.build），所以這裡不重新判斷 state。
 *
 * 🔴 目的地的鍵**就是模組鍵**，而 TAB_MAP 也是用模組鍵鍵的 → 一張既有的表
 * 就換得到 section id，不必再維護第二份「目的地 → 網址」對照（那份會跟後端
 * 的 DESTS 漂掉）。換不到（有模組但沒 tab）就不畫 —— 不畫點了沒反應的東西。
 */
function _dest(row, links) {
    const L = row.dest && links[row.dest];
    const section = L && TAB_MAP[row.dest];
    return (L && section) ? { section, label: L.label, allowed: !!L.allowed } : null;
}

/** deep-link 的 href/target。SPA 裡由 _onHit 接走做原地切換，其他頁
 *  （/proposal-plan.html）就讓瀏覽器自己開新分頁。
 *  寫成真的 `<a href>` 而不是 onclick —— 中鍵/Ctrl 點開新分頁、右鍵複製連結
 *  是使用者本來就會做的事，span 一律做不到。 */
function _goAttrs(section, inSpa) {
    return ` href="/#${esc(section)}" data-go="${esc(section)}"`
         + (inSpa ? '' : ' target="_blank" rel="noopener"');
}

function _itemHtml(it, canCheck, color, links, inSpa) {
    const clickable = it.kind === 'manual' && canCheck;
    const d = clickable ? null : _dest(it, links);
    const go = !!(d && d.allowed);
    // state/kind 本身就是 on|off|skip、auto|manual，直接當 class 用
    const cls = `pflow-item ${it.state} ${it.kind}`
              + (clickable || go ? ' clickable' : '') + (d ? (go ? ' go' : ' nogo') : '');
    // 顏色一律傳下去（由 CSS 決定亮不亮），純 class 切換就有正確視覺
    const dot = `<span class="pflow-dot" style="--dot:${color}"></span>`;
    let tag = 'span', attrs = '', tip = _why(it, clickable);
    if (clickable) {
        attrs = ` data-check="${esc(it.key)}" role="button" tabindex="0"`;
    } else if (go) {
        tag = 'a';
        attrs = _goAttrs(d.section, inSpa);
        tip += `　→ 去「${d.label}」完成`;
    } else if (d) {
        tip += `　→ 在「${d.label}」完成（你的帳號沒有這個模組權限）`;
    }
    return `<${tag} class="${cls}"${attrs} title="${esc(tip)}">`
         + `${dot}${esc(it.label)}</${tag}>`;
}

function _trackHtml(t, canCheck, links, inSpa) {
    const color = TRACK_COLOR[t.key] || '#888';
    const items = t.items.map(i => _itemHtml(i, canCheck, color, links, inSpa)).join('');
    return `<div class="pflow-track">
        <div class="pflow-tname" style="color:${color}">${esc(t.label)}</div>
        <div class="pflow-items">${items}</div>
        <div class="pflow-count">${t.done}/${t.total}</div>
    </div>`;
}

/** 後端已經把缺項拆成 blocking / advisory 兩袋 —— 前端不重新詮釋那個語意。
 *  （形狀由 core.project_flow.missing_for 保證，單元測試釘住兩個 key 一定在，
 *  所以這裡直接解構、不層層補預設值。） */
function _missingHtml({ blocking, advisory }, next, links, inSpa) {
    if (!blocking.length && !advisory.length) return '';
    // 缺項也帶去處（規格 §14.4「各附 deep-link」）—— 缺項橫幅正是最該能直接
    // 動身的地方。沒權限就退回純文字，不畫一條點不動的連結。
    const name = (m, bold) => {
        const d = _dest(m, links);
        const t = bold ? `<b>${esc(m.label)}</b>` : esc(m.label);
        return (d && d.allowed)
            ? `<a class="pflow-golink"${_goAttrs(d.section, inSpa)}`
              + ` title="去「${esc(d.label)}」完成">${t}</a>` : t;
    };
    const parts = [];
    if (blocking.length) {
        parts.push(`推進到「${esc(next)}」前建議先完成：` +
            blocking.map(m => name(m, true)).join('、'));
    }
    if (advisory.length) {
        parts.push(`<span class="adv">提醒（不影響推進）：` +
            advisory.map(m => name(m, false)).join('、') + `</span>`);
    }
    return `<div class="pflow-miss">${parts.join('<br>')}</div>`;
}

/**
 * @param host   掛載節點
 * @param opts   { projectId, onAdvanced, onNavigate }  —— 沒有殼專案的提案
 *               不要呼叫這支。
 *               onAdvanced(status) 在推進成功後呼叫，讓呼叫端更新自己那份
 *               （詳情標頭的狀態、清單的階段 chip…）。
 *               onNavigate() 在 SPA 裡原地切走 tab **之前**呼叫 —— 掛在
 *               overlay 裡的話要先關掉它，不然切過去的畫面被蓋在底下。
 *               這個模組不自己去 remove 別人的 DOM：誰掛的誰收。
 *               用回呼而不是全域 CustomEvent —— 這個模組的既有慣例
 *               （onPlanStarted / toast / onSaved）都是回呼，而全域事件沒有
 *               owner，之後要拆得 grep 全 repo。
 */
export async function renderFlow(host, { projectId, onAdvanced = null,
                                         onNavigate = null }) {
    ensureStyle('pflow-css', CSS);
    if (!projectId) {
        _msg(host, '這個提案還沒有關聯專案。<br>補上客戶之後系統會自動建立殼專案，進度才有東西可以追。');
        return;
    }
    // `__flow` 是這個 host 的全部狀態，形狀在這裡宣告一次（也兼作「掛過了沒」
    // 的旗標）。每次都整個換掉：同一個 host 換提案時，closure 會永久釘住
    // 第一次的 projectId，而殘留的 missing/collectsReason 是上一個專案的。
    const wired = !!host.__flow;
    host.__flow = { pid: projectId, onAdvanced, onNavigate, links: {},
                    missing: { blocking: [], advisory: [] }, collectsReason: false };
    if (!wired) {
        // 事件委派掛一次就好 —— 每次重畫都重掛會累積成一次點擊送 N 個請求
        const on = (ev) => _onHit(host, ev);
        host.addEventListener('click', on);
        host.addEventListener('keydown', on);
    }
    _msg(host, '載入中…');
    await _load(host, projectId);
}

/** 訊息狀態也要包在 .pflow 裡 —— 主題變數定義在那一層，裸著放的話
 *  白底頁上會拿不到 --pf-sub 而變成繼承色（看起來像沒套樣式）。 */
function _msg(host, t) {
    host.innerHTML = `<div class="pflow"><div class="pflow-empty">${t}</div></div>`;
}

/** 抓一份權威 payload 畫上去。推進後也走這支 —— 不回頭呼叫 renderFlow
 *  （那讀起來像遞迴，還會為了重掛事件而多一層守衛）。
 *  刻意不先清成「載入中…」：舊資料在新的到之前都還是對的，清掉只換來一次
 *  整片空白閃爍。 */
async function _load(host, projectId) {
    try {
        const d = await tfetch(`/api/v1/crm/projects/${encodeURIComponent(projectId)}/flow`);
        if (host.isConnected) _paint(host, d);
    } catch (e) {
        if (host.isConnected) _msg(host, '進度載入失敗：' + esc(e.message || e));
    }
}

/** 純 dispatcher：三條路徑各自一支，這裡只決定走哪條。 */
async function _onHit(host, ev) {
    const go = ev.target.closest?.('[data-go]');
    if (go && host.contains(go) && ev.type === 'click') {
        // 非 SPA、或使用者自己要開新分頁（Ctrl/⌘/Shift/中鍵）→ 什麼都不做，
        // 讓 <a> 的原生行為跑完。攔下來自己 open() 會被彈窗封鎖擋掉。
        if (!_inSpa() || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
        ev.preventDefault();
        host.__flow.onNavigate?.();     // overlay 先關（誰掛的誰收）
        window.switchTab(go.dataset.go);
        return;
    }
    const adv = ev.target.closest?.('[data-advance]');
    if (adv && host.contains(adv) && ev.type === 'click') {
        ev.preventDefault();
        await _advance(host, adv);
        return;
    }
    const el = ev.target.closest?.('[data-check]');
    if (!el || !host.contains(el)) return;
    if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    await _toggleCheck(host, el);
}

async function _toggleCheck(host, el) {
    if (el.dataset.busy) return;          // 連點兩下不送兩次
    el.dataset.busy = '1';
    const nowOn = el.classList.contains('on');
    // 先動畫面再送請求：那一趟要等後端重算整片進度（幾百 ms），不先回應
    // 的話點下去像沒反應。權威狀態由下面的重畫覆蓋，失敗則還原。
    el.classList.toggle('on', !nowOn);
    try {
        const fresh = await tfetch(
            `/api/v1/crm/projects/${encodeURIComponent(host.__flow.pid)}/flow/check`,
            { method: 'POST', json: { item_key: el.dataset.check, checked: !nowOn } });
        if (host.isConnected) _paint(host, fresh);
    } catch (e) {
        el.classList.toggle('on', nowOn);   // 還原樂觀更新
        delete el.dataset.busy;
        alert('標記失敗：' + (e.message || e));
    }
}

/**
 * 推進一階。**打的是既有的專案狀態端點** —— 那支帶著完整副作用鏈
 * （客戶分級重算、衛星提案 win/loss、階段時間戳）。這裡不另開寫入路，
 * 也不在前端重做那些副作用。
 */
async function _advance(host, btn) {
    if (btn.disabled) return;
    const f = host.__flow;
    const next = btn.dataset.advance;
    // 先鎖住再開對話框 —— 鎖在 await 之後的話，連點兩下會開出兩個對話框、
    // 送出兩次 PATCH
    btn.disabled = true;
    try {
        // 「這次會不會用到成案原因」由後端宣告（它與真正去標的那支共用同一
        // 份判定）—— 前端自己算的話，多筆衛星提案時會白問一次，使用者打的
        // 字被靜默丟掉
        const reason = await _confirmAdvance(next, f.missing, f.collectsReason, f.links);
        if (reason === null) return;      // 取消
        const body = { status: next };
        if (reason) body.outcome_reason = reason;
        await tfetch(`/api/v1/crm/projects/${encodeURIComponent(f.pid)}/status`,
                     { method: 'PATCH', json: body });
        // 階段變了 → 重抓（推進會連動衛星提案狀態與一堆訊號，前端自己推導
        // 只會跟後端各算各的）
        await _load(host, f.pid);
        if (f.onAdvanced) f.onAdvanced(next);
    } catch (e) {
        // 422 帶 code 的（如轉未成案要原因）後端訊息已經說清楚了，直接轉述
        alert('推進失敗：' + (e.message || e));
    } finally {
        // 成功路徑上 _load 已經重畫、這顆按鈕早被換掉（detached）
        if (btn.isConnected) btn.disabled = false;
    }
}

/** 推進鈕：掛在階段列右端。終態（歸檔／未成案）沒有下一站就不畫。 */
function _advanceHtml(st, canAdvance) {
    if (!st.next) return '';
    const attrs = canAdvance
        // 不寫死「需要管理員」—— 門檻由 core.project_flow.ADVANCE_MODULES 決定，
        // 鬆綁時這句話會是第三個忘記改的地方（而且沒有測試守）
        ? `data-advance="${esc(st.next)}"`
        : `disabled title="你的帳號沒有推進階段的權限（推進會連動客戶分級與錢流口徑）"`;
    return `<button class="pflow-adv" ${attrs}>推進到「${esc(st.next)}」</button>`;
}

/** 在後台 SPA 裡嗎？—— 有 `window.switchTab` 就是。這決定 deep-link 是原地
 *  換 tab 還是開新分頁；`/proposal-plan.html` 沒有那個全域函式。
 *  用能力偵測而不是叫呼叫端傳旗標：兩個掛載點各傳一次就會有一天忘記改。 */
function _inSpa() {
    return typeof window.switchTab === 'function';
}

function _paint(host, d) {
    const st = d.stage || {};
    const can = !!d.can_check;
    const links = d.links || {};
    const inSpa = _inSpa();
    const missing = d.missing || { blocking: [], advisory: [] };
    // 推進對話框要的三樣東西 —— 存最後一次的**權威**資料，別讓它去讀畫面
    // （讀畫面的話，樂觀更新那一瞬間的 class 會被當成事實）。刻意不存整包
    // payload：tracks 佔了 4KB 的 88%，畫成 HTML 之後就沒人要了。
    host.__flow = { ...host.__flow, missing, links,
                    collectsReason: !!d.collects_outcome_reason };
    host.innerHTML = `<div class="pflow">
        ${_stageHtml(st, _advanceHtml(st, !!d.can_advance))}
        ${(d.tracks || []).map(t => _trackHtml(t, can, links, inSpa)).join('')}
        ${_missingHtml(missing, st.next, links, inSpa)}
        ${can ? '' : '<div class="pflow-note">里程碑（開拍／剪輯完成）需要專案管理權限才能標記。</div>'}
    </div>`;
}

/**
 * 軟擋（owner 決策點 2）：缺項只列出來，確認後仍然推得動。
 * 硬守衛留在既有端點（轉未成案要原因那類）—— 這裡不新蓋假守門。
 *
 * 對話框的殼、按鈕、欄位樣式全部用 prop-dialog 既有的 `.pdlg-*`
 * （`field()` / `.pdlg-acts` / `.pdlg-btn`）—— 自己抄一份的下場是抄到
 * 沒有 hover、沒有 disabled、沒有 focus ring，而且 textarea 那幾條會被
 * `.pdlg textarea` 的特異性整段蓋掉（寫了等於沒寫）。
 *
 * @returns {Promise<string|null>} 確認＝成案原因字串（可為空），取消＝null
 */
async function _confirmAdvance(next, { blocking, advisory }, collectsReason, links) {
    const { openDialog, field } = await import('./prop-dialog.js');
    // 缺項的「去完成」在對話框裡**一律開新分頁**（inSpa 傳 false）：這裡原地
    // 切走會把對話框連同你正在做的決定一起毀掉。新分頁去完成、回來仍在原處。
    const item = (m) => {
        const d = _dest(m, links);
        return (d && d.allowed)
            ? `${esc(m.label)}　<a class="pflow-golink"${_goAttrs(d.section, false)}`
              + `>去「${esc(d.label)}」完成</a>` : esc(m.label);
    };
    const list = (arr, cls) => arr.length
        ? `<ul class="pflow-mlist ${cls}">${arr.map(m => `<li>${item(m)}</li>`).join('')}</ul>` : '';
    const body = `
        ${blocking.length
            ? `<p class="pflow-dlg-p">這幾項還沒完成，確定要推進嗎？</p>${list(blocking, '')}`
            : `<p class="pflow-dlg-p">建議完成的項目都到齊了。</p>`}
        ${advisory.length
            ? `<p class="pflow-dlg-p sub">以下只是提醒，不影響推進：</p>${list(advisory, 'adv')}` : ''}
        ${collectsReason ? field('成案原因（組織學習欄，可留空）',
            `<textarea id="pflow-reason" rows="3"
                placeholder="為什麼拿到這個案子？下一次要複製什麼？"></textarea>`) : ''}
        <div class="pdlg-acts">
            <button class="pdlg-btn ghost" id="pflow-cancel">取消</button>
            <button class="pdlg-btn" id="pflow-go">推進到「${esc(next)}」</button>
        </div>`;
    return new Promise(resolve => {
        // 關掉（X／Esc／點背景）＝取消。已 settle 的 resolve 是 no-op，
        // 所以 finish() 自己觸發的 onClose 不會蓋掉答案（先 resolve 再 close）。
        const dlg = openDialog({ title: '推進階段', body, width: 460,
                                 onClose: () => resolve(null) });
        const finish = (go) => {
            const ta = dlg.el.querySelector('#pflow-reason');
            resolve(go ? (ta ? ta.value.trim() : '') : null);
            dlg.close();
        };
        dlg.el.querySelector('#pflow-go').addEventListener('click', () => finish(true));
        dlg.el.querySelector('#pflow-cancel').addEventListener('click', () => finish(false));
    });
}
