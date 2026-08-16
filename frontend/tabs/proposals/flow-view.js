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
import { TAB_MAP, tabLabel } from '../../js/shared/tab-config.js';
import { tfetch } from './prop-fetch.js';
// 軌色與清單的微型完成條共用一份（見 prop-const.TRACK_COLOR）
import { TRACK_COLOR } from './prop-const.js';

// 主題化＝元件自帶 --pf-* 變數、深色為預設、白底靠 html.plan-theme-light 覆寫
// （比照 plan-matrix / meeting-view / folder-view 的既有慣例）。這頁在兩個地方
// 掛：後台 SPA（深色）與 /project.html（官網白底）—— 寫死顏色的話，
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
/* 「去完成」的記號（沒權限的更淡，但照畫）。純文字箭頭 —— UI 無 emoji 鐵則。
   走 ::after 而不是塞一個 <span>：它是裝飾不是內容 —— 混進 textContent 的話
   連結的可及性名稱會多念一個箭頭，讀燈號名的測試也要各自去剝它。 */
a.pflow-item::after, .pflow-item.nogo::after { content:'↗'; font-size:10px;
    margin-left:1px; opacity:.7; }
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
.pflow-empty .pflow-adv { margin-top:14px; }
.pflow-note { margin-top:12px; font-size:11.5px; color:var(--pf-sub); }
/* 終態唯讀（§14.4 邊界態）：未成案／歸檔。灰掉的是**還沒做的那一面** ——
   已亮的燈維持原樣：對一個結束的案子，那些是歷史紀錄不是待辦。
   凍結只做視覺與互動，「去完成」不畫（見 _dest）；缺項橫幅則不必特別處理
   —— 終態沒有下一站，後端的閘門查詢自然就空了（core.project_flow.GATES）。 */
.pflow.frozen .pflow-item { opacity:.5; }
.pflow.frozen .pflow-item.on { opacity:.9; }
.pflow.frozen .pflow-tname, .pflow.frozen .pflow-dot { filter:grayscale(.9); }
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

// 後台 SPA 的網址。deep-link 一律指這裡的 `#<section>`：SPA 從開頁
// （app.js 讀 location.hash）到之後的 hashchange 都吃這個形式，所以一條純
// `<a>` 就會換 tab —— 不必攔 click，也不必碰 `window.switchTab`。
// 「SPA 就在本站的 /」這個假設在 NAS 對外容器上不成立，但進度分頁在那裡
// 本來就掛不起來（它要先同源打到 /api/v1/crm/…，那邊只 proxy 幾個 shared/）。
const SPA = '/';
// pathname 不會變（SPA 走 hash 路由），所以這是文件層級的常數。
// `/project.html` 上點 `/#tab_x` 是一次**跨頁導覽**，會把企劃人員手上
// 正在編的東西帶走 → 開新分頁。判斷不出來就開新分頁：最壞多一個分頁，而不是
// 弄丟內容。
const NEW_TAB = location.pathname !== SPA;

/**
 * 「去完成」的去處。`dest` 是**模組鍵**（為什麼見 core.project_flow.ITEM_DEST），
 * 所以 TAB_MAP／tabLabel 這兩張既有的表就換得到 section id 與中文名。
 * 它也只在該畫連結時才由後端給，這裡不重新判斷 state。
 *
 * 不畫的三種情況：終態（案子結束了，不再叫人去做事 —— 見 _paint 的 frozen）、
 * 換不到 section（有模組沒 tab，如 me_*）、或 `here`（呼叫端說「這個畫面本身
 * 就是那個 tab」）。後兩者都是「點了不會發生任何事」。
 */
function _dest(row, ctx) {
    if (ctx.frozen) return null;
    const section = row.dest && row.dest !== ctx.here && TAB_MAP[row.dest];
    return section
        ? { section, label: tabLabel(row.dest), allowed: !!ctx.links[row.dest] }
        : null;
}

