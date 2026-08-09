/**
 * plan-matrix.js — 提案企劃矩陣元件（docs/PROPOSAL_PLANNER.md §6.4/§7.3）
 *
 * 同一份 UI 活在兩個色系：CRM SPA overlay（深色）與 /proposal-plan.html（官網白底）。
 * 主題化 = 元件自帶 --plc-* CSS 變數（深色為預設，定義在 .plc 上），
 * 白底靠 html.plan-theme-light 覆寫（specificity 一定贏）— 比照 media-log 元件做法。
 *
 * 共編：逐格 debounce PATCH（800ms）+ blur flush；409 → 並列兩版本讓人決定，
 * 絕不自動合併。updated_at 由伺服器蓋章，PATCH 回傳後更新 base。
 * 空白格是合法狀態 — 本元件沒有任何「少填了」的提示（決策見 doc §5）。
 *
 * 用法：
 *   import { renderPlan } from './plan-matrix.js';
 *   renderPlan(container, { proposalId, plan, onPlanStarted?, readonly?, fetcher? });
 *   plan=null → 顯示方法論按鈕列＋範例；readonly=true → 唯讀渲染（範例模式）。
 */

import { PLAN_TEMPLATES, PLAN_EXAMPLES } from './plan-templates.js';
import { tfetch } from './prop-fetch.js';          // 共用 fetcher（帶 err.status/detail — 409 UI 依賴）
// esc 走 js/shared/utils.js 而不是 crm-utils —— 公開共編頁要在 NAS 對外容器
// serve（master 關機也開得了），那台只 serve tabs/proposals 與 js/shared 兩個
// 子目錄；從 ../crm/ 拉東西會讓客戶那頁在 NAS 上直接載入失敗。
import { ensureStyle, esc } from '../../js/shared/utils.js';

const API = '/api/v1/proposals';

