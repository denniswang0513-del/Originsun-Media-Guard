/**
 * crm-projects-plan.js — 專案詳情「提案企劃」分頁（提案=專案合體，2026-08-06）
 *
 * 專案的提案衛星列（preprod_proposals.project_id）→ 嵌現成的 plan-matrix
 * 企劃元件（同 提案庫 overlay / proposal-plan.html 的登入模式）。
 * 沒有衛星列（非提案出身的專案）→ 空狀態 + 一鍵建立
 * （POST /proposals {title: 專案名, project_id} — 後端掛載、不另建殼專案）。
 */

import { crmFetch, crmToast, esc } from './crm-utils.js';
import { fmtSize } from '../../js/shared/clip_utils.js';
import { authDownload, copyText, folderCrumbsHtml, wireFileDrop } from '../../js/shared/utils.js';
import { tfetch } from '../proposals/prop-fetch.js';
import { state } from './crm-projects-state.js';

const API = '/api/v1/proposals';

// ES module map 會永久快取 rejected import — **失敗後的重試**才帶 cache-bust
// query（proposals.js / subview-loader.js 同款教訓）；成功路徑永遠走裸路徑
// 吃 module cache，不然每次開分頁都重載一份新模組實例。
let _matrixFailed = false;

function _fail(host, prefix, e) {
    if (!host.isConnected) return;
    host.innerHTML = `<div class="crm-empty" style="padding:24px;color:#fca5a5;">${prefix}: ${esc(e.message || String(e))}</div>`;
}

export async function loadPlanTab(projectId, host) {
    host.innerHTML = '<div class="crm-empty">載入中...</div>';
    let props, assets;
    try {
        [props, assets] = await Promise.all([
            tfetch(`${API}?project_id=${encodeURIComponent(projectId)}`)
                .then(d => d.proposals || []),
            // 資產夾設定：拿不到（權限/DB）不擋企劃本體
            crmFetch(`/projects/${projectId}/proposal-assets`).catch(() => null),
        ]);
    } catch (e) {
        _fail(host, '提案企劃載入失敗', e);
        return;
    }
    if (state.selectedId !== projectId || !host.isConnected) return;   // await 期間已切走
    if (!props.length) {
        _renderEmpty(projectId, host);
    } else {
        await _renderPlan(props[0], projectId, host);
    }
    if (assets && host.isConnected) _mountAssetsCard(projectId, host, assets);
}

