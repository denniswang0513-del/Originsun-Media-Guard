/**
 * prop-editor.js — 新增 / 編輯提案的表單（後台提案庫與獨立企劃頁共用）。
 *
 * 🔴 狀態**刻意不在表單裡**：成案/未成案會建立或推進 CRM 專案、而且要留下
 * 組織學習的原因，那是管線動作不是欄位編輯（見 prop-actions.changeStatus）。
 * 把它做成一個 select 混在表單中間，等於讓人「順手」改掉一個有副作用的東西。
 *
 * 這裡是「一筆提案有哪些欄位」的唯一定義 —— 兩個介面共用同一份，以後加欄位
 * 兩邊同時有。
 *
 * ⚠️ **只在登入模式載入**：它打 /api/v1/crm/clients，而 NAS 對外容器的 nginx
 * 沒有 /api/v1/crm/ 這條 location。客戶走的公開 ?t= 路徑碰不到這支（也不該碰
 * —— 客戶不編提案）。
 */

import { esc } from '../../js/shared/utils.js';
import { tfetch } from './prop-fetch.js';
import { API, PTYPES } from './prop-const.js';
import { field, openDialog } from './prop-dialog.js';

// 客戶名錄變動很少，開表單開一次就好；快速建客戶後作廢。
let _clientsCache = null;
let _clientsInFlight = null;      // 同時開兩個表單只打一次

async function _clients() {
    if (_clientsCache) return _clientsCache;
    if (_clientsInFlight) return _clientsInFlight;
    _clientsInFlight = (async () => {
        try {
            const d = await tfetch('/api/v1/crm/clients');
            _clientsCache = (d.clients || []).map(c => ({ id: c.id, name: c.short_name }));
            return _clientsCache;
        } catch {
            // 🔴 **失敗不進快取**：`[]` 是 truthy，寫進去等於這個 session 之後每次
            // 開表單客戶下拉都是空的、而且永遠不會再試。客戶本來就可留空，
            // 這一次拿不到就這一次少一個下拉。
            return [];
        } finally {
            _clientsInFlight = null;
        }
    })();
    return _clientsInFlight;
}

/**
 * @param opts.proposal 要編輯的提案；null/省略 = 新增
 * @param opts.onSaved  async (proposal) => void   存好之後呼叫（呼叫端自己重畫）
 * @param opts.onCancel 取消/關閉時呼叫（可省略）
 */
