/**
 * gear.js — 🎥 器材清冊（財務視角的器材清單；2026-08-25）。
 *
 * owner：「我的器材清冊拉到我的私帳頁面可以看到，crm 如果是我的清冊就不要
 * 看到」。私帳的 123 件在這裡看與管 —— 沒有這頁的話 owner 的器材沒地方編。
 *
 * 🔴 2026-08-30 起**跟著當前帳本**（原本寫死 finFetchMine）：owner「器材清單
 * 這些清單是私帳的，跟母公司沒關係，母公司的器材清單要另外建」。母公司那本
 * 是空的（生產實查：123 件全在私帳、母公司 0 件），從這裡開始建。
 * 兩本帳的欄位一樣（名稱/類別/建置日/金額/攤提/狀態/備註），差別只在資料屬於
 * 哪一本 —— 所以是同一個畫面換帳本，不是兩支子視圖。
 *
 * 口徑：清單與淨值走 /finance/assets/equipment（逐件過報表引擎，與 BS 的
 * 「器材淨值」同一份算法 —— 見該端點的說明）；新增/編修走 /api/v1/equipment
 * CRUD（寫入側 mine 那半有 _assert_mine_writable 的牆）。
 * 刻意**不是** CRM 器材庫的複本：領用/歸還/稼動率是公司的工作流，這頁是
 * 財務視角（金額/攤提/淨值）—— 對齊 owner 原 Sheet 的欄位。
 */
import { finFetch, finEntity, finSubviewBoot, esc, fmtNum, finToast }
    from '../fin-utils.js';
import { authFetch } from '../../../js/shared/utils.js';

const API = '/api/v1/equipment';

let _c = null;
let _isCurrent = () => true;
let _data = null;      // /finance/assets/equipment 的回應
let _editing = null;   // 編輯中的 item id；'new'＝新增

async function _efetch(path, opts = {}) {
    // 器材 CRUD 不在 /api/v1/finance 底下 —— token/JSON 殼走 shared authFetch
    //（body 傳物件會自動 stringify），這裡只補 detail 抽取
    const res = await authFetch(API + path, opts);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || '請求失敗');
    }
    return res.json();
}

export default async function render(container, ctx = {}) {
    _c = container;
    if (ctx.isCurrent) _isCurrent = ctx.isCurrent;
    await _load();
}

async function _load() {
    const r = await finSubviewBoot(_c, {
        title: '🎥 器材清冊', isCurrent: _isCurrent,
        fetchers: [() => finFetch('/assets/equipment')],
        retry: 'window._finGear.reload()',
    });
    if (!r) return;
    _data = r[0];
    _editing = null;
    _render();
}

function _cats() {
    const s = new Set(['機身', '鏡頭', '燈光', '收音', '週邊', '電腦', '其他']);
    (_data.items || []).forEach((x) => { if (x.category) s.add(x.category); });
    return [...s];
}