// ── 資產資料夾設定（比照影像紀錄：root 是全站共用的一份，路徑帶 project_id
//    只是讓人從專案面板順手改；顯示的資料夾是這個專案的）────────────
function _mountAssetsCard(projectId, host, d) {
    const box = document.createElement('div');
    box.style.cssText = 'padding:10px 12px 16px;';
    box.innerHTML = `
        <div style="border:1px solid #2a2a2a;border-radius:6px;padding:10px 12px;background:#181818;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                <span style="font-size:12px;color:#8b8b8b;">提案資產資料夾${
                    d.root_set ? '' : '<span style="color:#fbbf24;"> — 目前搆不到這個路徑</span>'}</span>
                ${d.folder_name ? `<code id="pp-path" title="點一下複製完整路徑（貼進檔案總管就能開）"
                    style="font-size:11.5px;color:#8b8b8b;cursor:pointer;text-decoration:underline;text-underline-offset:3px;"
                    >${esc(d.folder_name)}</code>` : ''}
                <span style="flex:1;"></span>
                <button id="pp-mkdir" class="crm-btn crm-btn-secondary crm-btn-sm">＋ 新增資料夾</button>
                <button id="pp-add" class="crm-btn crm-btn-primary crm-btn-sm">＋ 上傳檔案</button>
                <button id="pp-open" class="crm-btn crm-btn-secondary crm-btn-sm"
                        title="在這台伺服器上開啟檔案總管 — 你人不在伺服器前的話請改用「點路徑複製」">開啟資料夾</button>
                <button id="pp-cfg" class="crm-btn crm-btn-secondary crm-btn-sm" title="設定根目錄">⚙</button>
            </div>
            <div id="pp-drop" style="border:1px dashed #3a3a3a;border-radius:4px;">
                <div id="pp-files"></div>
            </div>
            <input id="pp-file-input" type="file" multiple style="display:none;">
            <div id="pp-cfg-row" style="display:none;gap:6px;align-items:center;margin-top:8px;">
                <input id="pp-root" type="text" class="crm-input" style="flex:1;"
                       value="${esc(d.root || '')}" placeholder="例：\\\\192.168.1.132\\Archive\\00_提案企劃">
                <button id="pp-root-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存</button>
            </div>
            <div style="font-size:11.5px;color:#6b6b6b;margin-top:6px;">
                所有專案共用根資料夾，各專案自動建子夾。單檔上限 300MB，可執行檔會被擋下。
            </div>
        </div>`;
    host.appendChild(box);
    _renderFiles(box, d);
    _wireUpload(projectId, box, host, d);

    box.querySelector('#pp-cfg').addEventListener('click', () => {
        const row = box.querySelector('#pp-cfg-row');
        row.style.display = row.style.display === 'none' ? 'flex' : 'none';
    });

    box.querySelector('#pp-root-save').addEventListener('click', async () => {
        const root = box.querySelector('#pp-root').value.trim();
        try {
            await crmFetch(`/projects/${projectId}/proposal-assets/settings`,
                { method: 'POST', body: JSON.stringify({ root }) });
            crmToast('已儲存資產資料夾設定');
            loadPlanTab(projectId, host);   // root_set / 資料夾路徑會跟著變
        } catch (e) { alert('儲存失敗：' + (e.message || e)); }
    });
    // 「開啟資料夾」在**伺服器上**跑 explorer（api_utils.open_folder）—— 你人坐在
    // master 前才看得到視窗跳出來。從別台電腦的瀏覽器點是靜靜什麼都不發生，
    // 所以路徑本身要點得到、複製得走（貼進檔案總管是那條路真正可用的版本）。
    box.querySelector('#pp-open').addEventListener('click', () => {
        const path = d.project_folder || d.root || '';
        if (!path) { alert('尚未設定提案資產資料夾'); return; }
        fetch('/api/v1/utils/open_folder', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        }).catch(() => {});
    });
    box.querySelector('#pp-path')?.addEventListener('click', (e) => {
        const path = d.project_folder || d.root || '';
        if (path) copyText(path, e.currentTarget);   // 回饋走按鈕本身，不再疊 toast
    });
}

// 檔案列表：一次列一層（子資料夾可以走進去；連結進來的舊資料夾常有好幾層），
// 標星號的是「提案簡報」（提案詳情與公開共編頁顯示的那一份）
function _renderFiles(box, d) {
    const dirs = d.dirs || [];
    const files = d.files || [];
    const list = box.querySelector('#pp-files');
    const crumbs = _crumbsHtml(d);
    if (!dirs.length && !files.length) {
        list.innerHTML = crumbs + `<div style="padding:18px;text-align:center;color:#6b6b6b;font-size:12px;">
            這一層是空的 — 把檔案拖進來，或按「＋ 上傳檔案」／「＋ 新增資料夾」</div>`;
        return;
    }
    list.innerHTML = crumbs + dirs.map(x => `
        <div data-dir="${esc(x.rel)}"
             style="display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;
                    border-bottom:1px solid #242424;font-size:12.5px;">
            <span style="color:#8b8b8b;">▸</span>
            <span style="font-weight:600;">${esc(x.name)}</span>
        </div>`).join('')
        + files.map(f => {
            const isDeck = f.rel === d.deck_rel;
            return `
        <div class="pp-file" data-rel="${esc(f.rel)}"
             style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid #242424;font-size:12.5px;">
            <span title="${isDeck ? '目前的提案簡報' : '設為提案簡報'}" data-deck
                  style="cursor:pointer;color:${isDeck ? '#fbbf24' : '#3a3a3a'};">★</span>
            <span data-dl style="flex:1;cursor:pointer;" title="下載">${esc(f.filename || f.rel)}</span>
            <span style="color:#6b6b6b;">${fmtSize(f.size_bytes)}</span>
            <span style="color:#6b6b6b;">${esc((f.mtime ? new Date(f.mtime * 1000).toISOString() : '').slice(0, 10))}</span>
            <button data-del class="crm-btn crm-btn-secondary crm-btn-sm" title="刪除">✕</button>
        </div>`;
        }).join('') + (d.truncated
        ? '<div style="padding:6px 10px;color:#fbbf24;font-size:11.5px;">項目過多，只顯示前 1000 筆</div>' : '');
}