export async function openProposalEditor({ proposal = null, onSaved, onCancel = null } = {}) {
    const isNew = !proposal;
    const v = (k) => esc((proposal && proposal[k]) || '');
    const opt = (val, label, on) => `<option value="${esc(val)}"${on ? ' selected' : ''}>${esc(label)}</option>`;

    const dlg = openDialog({
        title: isNew ? '＋ 新提案' : '編輯：' + (proposal.title || ''),
        width: 560,
        onClose: () => onCancel && onCancel(),
        body: `
            ${field('標題 *', `<input id="pe-title" value="${v('title')}"
                     placeholder="例：某公司 2026 品牌形象片提案">`)}
            <div class="pdlg-cols">
                ${field('客戶（可留空，之後在專案補）', `<div class="pdlg-inline">
                    <select id="pe-client">
                        ${opt('', '（未選客戶）')}
                        ${/* 🔴 現在的客戶要**同步**先放進去（詳情 payload 本來就
                              帶 client_name）。只等名錄回來才填的話，使用者在那之前
                              按儲存 → client_id 送空字串 → 後端 setattr 把既有的
                              客戶連結靜默清掉。名錄回來只負責補「其他」選項。 */
                          proposal && proposal.client_id
                            ? opt(proposal.client_id, proposal.client_name || '（目前客戶）', true)
                            : ''}
                    </select>
                    <button id="pe-client-new" class="pdlg-btn ghost"
                            title="快速建立潛在客戶" style="white-space:nowrap;">＋ 新客戶</button>
                </div>`)}
                ${field('類型', `<select id="pe-ptype">
                    ${opt('', '（未分類）')}
                    ${PTYPES.map(t => opt(t, t, proposal && proposal.ptype === t)).join('')}
                </select>`)}
            </div>
            <div class="pdlg-cols">
                ${field('提案日', `<input id="pe-pitch-date" type="date" value="${v('pitch_date')}">`)}
                ${field('預算範圍', `<input id="pe-budget" value="${v('budget_range')}" placeholder="例：80-120 萬">`)}
            </div>
            ${field('報價單 ID（可空，連 CRM 報價）', `<input id="pe-quotation" value="${v('quotation_id')}">`)}
            ${field('標籤（逗號分隔）', `<input id="pe-tags"
                     value="${esc(((proposal && proposal.tags) || []).join(', '))}"
                     placeholder="政府案, 高雄, 雙語">`)}
            ${field('成案/未成案原因（組織學習欄）', `<textarea id="pe-outcome" rows="3"
                     placeholder="轉成案或未成案時必填">${v('outcome_reason')}</textarea>`)}
            ${field('備註', `<textarea id="pe-notes" rows="3"
                     placeholder="補充說明、客戶偏好、內部提醒（企劃頁側欄也看得到）">${v('notes')}</textarea>`)}
            <div class="pdlg-err" id="pe-err"></div>
            <div class="pdlg-acts">
                <button id="pe-cancel" class="pdlg-btn ghost">取消</button>
                <button id="pe-save" class="pdlg-btn">${isNew ? '建立提案' : '儲存變更'}</button>
            </div>`,
    });

    const $ = (sel) => dlg.el.querySelector(sel);
    const err = $('#pe-err');
    $('#pe-cancel').addEventListener('click', dlg.close);
    // 客戶名錄晚一步填：冷快取時要打一次 API，先把框開起來比讓人乾等好
    // （同 project.html 的片庫挑選器）。現在的客戶已經在上面同步放進去了，
    // 這裡只補「其他」選項，而且**不動選取值** —— 使用者可能已經改過了
    // （包含剛按「＋ 新客戶」建的那個）。
    _clients().then(list => {
        const sel = $('#pe-client');
        if (!sel.isConnected) return;
        const have = new Set([...sel.options].map(o => o.value));
        sel.insertAdjacentHTML('beforeend',
            list.filter(c => !have.has(String(c.id))).map(c => opt(c.id, c.name)).join(''));
    });

    // 快速建立潛在客戶（後端 ClientPayload 預設 status=潛在客戶）→ 選單即時補上並選中
    $('#pe-client-new').addEventListener('click', async () => {
        const name = prompt('新客戶名稱（將以「潛在客戶」建檔）');
        if (!name || !name.trim()) return;
        try {
            const d = await tfetch('/api/v1/crm/clients',
                { method: 'POST', json: { short_name: name.trim() } });
            _clientsCache = null;                    // 下次開表單重抓
            const sel = $('#pe-client');
            const o = document.createElement('option');
            o.value = d.client.id;
            o.textContent = d.client.short_name;     // textContent：客戶名不進模板字串
            sel.appendChild(o);
            sel.value = d.client.id;
        } catch (e) { err.textContent = '建立客戶失敗：' + (e.message || e); }
    });

    const save = $('#pe-save');
    save.addEventListener('click', async () => {
        const title = $('#pe-title').value.trim();
        if (!title) { err.textContent = '標題必填'; $('#pe-title').focus(); return; }
        const body = {
            title,
            client_id: $('#pe-client').value,
            ptype: $('#pe-ptype').value,
            pitch_date: $('#pe-pitch-date').value || null,
            budget_range: $('#pe-budget').value.trim(),
            quotation_id: $('#pe-quotation').value.trim(),
            tags: $('#pe-tags').value.split(/[,，]/).map(t => t.trim()).filter(Boolean),
            outcome_reason: $('#pe-outcome').value.trim(),
            notes: $('#pe-notes').value,
        };
        save.disabled = true;
        err.textContent = '';
        try {
            const d = isNew
                ? await tfetch(API, { method: 'POST', json: body })
                : await tfetch(`${API}/${proposal.id}`, { method: 'PUT', json: body });
            dlg.close();
            if (onSaved) await onSaved(d.proposal);
        } catch (e) {
            err.textContent = (isNew ? '建立' : '儲存') + '失敗：' + (e.message || e);
            save.disabled = false;
        }
    });
    $('#pe-title').focus();
}
