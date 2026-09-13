/**
 * projects-push.js — 執行專案（私帳）詳情的「私帳 ↔ 母帳」段：連結鈕、跳母帳、推送到母帳彈窗
 * （建立／連既有／搬回公司帳）。2026-09-13 從 projects.js 原樣切出（純搬移；那檔 1,359 行）。
 *
 * ES module 規矩（同 crm-cashbook-*.js 那套）：主檔 `export let _detail`（live binding，這裡**只讀**）、
 * 改狀態走主檔的 `setDirty()`／`resetDetail()`；`_fp`（window._finProjLedger）用同一個 idiom 自己取，
 * 不從主檔 import（主檔的 `const _fp` 在這支評估時還在 TDZ）；主檔反過來 import `_closeLocked`／`_linkBtnHtml`，
 * 兩邊都只在函式內用對方的東西。
 */
import { finIsMine as _isMine, esc, finToast } from '../fin-utils.js';
import { crmFetch, searchableSelect } from '../../crm/crm-utils.js';
import { _detail, _sel, resetDetail, setDirty } from './projects.js';

const _fp = (window._finProjLedger = window._finProjLedger || {});

// ── 私帳 ↔ 母帳（owner 2026-09-12「私帳建的專案希望可以推送到母帳」）──────
// 「推送」＝在母帳建一個對應的案並連結，兩案並存、各記各的錢（規劃正本
// docs/LEDGER_UNIFY_PLAN.md §8）。舊的「⬆ 推專案管理」（crm_pushed 旗標）拿掉了：
// 有了母帳分身，分身本來就在管線裡；旗標本身保留（既有 6 案不動、gotoCrm 沿用）。

/** 1:1 連著母帳時，識別欄由母帳決定 —— 後端在 locked_fields 講哪幾格鎖。 */
export const _closeLocked = (p) => ((p && p.locked_fields) || []).includes('close_date');

/** 動作列上「跟母帳的關係」那一顆：已連結→跳過去；未連結→推送。 */
export function _linkBtnHtml(p) {
    const links = (p && p.parent_links) || [];
    if (links.length) {
        const label = links.length > 1 ? `母帳：連 ${links.length} 案 ↗` : `母帳：${esc(links[0].name)} ↗`;
        return `
                <button class="crm-btn crm-btn-secondary crm-btn-sm"
                        title="到專案管理開啟連著的母帳案（${esc(links.map((l) => l.name).join('、'))}）"
                        onclick="window._finProjLedger.gotoParent()">${label}</button>`;
    }
    if (!_isMine()) return '';
    return `
                <button class="crm-btn crm-btn-secondary crm-btn-sm"
                        title="在母帳建一個對應的案並連結，或連到既有的母帳案；記錯帳本的整案搬回公司帳"
                        onclick="window._finProjLedger.pushParent()">推送到母帳</button>`;
}

_fp.gotoParent = () => {
    const links = (_detail && _detail.project && _detail.project.parent_links) || [];
    if (!links.length) return;
    // 私帳案只住在獨立頁 /my-ledger.html（沒有 switchTab），母帳案在 SPA 的專案管理：
    // 開新分頁帶 ?project=，crm-projects.js 的 tab-changed hook 會接（sessionStorage 跨分頁帶不過去）
    window.open('/?project=' + encodeURIComponent(links[0].id) + '#tab_crm_projects', '_blank', 'noopener');
};

/** 疊在詳情之上的視窗（同 recon.js 的 _pickModal：自己一個 overlay、重複使用）。 */
function _pushModal(title, bodyHtml) {
    let o = document.getElementById('fpl-push-modal');
    if (!o) {
        o = document.createElement('div');
        o.id = 'fpl-push-modal';
        o.className = 'crm-modal-overlay';
        o.style.zIndex = '1100';
        o.innerHTML = `<div class="crm-modal" style="max-width:min(560px,94vw);">
            <div class="crm-modal-header">
                <h3 id="fpl-push-title"></h3>
                <button class="crm-detail-close" onclick="window._finProjLedger.pushClose()">&#x2715;</button>
            </div>
            <div class="crm-modal-body" id="fpl-push-body"></div>
        </div>`;
        document.body.appendChild(o);
    }
    o.querySelector('#fpl-push-title').textContent = title;
    o.querySelector('#fpl-push-body').innerHTML = bodyHtml;
    o.style.display = 'flex';
}

_fp.pushClose = () => {
    const o = document.getElementById('fpl-push-modal');
    if (o) { o.style.display = 'none'; o.querySelector('#fpl-push-body').innerHTML = ''; }
};

/** 推送完的收尾：詳情與清單重抓（連結狀態、鎖住的格子、顯示名都變了）——
 *  refresh 自己會重抓開著的詳情（_sel && _detail），不必先抓一次。 */
async function _afterPush(msg) {
    _fp.pushClose();
    finToast(msg);
    setDirty(false);                       // refresh 看到 dirty 會整段跳過
    try {
        await _fp.refresh();
    } catch (e) {
        finToast('重新載入失敗：' + e.message, 'error');
    }
}

