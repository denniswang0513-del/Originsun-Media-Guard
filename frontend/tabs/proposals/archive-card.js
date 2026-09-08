/**
 * archive-card.js — 結案歸檔清單 + 專案回顧（KPTA）
 *
 * 掛在「完稿結案」的最上方，由 `delivery-view._mountArchive` 掛載（唯一的
 * 呼叫端 —— CRM 與專案頁都是經過它進來的）。fetcher 由呼叫端注入，為什麼
 * 非注入不可見 delivery-view 檔頭與 docs/PROPOSAL_PLANNER.md §15.3。
 *
 * 對齊 owner 的 Notion 專案啟動面版：「歸檔資料確認事項」+「專案回顧」。
 *
 * 範本、狀態選項、齊備度判定全部由後端給（core/project_archive.py 是正本）——
 * 這裡不硬寫任何一列項目名稱，之後後端加項目，前端不用改。
 *
 * 資料夾整合：「建立歸檔資料夾」在專案資產夾底下開 `歸檔/01_PPM資料…`，
 * 「掃描資料夾」把有檔案的項目自動標已收（只往前推進，不倒退人工標記）。
 *
 * 🔴 存檔**不重繪**（只更新記憶體 + 齊備度徽章）—— 備註/KPTA 是 debounce 存檔，
 * 打字停半秒就重建 DOM 會把游標和焦點吃掉。只有動到「有幾列」的操作
 * （加列/刪列/建夾/掃描）才整塊重畫。
 */

import { autosaveDelegated, syncBaseline } from '../../js/shared/autosave.js';
import { esc } from '../../js/shared/dom.js';

const AUTOSAVE_SEL = 'textarea[data-kpta], input[data-field="note"]';

/**
 * @param host       掛載節點
 * @param projectId
 * @param crmFetch   CRM 前綴的 fetcher（`(path, opts) => Promise<json>`）——
 *                   注入而不是 import，理由見檔頭。
 * @param opts.canManageFolders  明確 false＝不畫「建立歸檔資料夾」「掃描資料夾」
 *                   （兩支都動 NAS，後端管理員限定；旗標由呼叫端注入，理由同 delivery-view）。
 */
