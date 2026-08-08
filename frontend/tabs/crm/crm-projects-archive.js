/**
 * crm-projects-archive.js — 結案歸檔清單 + 專案回顧（KPTA）
 *
 * 掛在專案詳情「完稿結案」分頁的最上方（官網上架編輯器 iframe 之前）。
 * 對齊 owner 的 Notion 專案啟動面版：「歸檔資料確認事項」+「專案回顧」。
 *
 * 範本、狀態選項、齊備度判定全部由後端給（core/project_archive.py 是正本）——
 * 這裡不硬寫任何一列項目名稱，之後後端加項目，前端不用改。
 *
 * 資料夾整合：「建立歸檔資料夾」在專案資產夾底下開 `歸檔/01_PPM資料…`，
 * 「掃描資料夾」把有檔案的項目自動標已收（只往前推進，不倒退人工標記）。
 */

import { crmFetch, esc } from './crm-utils.js';

const SAVE_DEBOUNCE_MS = 800;
let _state = null;          // { projectId, data }
const _timers = new Map();

export async function renderArchiveCard(host, projectId) {
    host.innerHTML = '<div class="crm-empty" style="padding:12px;">歸檔清單載入中…</div>';
    try {
        const data = await crmFetch(`/projects/${projectId}/archive`);
        _state = { projectId, data };
        _paint(host);
    } catch (e) {
        host.innerHTML = `<div class="crm-empty" style="padding:12px;">歸檔清單載入失敗：${esc(e.message || e)}</div>`;
    }
}

function _say(host, text, err = false) {
    const el = host.querySelector('#arc-msg');
    if (!el) return;
    el.textContent = text;
    el.style.color = err ? '#f87171' : '#888';
}

/** crmFetch 的 body 是**字串**（直接餵 fetch）—— 這裡統一 stringify，
 *  呼叫端傳物件就好。 */
async function _post(host, path, { method, body }, okText) {
    _say(host, '處理中…');
    try {
        _state.data = await crmFetch(`/projects/${_state.projectId}${path}`,
                                     { method, body: JSON.stringify(body || {}) });
        _paint(host);
        _say(host, okText || '已儲存');
        return _state.data;
    } catch (e) {
        _say(host, (okText ? '' : '儲存失敗：') + (e.detail || e.message || e), true);
        return null;
    }
}

function _debounced(host, id, fn) {
    clearTimeout(_timers.get(id));
    _timers.set(id, setTimeout(fn, SAVE_DEBOUNCE_MS));
}

