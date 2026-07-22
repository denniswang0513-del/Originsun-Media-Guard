import { getComputeBaseUrl, appendLog, resetProgress, resolveDropPath, pickPath, setupInputDrop, setupDragAndDrop, renderHostCheckboxes, collectSelectedHosts } from '../../js/shared/utils.js';

let sourceIndex = 0;

export function addSourceRow(defaultName = '', defaultPath = '') {
    sourceIndex++;
    const name = defaultName || `Card_${String.fromCharCode(64 + sourceIndex)}`;
    const container = document.getElementById('source_list');
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center';
    row.id = `src_row_${sourceIndex}`;
    row.innerHTML = `
        <input type="text" class="w-1/4 bg-[#2a2a2a] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${name}" placeholder="卡匣名稱">
        <input type="text" id="src_path_${sourceIndex}" class="flex-1 bg-[#1e1e1e] border border-[#555] rounded px-2 py-1 text-sm focus:border-blue-500" value="${defaultPath}" placeholder="伺服器端來源絕對路徑...">
        <button type="button" class="btn-pick-folder bg-[#333] hover:bg-[#444] px-2 rounded text-sm border border-[#555] text-gray-300 transition" data-target="src_path_${sourceIndex}">📁</button>
        <button type="button" class="btn-remove-row text-red-400 hover:text-red-300 font-bold px-2 rounded" data-target="src_row_${sourceIndex}">X</button>
    `;
    container.appendChild(row);

    // Bind events for the newly created row
    row.querySelector('.btn-pick-folder').addEventListener('click', function() {
        pickPath(this.dataset.target, 'folder');
    });
    row.querySelector('.btn-remove-row').addEventListener('click', function() {
        document.getElementById(this.dataset.target).remove();
    });

    return `src_path_${sourceIndex}`;
}

export function setTodayName() {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    document.getElementById('proj_name').value = `${yyyy}${mm}${dd}`;
}

function getSelectedHosts() {
    const result = collectSelectedHosts('host_selector_checkboxes');
    if (!result.length) result.push({ name: '本機', ip: 'local' });
    return result;
}


export function collectBackupPayload() {
    const srcRows = document.getElementById('source_list').children;
    const cards = [];
    for (let row of srcRows) {
        const inputs = row.querySelectorAll('input');
        if (inputs[0].value.trim() && inputs[1].value.trim()) {
            cards.push([inputs[0].value.trim(), inputs[1].value.trim()]);
        }
    }
    if (cards.length === 0) {
        alert('至少需要一個有效的來源路徑！');
        return { valid: false };
    }

    const chkReport = document.getElementById('chk_report');
    const doReport = chkReport ? chkReport.checked : false;
    const projectName = document.getElementById('proj_name').value.trim();

    const payload = {
        project_name: projectName,
        local_root: document.getElementById('local_root').value.trim(),
        nas_root: document.getElementById('nas_root').value.trim(),
        proxy_root: document.getElementById('proxy_root').value.trim(),
        cards: cards,
        do_hash: document.getElementById('chk_hash').checked,
        do_transcode: document.getElementById('chk_transcode').checked,
        do_concat: document.getElementById('chk_concat').checked,
        do_report: doReport,
        // Concat settings
        concat_resolution: document.getElementById('bk_cc_res')?.value || '720P',
        concat_codec: document.getElementById('bk_cc_codec')?.value || 'H.264 (NVENC)',
        concat_burn_tc: document.getElementById('bk_cc_burn_tc')?.checked ?? true,
        concat_burn_fn: document.getElementById('bk_cc_burn_fn')?.checked ?? false,
        // Report settings
        report_name: document.getElementById('bk_rpt_name')?.value.trim() || '',
        report_output: document.getElementById('bk_rpt_output')?.value.trim() || '',
        report_filmstrip: document.getElementById('bk_rpt_filmstrip')?.checked ?? true,
        report_techspec: document.getElementById('bk_rpt_techspec')?.checked ?? true,
        report_hash: document.getElementById('bk_rpt_hash')?.checked ?? false,
    };

    return { valid: true, payload, name: projectName };
}
window.collectBackupPayload = collectBackupPayload;

let _submitting = false;