// ── 樣式（注入一次） ─────────────────────────────────────
const STYLE_ID = 'plc-style';
const CSS = `
.plc { /* 深色預設（SPA overlay） */
  --plc-bg: #1e1e1e; --plc-card: #262626; --plc-line: #3a3a3a;
  --plc-ink: #ddd; --plc-sub: #888; --plc-accent: #3b82f6;
  --plc-head: #2e2e2e; --plc-cell: #222;
  color: var(--plc-ink); font-size: 13px; text-align: left;
}
html.plan-theme-light .plc { /* 官網白底（獨立網址） */
  --plc-bg: #fff; --plc-card: #fafafa; --plc-line: #e5e5e5;
  --plc-ink: #262626; --plc-sub: #737373; --plc-accent: #c9372c;
  --plc-head: #f5f5f4; --plc-cell: #fff;
}
.plc * { box-sizing: border-box; }
.plc .plc-theme { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; }
.plc .plc-theme label { font-size: 11px; letter-spacing: .2em; color: var(--plc-sub); white-space: nowrap; }
.plc .plc-theme input { flex: 1; background: var(--plc-cell); color: var(--plc-ink);
  border: 1px solid var(--plc-line); border-radius: 2px; padding: 8px 10px; font-size: 14px; }
.plc .plc-theme input:focus { border-color: var(--plc-accent); outline: none; }
.plc .plc-grid { display: grid; grid-template-columns: 90px repeat(var(--plc-cols, 3), 1fr); gap: 1px;
  background: var(--plc-line); border: 1px solid var(--plc-line); }  /* 欄數由模板決定，不硬編 */
.plc .plc-hcell { background: var(--plc-head); padding: 10px; }
.plc .plc-hcell .t { font-weight: 600; font-size: 13px; }
.plc .plc-hcell .lead { color: var(--plc-sub); font-size: 11px; margin-top: 3px; }
.plc .plc-rowhead { background: var(--plc-head); padding: 10px; display: flex;
  flex-direction: column; justify-content: center; }
.plc .plc-rowhead .t { font-weight: 600; font-size: 12px; }
.plc .plc-rowhead .hint { color: var(--plc-sub); font-size: 10px; margin-top: 4px; line-height: 1.5; }
.plc .plc-cell { background: var(--plc-cell); padding: 6px; }
/* 表格排版時欄標題就在正上方，格子自己不必再標一次 —— 堆疊後才顯示（見下） */
.plc .plc-cell-lens { display: none; }
.plc .plc-cell textarea, .plc .plc-dir textarea { width: 100%; min-height: 110px; resize: none;
  box-sizing: border-box; overflow-y: hidden;
  background: transparent; color: var(--plc-ink); border: 1px solid transparent;
  border-radius: 2px; padding: 6px; font-size: 12.5px; line-height: 1.7; font-family: inherit; }
.plc .plc-dir textarea { min-height: 60px; }
.plc .plc-cell textarea:focus, .plc .plc-dir textarea:focus { border-color: var(--plc-accent); outline: none; }
.plc .plc-cell textarea::placeholder { color: var(--plc-sub); opacity: .6; }
.plc .plc-premises { margin-top: 8px; }
.plc .plc-premises summary { cursor: pointer; font-size: 11px; color: var(--plc-sub); user-select: none; }
.plc .plc-premise { border: 1px solid var(--plc-line); border-radius: 2px;
  padding: 6px 8px; margin-top: 6px; background: var(--plc-card); }
.plc .plc-premise .pt { font-size: 11.5px; font-weight: 600; }
.plc .plc-premise .pb { font-size: 11px; color: var(--plc-sub); margin-top: 2px; line-height: 1.6; }
.plc .plc-premise.decision .pt::before { content: "⚖ "; }
.plc .plc-field { display: flex; gap: 6px; align-items: center; margin-top: 8px; }
.plc .plc-field label { font-size: 10.5px; color: var(--plc-sub); white-space: nowrap; }
.plc .plc-field input { flex: 1; min-width: 0; background: var(--plc-cell); color: var(--plc-ink);
  border: 1px solid var(--plc-line); border-radius: 2px; padding: 4px 6px; font-size: 11.5px; }
.plc .plc-motto { font-size: 11px; color: var(--plc-sub); font-style: italic; margin-top: 4px; }
.plc .plc-dirs { margin-top: 22px; }
.plc .plc-dirs h4 { margin: 0 0 4px; font-size: 14px; }
.plc .plc-dirs .plc-dirs-sub { color: var(--plc-sub); font-size: 11.5px; margin-bottom: 12px; line-height: 1.7; }
.plc .plc-dir { border: 1px solid var(--plc-line); border-radius: 2px; padding: 10px 12px;
  margin-bottom: 10px; background: var(--plc-card); }
.plc .plc-dir .q { font-size: 12px; margin-bottom: 6px; }
.plc .plc-dir .q b { font-size: 12px; margin-right: 8px; }
.plc .plc-tip { margin: 2px 6px 4px; }
.plc .plc-tip summary { cursor: pointer; font-size: 10.5px; color: var(--plc-sub); user-select: none; opacity: .75; }
.plc .plc-tip summary:hover { opacity: 1; }
.plc .plc-tip div { font-size: 11px; color: var(--plc-sub); line-height: 1.7; padding: 6px 8px;
  border: 1px solid var(--plc-line); border-radius: 2px; background: var(--plc-card); margin-top: 4px; }
.plc .plc-summary { border: 1px solid var(--plc-line); border-radius: 2px; padding: 10px 12px;
  margin-top: 14px; background: var(--plc-card); }
.plc .plc-summary .mt { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.plc .plc-summary .plc-field { margin-top: 6px; }
.plc .plc-summary .plc-field label { min-width: 110px; }
.plc .plc-summary .sh { font-size: 11px; color: var(--plc-sub); margin-top: 8px; line-height: 1.7; }
.plc a.plc-tb-btn { text-decoration: none; display: inline-block; }
.plc .plc-memo { border: 1px solid var(--plc-line); border-radius: 2px; padding: 10px 12px;
  margin-top: 14px; background: var(--plc-card); }
.plc .plc-memo .mt { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.plc .plc-memo textarea { width: 100%; min-height: 90px; resize: none; background: transparent;
  box-sizing: border-box; overflow-y: hidden;
  color: var(--plc-ink); border: 1px solid transparent; border-radius: 2px; padding: 6px;
  font-size: 12.5px; line-height: 1.7; font-family: inherit; }
.plc .plc-memo textarea:focus { border-color: var(--plc-accent); outline: none; }
.plc .plc-memo textarea::placeholder { color: var(--plc-sub); opacity: .6; }
.plc .plc-save { position: sticky; bottom: 0; text-align: right; font-size: 11px;
  color: var(--plc-sub); padding: 4px 0; pointer-events: none; }
.plc .plc-save.err { color: #e05252; }
.plc .plc-conflict { border: 1px solid #e05252; border-radius: 2px; margin-top: 6px;
  padding: 8px; font-size: 11.5px; background: var(--plc-card); }
.plc .plc-conflict .ct { color: #e05252; font-weight: 600; margin-bottom: 4px; }
.plc .plc-conflict pre { white-space: pre-wrap; margin: 4px 0; padding: 6px;
  background: var(--plc-cell); border: 1px solid var(--plc-line); font-family: inherit; font-size: 11.5px; }
.plc .plc-conflict button { margin-right: 8px; margin-top: 4px; cursor: pointer;
  background: none; border: 1px solid var(--plc-line); border-radius: 2px;
  color: var(--plc-ink); padding: 3px 10px; font-size: 11px; }
.plc .plc-conflict button:hover { border-color: var(--plc-accent); color: var(--plc-accent); }
.plc .plc-start { text-align: center; padding: 30px 10px; }
.plc .plc-start .st { font-size: 14px; margin-bottom: 6px; }
.plc .plc-start .ss { color: var(--plc-sub); font-size: 12px; margin-bottom: 20px; }
.plc .plc-start button { display: block; margin: 0 auto 10px; cursor: pointer; min-width: 280px;
  background: var(--plc-card); border: 1px solid var(--plc-line); border-radius: 2px;
  color: var(--plc-ink); padding: 12px 18px; font-size: 13px; }
.plc .plc-start button:hover { border-color: var(--plc-accent); color: var(--plc-accent); }
.plc .plc-start button.ex { font-size: 11.5px; padding: 7px 14px; min-width: 280px; color: var(--plc-sub); }
.plc .plc-banner { border: 1px solid var(--plc-accent); border-radius: 2px; padding: 8px 12px;
  margin-bottom: 14px; font-size: 12px; display: flex; justify-content: space-between; align-items: center; }
.plc .plc-banner button { cursor: pointer; background: none; border: 1px solid var(--plc-line);
  border-radius: 2px; color: var(--plc-ink); padding: 3px 10px; font-size: 11px; }
.plc .plc-toolbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.plc .plc-tb-label { font-size: 11px; color: var(--plc-sub); }
.plc .plc-tb-btn { cursor: pointer; background: var(--plc-card); border: 1px solid var(--plc-line);
  border-radius: 2px; color: var(--plc-ink); padding: 4px 10px; font-size: 11px; max-width: 220px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.plc .plc-tb-btn:hover { border-color: var(--plc-accent); color: var(--plc-accent); }
.plc .plc-tb-btn.danger:hover { border-color: #e05252; color: #e05252; }
.plc .plc-share { border: 1px solid var(--plc-line); border-radius: 2px; padding: 10px 12px;
  margin-bottom: 12px; background: var(--plc-card); }
.plc .plc-share .sh-hint { font-size: 11.5px; color: var(--plc-sub); margin-bottom: 8px; line-height: 1.7; }
.plc .plc-share .sh-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.plc .plc-share .sh-link { flex: 1; min-width: 220px; background: var(--plc-cell); color: var(--plc-ink);
  border: 1px solid var(--plc-line); border-radius: 2px; padding: 5px 8px; font-size: 11.5px; }
@media (max-width: 900px) { /* 窄螢幕：縱向堆疊（排版退化，非流程限制） */
  .plc .plc-grid { grid-template-columns: 1fr; }
  .plc .plc-rowhead { flex-direction: row; gap: 10px; align-items: baseline; }
  /* 🔴 堆疊之後每一格要自己說「我在回答哪一個視角」——欄標題只在整份矩陣
     最上面出現一次，捲到第 8 格時早就看不到了，格子等於沒有標籤。 */
  .plc .plc-cell-lens { display: block; font-size: 11.5px; font-weight: 600;
    color: var(--plc-sub); padding: 2px 6px 0; }
  /* 空格不要各佔 110px：12 格全空等於要捲過 1300px 才看得到下一段。
     有內容的格子照樣由 _autoGrow 撐開。 */
  .plc .plc-cell textarea { min-height: 46px; }
  /* 還沒填的格子收成一行 —— 但**顯示的是提問本身的第一行**，不是「（空）」：
     那句提問就是方法論，新開一份企劃時 12 格全是空的，正是最需要看到它的
     時候。點下去（focus）就展開回完整高度，看得到整段提問也填得下去。

     :placeholder-shown 就是「這格是空的」，所以不需要任何 JS 去追狀態
     （這整段 CSS 住在 template literal 裡，註解也不能出現反引號）。
     height 要 !important 是因為 _autoGrow 把量到的 scrollHeight 寫在
     inline style 上（而 placeholder 也會被算進 scrollHeight）—— 那是它
     唯一能寫的地方，不是可以繞過的實作細節。 */
  .plc .plc-cell textarea:placeholder-shown:not(:focus) {
    /* nowrap 才是關鍵：靠高度去切會露出第二行的頭（textarea 的可見區含
       padding-bottom，切不乾淨），看起來像壞掉而不像「還有下文」。
       不換行 → 提問只佔一行，右邊被裁掉的斷口本身就是「還有」的訊號。 */
    height: 44px !important; min-height: 44px;
    white-space: nowrap; overflow: hidden;
  }
}
/* 手機：工具列裡的「方法論筆記」是**長得像按鈕的 <a>**，頁面層那條
   「button 至少 44px」抓不到它 —— 元件自己把它做成合格的觸控目標。 */
@media (max-width: 720px) {
  .plc a.plc-tb-btn { min-height: 44px; display: inline-flex; align-items: center; }
}
`;