/** deep-link 的屬性。寫成真的 `<a href>` 而不是 onclick —— Ctrl 點開新分頁、
 *  右鍵複製連結是使用者本來就會做的事，span 一律做不到。 */
function _goAttrs(section, newTab) {
    return ` href="${SPA}#${esc(section)}"`
         + (newTab ? ' target="_blank" rel="noopener"' : '');
}

/** 連結的說明。燈號 tooltip 與缺項連結共用同一句 —— 分兩處寫，改了一邊
 *  另一邊就對不上，而且沒有測試會發現。 */
function _goTip(d) {
    return d.allowed ? `去「${d.label}」完成`
                     : `在「${d.label}」完成（你的帳號沒有這個模組權限）`;
}

/** 缺項的名字：進得去就是一條 deep-link，進不去就純文字（不畫點不動的連結）。
 *  缺項橫幅與推進確認框共用。 */
function _goName(row, ctx) {
    const d = _dest(row, ctx);
    return (d && d.allowed)
        ? `<a class="pflow-golink"${_goAttrs(d.section, ctx.newTab)}`
          + ` title="${esc(_goTip(d))}">${esc(row.label)}</a>` : esc(row.label);
}

function _itemHtml(it, canCheck, color, ctx) {
    const clickable = it.kind === 'manual' && canCheck;
    // 手動項不會帶 dest（後端只給未亮的自動燈），所以兩者互斥是資料保證的，
    // 不必在這裡再用 clickable 擋一次
    const d = _dest(it, ctx);
    const go = !!(d && d.allowed);
    // state/kind 本身就是 on|off|skip、auto|manual，直接當 class 用。
    // 「有連結」不另給 class —— 那就是 <a>（CSS 用 a.pflow-item 選）。
    const cls = `pflow-item ${it.state} ${it.kind}`
              + (clickable || go ? ' clickable' : '') + (d && !go ? ' nogo' : '');
    // 顏色一律傳下去（由 CSS 決定亮不亮），純 class 切換就有正確視覺
    const dot = `<span class="pflow-dot" style="--dot:${color}"></span>`;
    const tag = go ? 'a' : 'span';
    const attrs = clickable ? ` data-check="${esc(it.key)}" role="button" tabindex="0"`
                : go ? _goAttrs(d.section, ctx.newTab) : '';
    const tip = _why(it, clickable) + (d ? `　→ ${_goTip(d)}` : '');
    return `<${tag} class="${cls}"${attrs} title="${esc(tip)}">`
         + `${dot}${esc(it.label)}</${tag}>`;
}

function _trackHtml(t, canCheck, ctx) {
    const color = TRACK_COLOR[t.key] || '#888';
    const items = t.items.map(i => _itemHtml(i, canCheck, color, ctx)).join('');
    return `<div class="pflow-track">
        <div class="pflow-tname" style="color:${color}">${esc(t.label)}</div>
        <div class="pflow-items">${items}</div>
        <div class="pflow-count">${t.done}/${t.total}</div>
    </div>`;
}

/** 後端已經把缺項拆成 blocking / advisory 兩袋 —— 前端不重新詮釋那個語意。
 *  （形狀由 core.project_flow.missing_for 保證，單元測試釘住兩個 key 一定在，
 *  所以這裡直接解構、不層層補預設值。） */
function _missingHtml({ blocking, advisory }, next, ctx) {
    if (!blocking.length && !advisory.length) return '';
    // 缺項也帶去處（規格 §14.4「各附 deep-link」）—— 缺項橫幅正是最該能直接
    // 動身的地方
    const parts = [];
    if (blocking.length) {
        parts.push(`推進到「${esc(next)}」前建議先完成：` +
            blocking.map(m => `<b>${_goName(m, ctx)}</b>`).join('、'));
    }
    if (advisory.length) {
        parts.push(`<span class="adv">提醒（不影響推進）：` +
            advisory.map(m => _goName(m, ctx)).join('、') + `</span>`);
    }
    return `<div class="pflow-miss">${parts.join('<br>')}</div>`;
}

