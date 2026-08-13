/**
 * flow-view.js — 專案工作流：階段（單線）× 五軌進度（多方前進）。唯讀（階段一）。
 *
 * 規格正本 docs/PROPOSAL_PLANNER.md §14。判定邏輯全在後端
 * （core/project_flow.py + routers/api_project_flow.py）—— 這裡只畫。
 *
 * 🔴 import closure 鐵則：只准 `tabs/proposals/` 與 `js/shared/`。NAS 對外容器
 * 只 serve 這兩處（core/public_assets.py MODULE_DIRS），碰到 tabs/crm/ 會壞掉。
 * tests/unit/test_public_surface.py 守著。
 *
 * 🔴 UI 無 emoji（owner 2026-07-17 鐵則）：新 UI 一律純文字。
 */
import { ensureStyle, esc } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';

// 軌色沿用四段進度條的既有色彙（備份藍/轉檔橘/串接綠/報表紫）—— 同事已經
// 認得這套顏色語言，不另外發明一組。收割用灰藍（PARA 的 Resources，非主線）。
const TRACK_COLOR = {
    plan: '#1f538d', prod: '#d48a04', biz: '#228b22',
    deliver: '#7c3aed', harvest: '#546e7a',
};

const CSS = `
.pflow { padding: 4px 2px 18px; }
.pflow-stage { display:flex; align-items:center; gap:6px; flex-wrap:wrap;
    padding:12px 14px; background:#181818; border:1px solid #2e2e2e;
    border-radius:8px; margin-bottom:14px; }
.pflow-step { font-size:12px; padding:5px 12px; border-radius:999px;
    border:1px solid #3a3a3a; color:#777; white-space:nowrap; }
.pflow-step.done { color:#9ccc65; border-color:#3d5c2a; background:#1d2a16; }
.pflow-step.cur { color:#fff; border-color:#1f538d; background:#1f538d;
    font-weight:600; }
.pflow-arrow { color:#3a3a3a; font-size:11px; }
.pflow-lost { padding:12px 14px; border-radius:8px; margin-bottom:14px;
    background:#2a1618; border:1px solid #6e2b2b; color:#e88; font-size:12.5px; }
.pflow-track { display:flex; align-items:flex-start; gap:10px; padding:9px 0;
    border-bottom:1px solid #242424; }
.pflow-track:last-child { border-bottom:0; }
.pflow-tname { flex:0 0 52px; font-size:12px; color:#bbb; font-weight:600;
    padding-top:4px; }
.pflow-items { flex:1; display:flex; flex-wrap:wrap; gap:6px; }
.pflow-item { display:inline-flex; align-items:center; gap:5px; font-size:11.5px;
    padding:4px 9px; border-radius:6px; border:1px solid #2e2e2e;
    background:#161616; color:#888; cursor:default; }
.pflow-item.on { color:#ddd; border-color:#3a3a3a; background:#1c1c1c; }
.pflow-item.skip { opacity:.45; border-style:dashed; }
.pflow-dot { width:8px; height:8px; border-radius:50%; flex:0 0 8px;
    border:1px solid currentColor; }
.pflow-item.on .pflow-dot { border:0; }
.pflow-item.manual .pflow-dot { border-radius:2px; }
.pflow-count { flex:0 0 auto; font-size:11px; color:#666; padding-top:5px;
    min-width:34px; text-align:right; }
.pflow-miss { margin-top:14px; padding:10px 12px; border-radius:8px;
    background:#1e1a12; border:1px solid #4a3c1a; font-size:12px; color:#d9b45a; }
.pflow-miss b { color:#f0c96a; font-weight:600; }
.pflow-miss .adv { color:#8a8a8a; }
.pflow-empty { padding:22px; text-align:center; color:#777; font-size:12.5px; }
.pflow-note { margin-top:12px; font-size:11.5px; color:#666; }
.pflow-item.clickable { cursor:pointer; }
.pflow-item.clickable:hover { border-color:#4a4a4a; background:#202020; color:#ddd; }
.pflow-item.clickable:focus-visible { outline:2px solid #1f538d; outline-offset:1px; }
.pflow-item[data-busy] { opacity:.5; pointer-events:none; }
@media (max-width: 720px) {
    .pflow-track { flex-direction:column; gap:4px; }
    .pflow-tname { flex:none; padding-top:0; }
    .pflow-count { text-align:left; padding-top:0; }
}
`;

