/**
 * clients.js — 👥 客戶管理（私帳；owner 2026-08-26「新增一個客戶管理，讓我可以
 * 比對 crm 與我的客戶」「基本上我希望跟 crm 同步，但是用連結的方式」）。
 *
 * 🔴 2026-08-26 owner 拍板「把私帳的客戶都整合到 crm 系統裡面」→ 84 家私帳
 * 客戶已併入 CRM，**客戶主檔只有一份**。所以本頁的主表＝「我用到的客戶」
 * （都是 CRM 客戶，帶私帳/公司各自的案數）；錢仍分帳本（金額的門綁在專案的
 * entity，不在客戶）。
 * 上半的「私帳專屬客戶＋連結對應」只在真的還有 entity='mine' 客戶時才出現
 * （併完為空）—— 留著是防呆：又冒出私帳客戶時，這裡看得到也連得起來。
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
    // 併完之後 mine 為空 —— 這一區整段不畫（留碼是防呆，見檔頭）
    const mineBlock = d.mine.length ? `
        <div style="background:#2a2320;border:1px solid #7c5b2e;border-radius:8px;padding:10px 12px;margin-bottom:12px;">
            <div style="color:#fbbf24;font-size:12px;margin-bottom:8px;">
                還有 ${d.mine.length} 家私帳專屬客戶沒進 CRM 主檔（已連結 ${linked.length}・有建議 ${sugg.length}）</div>
            <table class="crm-table" style="width:100%;font-size:12px;">
                <thead><tr><th>私帳客戶</th><th style="text-align:right;">案數</th>
                    <th>對應 CRM 客戶</th></tr></thead>
                <tbody>${d.mine.map((m) => `
                    <tr><td style="color:#e0e0e0;">${esc(m.short_name)}</td>
                        <td style="text-align:right;color:#888;">${fmtNum(m.n_projects)}</td>
                        <td class="fcl-link" data-id="${m.id}" style="overflow:visible;">${linkCell(m)}</td></tr>`).join('')}</tbody>
            </table>
        </div>` : '';

    const both = d.shared.filter((s) => s.n_parent > 0).length;
    _c.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px;">
            <h2 style="color:#eee;margin:0;font-size:18px;">👥 客戶管理</h2>
            <span style="color:#888;font-size:12px;">我用到的客戶 ${d.shared.length} 家・
                其中 ${both} 家也有公司案・CRM 主檔共 ${d.crm.length} 家</span>
        </div>
        ${mineBlock}
        <div style="max-height:calc(100vh - 300px);overflow-y:auto;background:#202020;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                <th>客戶</th><th>抬頭</th><th>統編</th>
                <th style="text-align:right;">私帳案</th>
                <th style="text-align:right;">公司案</th></tr></thead>
            <tbody>${d.shared.map((s) => `
                <tr><td style="color:#e0e0e0;">${esc(s.short_name)}</td>
                    <td style="color:#9a9a9a;overflow:hidden;text-overflow:ellipsis;max-width:280px;white-space:nowrap;"
                        title="${esc(s.full_name)}">${esc(s.full_name)}</td>
                    <td style="color:#888;font-variant-numeric:tabular-nums;">${esc(s.tax_id)}</td>
                    <td style="text-align:right;color:#c4b5fd;">${fmtNum(s.n_projects)}</td>
                    <td style="text-align:right;color:${s.n_parent ? '#86efac' : '#4b5563'};">${s.n_parent ? fmtNum(s.n_parent) : '—'}</td>
                </tr>`).join('')
                || '<tr><td colspan="5" style="color:#666;padding:14px;">（私帳還沒有掛客戶的案子）</td></tr>'}</tbody>
        </table></div>
        <div style="color:#666;font-size:11px;margin-top:10px;">
            客戶主檔與 CRM 統一（2026-08-26）——同一家客戶只有一筆資料，兩邊共用；
            金額仍分帳本（私帳案的錢只有你看得到）。要改客戶資料到 CRM 的客戶管理改。</div>`;
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