/**
 * @param host   掛載節點
 * @param opts   { projectId, proposalId, onChanged, here }
 *               projectId 空＝這個提案還沒入管線 → 畫自癒 CTA（見 HEAL_HTML），
 *               所以那條路上 proposalId 是必要的。
 *
 *               onChanged()：這個元件剛動了後端的東西（推進階段／補殼專案），
 *               呼叫端那份鏡像（詳情標頭的狀態、清單的階段 chip 與「專案」欄）
 *               已經是舊的。**一個回呼涵蓋兩種動作** —— 分開的唯一效果就是
 *               可以漏接一半，而那真的發生過（§14.4.4）。用回呼而不是全域
 *               CustomEvent：這個模組的既有慣例（onPlanStarted / toast /
 *               onSaved）都是回呼，全域事件沒有 owner，之後要拆得 grep 全 repo。
 *
 *               它會被 await，且在元件重畫自己**之前**呼叫（你有權把這個 host
 *               拆掉）—— 所以要拆的話請回傳你的 promise。忘了回傳不會壞，
 *               只是多抓一趟然後丟掉；理由見 _settled。
 *               here：「這個畫面本身就是哪個 tab」的模組鍵。指向這裡的
 *               deep-link 不畫 —— 兩個掛載點都在提案工作區裡，那幾盞燈
 *               （提案已建立／企劃書…）該做的事就在手邊，把人送去提案庫清單
 *               反而是把他推離現場。由呼叫端說而不是讀 location：兩個掛載點
 *               的答案一樣，讀網址卻只在其中一個會對。
 */
export async function renderFlow(host, { projectId, proposalId = '',
                                         onChanged = null, here = '' }) {
    ensureStyle('pflow-css', CSS);
    // `__flow` 是這個 host 的全部狀態，形狀在這裡宣告一次（也兼作「掛過了沒」
    // 的旗標）。每次都整個換掉：同一個 host 換提案時，closure 會永久釘住
    // 第一次的 projectId，而殘留的 missing/collectsReason 是上一個專案的。
    const wired = !!host.__flow;
    host.__flow = { pid: projectId, propId: proposalId, onChanged,
                    ctx: { links: {}, here, newTab: NEW_TAB, frozen: false },
                    missing: { blocking: [], advisory: [] }, collectsReason: false,
                    // at＝上次抓取的時刻（節流用，見 _load / _paint）；
                    // visible＝觀察者上次看到的可見狀態（起始 true 的理由見
                    // _visitVisible）
                    at: 0, visible: true };
    if (!wired) {
        // 事件委派掛一次就好 —— 每次重畫都重掛會累積成一次點擊送 N 個請求
        const on = (ev) => _onHit(host, ev);
        host.addEventListener('click', on);
        host.addEventListener('keydown', on);
        // 進名冊 —— observe() 當下的那一次回呼順便把上一個死掉的清掉（見 _sweep）
        _watched.add(host);
        _obs.observe(host);
    }
    if (!projectId) {
        _msg(host, HEAL_HTML);
        return;
    }
    _msg(host, '載入中…');
    await _load(host, projectId);
}

// 重抓門檻：切回分頁比這還新就不重抓（§14.4「開分頁抓一次＋切回分頁重抓」）。
// 有門檻是因為分頁來回是很便宜的動作 —— 沒有它，在兩個分頁之間點五下就是
// 五趟那支不便宜的聚合查詢。
const _FRESH_MS = 15000;