function _stageHtml(st) {
    if (st.is_lost) {
        return `<div class="pflow-lost">未成案 — 這個案子沒有拿到。原因記在提案的組織學習欄。</div>`;
    }
    const steps = st.pipeline.map((s, i) => {
        const cls = s === st.status ? 'cur' : (st.index >= 0 && i < st.index ? 'done' : '');
        return `<span class="pflow-step ${cls}">${esc(s)}</span>`;
    }).join('<span class="pflow-arrow">—</span>');
    return `<div class="pflow-stage">${steps}</div>`;
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

function _itemHtml(it, canCheck, color) {
    const clickable = it.kind === 'manual' && canCheck;
    // state/kind 本身就是 on|off|skip、auto|manual，直接當 class 用
    const cls = `pflow-item ${it.state} ${it.kind}${clickable ? ' clickable' : ''}`;
    const dot = it.state === 'on'
        ? `<span class="pflow-dot" style="background:${color}"></span>`
        : '<span class="pflow-dot"></span>';
    const attrs = clickable ? ` data-check="${esc(it.key)}" role="button" tabindex="0"` : '';
    return `<span class="${cls}"${attrs} title="${esc(_why(it, clickable))}">${dot}${esc(it.label)}</span>`;
}

function _trackHtml(t, canCheck) {
    const color = TRACK_COLOR[t.key] || '#888';
    const items = t.items.map(i => _itemHtml(i, canCheck, color)).join('');
    return `<div class="pflow-track">
        <div class="pflow-tname" style="color:${color}">${esc(t.label)}</div>
        <div class="pflow-items">${items}</div>
        <div class="pflow-count">${t.done}/${t.total}</div>
    </div>`;
}

function _missingHtml(missing, next) {
    if (!missing.length) return '';   // 沒有下一階段時後端本來就回空陣列
    const blocking = missing.filter(m => !m.advisory);
    const advisory = missing.filter(m => m.advisory);
    const parts = [];
    if (blocking.length) {
        parts.push(`推進到「${esc(next)}」前建議先完成：` +
            blocking.map(m => `<b>${esc(m.label)}</b>`).join('、'));
    }
    if (advisory.length) {
        parts.push(`<span class="adv">提醒（不影響推進）：` +
            advisory.map(m => esc(m.label)).join('、') + `</span>`);
    }
    return `<div class="pflow-miss">${parts.join('<br>')}</div>`;
}

/**
 * @param host   掛載節點
 * @param opts   { projectId }  —— 沒有殼專案的提案不要呼叫這支
 */
export async function renderFlow(host, { projectId }) {
    ensureStyle('pflow-css', CSS);
    if (!projectId) {
        host.innerHTML = `<div class="pflow-empty">這個提案還沒有關聯專案。<br>
            補上客戶之後系統會自動建立殼專案，進度才有東西可以追。</div>`;
        return;
    }
    host.innerHTML = `<div class="pflow-empty">載入中…</div>`;
    let d;
    try {
        d = await tfetch(`/api/v1/crm/projects/${encodeURIComponent(projectId)}/flow`);
    } catch (e) {
        if (!host.isConnected) return;
        host.innerHTML = `<div class="pflow-empty">進度載入失敗：${esc(e.message || e)}</div>`;
        return;
    }
    if (!host.isConnected) return;      // 載入期間視窗被關掉了

    _paint(host, d);
    if (host.__flowWired) return;
    host.__flowWired = true;
    // 事件委派掛一次就好 —— 每次重畫都重掛會累積成一次點擊送 N 個請求
    const onHit = async (ev) => {
        const el = ev.target.closest?.('[data-check]');
        if (!el || !host.contains(el)) return;
        if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
        ev.preventDefault();
        if (el.dataset.busy) return;          // 連點兩下不送兩次
        el.dataset.busy = '1';
        const nowOn = el.classList.contains('on');
        try {
            const fresh = await tfetch(
                `/api/v1/crm/projects/${encodeURIComponent(projectId)}/flow/check`,
                { method: 'POST', json: { item_key: el.dataset.check, checked: !nowOn } });
            if (host.isConnected) _paint(host, fresh);
        } catch (e) {
            alert('標記失敗：' + (e.message || e));
            delete el.dataset.busy;
        }
    };
    host.addEventListener('click', onHit);
    host.addEventListener('keydown', onHit);
}

function _paint(host, d) {
    const st = d.stage || {};
    const can = !!d.can_check;
    host.innerHTML = `<div class="pflow">
        ${_stageHtml(st)}
        ${(d.tracks || []).map(t => _trackHtml(t, can)).join('')}
        ${_missingHtml(d.missing || [], st.next)}
        ${can ? '' : '<div class="pflow-note">里程碑（開拍／剪輯完成）需要專案管理權限才能標記。</div>'}
    </div>`;
}