export async function submitJob() {
    if (_submitting) return;
    _submitting = true;
    window._activeJobTab = 'backup';

    const submitBtn = document.querySelector('#tab_backup button[onclick="submitJob()"]');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add('opacity-70', 'cursor-not-allowed');
        submitBtn._origText = submitBtn.textContent;
        submitBtn.textContent = '提交中...';
    }

    try {
    // Reset global progress
    resetProgress();
    window._isStandaloneTranscode = false;
    window._remoteDispatchExpectedRetryCount = 0;

    const collected = collectBackupPayload();
    if (!collected.valid) return;
    const payload = collected.payload;

    const doReport = payload.do_report;

    // 根據勾選項目顯示/隱藏對應的進度段和圖例
    const doTranscode = payload.do_transcode;
    const doConcat = payload.do_concat;
    const segTrans = document.getElementById('bk-seg-trans');
    const segConcat = document.getElementById('bk-seg-concat');
    const segReport = document.getElementById('bk-seg-report');
    const legendTrans = segTrans?.closest('.flex')?.querySelectorAll('span')?.[0]?.parentElement;
    const legendConcat = segConcat?.closest('.flex')?.querySelectorAll('span')?.[0]?.parentElement;
    const legendReport = document.getElementById('bk-legend-report');
    // 用更直接的方式找圖例
    const legendContainer = document.querySelector('#bk-progress .flex.gap-4');
    if (legendContainer) {
        const legends = legendContainer.children;
        // [0]=備份, [1]=轉檔, [2]=串帶, [3]=報表
        if (legends[1]) legends[1].classList.toggle('hidden', !doTranscode);
        if (legends[2]) legends[2].classList.toggle('hidden', !doConcat);
    }
    if (segTrans) segTrans.classList.toggle('hidden', !doTranscode);
    if (segConcat) segConcat.classList.toggle('hidden', !doConcat);
    if (segReport) segReport.classList.toggle('hidden', !doReport);
    if (legendReport) legendReport.classList.toggle('hidden', !doReport);
    // 標記報表待完成（防止 task_status:done 過早顯示完成摘要）
    window._backupReportPending = doReport;
    // 記錄勾選項目 + 待完成集合（不分順序，全部完成才顯示摘要）
    const pending = new Set();
    if (doConcat) pending.add('concat');
    if (doReport) pending.add('report');
    window._backupPipeline = {
        phases: ['備份', ...(doTranscode ? ['轉檔'] : []), ...(doConcat ? ['串帶'] : []), ...(doReport ? ['報表'] : [])],
        pending,  // 尚未完成的非同步階段
        startTime: Date.now(),
    };

    const _selH = getSelectedHosts();
    const _hasRemote = _selH.some(h => h.ip !== 'local');
    if (_hasRemote) {
        const dispatchHosts = _selH.map(h => (h.ip === 'local') ? { name: h.name, ip: window.location.host } : h);
        window._remoteDispatch = { hosts: dispatchHosts, proxy_root: payload.proxy_root, project_name: payload.project_name, local_root: payload.local_root, cards: payload.cards };
        window._postMergeFlags = {
            do_concat: payload.do_concat,
            do_report: payload.do_report,
            project_name: payload.project_name,
            local_root: payload.local_root,
            nas_root: payload.nas_root,
            proxy_root: payload.proxy_root,
            cards: payload.cards,
            do_hash: false,
            do_transcode: false,
            // 串帶永遠在主控端執行（只有主控端保證能存取 local_root 原始影片）
            concat_host_url: '',
            concat_host_name: '本機 (主控端)',
            // 串帶進階設定
            concat_resolution: document.getElementById('bk_cc_res')?.value || '720P',
            concat_codec: document.getElementById('bk_cc_codec')?.value || 'H.264 (NVENC)',
            concat_burn_tc: document.getElementById('bk_cc_burn_tc')?.checked ?? true,
            concat_burn_fn: document.getElementById('bk_cc_burn_fn')?.checked ?? false,
            // 報表進階設定
            report_name: document.getElementById('bk_rpt_name')?.value.trim() || '',
            report_output: document.getElementById('bk_rpt_output')?.value.trim() || '',
            report_filmstrip: document.getElementById('bk_rpt_filmstrip')?.checked ?? true,
            report_techspec: document.getElementById('bk_rpt_techspec')?.checked ?? true,
            report_hash: document.getElementById('bk_rpt_hash')?.checked ?? false,
        };
        payload.do_transcode = false;
        payload.do_concat = false;
        payload.do_report = false;
    } else {
        window._remoteDispatch = null;
        window._postMergeFlags = null;
    }

    try {
        window._lastJob = { url: getComputeBaseUrl() + '/api/v1/jobs', payload };
        const res = await fetch(window._lastJob.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (!res.ok) {
            appendLog(`任務提交失敗: ${result.detail || result.message || JSON.stringify(result)}`, 'error');
            alert(result.detail || '任務提交失敗');
            return;
        }
        const btnRetry = document.getElementById('btn_retry');
        if (btnRetry) btnRetry.style.display = 'none';
        appendLog(`請求已送出，伺服器排序狀態: ${result.status}, 任務 ID: ${result.job_id || '?'}`, 'system');
        if (result.warning) {
            appendLog(`⚠️ ${result.warning}`, 'system');
        }
    } catch (err) {
        appendLog(`請求發送失敗: ${err.message}`, 'error');
    }

    } finally {
        _submitting = false;
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('opacity-70', 'cursor-not-allowed');
            submitBtn.textContent = submitBtn._origText || '開始備份';
        }
    }
}