function _paint(host) {
    const d = _state.data;
    const p = d.progress || { done: 0, total: 0, ready: false };
    const isExtra = (k) => String(k).startsWith('x-');
    const badge = p.ready
        ? '<span class="crm-badge" style="background:#166534;color:#dcfce7;">歸檔資料到齊</span>'
        : `<span class="crm-badge" style="opacity:.8;">${p.done}/${p.total}</span>`;

    host.innerHTML = `
        <div class="crm-card" style="margin-bottom:12px;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
                <h3 style="margin:0;font-size:14px;color:#ddd;">歸檔資料確認事項</h3>
                ${badge}
                <span style="flex:1;"></span>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" data-act="folders"
                    title="在專案資產夾底下建立 ${esc(d.root_folder)}/01_PPM資料… 這組子資料夾">建立歸檔資料夾</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" data-act="scan"
                    title="掃描歸檔資料夾，有檔案的項目自動標已收">掃描資料夾</button>
                <button class="crm-btn crm-btn-secondary crm-btn-sm" data-act="add">＋ 自訂項目</button>
            </div>
            <table style="width:100%;border-collapse:collapse;font-size:12.5px;color:#ccc;table-layout:fixed;">
                <thead><tr style="color:#888;text-align:left;font-size:11.5px;">
                    <th style="padding:4px 6px;width:24%;">項目</th>
                    <th style="padding:4px 6px;">說明</th>
                    <th style="padding:4px 6px;width:110px;">狀態</th>
                    <th style="padding:4px 6px;width:26%;">備註</th>
                </tr></thead>
                <tbody>${(d.checklist || []).map(r => `
                    <tr data-key="${esc(r.key)}" style="border-top:1px solid #333;">
                        <td style="padding:6px;font-weight:600;word-break:break-all;">
                            ${esc(r.label)}
                            ${isExtra(r.key) ? '<button class="crm-btn-link" data-act="del" style="border:0;background:none;color:#666;cursor:pointer;">×</button>' : ''}
                            ${r.folder ? `<div style="color:#666;font-size:10.5px;font-weight:400;font-family:monospace;">${esc(r.folder)}</div>` : ''}
                        </td>
                        <td style="padding:6px;color:#999;white-space:pre-line;">${esc(r.hint || '')}</td>
                        <td style="padding:6px;">
                            <select data-field="status" style="width:100%;">
                                ${(d.statuses || []).map(s =>
                                    `<option value="${esc(s)}"${s === r.status ? ' selected' : ''}>${esc(s)}</option>`).join('')}
                            </select>
                        </td>
                        <td style="padding:6px;"><input data-field="note" style="width:100%;box-sizing:border-box;"></td>
                    </tr>`).join('')}
                </tbody>
            </table>
            <div id="arc-msg" style="font-size:11.5px;color:#888;padding:8px 0 0;"></div>
        </div>

        <div class="crm-card" style="margin-bottom:12px;">
            <h3 style="margin:0 0 10px;font-size:14px;color:#ddd;">專案回顧</h3>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;">
                ${(d.kpta_fields || []).map(f => `
                    <div>
                        <div style="color:#888;font-size:11.5px;margin-bottom:4px;">${esc(f.label)}</div>
                        <textarea data-kpta="${esc(f.key)}" rows="4"
                            style="width:100%;box-sizing:border-box;font-size:12.5px;line-height:1.7;"></textarea>
                    </div>`).join('')}
            </div>
            <div style="color:#666;font-size:11px;padding:8px 0 0;">改完會自動儲存。</div>
        </div>`;

    // 值走 DOM property（不進模板字串）
    (d.checklist || []).forEach(r => {
        const tr = host.querySelector(`tr[data-key="${CSS.escape(r.key)}"]`);
        if (!tr) return;
        const note = tr.querySelector('[data-field="note"]');
        note.value = r.note || '';
        note.addEventListener('input', () => _debounced(host, `${r.key}/note`,
            () => _post(host, '/archive', { method: 'PATCH', body: { key: r.key, field: 'note', value: note.value } })));
        tr.querySelector('[data-field="status"]').addEventListener('change', (e) =>
            _post(host, '/archive', { method: 'PATCH', body: { key: r.key, field: 'status', value: e.target.value } }));
        tr.querySelector('[data-act="del"]')?.addEventListener('click', () => {
            if (!confirm(`移除項目「${r.label}」？`)) return;
            _post(host, `/archive/rows/${encodeURIComponent(r.key)}`, { method: 'DELETE' });
        });
    });

    (d.kpta_fields || []).forEach(f => {
        const ta = host.querySelector(`[data-kpta="${CSS.escape(f.key)}"]`);
        if (!ta) return;
        ta.value = (d.kpta || {})[f.key] || '';
        ta.addEventListener('input', () => _debounced(host, `kpta/${f.key}`,
            () => _post(host, '/review', { method: 'PATCH', body: { key: f.key, value: ta.value } })));
    });

    host.querySelector('[data-act="add"]').addEventListener('click', () => {
        const label = (prompt('新項目名稱（例：客戶簽收單）：') || '').trim();
        if (label) _post(host, '/archive/rows', { method: 'POST', body: { label } });
    });
    host.querySelector('[data-act="folders"]').addEventListener('click', () =>
        _folderAction(host, '/archive/folders', '資料夾已建立'));
    host.querySelector('[data-act="scan"]').addEventListener('click', () =>
        _folderAction(host, '/archive/scan', '掃描完成'));
}

async function _folderAction(host, path, okPrefix) {
    _say(host, '處理中…（要走 NAS，可能要等幾秒）');
    const d = await _post(host, path, { method: 'POST', body: {} }, ' ');
    if (!d) return;
    const marked = (d.marked || []).length;
    _say(host, `${okPrefix}：掃到 ${(d.scanned || []).length} 個有東西的資料夾`
              + (marked ? `，自動標了 ${marked} 項已收` : '，沒有新的可標記'));
}
