import { getComputeBaseUrl, appendLog, pickPath, setupDragAndDrop, setupInputDrop } from '../../js/shared/utils.js';

let tcSourceIndex = 0;

export function addSourceRowTc(defaultName = '', defaultPath = '') {
    tcSourceIndex++;
    const name = defaultName || `Card_TC_${String.fromCharCode(64 + tcSourceIndex)}`;
    const container = document.getElementById('tc_source_list');
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    row.id = `tc_src_row_${tcSourceIndex}`;
    row.innerHTML = `
        <input type="text" class="w-1/4 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${name}" placeholder="卡匣名稱">
        <input type="text" id="tc_src_path_${tcSourceIndex}" class="flex-1 bg-[#1e1e1e] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath}" placeholder="需產生Proxy的來源絕對路徑...">
        <button type="button" class="btn-pick-folder bg-[#333] hover:bg-[#444] px-2 rounded text-sm border border-[#555] text-gray-300 transition" data-target="tc_src_path_${tcSourceIndex}">📁</button>
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded" data-target="tc_src_row_${tcSourceIndex}">X</button>
    `;
    container.appendChild(row);

    row.querySelector('.btn-pick-folder').addEventListener('click', function() {
        pickPath(this.dataset.target, 'folder');
    });
    
    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        document.getElementById(this.dataset.target).remove();
    });

    return `tc_src_path_${tcSourceIndex}`;
}

export function setTodayNameTc() {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    document.getElementById('tc_proj_name').value = `${yyyy}${mm}${dd}_Standalone`;
}

export function getTcSelectedHosts() {
    const hosts = window._computeHosts || [];
    const result = [];
    const localChk = document.getElementById('tc_host_chk_local');
    if (localChk && localChk.checked) result.push({ name: '本機', ip: 'local' });
    hosts.forEach((h, i) => {
        const chk = document.getElementById('tc_host_chk_' + i);
        if (chk && chk.checked) result.push(h);
    });
    if (!result.length) result.push({ name: '本機', ip: 'local' });
    return result;
}

export async function submitTranscode() {
    const tcHostsRaw = getTcSelectedHosts();
    if (!tcHostsRaw || tcHostsRaw.length === 0) {
        alert('請至少選擇一台主機');
        return;
    }
    
    const tcHosts = tcHostsRaw.map(h => (h.ip === 'local') ? { name: h.name, ip: window.location.host } : h);

    const rows = document.getElementById('tc_source_list').children;
    const cards = [];
    for (let row of rows) {
        const inputs = row.querySelectorAll('input');
        const cardName = inputs[0]?.value.trim();
        const srcPath = inputs[1]?.value.trim();
        if (cardName && srcPath) {
            cards.push([cardName, false, srcPath]);
        }
    }

    if (cards.length === 0) {
        alert('請至少提供一個來源。');
        return;
    }

    const projectName = document.getElementById('tc_proj_name')?.value.trim() || 'Standalone_Proxy';
    const destDir = document.getElementById('tc_dest')?.value.trim();

    if (!destDir) {
        alert('請設定目標輸出資料夾！');
        return;
    }

    window._isStandaloneTranscode = true; 

    window._standaloneState = {
        sources: cards.map(c => ({ cardName: c[0], path: c[2] })).filter(c => c.path),
        destDir: destDir,
        projectName: projectName
    };
    window._standaloneRetryCount = 0;  

    const ctx = {
        job_type: 'transcode',
        use_absolute_paths: true,
        project_name: projectName,
        cards: cards,
        hosts: tcHosts,
        proxy_root: destDir, 
        settings: null 
    };

    if (window.dispatchRemoteTranscode) {
        await window.dispatchRemoteTranscode(ctx);
    } else {
        appendLog('❌ window.dispatchRemoteTranscode not found!', 'error');
    }
}