// ── 主入口 ───────────────────────────────────────────────
// opts: { proposalId, plan, fetcher?, onPlanStarted?, canShare?, getGuestName?,
//         endpoints?: {cell, meta, share}, refetch?: async()=>plan }
// 公開共編頁傳自己的 endpoints（/shared/{token}/...）+ refetch；登入路徑用預設。
export function renderPlan(container, opts) {
    ensureStyle(STYLE_ID, CSS);
    opts = { fetcher: tfetch, ...opts };
    if (!opts.endpoints) {
        opts.endpoints = {
            cell: `${API}/${opts.proposalId}/plan/cell`,
            meta: `${API}/${opts.proposalId}/plan/meta`,
            share: `${API}/${opts.proposalId}/plan/share`,
        };
    }
    if (!opts.refetch) {
        opts.refetch = async () => (await opts.fetcher(`${API}/${opts.proposalId}`)).proposal.plan;
    }
    container.classList.add('plc');
    // 沒 template_id = 企劃未開始（可能是 null，也可能是「先開了公開頁」的
    // share_token 殼 — 後端 PUT /plan 會保留殼裡的 token，這裡照常給開始畫面）
    if (!opts.plan || !opts.plan.template_id) { _renderStart(container, opts); return; }
    const tpl = PLAN_TEMPLATES[opts.plan.template_id];
    if (!tpl) {
        container.innerHTML = `<div class="plc-start"><div class="ss">未知的方法論模板：${esc(opts.plan.template_id)}（可能是新版程式才有的模板）</div></div>`;
        return;
    }
    _renderMatrix(container, opts, tpl, {});
}