// ── 路徑書籤（2026-07-21 復活）───────────────────────────────
// 動機：磁碟代號（T:/S:/V:）在各機器不一定有對映，任務在哪台跑就用哪台的
// 磁碟 → 換機器就炸（煥民新村備份 6 連敗實案）。書籤存 UNC 路徑組 +
// 執行設定，進中央 DB 全機隊共用，選了就帶入。
// 不含：來源卡匣（每日不同）、專案名稱（setTodayName 管）。

let _bookmarks = [];

async function loadBookmarks() {
    try {
        const res = await fetch(getComputeBaseUrl() + '/api/v1/bookmarks');
        if (!res.ok) return;
        const data = await res.json();
        _bookmarks = (Array.isArray(data) ? data : []).filter(b => b.task_type === 'backup');
        const sel = document.getElementById('bk_bookmark_sel');
        if (!sel) return;
        const prev = sel.value;
        sel.innerHTML = '<option value="">選擇書籤帶入路徑與設定...</option>'
            + _bookmarks.map(b => `<option value="${b.id}">${(b.name || b.id).replace(/</g, '&lt;')}</option>`).join('');
        if (prev && _bookmarks.some(b => b.id === prev)) sel.value = prev;
    } catch { /* 離線/舊 agent 無此端點 — 書籤列靜默留空 */ }
}

function bkApplyBookmark(id) {
    const bm = _bookmarks.find(b => b.id === id);
    if (!bm || !bm.request) return;
    const r = bm.request;
    const setVal = (elId, v) => { const el = document.getElementById(elId); if (el && v != null) el.value = v; };
    const setChk = (elId, v) => { const el = document.getElementById(elId); if (el && v != null) el.checked = !!v; };
    setVal('local_root', r.local_root);
    setVal('nas_root', r.nas_root);
    setVal('proxy_root', r.proxy_root);
    setChk('chk_hash', r.do_hash);
    setChk('chk_transcode', r.do_transcode);
    setChk('chk_concat', r.do_concat);
    setChk('chk_report', r.do_report);
    setVal('bk_cc_res', r.concat_resolution);
    setVal('bk_cc_codec', r.concat_codec);
    setChk('bk_cc_burn_tc', r.concat_burn_tc);
    setChk('bk_cc_burn_fn', r.concat_burn_fn);
    setVal('bk_rpt_name', r.report_name);
    setVal('bk_rpt_output', r.report_output);
    setChk('bk_rpt_filmstrip', r.report_filmstrip);
    setChk('bk_rpt_techspec', r.report_techspec);
    setChk('bk_rpt_hash', r.report_hash);
    // 勾選狀態變了 → 同步面板顯示與主機選擇器
    toggleConcatOptions();
    toggleReportOptions();
    window.renderHostSelector?.();
    appendLog(`已套用路徑書籤「${bm.name}」`, 'system');
}

