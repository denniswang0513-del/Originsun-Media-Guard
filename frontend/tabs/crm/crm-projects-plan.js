/**
 * crm-projects-plan.js — 專案詳情「提案企劃」分頁（提案=專案合體，2026-08-06）
 *
 * 專案的提案衛星列（preprod_proposals.project_id）→ 嵌現成的 plan-matrix
 * 企劃元件（同 提案庫 overlay / proposal-plan.html 的登入模式）。
 * 沒有衛星列（非提案出身的專案）→ 空狀態 + 一鍵建立
 * （POST /proposals {title: 專案名, project_id} — 後端掛載、不另建殼專案）。
 */

import { crmFetch, crmToast, esc } from './crm-utils.js';
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
            <div style="font-size:12px;color:#8b8b8b;margin-bottom:6px;">
                提案資產資料夾${d.root_set ? '' : '<span style="color:#fbbf24;"> — 目前搆不到這個路徑</span>'}
            </div>
            <div style="display:flex;gap:6px;align-items:center;">
                <input id="pp-root" type="text" class="crm-input" style="flex:1;"
                       value="${esc(d.root || '')}" placeholder="例：\\\\192.168.1.132\\Archive\\00_提案企劃">
                <button id="pp-root-save" class="crm-btn crm-btn-primary crm-btn-sm">儲存</button>
                <button id="pp-open" class="crm-btn crm-btn-secondary crm-btn-sm">開啟資料夾</button>
            </div>
            <div style="font-size:11.5px;color:#6b6b6b;margin-top:6px;">
                所有專案共用根資料夾，各專案自動建子夾${d.folder_name ? `：<code>${esc(d.folder_name)}</code>` : ''}
                ${d.created ? '' : '（尚未建立，第一次上傳簡報時才會建）'}
            </div>
        </div>`;
    host.appendChild(box);

    box.querySelector('#pp-root-save').addEventListener('click', async () => {
        const root = box.querySelector('#pp-root').value.trim();
        try {
            await crmFetch(`/projects/${projectId}/proposal-assets/settings`,
                { method: 'POST', body: JSON.stringify({ root }) });
            crmToast('已儲存資產資料夾設定');
            loadPlanTab(projectId, host);   // root_set / 資料夾路徑會跟著變
        } catch (e) { alert('儲存失敗：' + (e.message || e)); }
    });
    box.querySelector('#pp-open').addEventListener('click', () => {
        const path = d.project_folder || d.root || '';
        if (!path) { alert('尚未設定提案資產資料夾'); return; }
        fetch('/api/v1/utils/open_folder', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        }).catch(() => {});
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
