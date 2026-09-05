/**
 * clients.js — 👥 客戶管理（私帳；owner 2026-08-26「新增一個客戶管理，讓我可以
 * 比對 crm 與我的客戶」「基本上我希望跟 crm 同步，但是用連結的方式」）。
 *
 * 🔴 owner 2026-08-26 定案：**兩邊各一筆＋連結** —— 同一家公司 CRM 一筆、
 * 私帳一筆，中間用 crm_link_id 對照；「連結後以 crm 的客戶清單為主要清單」。
 * 所以本頁＝**那份連結清單**（要留著日後使用）：私帳每一家的對應狀態、
 * 一鍵連結/解除、名稱吻合給建議（唯一候選才給，多個不猜）。
 * 資料兩邊都不動 —— 連結只記「這兩筆是同一家」。
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
    const unlinked = d.mine.filter((m) => !m.crm_link_id);
    const sugg = unlinked.filter((m) => m.suggest_id);
    // 按鈕依實際狀況 Link / Unlink（owner 2026-08-27），放在**最左邊**一欄 ——
    // 擠在名字右側時列距只有幾像素，很容易按到隔壁那家
    const linkCell = (m) => m.crm_link_id
        ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="color:#f87171;width:74px;"
                   title="解除與「${esc(m.crm_link_name)}」的對應（只解連結，兩邊資料都不動）"
                   onclick="window._finCli.link('${m.id}','')">Unlink</button>`
        : `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="width:74px;"
                   title="連到 CRM 客戶（只記對應，不搬資料）"
                   onclick="window._finCli.pick(event,'${m.id}')">Link</button>`;

    const row = (m) => `
        <tr><td class="fcl-link" data-id="${m.id}" style="overflow:visible;width:86px;">${linkCell(m)}</td>
            <td style="color:#e0e0e0;">${esc(m.short_name)}</td>
            <td style="color:#888;font-variant-numeric:tabular-nums;">${esc(m.tax_id || '')}</td>
            <td style="text-align:right;color:#c4b5fd;">${fmtNum(m.n_projects)}</td>
            <td style="color:${m.crm_link_id ? '#86efac' : '#777'};">${
                m.crm_link_id ? '→ ' + esc(m.crm_link_name)
                : m.suggest_id
                    ? `建議：${esc(m.suggest_name)}
                       <button class="crm-btn crm-btn-primary crm-btn-sm" style="margin-left:6px;"
                               onclick="window._finCli.link('${m.id}','${m.suggest_id}')">採用</button>`
                    // 連不到的就在母帳建一筆（owner 2026-09-05「私帳的客戶
                    // 母帳都有包含」）—— 只複製代稱／全稱／統編，匯款資訊與
                    // 聯絡人是私帳自己談的關係，不往母帳倒。
                    : `（未對應）
                       <button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:6px;"
                               title="在 CRM 建一筆同名客戶並連結（只帶代稱／全稱／統編）"
                               onclick="window._finCli.createInCrm('${m.id}','${esc(m.short_name)}')">在 CRM 建立</button>`}</td></tr>`;

    _c.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px;">
            <h2 style="color:#eee;margin:0;font-size:18px;">👥 客戶管理</h2>
            <span style="color:#888;font-size:12px;">私帳客戶 ${d.mine.length} 家・
                <b style="color:#86efac;">已連結 ${linked.length}</b>・
                未連結 ${unlinked.length}（其中 ${sugg.length} 家有建議）・
                CRM 主檔 ${d.crm.length} 家</span>
            <div style="flex:1;"></div>
            <button class="crm-btn crm-btn-secondary crm-btn-sm"
                    title="把連結清單存成 CSV（日後對照用）"
                    onclick="window._finCli.exportCsv()">⭳ 匯出連結清單</button>
        </div>
        <div style="max-height:calc(100vh - 300px);overflow-y:auto;background:#202020;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                <th>連結</th><th>私帳客戶</th><th>統編</th>
                <th style="text-align:right;">私帳案</th>
                <th>對應的 CRM 客戶</th></tr></thead>
            <tbody>${[...unlinked, ...linked].map(row).join('')
                || '<tr><td colspan="5" style="color:#666;padding:14px;">（沒有私帳客戶）</td></tr>'}</tbody>
        </table></div>
        ${d.shared.length ? `
        <div style="color:#ddd;font-size:12px;font-weight:600;margin:14px 0 6px;">
            私帳直接用 CRM 客戶的案子（不需要連結 —— 本來就是同一筆）</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
            ${d.shared.map((s) => `<span style="background:#242424;border:1px solid #333;border-radius:12px;
                padding:3px 10px;font-size:11px;color:#bbb;">${esc(s.short_name)}
                <span style="color:#666;">${fmtNum(s.n_projects)} 案</span></span>`).join('')}
        </div>` : ''}
        <div style="color:#666;font-size:11px;margin-top:10px;">
            連結＝確認「這兩筆是同一家客戶」，兩邊資料都不動；連結後以 CRM 那份為主清單。
            日後私帳新增客戶直接建進 CRM（執行專案的「或新客戶」就是建 CRM 客戶）。</div>`;
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

_fc.createInCrm = async (id, name) => {
    if (!window.confirm(`在 CRM（母帳）建立客戶「${name}」並連結？`)) return;
    try {
        const r = await crmFetch(`/clients/${id}/crm-create`, { method: 'POST' });
        // 母帳已經有同代稱的那一家時後端不建新的、直接連過去 —— 要講出來，
        // 否則使用者會以為多了一筆
        finToast(r.created ? '已在 CRM 建立並連結' : `CRM 已有「${name}」，直接連結`);
        _fc.reload();
    } catch (e) {
        finToast('建立失敗：' + e.message, 'error');
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
    // 🔴 這格只有 86px，搜尋框與選單跟著縮成 86px 就打不了字也看不到客戶全名
    // （owner 2026-08-27「link 出來的搜尋框太小了 小到無法作業」）。
    // 讓它**浮起來蓋在表格上**（cell 當定位原點）——比放大格子好，欄寬不會抖動。
    cell.style.position = 'relative';
    const wrap = cell.querySelector('.ss-wrap');
    if (wrap) wrap.style.cssText += ';position:absolute;top:2px;left:2px;width:300px;z-index:320;';
    const panel = cell.querySelector('.ss-panel');
    if (panel) panel.style.maxHeight = '320px';
    const inp = cell.querySelector('.ss-input');
    if (inp) {
        inp.style.cssText += ';font-size:13px;padding:6px 10px;';
        inp.focus();
        inp.addEventListener('click', (k) => k.stopPropagation());
    }
    let saved = false;
    sel.addEventListener('change', () => {
        if (saved || !sel.value) return;
        saved = true;
        _fc.link(id, sel.value);
    });
    if (inp) inp.addEventListener('blur', () => setTimeout(() => { if (!saved) _render(); }, 200));
};

_fc.exportCsv = () => {
    // 連結清單存檔（BOM，Excel 開中文不亂碼；欄位對得上 CRM 匯入的詞彙）
    const rows = [["私帳客戶", "統編", "私帳案數", "對應CRM客戶", "連結狀態"]];
    _d.mine.forEach((m) => rows.push([
        m.short_name, m.tax_id || "", m.n_projects,
        m.crm_link_name || (m.suggest_name ? "（建議：" + m.suggest_name + "）" : ""),
        m.crm_link_id ? "已連結" : "未連結",
    ]));
    const NL = String.fromCharCode(10);
    const cell = (v) => {
        const t = String(v ?? "");
        // 含逗號/引號/換行才包引號（CSV 規則；不用正則以免跳脫字元被工具鏈吃掉）
        return (t.includes(",") || t.includes(String.fromCharCode(34)) || t.includes(NL))
            ? String.fromCharCode(34) + t.split(String.fromCharCode(34)).join(String.fromCharCode(34, 34)) + String.fromCharCode(34)
            : t;
    };
    const csv = String.fromCharCode(0xFEFF)
        + rows.map((r) => r.map(cell).join(",")).join(String.fromCharCode(13, 10));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    a.download = "私帳客戶_CRM連結清單.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
};