export async function renderArchiveCard(host, projectId, crmFetch, opts = {}) {
    const canFolders = opts.canManageFolders !== false;
    host.innerHTML = '<div class="crm-empty" style="padding:12px;">歸檔清單載入中…</div>';
    // 失敗直接往上拋 —— 唯一的呼叫端（delivery-view._mountArchive）有同一句
    // 錯誤畫面，這裡再 catch 一次就是同一段字的第二份（改字時必漂）
    let data = await crmFetch(`/projects/${projectId}/archive`);

    const say = (text, err = false) => {
        const el = host.querySelector('#arc-msg');
        if (!el) return;
        el.textContent = text;
        el.style.color = err ? '#f87171' : '#888';
    };

    /** 回後端算好的完整狀態；失敗一律丟出去給呼叫端決定怎麼說。 */
    const post = (path, method = 'POST', body = {}) =>
        crmFetch(`/projects/${projectId}${path}`,
                 { method, body: JSON.stringify(body) });

    /** 只更新齊備度徽章 —— 存單格時用，不動 DOM 其他地方（免搶焦點）。 */
    const syncBadge = (fresh) => {
        data = fresh;
        const el = host.querySelector('#arc-badge');
        const p = fresh.progress || {};
        if (!el) return;
        el.textContent = p.ready ? '歸檔資料到齊' : `${p.done}/${p.total}`;
        el.style.background = p.ready ? '#166534' : '';
        el.style.color = p.ready ? '#dcfce7' : '';
    };

    // 備註 / KPTA 的自動儲存：委派在 host 上，整塊重畫換新節點也不用重綁
    autosaveDelegated(host, AUTOSAVE_SEL, async (value, el) => {
        say('儲存中…');
        const kp = el.dataset.kpta;
        syncBadge(kp
            ? await post('/review', 'PATCH', { key: kp, value })
            : await post('/archive', 'PATCH',
                         { key: el.closest('tr').dataset.key, field: 'note', value }));
    }, {
        onOk: () => say('已儲存'),
        onError: (e) => say('儲存失敗：' + (e.message || e), true),
    });

    /** 會動到列數的操作 → 整塊重畫。 */
    async function structural(path, method, body, okText) {
        say('處理中…');
        try {
            data = await post(path, method, body);
            paint();
            say(okText || '已儲存');
            return data;
        } catch (e) {
            say('失敗：' + (e.message || e), true);
            return null;
        }
    }

    async function folderAction(path, okPrefix) {
        say('處理中…（要走 NAS，可能要等幾秒）');
        try {
            data = await post(path);
            paint();
            const marked = (data.marked || []).length;
            say(`${okPrefix}：掃到 ${(data.scanned || []).length} 個有東西的資料夾`
                + (marked ? `，自動標了 ${marked} 項已收` : '，沒有新的可標記'));
        } catch (e) {
            say('失敗：' + (e.message || e), true);
        }
    }

    function paint() {
        const p = data.progress || { done: 0, total: 0, ready: false };
        const isExtra = (k) => String(k).startsWith('x-');

        host.innerHTML = `
            <div class="crm-card" style="margin-bottom:12px;">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
                    <h3 style="margin:0;font-size:14px;color:#ddd;">歸檔資料確認事項</h3>
                    <span class="crm-badge" id="arc-badge"${p.ready ? ' style="background:#166534;color:#dcfce7;"' : ' style="opacity:.8;"'}
                        >${p.ready ? '歸檔資料到齊' : `${p.done}/${p.total}`}</span>
                    <span style="flex:1;"></span>
                    ${canFolders ? `<button class="crm-btn crm-btn-secondary crm-btn-sm" data-act="folders"
                        title="在專案資產夾底下建立 ${esc(data.root_folder)}/01_PPM資料… 這組子資料夾">建立歸檔資料夾</button>
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" data-act="scan"
                        title="掃描歸檔資料夾，有檔案的項目自動標已收">掃描資料夾</button>` : ''}
                    <button class="crm-btn crm-btn-secondary crm-btn-sm" data-act="add">＋ 自訂項目</button>
                </div>
                <table style="width:100%;border-collapse:collapse;font-size:12.5px;color:#ccc;table-layout:fixed;">
                    <thead><tr style="color:#888;text-align:left;font-size:11.5px;">
                        <th style="padding:4px 6px;width:24%;">項目</th>
                        <th style="padding:4px 6px;">說明</th>
                        <th style="padding:4px 6px;width:110px;">狀態</th>
                        <th style="padding:4px 6px;width:26%;">備註</th>
                    </tr></thead>
                    <tbody>${(data.checklist || []).map(r => `
                        <tr data-key="${esc(r.key)}" style="border-top:1px solid #333;">
                            <td style="padding:6px;font-weight:600;word-break:break-all;">
                                ${esc(r.label)}
                                ${isExtra(r.key) ? '<button data-act="del" style="border:0;background:none;color:#666;cursor:pointer;">×</button>' : ''}
                                ${r.folder ? `<div style="color:#666;font-size:10.5px;font-weight:400;font-family:monospace;">${esc(r.folder)}</div>` : ''}
                            </td>
                            <td style="padding:6px;color:#999;white-space:pre-line;">${esc(r.hint || '')}</td>
                            <td style="padding:6px;">
                                <select data-field="status" style="width:100%;">
                                    ${(data.statuses || []).map(s =>
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
                    ${(data.kpta_fields || []).map(f => `
                        <div>
                            <div style="color:#888;font-size:11.5px;margin-bottom:4px;">${esc(f.label)}</div>
                            <textarea data-kpta="${esc(f.key)}" rows="4"
                                style="width:100%;box-sizing:border-box;font-size:12.5px;line-height:1.7;"></textarea>
                        </div>`).join('')}
                </div>
                <div style="color:#666;font-size:11px;padding:8px 0 0;">改完會自動儲存。</div>
            </div>`;

        // 值走 DOM property（不進模板字串）
        (data.checklist || []).forEach(r => {
            const tr = host.querySelector(`tr[data-key="${CSS.escape(r.key)}"]`);
            if (!tr) return;
            tr.querySelector('[data-field="note"]').value = r.note || '';
            // 狀態是 select：change 即送，不需要 debounce（且會動到齊備度）
            tr.querySelector('[data-field="status"]').addEventListener('change', async (e) => {
                say('儲存中…');
                try {
                    syncBadge(await post('/archive', 'PATCH',
                                         { key: r.key, field: 'status', value: e.target.value }));
                    say('已儲存');
                } catch (err) { say('儲存失敗：' + (err.message || err), true); }
            });
            tr.querySelector('[data-act="del"]')?.addEventListener('click', () => {
                if (confirm(`移除項目「${r.label}」？`)) {
                    structural(`/archive/rows/${encodeURIComponent(r.key)}`, 'DELETE', {}, '已移除');
                }
            });
        });
        (data.kpta_fields || []).forEach(f => {
            const ta = host.querySelector(`[data-kpta="${CSS.escape(f.key)}"]`);
            if (ta) ta.value = (data.kpta || {})[f.key] || '';
        });
        syncBaseline(host, AUTOSAVE_SEL);   // 重畫後把 dirty 基準對齊當下值

        host.querySelector('[data-act="add"]').addEventListener('click', () => {
            const label = (prompt('新項目名稱（例：客戶簽收單）：') || '').trim();
            if (label) structural('/archive/rows', 'POST', { label }, '已新增');
        });
        host.querySelector('[data-act="folders"]')
            ?.addEventListener('click', () => folderAction('/archive/folders', '資料夾已建立'));
        host.querySelector('[data-act="scan"]')
            ?.addEventListener('click', () => folderAction('/archive/scan', '掃描完成'));
    }

    paint();
}