// ── 範例唯讀檢視（起始畫面與編輯工具列共用） ──────────────
function _openExample(container, opts, exIdx, backFn) {
    const ex = PLAN_EXAMPLES[exIdx];
    _renderMatrix(container, { ...opts, plan: _exampleToPlan(ex) },
                  PLAN_TEMPLATES[ex.template_id], { readonly: true, exampleBack: backFn });
}

// ── 未開始：方法論按鈕列 + 範例 ──────────────────────────
function _renderStart(container, opts) {
    const { proposalId, fetcher, onPlanStarted } = opts;
    container.innerHTML = `
        <div class="plc-start">
            <div class="st">用一套方法論開始企劃</div>
            <div class="ss">選一套方法論，把想法攤在矩陣上。之後上了新課，這裡會長出新按鈕。</div>
            ${Object.values(PLAN_TEMPLATES).map(t =>
                `<button data-tid="${esc(t.id)}">📋 ${esc(t.label)}</button>`).join('')}
            <div class="ss" style="margin:18px 0 8px;">或先看範例（唯讀，不會存檔）</div>
            ${PLAN_EXAMPLES.map((ex, i) =>
                `<button class="ex" data-ex="${i}">${esc(ex.label)}</button>`).join('')}
        </div>`;
    container.querySelectorAll('button[data-tid]').forEach(btn => btn.addEventListener('click', async () => {
        const tpl = PLAN_TEMPLATES[btn.dataset.tid];
        btn.disabled = true;
        try {
            const d = await fetcher(`${API}/${proposalId}/plan`, { method: 'PUT', json: {
                template_id: tpl.id, template_version: tpl.version,
                theme: '', cells: {}, directions: {}, field_values: {},
            } });
            if (onPlanStarted) onPlanStarted(d.plan);
            renderPlan(container, { ...opts, plan: d.plan });
        } catch (e) { alert('開始企劃失敗：' + (e.message || e)); btn.disabled = false; }
    }));
    container.querySelectorAll('button[data-ex]').forEach(btn => btn.addEventListener('click', () => {
        _openExample(container, opts, +btn.dataset.ex, () => _renderStart(container, opts));
    }));
}

