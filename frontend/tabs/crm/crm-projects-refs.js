/**
 * crm-projects-refs.js — CRM 專案詳情的「參考影片」分頁（參考影片庫 v2 階段 3）
 * ---
 * 專案可以引用片庫裡的片（跨提案共用資產）：掛上／改本案用途備註／解除，
 * 每列可跳該片的研究頁（/reference.html?id=）。掛載寫 preprod_reference_links。
 *
 * 照 crm-projects-media.js 的 lazy loader 慣例：分頁被點到才 import，
 * 每次點擊都重跑 loadRefsTab（內容依當前選中專案重抓）。
 */

import { esc } from './crm-utils.js';
// 用 authFetch（不是 crmFetch）—— 後者會自動加 /api/v1/crm 前綴，參考片端點不在那底下
import { authFetch } from '../../js/shared/utils.js';
import { autosave } from '../../js/shared/autosave.js';

const API = '/api/v1/references';

async function rfetch(path, opts = {}) {
    const r = await authFetch(path, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || ('HTTP ' + r.status));
    return d;
}

export async function loadRefsTab(projectId, container, libCache) {
    container.innerHTML = '<div class="crm-empty" style="padding:24px;">載入中…</div>';
    let linked = [];
    let lib = [];
    try {
        // 片庫清單（最多 500 支、每支帶 note/description/facets）只在第一次抓：
        // 掛上/解除只改「已引用」那份，沒必要把整個片庫再拖一次
        [linked, lib] = await Promise.all([
            rfetch(`${API}/for/crm_project/${encodeURIComponent(projectId)}`).then(d => d.references || []),
            libCache ? Promise.resolve(libCache)
                     : rfetch(`${API}?limit=500`).then(d => d.references || []),
        ]);
    } catch (e) {
        container.innerHTML = `<div class="crm-empty" style="padding:24px;color:#fca5a5;">參考影片載入失敗：${esc(e.message || e)}</div>`;
        return;
    }

    const linkedIds = new Set(linked.map(r => r.id));
    const pickable = lib.filter(r => !linkedIds.has(r.id));

    const rows = linked.map(r => `
        <div class="pjref-row" data-link="${esc(r.link_id)}" data-rid="${esc(r.id)}"
             data-embed="${esc(r.embed_url || '')}">
            <div class="pjref-thumb${r.embed_url ? ' playable' : ''}" ${r.embed_url ? 'title="點擊播放"' : ''}>${r.thumb_url
                // 縮圖 404（片庫封面過期、YouTube 換網址）要退回「無封面」，
                // 不能留一塊空框 —— 同 pins-panel / refThumb 的契約
                ? `<img src="${esc(r.thumb_url)}" alt="" loading="lazy"
                        onerror="this.insertAdjacentHTML('afterend','&lt;span&gt;無封面&lt;/span&gt;');this.remove();">`
                : '<span>無封面</span>'}${r.embed_url ? '<span class="playbtn">▶</span>' : ''}</div>
            <div class="pjref-main">
                <div class="pjref-title">${esc(r.title || r.url)}</div>
                ${r.note ? `<div class="pjref-note">${esc(r.note)}</div>` : ''}
                <input class="pjref-usenote" placeholder="本案為什麼引用它？（例：客戶指定這種節奏）"
                       value="${esc(r.link_note || '')}">
            </div>
            <div class="pjref-acts">
                <a href="/reference.html?id=${encodeURIComponent(r.id)}" target="_blank" rel="noopener">研究頁 ↗</a>
                ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">影片 ↗</a>` : ''}
                <button class="pjref-del">解除</button>
            </div>
        </div>`).join('');

    container.innerHTML = `
        <style>
        .pjref-wrap { padding: 14px 16px; }
        .pjref-row { display: flex; gap: 12px; align-items: flex-start; padding: 10px 0;
          border-bottom: 1px solid #2a2a2a; }
        .pjref-row:last-of-type { border-bottom: none; }
        .pjref-thumb { width: 120px; aspect-ratio: 16/9; background: #111; border: 1px solid #333;
          flex: none; display: flex; align-items: center; justify-content: center;
          color: #666; font-size: 10.5px; overflow: hidden; }
        .pjref-thumb img { width: 100%; height: 100%; object-fit: cover; }
        .pjref-thumb.playable { cursor: pointer; position: relative; }
        .pjref-thumb .playbtn { position: absolute; inset: 0; display: flex; align-items: center;
          justify-content: center; font-size: 22px; color: #fff;
          background: rgba(0,0,0,.35); opacity: .85; transition: opacity .15s; }
        .pjref-thumb.playable:hover .playbtn { opacity: 1; background: rgba(0,0,0,.5); }
        .pjref-player { width: 100%; aspect-ratio: 16/9; background: #000;
          border: 1px solid #333; margin: 8px 0 2px; position: relative; }
        .pjref-player iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
        .pjref-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
        .pjref-title { font-size: 13.5px; }
        .pjref-note { font-size: 11.5px; color: #8b8b8b; line-height: 1.6; }
        .pjref-usenote { background: transparent; border: 1px solid transparent; border-radius: 2px;
          color: #e8e8e8; font-size: 12px; padding: 4px 6px; font-family: inherit; outline: none; }
        .pjref-usenote:hover { border-color: #333; }
        .pjref-usenote:focus { border-color: #3b82f6; background: #1f1f1f; }
        .pjref-acts { display: flex; flex-direction: column; gap: 5px; align-items: flex-end; flex: none; }
        .pjref-acts a { font-size: 11.5px; color: #93c5fd; text-decoration: none; white-space: nowrap; }
        .pjref-acts a:hover { text-decoration: underline; }
        .pjref-del { background: none; border: 1px solid #333; border-radius: 2px; color: #8b8b8b;
          font-size: 11px; padding: 2px 8px; cursor: pointer; }
        .pjref-del:hover { border-color: #e05252; color: #e05252; }
        .pjref-add { display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
          margin-top: 14px; padding-top: 12px; border-top: 1px solid #2a2a2a; }
        .pjref-add select, .pjref-add input { background: #1f1f1f; border: 1px solid #333;
          border-radius: 2px; color: #e8e8e8; font-size: 12.5px; padding: 6px 8px; outline: none; }
        .pjref-add select { flex: 1; min-width: 200px; }
        .pjref-add button { background: #1f538d; border: 0; border-radius: 2px; color: #fff;
          font-size: 12px; padding: 6px 14px; cursor: pointer; }
        .pjref-msg { font-size: 11.5px; color: #8b8b8b; margin-top: 8px; min-height: 15px; }
        .pjref-msg.err { color: #fca5a5; }
        </style>
        <div class="pjref-wrap">
            ${rows || '<div class="crm-empty" style="padding:10px 0;">這個專案還沒引用任何參考影片。</div>'}
            <div class="pjref-add">
                <select id="pjref-pick">
                    <option value="">（從片庫挑一支…）</option>
                    ${pickable.map(r => `<option value="${esc(r.id)}">${esc(r.title || r.url)}</option>`).join('')}
                </select>
                <input id="pjref-why" placeholder="本案用途（可空）" style="min-width:180px;">
                <button id="pjref-link">＋ 引用</button>
                <a href="/reference.html" target="_blank" rel="noopener"
                   style="font-size:11.5px;color:#93c5fd;">開片庫 ↗</a>
            </div>
            <div class="pjref-add" style="border-top:none;margin-top:6px;padding-top:0;">
                <input id="pjref-new-url" placeholder="https://…（貼新片網址，YouTube / Vimeo / FB）" style="flex:1;min-width:220px;">
                <input id="pjref-new-title" placeholder="標題（可空）" style="min-width:140px;">
                <button id="pjref-create">＋ 入庫並引用</button>
            </div>
            <div class="pjref-msg" id="pjref-msg">片庫跨提案／專案共用：解除只拿掉本專案的引用，片子仍留在庫裡。</div>
        </div>`;

    const msg = container.querySelector('#pjref-msg');
    const say = (t, err) => { msg.textContent = t; msg.classList.toggle('err', !!err); };

    container.querySelector('#pjref-link').addEventListener('click', async () => {
        const rid = container.querySelector('#pjref-pick').value;
        if (!rid) { say('先挑一支片', true); return; }
        try {
            await rfetch(`${API}/${encodeURIComponent(rid)}/links`, {
                method: 'POST',
                body: { target_type: 'crm_project', target_id: projectId,
                        note: container.querySelector('#pjref-why').value.trim() },
            });
            await loadRefsTab(projectId, container, lib);
        } catch (e) { say('引用失敗：' + (e.message || e), true); }
    });

    // 貼網址 → 入庫 + 掛上本專案（一個動作；同網址已在庫就直接引用既有那支）
    container.querySelector('#pjref-create').addEventListener('click', async () => {
        const url = container.querySelector('#pjref-new-url').value.trim();
        if (!/^https?:\/\//i.test(url)) { say('網址要以 http:// 或 https:// 開頭', true); return; }
        try {
            const d = await rfetch(API, { method: 'POST', body: {
                url, title: container.querySelector('#pjref-new-title').value.trim(),
                link: { target_type: 'crm_project', target_id: projectId,
                        note: container.querySelector('#pjref-why').value.trim() },
            } });
            if (!d.created) say('這支片已在片庫，直接引用既有那支');
            await loadRefsTab(projectId, container);   // 片庫多了一支 → 不能沿用快取
        } catch (e) { say('新增失敗：' + (e.message || e), true); }
    });
    container.querySelector('#pjref-new-url').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') container.querySelector('#pjref-create').click();
    });

    // 點縮圖就地播放（再點一次收起；同時只開一支 —— 兩支同時響會打架）
    container.querySelectorAll('.pjref-thumb.playable').forEach(th => th.addEventListener('click', () => {
        const row = th.closest('.pjref-row');
        const open = row.querySelector('.pjref-player');
        if (open) { open.remove(); return; }
        container.querySelectorAll('.pjref-player').forEach(x => x.remove());
        const embed = row.dataset.embed;
        if (!embed) return;
        const box = document.createElement('div');
        box.className = 'pjref-player';
        const iframe = document.createElement('iframe');
        iframe.src = embed + (embed.includes('?') ? '&' : '?') + 'autoplay=1';
        iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture';
        iframe.allowFullscreen = true;
        box.appendChild(iframe);
        row.querySelector('.pjref-main').prepend(box);
    }));

    container.querySelectorAll('.pjref-del').forEach(btn => btn.addEventListener('click', async () => {
        const row = btn.closest('.pjref-row');
        if (!confirm('解除這個專案對這支片的引用？（片子仍留在片庫）')) return;
        try {
            await rfetch(`${API}/links/${encodeURIComponent(row.dataset.link)}`, { method: 'DELETE' });
            await loadRefsTab(projectId, container, lib);
        } catch (e) { say('解除失敗：' + (e.message || e), true); }
    }));

    // 本案用途備註：走共用 autosave（機制與片庫其他欄位同一份）
    container.querySelectorAll('.pjref-usenote').forEach(inp => autosave(inp, async (value, el) => {
        const rid = el.closest('.pjref-row').dataset.rid;
        await rfetch(`${API}/${encodeURIComponent(rid)}/links`, {
            method: 'POST',
            body: { target_type: 'crm_project', target_id: projectId, note: value },
        });
    }, { onOk: () => say('已儲存 ✓'), onError: (e) => say('儲存失敗：' + (e.message || e), true) }));
}