/**
 * 「切回分頁重抓」（§14.4.3；v1 明確不做輪詢）。整個機制與設計理由在那一節，
 * 這裡只留三條會被「順手清乾淨」弄壞的：
 *
 * 🔴 **ResizeObserver 不是 IntersectionObserver**：IO 講「有沒有進到視窗裡」，
 * 所以捲到畫面外的 host 藏起來再顯示，兩次都是 not-intersecting、它根本不會
 * 叫。RO 講版面盒，與捲動位置無關。（換成 IO 的症狀是「單跑過、跟別支一起
 * 跑就紅」的假 flaky。）
 *
 * 🔴 **一個 RO 服務全體**，而收拾靠 `observe()` 當下必定觸發的那一次回呼 ——
 * 「開下一個提案的進度分頁」就是清掉上一個死掉的那個的時機。不能改成「等那
 * 個 host 自己的回呼發現自己被移除」：它常常是**已經** display:none 才被移除
 * （看完進度 → 切去別的分頁 → 關掉 overlay），那一刻尺寸沒變，回呼不會再來。
 *
 * ⚠️ RO 的回報是**合併**的：同一個 frame 內藏了又顯示只會收到一次「現在看得
 * 見」，不算轉換 → 不重抓（對使用者是對的，他根本沒離開）。寫測試時要真的等
 * `visible` 變 false 再顯示回來。
 */
const _watched = new Set();
const _obs = new ResizeObserver(_sweep);
document.addEventListener('visibilitychange', _sweep);

function _sweep() {
    for (const host of _watched) {
        if (!host.isConnected) {
            _obs.unobserve(host);
            _watched.delete(host);
            continue;
        }
        const f = host.__flow;
        // 「看得見」認得的只有 display:none（offsetParent 為 null 的另一個成因
        // 是 host 自己 position:fixed —— 這一格不是；SPA 的 .prop-ov 是 fixed，
        // 但那是祖先，不影響判讀）。改用 visibility / content-visibility /
        // height:0 藏的話這裡會判成看得見，重抓就靜靜地不發生了。
        const now = !document.hidden && !!host.offsetParent;
        // 起始值 true（見 renderFlow）：observe() 當下那次回呼才不會被當成
        // 「切回來了」而每次開分頁都抓兩趟。
        const back = now && !f.visible;
        f.visible = now;
        // 不會變成 _load → _paint → RO → _load 的迴圈，兩道各自成立的閘：
        // (1) 重畫前 `visible` 已經是 true，所以 back 是 false；
        // (2) _load/_paint 都蓋 `at`，所以那次回呼一定落在門檻內。
        if (back && f.pid && Date.now() - f.at >= _FRESH_MS) _load(host, f.pid);
    }
}

/**
 * 沒有殼專案時的自癒 CTA（§14.4.2）。打 `POST /proposals/{id}/project/shell`
 * —— 一個有名字的動作，不是借 `PUT /{id}` 的尾巴（理由在那支端點的 docstring）。
 *
 * 規格草案寫的是「補客戶 CTA」，但那是 `crm_projects.client_id` 放寬之前的
 * 前提 —— 現在客戶可空、殼專案不等客戶，所以按鈕直接做真正要做的事。
 */
const HEAL_LABEL = '建立專案並開始追蹤';
const HEAL_HTML =
    '這個提案還沒有關聯專案，所以沒有進度可以追。'
    + '<br>建一個殼專案把它放進管線，五軌訊號就會開始自己亮。'
    + `<div><button class="pflow-adv" data-heal>${HEAL_LABEL}</button></div>`;

/** 自癒：建殼專案 → 原地變成真的進度頁。（鎖與解鎖由 _onHit 負責） */
async function _heal(host, btn) {
    const f = host.__flow;
    btn.textContent = '建立中…';
    try {
        const d = await tfetch(
            `/api/v1/proposals/${encodeURIComponent(f.propId)}/project/shell`,
            { method: 'POST' });
        if (!host.isConnected) return;
        f.pid = d.project_id;
        await _settled(host, f);
    } catch (e) {
        btn.textContent = HEAL_LABEL;
        alert('建立專案失敗：' + (e.message || e));
    }
}