function _exampleToPlan(ex) {
    // 唯讀渲染只讀 answer；updated_at/by 缺省由 cellOf/whoTitle 的 falsy 分支吸收
    const wrap = (v) => ({ answer: v });
    const wrapAll = (o) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, wrap(v)]));
    return { template_id: ex.template_id, template_version: ex.template_version,
             theme: ex.theme,
             cells: Object.fromEntries(Object.entries(ex.cells || {}).map(([l, hows]) => [l, wrapAll(hows)])),
             directions: wrapAll(ex.directions),
             field_values: ex.field_values || {} };
}

// ── 矩陣本體 ─────────────────────────────────────────────
function _renderMatrix(container, opts, tpl, { readonly = false, exampleBack = null }) {
    const { plan } = opts;
    const cellOf = (lens, how) => (plan.cells?.[lens]?.[how]) || { answer: '', updated_at: null, updated_by: '' };
    const dirOf = (how) => (plan.directions?.[how]) || { answer: '', updated_at: null, updated_by: '' };
    const fieldOf = (lens, f) => (plan.field_values?.[lens]?.[f]) || '';
    const ro = readonly ? 'readonly' : '';
    const whoTitle = (e) => e.updated_by ? ` title="最後編輯：${esc(e.updated_by)}"` : '';

    container.innerHTML = `
        ${readonly ? `<div class="plc-banner"><span>📖 範例（唯讀，不會存檔）</span>${exampleBack ? '<button id="plc-ex-back">← 返回</button>' : ''}</div>` : `
        <div class="plc-toolbar">
            <span class="plc-tb-label">📖 參考範例：</span>
            ${PLAN_EXAMPLES.map((ex, i) =>
                `<button class="plc-tb-btn" data-ex="${i}" title="${esc(ex.label)}">${esc(ex.theme)}</button>`).join('')}
            ${tpl.notes_url ? `<a class="plc-tb-btn" href="${esc(tpl.notes_url)}" target="_blank" rel="noopener">📚 方法論筆記</a>` : ''}
            <span style="flex:1"></span>
            ${opts.canShare ? `<button class="plc-tb-btn" id="plc-share-btn">${plan.share_token ? '🔗 公開共編中' : '🔒 未公開'}</button>` : ''}
        </div>
        ${opts.canShare ? '<div id="plc-share-panel" style="display:none;"></div>' : ''}`}
        <div class="plc-theme">
            <label>${esc(tpl.theme_label)}</label>
            <input id="plc-theme" value="${esc(plan.theme || '')}" placeholder="${esc(tpl.theme_placeholder)}" ${ro}>
        </div>
        <div class="plc-grid" style="--plc-cols:${tpl.lenses.length}">
            <div class="plc-hcell"><div class="t">${esc(tpl.hows_label)}</div></div>
            ${tpl.lenses.map(lens => `
                <div class="plc-hcell">
                    <div class="t">${esc(lens.label)}</div>
                    <div class="lead">${esc(lens.lead)}</div>
                    ${lens.motto ? `<div class="plc-motto">${esc(lens.motto)}</div>` : ''}
                    ${lens.premises.length ? `
                    <details class="plc-premises">
                        <summary>思考錨點（${lens.premises.length}）</summary>
                        ${lens.premises.map(p => `
                            <div class="plc-premise ${p.kind}">
                                <div class="pt">${esc(p.title)}</div>
                                <div class="pb">${esc(p.body)}</div>
                            </div>`).join('')}
                    </details>` : ''}
                    ${lens.fields.map(f => `
                        <div class="plc-field"><label>${esc(f.label)}</label>
                        <input data-kind="field" data-lens="${esc(lens.key)}" data-field="${esc(f.key)}"
                               value="${esc(fieldOf(lens.key, f.key))}" placeholder="${esc(f.placeholder)}" ${ro}></div>`).join('')}
                </div>`).join('')}
            ${tpl.hows.map(how => `
                <div class="plc-rowhead"><div class="t">${esc(how.label)}</div><div class="hint">${esc(how.hint)}</div></div>
                ${tpl.lenses.map(lens => {
                    const e = cellOf(lens.key, how.key);
                    const tip = lens.tips?.[how.key] || '';
                    return `<div class="plc-cell">
                        <div class="plc-cell-lens">${esc(lens.label)}</div>
                        <textarea data-kind="cell" data-lens="${esc(lens.key)}" data-how="${esc(how.key)}"
                            placeholder="${esc(lens.prompts[how.key] || '')}" ${ro}${whoTitle(e)}></textarea>
                        ${tip && !readonly ? `<details class="plc-tip"><summary>💡 方法提示</summary><div>${esc(tip)}</div></details>` : ''}
                    </div>`;
                }).join('')}`).join('')}
        </div>
        ${(() => {
            const sf = tpl.summary_fields || [];
            if (!sf.length) return '';
            const anyVal = sf.some(f => (plan.field_values?.plan?.[f.key] || ''));
            if (readonly && !anyVal) return '';
            return `
        <div class="plc-summary">
            <div class="mt">${esc(tpl.summary_label || '取捨')}</div>
            ${sf.map(f => `
                <div class="plc-field"><label>${esc(f.label)}</label>
                <input data-kind="field" data-lens="plan" data-field="${esc(f.key)}"
                       value="${esc(fieldOf('plan', f.key))}" placeholder="${esc(f.placeholder)}" ${ro}></div>`).join('')}
            ${tpl.summary_hint ? `<div class="sh">${esc(tpl.summary_hint)}</div>` : ''}
        </div>`;
        })()}
        ${(readonly && !(plan.memo || '')) ? '' : `
        <div class="plc-memo">
            <div class="mt">📝 備忘</div>
            <textarea data-kind="memo" placeholder="拍攝備註、待查事項、開會結論…想到什麼放什麼（整份企劃共用一格，會自動儲存）" ${ro}${plan.memo_updated_by ? ` title="最後編輯：${esc(plan.memo_updated_by)}"` : ''}></textarea>
        </div>`}
        ${(tpl.directions || []).length ? `
        <div class="plc-dirs">
            <h4>${esc(tpl.directions_label || '')}</h4>
            <div class="plc-dirs-sub">${esc(tpl.directions_lead || '')}</div>
            ${tpl.directions.map(d => {
                const e = dirOf(d.key);
                return `<div class="plc-dir">
                    <div class="q"><b>${esc(d.label)}</b>${esc(d.question)}</div>
                    <textarea data-kind="direction" data-how="${esc(d.key)}" ${ro}${whoTitle(e)}></textarea>
                </div>`;
            }).join('')}
        </div>` : ''}
        ${readonly ? '' : '<div class="plc-save" id="plc-save"></div>'}`;

    // textarea 內容用 DOM property 賦值（不走模板內插 — XSS 防線之一）
    container.querySelectorAll('textarea[data-kind]').forEach(ta => {
        const k = ta.dataset.kind;
        ta.value = k === 'memo' ? (plan.memo || '')
            : (ta.dataset.lens ? cellOf(ta.dataset.lens, ta.dataset.how) : dirOf(ta.dataset.how)).answer;
        _autoGrow(ta);
        ta.addEventListener('input', () => _autoGrow(ta));   // 內容多長格子就多長
    });
    if (readonly) {
        container.querySelector('#plc-ex-back')?.addEventListener('click', exampleBack);
        return;
    }
    // 工具列：範例（唯讀檢視，返回時 refetch 保住共編期間別人的更新）+ 公開共編開關
    container.querySelectorAll('.plc-toolbar [data-ex]').forEach(btn => btn.addEventListener('click', () => {
        _openExample(container, opts, +btn.dataset.ex, async () => {
            let fresh = plan;
            try { fresh = await opts.refetch(); } catch { /* 離線時退回記憶中的 plan */ }
            renderPlan(container, { ...opts, plan: fresh });
        });
    }));
    if (opts.canShare) _wireSharePanel(container, opts);
    _wireEditing(container, opts);
}

