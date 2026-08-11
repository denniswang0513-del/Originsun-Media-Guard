/**
 * crm-projects-plan.js — 專案詳情「提案企劃」分頁（提案=專案合體，2026-08-06）
 *
 * 專案的提案衛星列（preprod_proposals.project_id）→ 嵌現成的 plan-matrix
 * 企劃元件（同 提案庫 overlay / proposal-plan.html 的登入模式）。
 * 沒有衛星列（非提案出身的專案）→ 空狀態 + 一鍵建立
 * （POST /proposals {title: 專案名, project_id} — 後端掛載、不另建殼專案）。
 */

import { crmFetch, crmToast, esc } from './crm-utils.js';
import { authDownload, bearerHeader, copyText, ensureStyle, proxyBodyLimit, uploadItems }
    from '../../js/shared/utils.js';
import { changeStatus } from '../proposals/prop-actions.js';
import { isTemplateFile, templateFromAsset } from '../proposals/brief-templates.js';
import { STATUSES, withCurrent } from '../proposals/prop-const.js';
import { tfetch } from '../proposals/prop-fetch.js';
import { renderFolderView } from '../proposals/folder-view.js';
import { renderPins } from '../proposals/pins-panel.js';
import { makePinsStore } from '../proposals/pins-store.js';
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
        // 一個專案可以並行多筆提案（同一案提了三個 concept）。預設看成案的
        // 那一筆，沒有的話看最新的 —— 「第一筆」不是有意義的預設。
        const pick = props.find(p => p.status === '成案') || props[0];
        await _renderPlan(pick, props, projectId, host);
    }
    if (assets && host.isConnected) {
        // store 建在這裡（緊鄰 assets 那次 fetch）而不是卡片渲染函式裡 ——
        // 生命週期是「這一個提案」，跟資料一起流動；企劃頁那邊也是這個高度。
        const pins = makePinsStore({
            base: `/projects/${projectId}/proposal-assets`,
            request: (url, init) => crmFetch(url, init),
        });
        pins.sync(assets);
        _mountAssetsCard(projectId, host, assets, pins);
    }
}

/** 資產檔案的下載網址（卡片列與檔案列共用一份組法）。 */
const _fileUrl = (projectId, rel) =>
    `/api/v1/crm/projects/${projectId}/proposal-assets/file?rel=${encodeURIComponent(rel)}`;