function _formHtml(x) {
    const v = (k, d = '') => esc(x ? (x[k] ?? d) : d);
    return `
    <div id="gear-form" style="background:#202020;border:1px solid #3b82f6;border-radius:8px;padding:14px;margin-bottom:12px;">
        <div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 1fr;gap:8px;">
            <label style="color:#888;font-size:11px;">名稱*<input class="crm-input" id="gf-name" value="${v('name')}"></label>
            <label style="color:#888;font-size:11px;">類別<input class="crm-input" id="gf-cat" list="gear-cats" value="${v('category')}"></label>
            <label style="color:#888;font-size:11px;">建置日<input class="crm-input" type="date" id="gf-date" value="${v('purchase_date')}"></label>
            <label style="color:#888;font-size:11px;">金額<input class="crm-input" type="number" id="gf-cost" value="${x ? x.cost : ''}"></label>
            <label style="color:#888;font-size:11px;">攤提(月)<input class="crm-input" type="number" id="gf-months" value="${x ? (x.months || 48) : 48}"></label>
            <label style="color:#888;font-size:11px;">狀態<select class="crm-input" id="gf-status">
                ${['在庫', '除役'].map((s) => `<option${(x ? x.status : '在庫') === s ? ' selected' : ''}>${s}</option>`).join('')}
            </select></label>
        </div>
        <datalist id="gear-cats">${_cats().map((c) => `<option value="${esc(c)}">`).join('')}</datalist>
        <label style="color:#888;font-size:11px;display:block;margin-top:8px;">備註
            <input class="crm-input" id="gf-note" value="${v('note')}" style="width:100%;"></label>
        <div style="display:flex;gap:8px;margin-top:10px;">
            <button class="crm-btn crm-btn-primary crm-btn-sm" onclick="window._finGear.save(this)">儲存</button>
            <button class="crm-btn crm-btn-secondary crm-btn-sm" onclick="window._finGear.cancel()">取消</button>
            ${x ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" style="margin-left:auto;color:#f87171;"
                          onclick="window._finGear.del('${x.id}')">刪除</button>` : ''}
        </div>
    </div>`;
}

function _render() {
    const eq = _data;
    const t = eq.totals;
    const editingItem = _editing && _editing !== 'new'
        ? (eq.items || []).find((x) => x.id === _editing) : null;
    const rows = (eq.items || []).map((x) => `
        <tr style="cursor:pointer;${x.counted ? '' : 'color:#555;'}${x.id === _editing ? 'outline:1px solid #3b82f6;' : ''}"
            onclick="window._finGear.edit('${x.id}')">
            <td title="${esc(x.note)}">${esc(x.name)}</td>
            <td style="color:#888;">${esc(x.category)}</td>
            <td style="color:#888;white-space:nowrap;">${esc(x.purchase_date || '—')}</td>
            <td style="text-align:right;">$${fmtNum(x.cost)}</td>
            <td style="text-align:right;color:#888;">${x.months || '—'}</td>
            <td style="text-align:right;color:#888;">${x.accum != null ? '$' + fmtNum(x.accum) : '—'}</td>
            <td style="text-align:right;${x.counted ? 'color:#eee;font-weight:600;' : ''}">${x.counted ? '$' + fmtNum(x.net) : '—'}</td>
            <td>${esc(x.status || '在庫')}</td>
        </tr>`).join('');
    _c.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
            <h2 style="color:#eee;margin:0;font-size:18px;">🎥 器材清冊</h2>
            <span style="color:#888;font-size:12px;">${t.count} 件・計入 ${t.counted} 件・淨值
                <b style="color:#eee;">$${fmtNum(t.net)}</b>（＝資產儀表板／BS 的「器材淨值」）</span>
            <button class="crm-btn crm-btn-primary crm-btn-sm" style="margin-left:auto;"
                    onclick="window._finGear.add()">＋ 新增器材</button>
        </div>
        ${_editing ? _formHtml(editingItem) : ''}
        <div style="max-height:calc(100vh - 260px);overflow-y:auto;background:#202020;border:1px solid #2e2e2e;border-radius:8px;">
        <table class="crm-table" style="width:100%;font-size:12px;">
            <thead><tr style="position:sticky;top:0;background:#202020;z-index:1;">
                <th>名稱</th><th>類別</th><th>建置日</th>
                <th style="text-align:right;">建構金額</th>
                <th style="text-align:right;">攤提(月)</th>
                <th style="text-align:right;">已折</th>
                <th style="text-align:right;">淨值</th><th>狀態</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="8" style="color:#666;padding:16px;">（清冊是空的）</td></tr>'}</tbody>
        </table></div>
        <div style="color:#666;font-size:11px;margin-top:8px;">
            點任一列編輯。淨值逐件走報表引擎（直線攤到 0、除役出表）；截至 ${esc(eq.as_of)}。
            CRM 器材庫只看公司器材，這裡的清冊只有你看得到。</div>`;
}

const _fg = (window._finGear = window._finGear || {});

_fg.reload = _load;
_fg.add = () => { _editing = 'new'; _render(); };
_fg.edit = (id) => { _editing = _editing === id ? null : id; _render(); };
_fg.cancel = () => { _editing = null; _render(); };

_fg.save = async (btn) => {
    const g = (id) => document.getElementById(id).value.trim();
    const name = g('gf-name');
    if (!name) { finToast('名稱必填', 'error'); return; }
    const body = {
        name, category: g('gf-cat') || null, purchase_date: g('gf-date'),
        purchase_cost: parseInt(g('gf-cost') || '0', 10) || 0,
        depreciation_months: parseInt(g('gf-months') || '0', 10) || 0,
        status: g('gf-status'), note: g('gf-note') || null,
    };
    btn.disabled = true;
    try {
        if (_editing === 'new') {
            // 建在**當前帳本**（母公司模式建的就是公司的器材）
            await _efetch('', { method: 'POST', body: { ...body, entity: finEntity() } });
            finToast('已新增到清冊');
        } else {
            await _efetch(`/${_editing}`, { method: 'PUT', body });
            finToast('已儲存');
        }
        await _load();      // 淨值要引擎重算，別在前端自己湊
    } catch (e) {
        finToast('儲存失敗：' + e.message, 'error');
        btn.disabled = false;
    }
};

_fg.del = async (id) => {
    const x = (_data.items || []).find((i) => i.id === id);
    if (!confirm(`刪除「${x ? x.name : id}」？\n（除役請改狀態；刪除是清冊上根本不該有這一列時用的）`)) return;
    try {
        await _efetch(`/${id}`, { method: 'DELETE' });
        finToast('已刪除');
        await _load();
    } catch (e) { finToast('刪除失敗：' + e.message, 'error'); }
};