_fp.pushParent = async () => {
    const p = _detail && _detail.project;
    if (!p) return;
    // 母帳候選與同名建議：對應表那支端點一次帶齊（parents 的 linked_mine_id／本案的 suggest_id）
    let links;
    try {
        links = await crmFetch('/projects-mine-links');
    } catch (e) {
        finToast('讀不到母帳專案清單：' + e.message, 'error');
        return;
    }
    const me = (links.mine || []).find((m) => m.id === _sel) || {};
    const clientNote = me.client_state === 'none'
        ? `<div style="font-size:11px;color:#fbbf24;margin-left:22px;">客戶「${esc(p.client)}」在母帳還沒有對應的一筆，會一併建立並連結。</div>`
        : '';
    const opts = (links.parents || [])
        .slice()
        .sort((a, b) => (b.id === me.suggest_id) - (a.id === me.suggest_id))
        .map((x) => `<option value="${x.id}"${x.linked_mine_id ? ' disabled' : ''}${
            x.id === me.suggest_id ? ' selected' : ''}>${esc(x.name)}${x.client ? `（${esc(x.client)}）` : ''}${
            x.linked_mine_id ? ' — 已對應' : x.id === me.suggest_id ? ' — 同名建議' : ''}</option>`).join('');
    _pushModal(`推送到母帳 — ${p.name}`, `
        <div style="display:flex;flex-direction:column;gap:10px;font-size:13px;">
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="fpp-mode" value="create" checked>
                公司的案，我做其中一部分 —— 在母帳建立對應的專案並連結</label>
            ${clientNote}
            <div style="font-size:11px;color:#888;margin-left:22px;">案名／客戶／結案日照帶，母帳的合約金額先用私帳的並標為待確認（那是你拿到的那段，不是公司跟客戶的合約額）。</div>
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="fpp-mode" value="link"> 連結到既有的母帳專案</label>
            <select class="crm-input" id="fpp-target" disabled style="margin-left:22px;">
                <option value="">— 選一個母帳專案 —</option>${opts}</select>
            <label style="display:flex;align-items:center;gap:6px;">
                <input type="radio" name="fpp-mode" value="move"> 整個是公司的案，記錯帳本了 —— 搬回公司帳</label>
            <div style="font-size:11px;color:#888;margin-left:22px;">一案只在一本：錢流歸屬改回母公司，之後掛在它身上的錢都算公司的。身上已有收支的案搬不動。</div>
        </div>
        <div style="margin-top:16px;display:flex;justify-content:flex-end;gap:8px;">
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finProjLedger.pushClose()">取消</button>
            <button class="crm-btn crm-btn-primary crm-btn-sm" id="fpp-go">執行</button>
        </div>`);
    const sel = document.getElementById('fpp-target');
    searchableSelect(sel, { placeholder: '搜尋母帳專案…' });
    const mode = () => (document.querySelector('input[name="fpp-mode"]:checked') || {}).value || 'create';
    // searchableSelect 把原生 select 藏起來、換成自己的殼（.ss-wrap + .ss-input）——
    // 縮排與鎖住都要做在殼上，原生 select 的 disabled 使用者看不到
    const wrap = sel.closest('.ss-wrap');
    if (wrap) wrap.style.marginLeft = '22px';
    const sync = () => {
        const off = mode() !== 'link';
        sel.disabled = off;
        const inp = wrap && wrap.querySelector('.ss-input');
        if (inp) inp.disabled = off;
        if (wrap) wrap.style.opacity = off ? '0.45' : '';
    };
    document.getElementById('fpl-push-body').addEventListener('change', sync);
    sync();
    document.getElementById('fpp-go').addEventListener('click', (ev) => _pushSubmit(ev.currentTarget, mode()));
};

async function _pushSubmit(btn, mode) {
    const p = _detail && _detail.project;
    const id = _sel;
    if (!p || !id) return;
    if (mode === 'link') {
        const target = document.getElementById('fpp-target').value || '';
        if (!target) { finToast('請先選一個母帳專案', 'error'); return; }
        btn.disabled = true;
        try {
            await crmFetch(`/projects/${id}/parent-link`, {
                method: 'PUT', body: JSON.stringify({ parent_id: target }),
            });
            await _afterPush('已連結到母帳專案');
        } catch (e) {
            btn.disabled = false;
            finToast('連結失敗：' + e.message, 'error');
        }
        return;
    }
    if (mode === 'move') {
        let chk;
        try {
            chk = await crmFetch(`/projects/${id}/ledger-move-check`);
        } catch (e) {
            finToast('查不到搬帳本的狀態：' + e.message, 'error');
            return;
        }
        if (!chk.can_move) {
            // 這句話的正本在後端（_blocked_reason）—— 前端不再拼一份
            finToast(chk.reason || '這個專案不能換帳本', 'error');
            return;
        }
        if (!window.confirm(`把「${chk.name}」搬回母公司帳？\n\n錢流歸屬改回母公司，之後掛在它身上的錢都算公司的；私帳這邊不再有這一案。`)) return;
        btn.disabled = true;
        try {
            await crmFetch(`/projects/${id}/move-ledger`, {
                method: 'POST', body: JSON.stringify({ entity: 'parent' }),
            });
            _fp.pushClose();
            finToast('已搬回公司帳');
            // 這一案已經不在私帳 —— 詳情關掉、清單重抓（refresh 只重抓還在的）
            resetDetail();                // 詳情關掉：選取、詳情、dirty 一起清（主檔的 setter）
            document.getElementById('fpl-detail').style.display = 'none';
            await _fp.refresh();
        } catch (e) {
            btn.disabled = false;
            finToast('搬帳本失敗：' + e.message, 'error');
        }
        return;
    }
    btn.disabled = true;
    try {
        const r = await crmFetch(`/projects/${id}/parent-create`, { method: 'POST' });
        await _afterPush(`已在母帳建立「${r.name || p.name}」並連結`);
    } catch (e) {
        btn.disabled = false;
        finToast('建立失敗：' + e.message, 'error');
    }
}
