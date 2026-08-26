/**
 * clients.js — 👥 客戶管理（私帳；owner 2026-08-26「新增一個客戶管理，讓我可以
 * 比對 crm 與我的客戶」「基本上我希望跟 crm 同步，但是用連結的方式」）。
 *
 * 語意：**只記對應、不搬資料**。私帳客戶連結到 CRM 客戶＝「這兩筆是同一個
 * 真實客戶」的確認 —— 之後要「併過去」時以此為依據。共用客戶（本來就是
 * parent、被私帳案引用的那 15 個）天生同步，列出來看就好不用連結。
 * 名稱吻合的自動給建議（唯一候選才建議，多個不猜）。
 * 同 projects/gear：入口由 .fin-nav-mine-only 指名門把關。
 */
import { finSubviewBoot, esc, fmtNum, finToast } from '../fin-utils.js';
import { crmFetch, searchableSelect } from '../../crm/crm-utils.js';

let _c = null;
let _isCurrent = () => true;
let _d = null;

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    const r = await finSubviewBoot(_c, {
        title: '👥 客戶管理', isCurrent: _isCurrent,
        fetchers: [() => crmFetch('/clients-mine-links')],
        retry: 'window._finCli.reload()',
    });
    if (!r) return;
    _d = r[0];
    _render();
}

function _render() {
    const d = _d;
    const linked = d.mine.filter((m) => m.crm_link_id);
    const sugg = d.mine.filter((m) => !m.crm_link_id && m.suggest_id);
    const linkCell = (m) => {
        if (m.crm_link_id) {
            return `<span style="color:#86efac;">→ ${esc(m.crm_link_name)}</span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:6px;color:#f87171;"
                        title="解除對應（只解連結，兩邊資料都不動）"
                        onclick="window._finCli.link('${m.id}','')">解除</button>`;
        }
        const pick = `<button class="crm-btn crm-btn-secondary crm-btn-sm"
                              onclick="window._finCli.pick(event,'${m.id}')">連結</button>`;
        return m.suggest_id
            ? `<span style="color:#888;">建議：${esc(m.suggest_name)}</span>
               <button class="crm-btn crm-btn-primary crm-btn-sm" style="margin-left:6px;"
                       title="採用建議的對應"
                       onclick="window._finCli.link('${m.id}','${m.suggest_id}')">採用</button> ${pick}`
            : pick;
    };
    _c.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px;">
            <h2 style="color:#eee;margin:0;font-size:18px;">👥 客戶管理</h2>
            <span style="color:#888;font-size:12px;">我的客戶 ${d.mine.length}・已連結
                <b style="color:#86efac;">${linked.length}</b>・有建議 ${sugg.length}・
                共用（本來就在 CRM）${d.shared.length}</span>
        </div>
        <div style="max-height:calc(100vh - 300px);overflow-y:auto;background:#202020;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                <th>我的客戶</th><th style="text-align:right;">案數</th>
                <th>對應 CRM 客戶（連結，不搬資料）</th></tr></thead>
            <tbody>${d.mine.map((m) => `
                <tr><td style="color:#e0e0e0;">${esc(m.short_name)}</td>
                    <td style="text-align:right;color:#888;">${fmtNum(m.n_projects)}</td>
                    <td class="fcl-link" data-id="${m.id}" style="overflow:visible;">${linkCell(m)}</td></tr>`).join('')
                || '<tr><td colspan="3" style="color:#666;padding:14px;">（沒有私帳客戶）</td></tr>'}</tbody>
        </table></div>
        <div style="color:#ddd;font-size:12px;font-weight:600;margin:14px 0 6px;">
            共用客戶（兩邊都有案，本來就是 CRM 客戶 —— 天生同步）</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
            ${d.shared.map((s) => `<span style="background:#242424;border:1px solid #333;border-radius:12px;
                padding:3px 10px;font-size:11px;color:#bbb;">${esc(s.short_name)}
                <span style="color:#666;">${fmtNum(s.n_projects)} 案</span></span>`).join('')
                || '<span style="color:#666;font-size:12px;">（無）</span>'}
        </div>
        <div style="color:#666;font-size:11px;margin-top:10px;">
            連結＝確認「這兩筆是同一個客戶」的對應，資料兩邊都不動。全部對應完之後要併回 CRM 再說一聲。</div>`;
}

const _fc = (window._finCli = window._finCli || {});

_fc.reload = () => { if (_c) render(_c, { isCurrent: _isCurrent }); };

_fc.link = async (id, crmId) => {
    try {
        await crmFetch(`/clients/${id}/crm-link`, {
            method: 'PUT', body: JSON.stringify({ crm_link_id: crmId || null }),
        });
        finToast(crmId ? '已連結' : '已解除');
        _fc.reload();
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
    }
};

_fc.pick = (ev, id) => {
    // 格內可搜尋下拉挑 CRM 客戶（同收支類別格的模式）——選定即存
    ev.stopPropagation();
    const cell = ev.currentTarget.closest('.fcl-link');
    if (!cell || cell.querySelector('select')) return;
    const sel = document.createElement('select');
    sel.className = 'crm-input';
    sel.innerHTML = '<option value="">— 選 CRM 客戶 —</option>'
        + _d.crm.map((c) => `<option value="${c.id}">${esc(c.short_name)}</option>`).join('');
    cell.innerHTML = '';
    cell.appendChild(sel);
    searchableSelect(sel, { placeholder: '搜尋 CRM 客戶…' });
    const inp = cell.querySelector('.ss-input');
    if (inp) { inp.focus(); inp.addEventListener('click', (k) => k.stopPropagation()); }
    let saved = false;
    sel.addEventListener('change', () => {
        if (saved || !sel.value) return;
        saved = true;
        _fc.link(id, sel.value);
    });
    if (inp) inp.addEventListener('blur', () => setTimeout(() => { if (!saved) _render(); }, 200));
};