/**
 * 動完後端之後的收尾，兩個寫入路徑共用。
 *
 * 順序是**先通知呼叫端、再重畫自己**：呼叫端有權把這個 host 拆掉（SPA 就是
 * 整個重開詳情），拆掉之後那趟 `/flow`（一列 18 個相關子查詢）問到的東西會
 * 直接被丟進垃圾桶 —— 而且使用者是在等它跑完才看到重開的詳情。
 * await 是必要的：呼叫端的拆除多半在它自己的 await 之後才發生，不等它的話
 * `isConnected` 這一刻永遠還是 true（第二輪實測，回歸測試釘著）。
 *
 * 呼叫端沒回傳 promise 也不會壞，只是退回「多抓一趟然後丟掉」—— 這是省一趟
 * 查詢的最佳化，不是正確性的前提。
 */
async function _settled(host, f) {
    if (f.onChanged) await f.onChanged();
    if (!host.isConnected) return;
    await _load(host, f.pid);
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
    // 🔴 時間戳蓋在**送出**的這一刻，不是拿到的那一刻：`at` 節流的是「要不要
    // 再去問一次」。蓋在 _paint 的話，抓的期間 at 還是舊值（來回切分頁就是
    // N 個並發的聚合查詢打同一個 5 條的連線池），而抓失敗時它永遠是 0 ——
    // 之後每次切回來都無節流地重打。
    host.__flow.at = Date.now();
    try {
        const d = await tfetch(`/api/v1/crm/projects/${encodeURIComponent(projectId)}/flow`);
        if (host.isConnected) _paint(host, d);
    } catch (e) {
        if (host.isConnected) _msg(host, '進度載入失敗：' + esc(e.message || e));
    }
}

/** 純 dispatcher：兩條路徑各自一支，這裡只決定走哪條。
 *  「去完成」不在這裡 —— 它是一條純 `<a>`（見 SPA 常數）。 */