export async function verifyStandaloneProxies() {
    const state = window._standaloneState;
    if (!state || !state.sources || !state.destDir) return;

    appendLog('🔍 正在驗證 Standalone Proxy 分卡產出完整性...', 'system');
    
    try {
        let allMissingSources = [];
        for (const srcObj of state.sources) {
            const cardName = srcObj.cardName;
            const srcPath = srcObj.path;
            
            const basePath = state.destDir.replace(/\\/g, '/') + '/' + (state.projectName || '');
            const proxyDir = cardName ? basePath + '/' + cardName : basePath;
            appendLog(`🔍 [${cardName || '(預設)'}] 比對來源: ${srcPath} → ${proxyDir}`, 'system');

            const r = await fetch(getComputeBaseUrl() + '/api/v1/verify_standalone_proxies', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sources: [srcPath], dest_dir: proxyDir })
            });
            const d = await r.json();
            
            if (d.status === 'ok') {
                if (d.missing_sources && d.missing_sources.length > 0) {
                    const missingWithContext = d.missing_sources.map(file => ({ cardName: cardName, path: file }));
                    allMissingSources = allMissingSources.concat(missingWithContext);
                } else {
                    appendLog(`✅ [${cardName || '(預設)'}] Proxy 皆已產出`, 'system');
                }
            }
        }

        const missingSources = allMissingSources;
        if (missingSources.length === 0) {
            appendLog('✅ 所有預期的 Proxy 檔案皆已正常產出！', 'system');

            const segTrans = document.getElementById('seg_trans');
            const lblTrans = document.getElementById('lbl_trans');
            if (segTrans) { segTrans.style.width = '100%'; segTrans.style.backgroundColor = '#228b22'; }
            if (lblTrans) lblTrans.textContent = '✅ 完成';

            const progLabel = document.getElementById('prog_label');
            const progEta   = document.getElementById('prog_eta');
            if (progLabel) progLabel.textContent = '🎉 轉 Proxy 完成 ✅';
            if (progEta) progEta.textContent = '';

            for (const [ip] of Object.entries(window._activeRemoteHosts || {})) {
                if(window.updateHostProgress) window.updateHostProgress(ip, 100, '✅ 驗證完成', '#228b22');
            }

            const ms = document.getElementById('merge_status_text');
            if (ms) ms.textContent = '轉檔完成 ✅';

            if (window._standaloneTranscodeResolve) {
                window._standaloneTranscodeResolve();
                window._standaloneTranscodeResolve = null;
            }
        } else {
            window._standaloneRetryCount = (window._standaloneRetryCount || 0) + 1;
            appendLog(`⚠️ 發現 ${missingSources.length} 個缺失的 Proxy 檔案，啟動補轉 (第 ${window._standaloneRetryCount} 次)...`, 'error');
            
            if (window._standaloneRetryCount > 2) {
                appendLog('❌ 補轉重試已達上限 (2次)，放棄補轉。', 'error');
                if (window._standaloneTranscodeResolve) {
                    window._standaloneTranscodeResolve();
                    window._standaloneTranscodeResolve = null;
                }
                return;
            }
            
            if (window.updateOverallProgress) window.updateOverallProgress(0, '準備補轉...');

            const activeHostsObj = window._activeRemoteHosts || {};
            let activeHostNames = Object.keys(activeHostsObj);
            if (activeHostNames.length === 0) {
                appendLog('⚠️ 沒有存活的遠端主機可供補轉，結束流程。', 'error');
                if (window._standaloneTranscodeResolve) {
                    window._standaloneTranscodeResolve(); 
                    window._standaloneTranscodeResolve = null;
                }
                return;
            }

            const reachableHosts = activeHostNames.map(ip => {
                const originalHost = activeHostsObj[ip].host;
                const finalIp = (ip === 'local' || ip === '127.0.0.1') ? window.location.host : ip;
                return Object.assign({}, originalHost, { ip: finalIp, name: originalHost.name || ip });
            });

            const retryCtx = {
                job_type: 'transcode',
                use_absolute_paths: true,
                project_name: state.projectName,
                cards: missingSources.map(obj => [ obj.cardName || "RetryCard", false, obj.path ]),
                hosts: reachableHosts,
                proxy_root: state.destDir,
                settings: null
            };

            appendLog('🔄 重新派發補救任務...', 'system');
            if (window.dispatchRemoteTranscode) window.dispatchRemoteTranscode(retryCtx);
        }
    } catch (e) {
        appendLog('❌ Standalone 驗證錯誤: ' + e.message, 'error');
        if (window._standaloneTranscodeResolve) {
            window._standaloneTranscodeResolve();
            window._standaloneTranscodeResolve = null;
        }
    }
}

export function initTranscodeTab() {
    addSourceRowTc();
    setupDragAndDrop('tc_source_list', addSourceRowTc);
    setupInputDrop('tc_dest');
}

window.addSourceRowTc = addSourceRowTc;
window.setTodayNameTc = setTodayNameTc;
window.submitTranscode = submitTranscode;
window.verifyStandaloneProxies = verifyStandaloneProxies;