async function bkSaveBookmark() {
    const name = prompt('書籤名稱（例：煥民新村、標準專案路徑）：',
        document.getElementById('proj_name')?.value.trim() || '');
    if (!name || !name.trim()) return;
    // 不經 collectBackupPayload（它要求至少一張卡）— 書籤只存路徑與設定
    const request = {
        project_name: '',
        cards: [],
        local_root: document.getElementById('local_root').value.trim(),
        nas_root: document.getElementById('nas_root').value.trim(),
        proxy_root: document.getElementById('proxy_root').value.trim(),
        do_hash: document.getElementById('chk_hash').checked,
        do_transcode: document.getElementById('chk_transcode').checked,
        do_concat: document.getElementById('chk_concat').checked,
        do_report: document.getElementById('chk_report')?.checked ?? false,
        concat_resolution: document.getElementById('bk_cc_res')?.value || '720P',
        concat_codec: document.getElementById('bk_cc_codec')?.value || 'H.264 (NVENC)',
        concat_burn_tc: document.getElementById('bk_cc_burn_tc')?.checked ?? true,
        concat_burn_fn: document.getElementById('bk_cc_burn_fn')?.checked ?? false,
        report_name: document.getElementById('bk_rpt_name')?.value.trim() || '',
        report_output: document.getElementById('bk_rpt_output')?.value.trim() || '',
        report_filmstrip: document.getElementById('bk_rpt_filmstrip')?.checked ?? true,
        report_techspec: document.getElementById('bk_rpt_techspec')?.checked ?? true,
        report_hash: document.getElementById('bk_rpt_hash')?.checked ?? false,
    };
    try {
        const res = await fetch(getComputeBaseUrl() + '/api/v1/bookmarks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim(), task_type: 'backup', request }),
        });
        if (!res.ok) {
            const e = await res.json().catch(() => ({}));
            alert('儲存失敗：' + (e.detail || res.status));
            return;
        }
        const saved = await res.json();
        await loadBookmarks();
        const sel = document.getElementById('bk_bookmark_sel');
        if (sel && saved.id) sel.value = saved.id;
        appendLog(`路徑書籤「${name.trim()}」已儲存（全機隊共用）`, 'system');
    } catch (err) {
        alert('儲存失敗：' + err.message);
    }
}

async function bkDeleteBookmark() {
    const sel = document.getElementById('bk_bookmark_sel');
    const id = sel?.value;
    if (!id) { alert('請先在下拉選單選擇要刪除的書籤'); return; }
    const bm = _bookmarks.find(b => b.id === id);
    if (!confirm(`刪除書籤「${bm?.name || id}」？（全部機器都會看不到）`)) return;
    try {
        const res = await fetch(getComputeBaseUrl() + `/api/v1/bookmarks/${id}`, { method: 'DELETE' });
        if (!res.ok) { alert('刪除失敗：' + res.status); return; }
        await loadBookmarks();
        sel.value = '';
        appendLog('書籤已刪除', 'system');
    } catch (err) {
        alert('刪除失敗：' + err.message);
    }
}

// ── 磁碟對應設定（磁碟代號 ↔ UNC；送出任務時後端自動翻譯）────