// 麵包屑（最外層不顯示 —— 沒得往回走時它只是噪音）
function _crumbsHtml(d) {
    if (!d.rel) return '';
    return `<div style="padding:6px 10px;font-size:12px;border-bottom:1px solid #242424;">${
        folderCrumbsHtml(d.folder_name || '根目錄', d.rel)}</div>`;
}

function _wireUpload(projectId, box, host, d) {
    const input = box.querySelector('#pp-file-input');
    const drop = box.querySelector('#pp-drop');
    // 先改 d、再無參重畫 —— 只重繪檔案區，不重跑 loadPlanTab（那會連
    // plan-matrix 一起重畫，使用者正在編的格子與捲動位置都沒了）
    const repaint = () => _renderFiles(box, d);
    // 走過的層記在 levels：往回走是最常見的動作，重打一次是整趟 NAS 掃描。
    // 存**快照**不是 d 本身 —— d 會被後續導覽 Object.assign 覆寫，存參照等於
    // 「回最外層」拿回來的是當前那一層。
    const levels = new Map([[d.rel || '', { ...d }]]);
    // 內容變了（上傳/開夾/刪檔）就換上這一層的新版本：寫進 d + 更新快取 + 重畫。
    // 三步分開手寫的話，忘記的永遠是中間那步 —— 走回這一層看到過期內容。
    const applyLevel = (r) => {
        if (r) Object.assign(d, { dirs: r.dirs, files: r.files, truncated: r.truncated });
        levels.set(d.rel || '', { ...d });
        repaint();
    };
    // 標星號：只動兩顆 span，不為了換一個顏色重建整張列表（可能上千列）
    const markDeck = (rel) => {
        d.deck_rel = rel;
        box.querySelectorAll('.pp-file').forEach(row => {
            const star = row.querySelector('[data-deck]');
            const on = row.dataset.rel === rel;
            star.style.color = on ? '#fbbf24' : '#3a3a3a';
            star.title = on ? '目前的提案簡報' : '設為提案簡報';
        });
    };

    // 走進子資料夾 / 麵包屑往回 —— 換 d 的這一層再重畫（企劃矩陣不受影響）
    const goto = async (rel) => {
        const hit = levels.get(rel);
        if (hit) { Object.assign(d, hit); repaint(); return; }
        try {
            const r = await crmFetch(
                `/projects/${projectId}/proposal-assets?rel=${encodeURIComponent(rel)}`);
            levels.set(rel, r);
            if (!box.isConnected || state.selectedId !== projectId) return;  // 已切走
            Object.assign(d, r);
            repaint();
        } catch (e) { alert('開啟資料夾失敗：' + (e.message || e)); }
    };

    const send = async (fileList) => {
        if (!fileList || !fileList.length) return;
        const fd = new FormData();
        for (const f of fileList) fd.append('files', f);
        try {
            // 落在使用者目前看的那一層
            const r = await crmFetch(`/projects/${projectId}/proposal-assets/upload`
                + `?rel=${encodeURIComponent(d.rel || '')}`,
                { method: 'POST', body: fd });   // crmFetch 認得 FormData，不設 Content-Type
            const bad = (r.skipped || []).map(s => `${s.filename}（${s.reason}）`);
            crmToast(`已上傳 ${(r.saved || []).length} 個檔案`
                + (bad.length ? `；略過 ${bad.length} 個` : ''), bad.length ? 6000 : 2000);
            if (bad.length) console.warn('略過：', bad.join('、'));
            if (r.files) applyLevel(r);         // 端點順手回了這一層，不必再掃一次
        } catch (e) { alert('上傳失敗：' + (e.message || e)); }
    };

    box.querySelector('#pp-add').addEventListener('click', () => input.click());
    input.addEventListener('change', () => { send(input.files); input.value = ''; });
    wireFileDrop(drop, send);

    box.querySelector('#pp-mkdir').addEventListener('click', async () => {
        const name = prompt('新資料夾名稱（會建在你目前看的這一層）：');
        if (!name || !name.trim()) return;
        try {
            const r = await crmFetch(`/projects/${projectId}/proposal-assets/mkdir`
                + `?rel=${encodeURIComponent(d.rel || '')}`,
                { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
            crmToast(`已建立「${r.created}」`);
            applyLevel(r);
        } catch (e) { alert('建立失敗：' + (e.message || e)); }
    });

    // 列表操作（事件委派 —— 列表每次重畫）
    box.querySelector('#pp-files').addEventListener('click', async (e) => {
        const nav = e.target.closest('[data-dir],[data-crumb]');
        if (nav) { goto(nav.dataset.dir ?? nav.dataset.crumb); return; }
        const row = e.target.closest('.pp-file');
        if (!row) return;
        const rel = row.dataset.rel;
        const q = `?rel=${encodeURIComponent(rel)}`;
        if (e.target.closest('[data-del]')) {
            if (!confirm(`確定刪除「${rel}」？檔案會從資料夾移除。`)) return;
            try {
                const r = await crmFetch(`/projects/${projectId}/proposal-assets/file${q}`,
                    { method: 'DELETE' });
                crmToast(r.deck_cleared ? '已刪除（原本是提案簡報，已取消指定）' : '已刪除');
                if (r.deck_cleared) d.deck_rel = '';
                d.files = (d.files || []).filter(f => f.rel !== rel);
                if (d.files.length) {
                    row.remove();           // 移一列就好，不必重建整張
                    levels.set(d.rel || '', { ...d });    // 快照跟著少一筆
                } else {
                    applyLevel(null);       // 空了要顯示空狀態（d 已是最新）
                }
            } catch (err) { alert('刪除失敗：' + (err.message || err)); }
        } else if (e.target.closest('[data-deck]')) {
            try {
                await crmFetch(`/projects/${projectId}/proposal-assets/deck`,
                    { method: 'POST', body: JSON.stringify({ rel }) });
                crmToast('已設為提案簡報');
                markDeck(rel);
            } catch (err) { alert('設定失敗：' + (err.message || err)); }
        } else if (e.target.closest('[data-dl]')) {
            authDownload(`/api/v1/crm/projects/${projectId}/proposal-assets/file${q}`, rel);
        }
    });
}

function _renderEmpty(projectId, host) {
    host.innerHTML = `
        <div class="crm-empty" style="padding:36px 24px;text-align:center;">
            <div style="margin-bottom:14px;">此專案還沒有提案企劃</div>
            <button id="pp-create" class="crm-btn crm-btn-primary">建立提案企劃</button>
            <div style="margin-top:10px;color:#8b8b8b;font-size:12px;">建立後可用企劃矩陣共編、掛參考影片、產生公開共編連結</div>
        </div>`;
    host.querySelector('#pp-create').addEventListener('click', async () => {
        const project = state.projects.find(p => p.id === projectId);
        try {
            await tfetch(API, { method: 'POST', json: {
                title: (project && project.name) || '未命名提案',
                project_id: projectId,
            } });
            loadPlanTab(projectId, host);
        } catch (e) { alert('建立失敗：' + (e.message || e)); }
    });
}

async function _renderPlan(listItem, projectId, host) {
    let prop;
    try {
        prop = (await tfetch(`${API}/${listItem.id}`)).proposal;
    } catch (e) {
        _fail(host, '提案載入失敗', e);
        return;
    }
    if (state.selectedId !== projectId || !host.isConnected) return;
    host.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;padding:10px 12px 0;">
            <span class="crm-badge" style="opacity:.8;">${esc(prop.status || '草稿')}</span>
            <span style="font-weight:600;">${esc(prop.title)}</span>
            ${prop.pitch_date ? `<span style="color:#8b8b8b;font-size:12px;">提案日 ${esc(prop.pitch_date)}</span>` : ''}
            <span style="flex:1;"></span>
            <a href="/proposal-plan.html?pid=${encodeURIComponent(prop.id)}" target="_blank" rel="noopener"
               style="color:#60a5fa;font-size:12px;">獨立視窗開啟 ↗</a>
        </div>
        <div id="pp-matrix" style="padding:4px 12px 16px;"></div>`;
    const matrixHost = host.querySelector('#pp-matrix');
    try {
        const path = _matrixFailed
            ? `../proposals/plan-matrix.js?t=${Date.now()}`
            : '../proposals/plan-matrix.js';
        const { renderPlan } = await import(path);
        _matrixFailed = false;
        if (!matrixHost.isConnected) return;
        renderPlan(matrixHost, {
            proposalId: prop.id, plan: prop.plan || null,
            fetcher: tfetch, canShare: true,
        });
    } catch (e) {
        _matrixFailed = true;
        if (matrixHost.isConnected) {
            matrixHost.innerHTML = `<div class="crm-empty" style="color:#fca5a5;">企劃元件載入失敗：${esc(e.message || e)}（切回再開重試）</div>`;
        }
    }
}
