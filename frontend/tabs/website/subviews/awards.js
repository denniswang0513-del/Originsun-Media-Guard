/**
 * awards.js — 站級獎項紀錄子視圖
 *
 * /portfolio 頁面頂部「Honors & Awards」榮譽牆來源。寫入後 60 秒對外網站重 build。
 */
import { websiteFetch, esc, toastOk, toastErr, renderLoadError, readRowPatch, emptyRow } from '../website-utils.js';

const _AWARD_LEVELS = ['獲獎', '入圍'];

function _levelOptions(selected) {
    return _AWARD_LEVELS.map(l =>
        `<option value="${l}" ${l === selected ? 'selected' : ''}>${l}</option>`
    ).join('');
}

let _awards = [];
let _container = null;

export default async function render(container, ctx = {}) {
    const { isCurrent = () => true } = ctx;
    _container = container;
    container.innerHTML = '<h2>🏆 獎項紀錄</h2><div style="color:#888;padding:20px;">載入中…</div>';
    try {
        const res = await websiteFetch('/api/website/admin/awards');
        if (!isCurrent()) return;
        _awards = res?.items || [];
    } catch (e) {
        if (!isCurrent()) return;
        renderLoadError(container, '🏆 獎項紀錄', e,
            'NAS website-api 可能跑舊版（沒 admin/awards endpoint）。請在 master 跑 /publish 同步後端到 NAS。');
        return;
    }
    _renderAll();
}

function _renderAll() {
    let visibleCount = 0, winCount = 0, nomCount = 0;
    for (const a of _awards) {
        if (!a.visible) continue;
        visibleCount++;
        if (a.level === '獲獎') winCount++;
        else if (a.level === '入圍') nomCount++;
    }
    const rows = _awards.map(a => `
        <tr>
            <td><input type="number" data-id="${a.id}" data-field="year" value="${a.year}" style="width:70px;" /></td>
            <td><input data-id="${a.id}" data-field="name_zh" value="${esc(a.name_zh)}" style="width:100%;" placeholder="獎項名稱" /></td>
            <td><input data-id="${a.id}" data-field="category" value="${esc(a.category || '')}" style="width:100%;" placeholder="類別" /></td>
            <td>
                <select data-id="${a.id}" data-field="level" style="width:100%;">${_levelOptions(a.level)}</select>
            </td>
            <td><input data-id="${a.id}" data-field="work_title" value="${esc(a.work_title || '')}" style="width:100%;" placeholder="作品" /></td>
            <td><input data-id="${a.id}" data-field="recipient" value="${esc(a.recipient || '')}" style="width:100%;" placeholder="得獎人" /></td>
            <td><input data-id="${a.id}" data-field="org" value="${esc(a.org || '')}" style="width:100%;" placeholder="頒獎單位" /></td>
            <td><input type="number" data-id="${a.id}" data-field="sort_order" value="${a.sort_order}" style="width:55px;" /></td>
            <td style="text-align:center;"><input type="checkbox" data-id="${a.id}" data-field="visible" ${a.visible ? 'checked' : ''} /></td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="window._awardsSave(${a.id})">💾</button>
                <button class="btn btn-sm btn-danger" onclick="window._awardsDelete(${a.id})">🗑</button>
            </td>
        </tr>
    `).join('');

    _container.innerHTML = `
        <h2>🏆 獎項紀錄 <span style="color:#888;font-size:13px;font-weight:400;">· ${visibleCount} 筆顯示中 · 獲獎 ${winCount} / 入圍 ${nomCount}</span></h2>
        <div style="color:#aaa;font-size:12px;margin-bottom:12px;">
            站級榮譽（公司獎項，不綁特定作品）。儲存後 60 秒內對外網站 /portfolio 頁面頂部會更新榮譽牆。
        </div>
        <div class="card" style="border-left:3px solid #c8a45c;">
            <div style="display:grid;grid-template-columns:70px 1.3fr 1fr 75px 1.3fr 1fr 1fr 55px auto;gap:6px;margin-bottom:8px;align-items:end;">
                <div><label style="color:#888;font-size:11px;">年份</label>
                    <input id="aw-new-year" type="number" min="1900" max="2100" placeholder="2024" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">獎項名稱 *</label>
                    <input id="aw-new-name" type="text" placeholder="第 60 屆金鐘獎" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">類別</label>
                    <input id="aw-new-cat" type="text" placeholder="商業類" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">等級</label>
                    <select id="aw-new-level" style="width:100%;">${_levelOptions('獲獎')}</select></div>
                <div><label style="color:#888;font-size:11px;">作品</label>
                    <input id="aw-new-work" type="text" placeholder="《父親的後座》" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">得獎人</label>
                    <input id="aw-new-recipient" type="text" placeholder="王小明（導演）" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">頒獎單位</label>
                    <input id="aw-new-org" type="text" placeholder="文化部影視局" style="width:100%;" /></div>
                <div><label style="color:#888;font-size:11px;">排序</label>
                    <input id="aw-new-sort" type="number" value="0" style="width:100%;" /></div>
                <button class="btn" onclick="window._awardsCreate()">+ 新增</button>
            </div>
            <table>
                <thead><tr><th style="width:70px;">年份</th><th>獎項</th><th>類別</th><th style="width:75px;">等級</th><th>作品</th><th>得獎人</th><th>頒獎單位</th><th style="width:55px;">排序</th><th style="width:50px;">顯示</th><th></th></tr></thead>
                <tbody>${rows || emptyRow(10, '尚無獎項，新增上方第一筆')}</tbody>
            </table>
        </div>
    `;
}


window._awardsCreate = async () => {
    const body = {
        year: Number(document.getElementById('aw-new-year').value || 0),
        name_zh: document.getElementById('aw-new-name').value.trim(),
        category: document.getElementById('aw-new-cat').value.trim() || null,
        level: document.getElementById('aw-new-level').value,
        work_title: document.getElementById('aw-new-work').value.trim() || null,
        recipient: document.getElementById('aw-new-recipient').value.trim() || null,
        org: document.getElementById('aw-new-org').value.trim() || null,
        sort_order: Number(document.getElementById('aw-new-sort').value || 0),
        visible: true,
    };
    if (!body.name_zh) { toastErr('獎項名稱必填'); return; }
    if (!body.year || body.year < 1900 || body.year > 2100) { toastErr('請填入有效年份'); return; }
    try {
        const created = await websiteFetch('/api/website/admin/awards', { method: 'POST', body });
        _awards.push(created);
        toastOk('已新增獎項');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};

window._awardsSave = async (id) => {
    try {
        const updated = await websiteFetch(`/api/website/admin/awards/${id}`, {
            method: 'PUT', body: readRowPatch('.card [data-id]', id),
        });
        const idx = _awards.findIndex(a => a.id === id);
        if (idx >= 0) _awards[idx] = updated;
        toastOk('已更新');
    } catch (e) { toastErr(e.message); }
};

window._awardsDelete = async (id) => {
    if (!confirm('確定刪除此獎項？')) return;
    try {
        await websiteFetch(`/api/website/admin/awards/${id}`, { method: 'DELETE' });
        _awards = _awards.filter(a => a.id !== id);
        toastOk('已刪除');
        _renderAll();
    } catch (e) { toastErr(e.message); }
};