function _dmRowHtml(letter = '', unc = '') {
    const esc = s => String(s ?? '').replace(/</g, '&lt;').replace(/"/g, '&quot;');
    return `
        <div class="flex gap-2 items-center dm-row">
            <input type="text" maxlength="1" value="${esc(letter)}" placeholder="T"
                class="dm-letter w-12 text-center bg-[#1e1e1e] border border-[#555] rounded px-2 py-1.5 text-sm uppercase focus:border-blue-500">
            <span class="text-gray-500 text-sm">:\\</span>
            <span class="text-gray-500">=</span>
            <input type="text" value="${esc(unc)}" placeholder="\\\\192.168.1.132\\ShareName"
                class="dm-unc flex-1 bg-[#1e1e1e] border border-[#555] rounded px-2 py-1.5 text-sm focus:border-blue-500">
            <button type="button" class="dm-del text-red-400 hover:text-red-300 font-bold px-2">X</button>
        </div>`;
}

async function bkOpenDriveMap() {
    const modal = document.getElementById('bk_drivemap_modal');
    const rows = document.getElementById('bk_drivemap_rows');
    rows.innerHTML = '<div class="text-xs text-gray-500">載入中...</div>';
    modal.classList.remove('hidden');
    try {
        const res = await fetch(getComputeBaseUrl() + '/api/v1/drive_map');
        const data = await res.json();
        const map = data.map || {};
        const letters = Object.keys(map).sort();
        rows.innerHTML = letters.map(l => _dmRowHtml(l, map[l])).join('')
            || '<div class="dm-empty text-xs text-gray-500">尚無對應 — 按「+ 新增對應」</div>';
    } catch (err) {
        rows.innerHTML = `<div class="text-xs text-red-400">載入失敗：${err.message}</div>`;
    }
}

function bkCloseDriveMap() {
    document.getElementById('bk_drivemap_modal').classList.add('hidden');
}

function bkDriveMapAddRow() {
    const rows = document.getElementById('bk_drivemap_rows');
    rows.querySelector('.dm-empty')?.remove();   // 只清空狀態提示，不動既有列
    rows.insertAdjacentHTML('beforeend', _dmRowHtml());
}

async function bkSaveDriveMap() {
    const map = {};
    for (const row of document.querySelectorAll('#bk_drivemap_rows .dm-row')) {
        const letter = row.querySelector('.dm-letter').value.trim().toUpperCase();
        const unc = row.querySelector('.dm-unc').value.trim();
        if (!letter && !unc) continue;   // 空列忽略
        if (!/^[A-Z]$/.test(letter)) { alert(`磁碟代號需為單一英文字母：「${letter}」`); return; }
        if (!unc.startsWith('\\\\')) { alert(`${letter}: 的對應需為 \\\\ 開頭的 UNC 路徑`); return; }
        map[letter] = unc;
    }
    try {
        const res = await fetch(getComputeBaseUrl() + '/api/v1/drive_map', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ drive_map: map }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert('儲存失敗：' + (data.detail || res.status)); return; }
        bkCloseDriveMap();
        appendLog(`磁碟對應已更新（${Object.keys(map).length} 組）`, 'system');
    } catch (err) {
        alert('儲存失敗：' + err.message);
    }
}

function toggleConcatOptions() {
    const panel = document.getElementById('concat_options_panel');
    const checked = document.getElementById('chk_concat')?.checked;
    if (panel) panel.classList.toggle('hidden', !checked);
}

function toggleReportOptions() {
    const panel = document.getElementById('report_options_panel');
    const checked = document.getElementById('chk_report')?.checked;
    if (panel) panel.classList.toggle('hidden', !checked);
}

// Ensure functions are added to window object so inline event handlers in index.html still work during refactor,
// or we attach them dynamically. Since we want to decouple, we attach them.
export function initBackupTab() {
    // Adding initial card
    addSourceRow('Card_A', 'C:/A_TEST_SRC');

    // Bind drag and drop for source list
    setupDragAndDrop('source_list', addSourceRow);
    setupInputDrop('local_root');
    setupInputDrop('nas_root');
    setupInputDrop('proxy_root');
    setupInputDrop('bk_rpt_output');

    // Sync initial visibility of concat/report options panels
    toggleConcatOptions();
    toggleReportOptions();

    // 路徑書籤：載入清單 + 選了即套用
    loadBookmarks();
    document.getElementById('bk_bookmark_sel')?.addEventListener('change', e => {
        if (e.target.value) bkApplyBookmark(e.target.value);
    });

    // 磁碟對應 modal：刪列（事件委派）+ 點背景關閉
    document.getElementById('bk_drivemap_rows')?.addEventListener('click', e => {
        if (e.target.classList.contains('dm-del')) e.target.closest('.dm-row')?.remove();
    });
    document.getElementById('bk_drivemap_modal')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) bkCloseDriveMap();
    });
}


// Bind to window for inline onclick execution (temporary during migration)
window.addSourceRow = addSourceRow;
window.setTodayName = setTodayName;
window.submitJob = submitJob;
window.toggleConcatOptions = toggleConcatOptions;
window.toggleReportOptions = toggleReportOptions;
window.bkSaveBookmark = bkSaveBookmark;
window.bkDeleteBookmark = bkDeleteBookmark;
window.bkOpenDriveMap = bkOpenDriveMap;
window.bkCloseDriveMap = bkCloseDriveMap;
window.bkDriveMapAddRow = bkDriveMapAddRow;
window.bkSaveDriveMap = bkSaveDriveMap;