async function _onHit(host, ev) {
    // 兩顆按鈕同一條路：找到、確認在自己家裡、擋掉預設、鎖住、交給對應的
    // 處理函式。鎖在這裡而不是各自的開頭 —— 兩邊原本連解鎖的時機都不一樣。
    const btn = ev.target.closest?.('[data-advance],[data-heal]');
    if (btn && host.contains(btn) && ev.type === 'click') {
        ev.preventDefault();
        if (btn.disabled) return;          // 連點兩下不送兩次
        btn.disabled = true;
        try {
            await (btn.hasAttribute('data-heal') ? _heal : _advance)(host, btn);
        } finally {
            // 成功路徑上 _load 已經重畫、這顆按鈕早被換掉（detached）
            if (btn.isConnected) btn.disabled = false;
        }
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
    const f = host.__flow;
    const next = btn.dataset.advance;
    // 鎖在 _onHit 就上了（在開對話框之前）—— 不然連點兩下會開出兩個對話框、
    // 送出兩次 PATCH
    try {
        // 「這次會不會用到成案原因」由後端宣告（它與真正去標的那支共用同一
        // 份判定）—— 前端自己算的話，多筆衛星提案時會白問一次，使用者打的
        // 字被靜默丟掉
        const reason = await _confirmAdvance(next, f.missing, f.collectsReason, f.ctx);
        if (reason === null) return;      // 取消
        const body = { status: next };
        if (reason) body.outcome_reason = reason;
        await tfetch(`/api/v1/crm/projects/${encodeURIComponent(f.pid)}/status`,
                     { method: 'PATCH', json: body });
        // 階段變了 → 通知呼叫端 + 重抓（推進會連動衛星提案狀態與一堆訊號，
        // 前端自己推導只會跟後端各算各的）。順序與拆除的處理見 _settled。
        await _settled(host, f);
    } catch (e) {
        // 422 帶 code 的（如轉未成案要原因）後端訊息已經說清楚了，直接轉述
        alert('推進失敗：' + (e.message || e));
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

/**
 * 頁尾那一句。三種情況共用一格且互斥 —— 分開寫的話「已凍結」與「需要權限
 * 才能標記」會同時出現，而它們互相矛盾（凍結時有權限的人一樣勾不動）。
 *
 * 🔴 終態的名字用**後端送來的 `status`**，不寫死「歸檔」：終態有哪些是
 * `core.project_flow.TERMINAL` 的政策，多一個終態時這裡不該是第二個要記得
 * 改的地方（不改的話，畫面會篤定地告訴使用者一個錯的狀態）。
 */
function _noteHtml(st, canCheck) {
    if (st.is_terminal) {
        return `<div class="pflow-note">這個案子已經${st.is_lost ? '結束' : ''}`
             + `「${esc(st.status || '')}」，`
             + `進度只供查閱：里程碑不再能勾，未完成的項目也不再提示「去完成」。</div>`;
    }
    return canCheck ? ''
        : '<div class="pflow-note">里程碑（開拍／剪輯完成）需要專案管理權限才能標記。</div>';
}

function _paint(host, d) {
    const st = d.stage || {};
    // 終態（未成案／歸檔）＝這個案子已經結束 → 凍結成唯讀（§14.4 邊界態）。
    //
    // 🔴 **凍結是畫面的事，不是守衛**：勾選端點仍然收得下（歸檔後才發現某個
    // 里程碑記錯，admin 要補得回來），推進端點也還在。這裡做的是不再把一個
    // 結束的案子畫成待辦清單 —— 與這功能其他地方一致的軟性口徑。
    //
    // 用後端給的 `is_terminal` 而不是自己比對「未成案」「歸檔」兩個字面值：
    // 哪些狀態算終態是 core.project_flow 的政策（TERMINAL），加一個終態時
    // 前端不該是第二個要記得改的地方。
    const frozen = !!st.is_terminal;
    const can = !!d.can_check && !frozen;
    const ctx = { ...host.__flow.ctx, links: d.links || {}, frozen };
    const missing = d.missing || { blocking: [], advisory: [] };
    // 推進對話框要的三樣東西 —— 存最後一次的**權威**資料，別讓它去讀畫面
    // （讀畫面的話，樂觀更新那一瞬間的 class 會被當成事實）。刻意不存整包
    // payload：tracks 佔了 4KB 的 88%，畫成 HTML 之後就沒人要了。
    // `at` 也在這裡重蓋一次：勾選端點回的是**同一份**權威 payload
    // （routers/crm/flow.py 的慣例），所以勾完之後這一格跟剛抓過一樣新。
    // 只在 _load 送出時蓋的話，勾完 14 秒切走再切回來會白抓一趟兩秒前的資料。
    host.__flow = { ...host.__flow, missing, ctx, at: Date.now(),
                    collectsReason: !!d.collects_outcome_reason };
    host.innerHTML = `<div class="pflow${frozen ? ' frozen' : ''}">
        ${_stageHtml(st, _advanceHtml(st, !!d.can_advance))}
        ${(d.tracks || []).map(t => _trackHtml(t, can, ctx)).join('')}
        ${_missingHtml(missing, st.next, ctx)}
        ${_noteHtml(st, can)}
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
async function _confirmAdvance(next, { blocking, advisory }, collectsReason, ctx) {
    const { openDialog, field } = await import('./prop-dialog.js');
    // 缺項的「去完成」在對話框裡**一律開新分頁**：原地切走會把對話框連同你
    // 正在做的決定一起毀掉。新分頁去完成、回來仍在原處。
    const dctx = { ...ctx, newTab: true };
    const list = (arr, cls) => arr.length
        ? `<ul class="pflow-mlist ${cls}">`
          + arr.map(m => `<li>${_goName(m, dctx)}</li>`).join('') + '</ul>' : '';
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