// ── 資產資料夾設定（比照影像紀錄：root 是全站共用的一份，路徑帶 project_id
//    只是讓人從專案面板順手改；顯示的資料夾是這個專案的）────────────
function _mountAssetsCard(projectId, host, d, pins) {
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
                <button id="pp-open" class="crm-btn crm-btn-secondary crm-btn-sm"
                        title="在這台伺服器上開啟檔案總管 — 你人不在伺服器前的話請改用「點路徑複製」">開啟資料夾</button>
                <button id="pp-cfg" class="crm-btn crm-btn-secondary crm-btn-sm" title="設定根目錄">⚙</button>
            </div>
            <div id="pp-pins"></div>
            <div id="pp-files"></div>
            <div id="pp-cfg-row" style="display:none;gap:6px;align-items:center;margin-top:8px;">
                <input id="pp-root" type="text" class="crm-input" style="flex:1;"
                       value="${esc(d.root || '')}" placeholder="例：\\\\192.168.1.132\\Archive\\00_提案企劃">
                <button id="pp-root-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存</button>
            </div>
            <div style="font-size:11.5px;color:#6b6b6b;margin-top:6px;">
                所有專案共用根資料夾，各專案自動建子夾。單檔上限 300MB，可執行檔會被擋下。${
                    // 走隧道時真正的天花板是 CDN 的 100MB/請求，不是後端的 300MB ——
                    // 卡片寫 300MB 而使用者拿到 413，那個落差要在這裡講清楚
                    proxyBodyLimit()
                        ? `<br><span style="color:#fbbf24;">你目前從公司外連線：單一檔案上限 100MB
                           （多檔會自動分批送）。更大的檔請到公司區網上傳。</span>` : ''}
            </div>
        </div>`;
    host.appendChild(box);
    // 檔案列走共用的 folder-view（企劃頁與提案庫總覽用的是同一支）——
    // 這裡只提供「怎麼拿資料、每列多兩個動作」。
    const nav = _mountFiles(box.querySelector('#pp-files'), projectId, d, pins);
    _mountPins(box.querySelector('#pp-pins'), projectId, pins, nav);

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

// 「重點提案」卡片列。渲染本體在 proposals/pins-panel.js、狀態與端點在
// proposals/pins-store.js —— 公開企劃頁（白底）用的是同兩份。這裡只接線。
// nav = folder-view 的把手：勾起來的**資料夾**點下去要走進去，不是下載。
function _mountPins(host, projectId, pins, nav) {
    // watch = 訂閱 + 立刻畫一次；store 只在內容真的變了才通知
    pins.watch(() => {
        // 請求還在飛的時候使用者可能已切走專案 —— 對已卸下的節點重建卡片
        // 只是白發圖片請求（同檔他處已有的 isConnected 慣例）
        if (!host.isConnected) return;
        renderPins(host, {
            state: pins.get(),
            setPublic: async (want) => {
                await pins.setPublic(want);
                crmToast(want ? '客戶現在看得到重點提案' : '已收回：客戶看不到了');
            },
            onOpen: (p) => (p.is_dir ? nav.go(p.rel)
                                     : authDownload(_fileUrl(projectId, p.rel),
                                                    p.rel.split('/').pop())),
        });
    });
}


/**
 * 檔案列：共用的 folder-view + 這個分頁自己的兩個列動作。
 *
 * ★＝設為提案簡報（提案詳情與公開共編頁顯示的那一份）、✕＝刪除。
 * 它們是 folder-view 的 `rowActions` 擴充點 —— 元件不知道★是什麼意思，
 * 只負責放位置與轉發點擊。
 *
 * @returns folder-view 的把手（{go}）；卡片列點「重點資料夾」要用
 */
function _mountFiles(host, projectId, d, pins) {
    const q = (rel) => `?rel=${encodeURIComponent(rel || '')}`;
    let deckRel = d.deck_rel || '';

    return renderFolderView(host, {
        rootLabel: d.folder_name || '根目錄',
        initial: d,                // 最外層剛剛才撈過（loadPlanTab）—— 別再掃一次
        load: async (rel) => {
            const r = await crmFetch(`/projects/${projectId}/proposal-assets${q(rel)}`);
            deckRel = r.deck_rel ?? deckRel;   // pinned 那半 folder-view 自己餵回 store
            return r;
        },
        onFile: (f) => authDownload(_fileUrl(projectId, f.rel), f.filename || f.rel),
        write: {
            mkdir: async (name, rel) => {
                const r = await crmFetch(
                    `/projects/${projectId}/proposal-assets/mkdir${q(rel)}`,
                    { method: 'POST', body: JSON.stringify({ name }) });
                crmToast(`已建立「${r.created}」`);
                return r;
            },
            // opts = {onProgress, signal, onUploaded}（folder-view 給的，它畫進度條）
            // 走 uploadItems 不走 crmFetch —— fetch 拿不到上傳進度，而這裡的檔
            // 動輒上百 MB，沒有進度就是盯著畫面猜（owner 2026-08-08）
            upload: async (items, rel, opts) => {
                const r = await uploadItems(
                    `/api/v1/crm/projects/${projectId}/proposal-assets/upload${q(rel)}`,
                    items, { headers: bearerHeader(), ...opts });
                // 被略過的項目由 folder-view 統一說明（三個呼叫端同一套說法）——
                // 全部被擋下時這裡不該再報一次「已上傳 0 個」
                if ((r.saved || []).length) crmToast(`已上傳 ${r.saved.length} 個項目`);
                return r;
            },
        },
        // 勾選＝策展（此刻以這份為準），不搬檔案、不等於給客戶看
        pin: pins,
        rowActions: [
            { at: 'lead', html: () => '★',
              cls: (f) => (f.rel === deckRel ? 'on' : ''),
              title: (f) => (f.rel === deckRel ? '目前的提案簡報' : '設為提案簡報'),
              errPrefix: '設定提案簡報失敗',
              run: async (f) => {
                  await crmFetch(`/projects/${projectId}/proposal-assets/deck`,
                      { method: 'POST', body: JSON.stringify({ rel: f.rel }) });
                  crmToast('已設為提案簡報');
                  deckRel = f.rel;
                  // 端點順手把它勾成提案資料（deck ⊆ 勾選是後端的單一真相）→
                  // 重載這一層，勾選框與卡片列才會跟著亮。多一趟掃描換
                  // 「畫面＝伺服器」，而按★是低頻動作。
                  const dir = f.rel.includes('/') ? f.rel.slice(0, f.rel.lastIndexOf('/')) : '';
                  const level = await crmFetch(
                      `/projects/${projectId}/proposal-assets${q(dir)}`);
                  pins.sync(level);     // apply() 不會餵 store，這裡自己餵
                  deckRel = level.deck_rel ?? deckRel;
                  return level;         // 整層換上（folder-view 的約定）
              } },
            // 抽不出文字的格式不長這顆 —— 讓人按下去才知道不行是浪費一次往返
            { at: 'trail', html: (f) => (isTemplateFile(f.filename) ? '📚' : ''),
              title: '設為企劃範本（複製一份到 _範本，原檔不動）',
              errPrefix: '設為範本失敗',
              run: async (f) => {
                  if (!isTemplateFile(f.filename)) return;
                  if (await templateFromAsset(projectId, f)) crmToast('已加入企劃範本庫');
              } },
            { at: 'trail', title: '刪除', html: () => '✕', errPrefix: '刪除失敗',
              run: async (f, ctx) => {
                  if (!confirm(`確定刪除「${f.rel}」？檔案會從資料夾移除。`)) return;
                  const r = await crmFetch(
                      `/projects/${projectId}/proposal-assets/file${q(f.rel)}`,
                      { method: 'DELETE' });
                  crmToast(r.deck_cleared ? '已刪除（原本是提案簡報，已取消指定）' : '已刪除');
                  if (r.deck_cleared) deckRel = '';
                  ctx.drop();           // 少一列而已，不必為此重掃一趟 NAS
              } },
        ],
    });
}

/** 同一個案子再提一個方向。標題不能沿用專案名 —— 那是要拿來區分的東西。 */
async function _addProposal(projectId, host) {
    const title = (prompt('新提案的標題（用來區分方向，例：A 版 / 第二次提案）') || '').trim();
    if (!title) return;
    try {
        await tfetch(API, { method: 'POST', json: { title, project_id: projectId } });
        loadPlanTab(projectId, host);
    } catch (e) { alert('建立失敗：' + (e.message || e)); }
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

/**
 * 提案切換列：只有一筆時不畫（現況零改變）。
 * 多筆＝同一案並行的幾個 concept，切一個看一個。
 */
function _switcherHtml(props, currentId) {
    if (props.length < 2) return '';
    return `<div class="pp-switch">
        ${props.map(p => `
            <button class="pp-chip${p.id === currentId ? ' on' : ''}" data-prop="${esc(p.id)}"
                    title="${esc(p.title)}">
                <span class="pp-chip-t">${esc(p.title)}</span>
                <span class="pp-chip-s">${esc(p.status || '草稿')}</span>
            </button>`).join('')}
    </div>`;
}

/** 多筆並行卻還沒有人贏 → 提醒負責人去標（成案不再自動標，見 projects.py）。 */
function _winHintHtml(props) {
    if (props.length < 2 || props.some(p => p.status === '成案')) return '';
    return `<div class="pp-hint">這個專案有 ${props.length} 筆提案並行 —— 哪一筆成案請切到它、
        把上面的狀態改成「成案」。系統不會替你猜（猜錯會把成案率和 win/loss 原因一起弄髒）。</div>`;
}

async function _renderPlan(listItem, props, projectId, host) {
    let prop;
    try {
        prop = (await tfetch(`${API}/${listItem.id}`)).proposal;
    } catch (e) {
        _fail(host, '提案載入失敗', e);
        return;
    }
    if (state.selectedId !== projectId || !host.isConnected) return;
    _ensureStyle();
    host.innerHTML = `
        ${_switcherHtml(props, prop.id)}
        <div style="display:flex;align-items:center;gap:10px;padding:10px 12px 0;flex-wrap:wrap;">
            <select id="pp-status" class="crm-input" style="width:auto;padding:3px 6px;font-size:12px;"
                    title="提案狀態（成案會推進這個專案的階段）">
                ${withCurrent(STATUSES, prop.status || '草稿').map(x =>
                    `<option${x === (prop.status || '草稿') ? ' selected' : ''}>${esc(x)}</option>`).join('')}
            </select>
            <span style="font-weight:600;">${esc(prop.title)}</span>
            ${prop.pitch_date ? `<span style="color:#8b8b8b;font-size:12px;">提案日 ${esc(prop.pitch_date)}</span>` : ''}
            <span style="flex:1;"></span>
            <button id="pp-add" class="crm-btn crm-btn-secondary crm-btn-sm"
                    title="同一個案子再提一個方向（各自有企劃、各自有資產子夾）">＋ 再加一筆提案</button>
            <a href="/proposal-plan.html?pid=${encodeURIComponent(prop.id)}" target="_blank" rel="noopener"
               style="color:#60a5fa;font-size:12px;">獨立視窗開啟 ↗</a>
        </div>
        ${_winHintHtml(props)}
        <div id="pp-matrix" style="padding:4px 12px 16px;"></div>`;

    host.querySelectorAll('[data-prop]').forEach(el => {
        el.addEventListener('click', () => {
            const next = props.find(p => p.id === el.dataset.prop);
            if (next && next.id !== prop.id) _renderPlan(next, props, projectId, host);
        });
    });
    host.querySelector('#pp-add').addEventListener('click', () => _addProposal(projectId, host));
    // 狀態規則（成案要原因、會推專案階段）走共用的動作層 —— 提案庫與企劃頁
    // 用的是同一支，不會一邊必填一邊選填
    host.querySelector('#pp-status').addEventListener('change', async (e) => {
        const next = e.target.value;
        try {
            const r = await changeStatus(prop, next);
            if (!r.ok) { e.target.value = prop.status || '草稿'; return; }
            if (r.message) crmToast(r.message);
            loadPlanTab(projectId, host);      // 專案階段可能跟著變 → 整塊重畫
        } catch (err) {
            e.target.value = prop.status || '草稿';
            alert('狀態變更失敗：' + (err.message || err));
        }
    });
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


// 切換列與提示的樣式（只注一次）。深色 SPA 專用 —— 這支不進 NAS 對外容器。
function _ensureStyle() {
    ensureStyle('pp-plan-style', `
.pp-switch { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px 0; }
.pp-chip { display: inline-flex; align-items: baseline; gap: 8px; max-width: 260px;
    background: none; border: 1px solid #2f2f2f; border-radius: 999px; cursor: pointer;
    color: #b4b4b4; font: inherit; font-size: 12px; padding: 4px 12px; }
.pp-chip:hover { border-color: #3b82f6; color: #ddd; }
.pp-chip.on { border-color: #3b82f6; background: #16283f; color: #eee; }
.pp-chip-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pp-chip-s { flex: none; font-size: 11px; opacity: .7; }
.pp-hint { margin: 8px 12px 0; padding: 7px 10px; border: 1px solid #3a3320;
    border-radius: 4px; background: #221e12; color: #d8c48a; font-size: 11.5px; line-height: 1.7; }
`);
}