// ── 公開共編開關（開放=鑄 token 連結；關閉=撤銷、連結立即失效） ──
function _wireSharePanel(container, opts) {
    const btn = container.querySelector('#plc-share-btn');
    const panel = container.querySelector('#plc-share-panel');
    if (!btn || !panel) return;
    const shareUrl = (tok) => `${location.origin}/proposal-plan.html?t=${encodeURIComponent(tok)}`;

    function paint() {
        const tok = opts.plan.share_token || '';
        btn.textContent = tok ? '🔗 公開共編中' : '🔒 未公開';
        if (panel.style.display === 'none') return;
        panel.innerHTML = tok ? `
            <div class="plc-share">
                <div class="sh-hint">已開放：拿到連結的人<b>不用登入</b>即可共同編輯這份企劃。停用後連結立即失效。</div>
                <div class="sh-row">
                    <input class="sh-link" readonly>
                    <button class="plc-tb-btn" data-act="copy">複製連結</button>
                    <button class="plc-tb-btn danger" data-act="off">停用共編</button>
                </div>
            </div>` : `
            <div class="plc-share">
                <div class="sh-hint">目前僅限登入帳號編輯。開放後會產生一條公開連結，拿到的人<b>不用登入</b>即可共同編輯。</div>
                <div class="sh-row"><button class="plc-tb-btn" data-act="on">開放公開共編</button></div>
            </div>`;
        if (tok) panel.querySelector('.sh-link').value = shareUrl(tok);   // DOM property — 不進模板字串
        panel.querySelector('[data-act="copy"]')?.addEventListener('click', async (e) => {
            try { await navigator.clipboard.writeText(shareUrl(opts.plan.share_token)); e.target.textContent = '已複製 ✓'; }
            catch { panel.querySelector('.sh-link').select(); document.execCommand('copy'); e.target.textContent = '已複製 ✓'; }
        });
        panel.querySelector('[data-act="on"]')?.addEventListener('click', async () => {
            try {
                const d = await opts.fetcher(opts.endpoints.share, { method: 'POST' });
                opts.plan.share_token = d.token;
                paint();
            } catch (e) { alert('開放失敗：' + (e.message || e)); }
        });
        panel.querySelector('[data-act="off"]')?.addEventListener('click', async () => {
            if (!confirm('停用後，先前分享出去的連結會立即失效。確定停用？')) return;
            try {
                await opts.fetcher(opts.endpoints.share, { method: 'DELETE' });
                delete opts.plan.share_token;
                paint();
            } catch (e) { alert('停用失敗：' + (e.message || e)); }
        });
    }
    btn.addEventListener('click', () => {
        panel.style.display = panel.style.display === 'none' ? '' : 'none';
        paint();
    });
    paint();
}

// ── 編輯 + 儲存（debounce 800ms + blur flush + dirty-check + 409 並列） ──
function _wireEditing(container, opts) {
    const { plan, fetcher, endpoints, getGuestName } = opts;
    const saveEl = container.querySelector('#plc-save');   // 非 readonly 必存在
    const timers = new Map();       // el → debounce timer
    const base = new Map();         // el → 該格載入時的 updated_at（樂觀鎖基準）
    let saveMsgTimer = null;
    container.querySelector('#plc-theme').dataset.kind = 'theme';   // 先標，讓下面的迴圈一體處理

    container.querySelectorAll('[data-kind]').forEach(el => {
        const k = el.dataset.kind;
        if (k === 'cell') base.set(el, plan.cells?.[el.dataset.lens]?.[el.dataset.how]?.updated_at || null);
        else if (k === 'direction') base.set(el, plan.directions?.[el.dataset.how]?.updated_at || null);
        else if (k === 'theme') base.set(el, plan.theme_updated_at || null);
        else if (k === 'memo') base.set(el, plan.memo_updated_at || null);
        el._plcSaved = el.value;    // dirty-check 基準 — 沒改過就不打 PATCH（也避免亂蓋 updated_by）
        el.addEventListener('input', () => queue(el));
        el.addEventListener('blur', () => { clearTimeout(timers.get(el)); save(el); });
    });

    async function save(el) {
        if (el.value === el._plcSaved) return;   // 內容沒變：不發請求、不重蓋時間戳
        const body = { kind: el.dataset.kind, answer: el.value,
                       lens: el.dataset.lens || null, how: el.dataset.how || null,
                       field: el.dataset.field || null, base_updated_at: base.get(el),
                       guest_name: getGuestName ? (getGuestName() || null) : null };
        try {
            const d = await fetcher(endpoints.cell, { method: 'PATCH', json: body });
            base.set(el, d.updated_at);
            el._plcSaved = body.answer;
            el.parentElement.querySelector('.plc-conflict')?.remove();
            saveEl.textContent = '已儲存 ✓'; saveEl.classList.remove('err');
            clearTimeout(saveMsgTimer);
            saveMsgTimer = setTimeout(() => { saveEl.textContent = ''; }, 1500);
        } catch (e) {
            if (e.status === 409 && e.detail && e.detail.reason === 'conflict') { _showConflict(el, e.detail, save, base); return; }
            saveEl.textContent = '儲存失敗：' + (e.message || e); saveEl.classList.add('err');
        }
    }
    const queue = (el) => {
        clearTimeout(timers.get(el));
        timers.set(el, setTimeout(() => save(el), 800));
    };

    // ── 共編輪詢（30s）：?since= 短路，比對各格 meta vs base 找「別人」的變更 ──
    // 自己的儲存會同步 base（save/409 兩路都 set），所以只有他人寫入會產生差異。
    let since = null;
    const poll = setInterval(async () => {
        if (!container.isConnected) { clearInterval(poll); return; }   // overlay 已關 → 自停
        try {
            const q = since ? `?since=${encodeURIComponent(since)}` : '';
            const m = await fetcher(endpoints.meta + q);
            if (m.unchanged) return;
            since = m.updated_at;
            const foreign = [...base.keys()].some(el => {
                const k = el.dataset.kind;
                const leaf = k === 'cell' ? m.cells?.[el.dataset.lens]?.[el.dataset.how]
                    : k === 'direction' ? m.directions?.[el.dataset.how]
                    : k === 'memo' ? { updated_at: m.memo_updated_at }
                    : { updated_at: m.theme_updated_at };
                return leaf?.updated_at && leaf.updated_at !== base.get(el);
            });
            if (foreign) _showRefreshBanner(container, opts);
        } catch { /* 輪詢失敗靜默，下一輪再試 */ }
    }, 30000);
}

function _showRefreshBanner(container, opts) {
    if (container.querySelector('.plc-refresh')) return;
    const bar = document.createElement('div');
    bar.className = 'plc-banner plc-refresh';
    bar.innerHTML = '<span>👥 其他人更新了這份企劃</span><button>重新載入</button>';
    bar.querySelector('button').addEventListener('click', async () => {
        const dirty = [...container.querySelectorAll('[data-kind]')].some(el => el.value !== el._plcSaved);
        if (dirty && !confirm('你有未儲存的修改，重新載入會丟失，繼續？')) return;
        const fresh = await opts.refetch();
        container.innerHTML = '';
        renderPlan(container, { ...opts, plan: fresh });
    });
    container.prepend(bar);
}

// 內容多長格子就多長：清掉舊高度再量 scrollHeight（min-height 由 CSS 保底）
function _autoGrow(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
}

function _showConflict(el, detail, save, base) {
    el.parentElement.querySelector('.plc-conflict')?.remove();
    const box = document.createElement('div');
    box.className = 'plc-conflict';
    box.innerHTML = `
        <div class="ct">⚠ 這格已被 ${esc(detail.updated_by || '其他人')} 更新</div>
        <div>對方的版本：</div><pre></pre>
        <button data-act="mine">保留我的（覆蓋對方）</button>
        <button data-act="theirs">改用對方的</button>`;
    box.querySelector('pre').textContent = detail.server_answer || '（空白）';   // textContent — 不走 innerHTML
    el.parentElement.appendChild(box);
    box.querySelector('[data-act="mine"]').addEventListener('click', () => {
        base.set(el, detail.updated_at);   // 以對方時間戳為基準重送 → 覆蓋是「明知而為」
        box.remove(); save(el);
    });
    box.querySelector('[data-act="theirs"]').addEventListener('click', () => {
        el.value = detail.server_answer || '';
        el._plcSaved = el.value;
        base.set(el, detail.updated_at);
        box.remove();
        _autoGrow(el);
    });
}
