import { getComputeBaseUrl, appendLog, resetProgress, resolveDropPath, pickPath, setupInputDrop, setupDragAndDrop, renderHostCheckboxes, collectSelectedHosts, todayStamp, authFetch, esc } from '../../js/shared/utils.js';
import { loadReportHistory } from '../../js/shared/report-history.js';
import { attachProjectPop } from '../../js/shared/project-pop.js';

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
    document.getElementById('proj_name').value = todayStamp();
}

function getSelectedHosts() {
    const result = collectSelectedHosts('host_selector_checkboxes');
    if (!result.length) result.push({ name: '本機', ip: 'local' });
    return result;
}


// ── 綁定 CRM 專案（2026-09-10 owner）────────────────────────────
// 三個根目錄（本機／NAS／Proxy）的**正本住在 CRM 專案上**。
// 優先序：專案設定 > 書籤 > 手打。有綁專案時後端也會以 DB 的三根為準、忽略
// 前端送的路徑，所以這裡送什麼都不影響安全性 —— 但仍照實送。
//
// 🔴 **逐根**判斷，不是整組（owner 追加）：
//   · 專案上那一根有值 → readonly + 灰底，要改去專案頁
//   · 專案上那一根是空的 → 照常可編輯，並長出「儲存到專案」把它補回去
//     （PUT /api/v1/projects/backup-roots/{id}，後端**只填空、不覆寫**）
//   三根混合狀態是正常的（NAS 已設定＝唯讀、本機還沒填＝可編輯可存），UI 要撐得住。
//
// 端點回的三個路徑後端**已經翻成「呼叫這台機器看得到的視角」**，
// 前端直接顯示即可，不要再做 UNC／磁碟代號加工。
//
// 清單不做任何過濾（私帳案 entity='mine' 照樣顯示、照樣可選 —— 後端已不過濾）。
//
// 讀不到專案清單（DB 離線 / 沒登入 / 舊 agent 沒這支端點）一律**不擋派工**：
// 選擇器停用 + 標示，三個 input 維持可編輯。
const BK_ROOT_IDS = ['local_root', 'nas_root', 'proxy_root'];
const BK_ROOT_LABELS = {
    local_root: '本機備份根目錄',
    nas_root: 'NAS 備份根目錄',
    proxy_root: 'Proxy 輸出根目錄',
};

let _bkProjects = [];
let _bkProjectsUnavailable = false;
// 這一輪的抓取。浮層 await 它（見 initBackupTab 那段）——**不要**改成把 _bkProjects
// 直接交給浮層：那是打開當下的快照，清單還沒到手就點進去會停在「一個案都沒有」。
let _bkProjectsReady = null;
// 正常狀態那句 placeholder 的正本在 html 上（讀不到清單時才換掉，之後換得回來）
let _bkOkPlaceholder = '';
// 讀不到清單時的說法只有一句（框裡的 placeholder 與下面的提示各印一次，別再分岔）
const BK_NO_LIST = '目前讀不到專案清單 —— 三個根目錄請手動填寫，不影響派工。';
// 剛存回專案 / 被 skip 的那一根要顯示的一次性小字：{ [rootId]: { msg, tone } }
let _bkRootFlash = {};

// 專案欄＝打字浮層（js/shared/project-pop.js），跟專案工時／零用金同一支元件。
// 選到的案 id 在 data-pid 上（浮層寫的），輸入框裡的字只是給人看的 label ——
// 人自己改了字，浮層會把 data-pid 清掉（＝不再是選到的那個案）。
function _bkProjectInput() { return document.getElementById('bk_project_pick'); }

/** 目前綁定的專案物件（沒綁 / 找不到 → null）。 */
function bkSelectedProject() {
    const id = _bkProjectInput()?.dataset.pid || '';
    if (!id) return null;
    return _bkProjects.find(p => String(p.id) === String(id)) || null;
}

/** 專案上這一根有沒有設定（沒綁專案 → 一律 false）。 */
function _bkRootIsSet(proj, rootId) {
    return !!(proj && String(proj[rootId] || '').trim());
}

/** 鎖住 / 放開一個根目錄輸入框（含旁邊的 📁 選路徑按鈕）。 */
function _bkSetRootLocked(elId, locked) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.readOnly = locked;
    el.classList.toggle('bg-[#1e1e1e]', !locked);
    el.classList.toggle('bg-[#2b2b2b]', locked);
    el.classList.toggle('text-gray-400', locked);
    el.classList.toggle('cursor-not-allowed', locked);
    el.title = locked ? '由綁定的專案帶入，要改請到專案頁' : '';
    // 用 data-pick-for 指名，別用 querySelector('button') —— 同一列現在有兩顆按鈕
    const pick = el.parentElement?.querySelector(`button[data-pick-for="${elId}"]`);
    if (pick) {
        pick.disabled = locked;
        pick.classList.toggle('opacity-40', locked);
        pick.classList.toggle('cursor-not-allowed', locked);
    }
}

/** 一根的小字提示（唯讀來源 / 可補存 / 剛存完的結果）。 */
function _bkSetRootNote(rootId, html, tone = 'muted') {
    const note = document.getElementById('bk_note_' + rootId);
    if (!note) return;
    const color = tone === 'warn' ? 'text-amber-400' : tone === 'ok' ? 'text-green-400' : 'text-gray-500';
    note.className = `text-[11px] ${color} mt-1${html ? '' : ' hidden'}`;
    note.innerHTML = html || '';
}

// 純導覽連結（`#tab_crm_projects` 交給 app.js 的 hashchange 自己走過去）。
// 🔴 **不要**在這裡用程式把 CRM 專案分頁拉起來 —— 備檔電腦開備份頁不該為了
// 一個連結付整頁 CRM 的請求代價。這條由
// tests/unit/test_frontend_request_budget.py::test_tab_defaults_live_in_their_own_init 釘著。
function _bkProjectLink(proj) {
    return `<a href="#tab_crm_projects"
        class="underline text-blue-400 hover:text-blue-300">到專案頁改「${esc(proj.name || proj.id)}」的備份根目錄 ↗</a>`;
}

/**
 * 依目前選中的專案同步三個根目錄的鎖定狀態與提示（逐根）。
 * @param {boolean} fill  true＝把專案**已設定**的那幾根填進 input；
 *                        false＝只改鎖定狀態，值一律保留（解除綁定時用）。
 *                        專案上沒設定的那幾根**任何情況都不清空**——使用者打的字
 *                        正是他要按「儲存到專案」補回去的東西。
 */
function bkSyncProjectRoots({ fill = true } = {}) {
    const proj = bkSelectedProject();

    for (const id of BK_ROOT_IDS) {
        const isSet = _bkRootIsSet(proj, id);
        const locked = !!proj && isSet;
        if (locked && fill) {
            const el = document.getElementById(id);
            if (el) el.value = proj[id];
        }
        _bkSetRootLocked(id, locked);

        const saveBtn = document.getElementById('bk_save_' + id);
        if (saveBtn) saveBtn.classList.toggle('hidden', !(proj && !isSet));

        const flash = _bkRootFlash[id];
        if (flash) {
            _bkSetRootNote(id, flash.msg, flash.tone);
        } else if (!proj) {
            _bkSetRootNote(id, '');
        } else if (isSet) {
            _bkSetRootNote(id, '來自專案設定，要改請到專案頁。');
        } else {
            _bkSetRootNote(id, `這個案還沒設定這一根 —— 填好可按「儲存到專案」補回專案（不會覆蓋別人已設定的）。`, 'warn');
        }
    }

    const hint = document.getElementById('bk_project_hint');
    if (!hint) return;

    if (!proj) {
        if (_bkProjectsUnavailable) {
            hint.className = 'text-[11px] text-amber-400 mt-1';
            hint.textContent = BK_NO_LIST;
        } else {
            hint.className = 'text-[11px] text-gray-500 mt-1 hidden';
            hint.textContent = '';
        }
        return;
    }

    const missing = BK_ROOT_IDS.filter(id => !_bkRootIsSet(proj, id));
    const link = _bkProjectLink(proj);
    if (missing.length === BK_ROOT_IDS.length) {
        hint.className = 'text-[11px] text-amber-400 mt-1';
        hint.innerHTML = `⚠️ 這個案還沒設定備份資料夾（本機／NAS／Proxy 三個根目錄都是空的）。`
            + `下面填好可以直接按「儲存到專案」補回去，或${link}`;
    } else if (missing.length) {
        hint.className = 'text-[11px] text-amber-400 mt-1';
        hint.innerHTML = `⚠️ 這個案的備份資料夾沒設定完，缺：${missing.map(id => esc(BK_ROOT_LABELS[id])).join('、')}。`
            + `填好可按「儲存到專案」補回去，或${link}`;
    } else {
        hint.className = 'text-[11px] text-gray-500 mt-1';
        hint.innerHTML = `三個路徑來自專案設定，要改請到專案頁。${link}`;
    }
}

/**
 * 把某一根存回綁定的專案（PUT /api/v1/projects/backup-roots/{project_id}）。
 * 後端**只填空、不覆寫**：專案上已經有值的會回在 `skipped`（代表別人剛好搶先設定），
 * 這時把那幾根切成唯讀並顯示 skipped_reason。db_offline **不清掉使用者打的字**。
 */
async function bkSaveRootToProject(rootId) {
    const proj = bkSelectedProject();
    if (!proj) return;
    const el = document.getElementById(rootId);
    const val = String(el?.value || '').trim();
    if (!val) { alert(`請先填「${BK_ROOT_LABELS[rootId] || rootId}」的路徑再儲存到專案`); return; }

    const btn = document.getElementById('bk_save_' + rootId);
    const origText = btn?.textContent;
    if (btn) { btn.disabled = true; btn.textContent = '儲存中…'; }
    try {
        const res = await authFetch(
            getComputeBaseUrl() + '/api/v1/projects/backup-roots/' + encodeURIComponent(proj.id),
            { method: 'PUT', body: { [rootId]: val } },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.message || ('HTTP ' + res.status));

        if (data.status === 'db_offline') {
            _bkRootFlash[rootId] = { msg: '⚠️ 存不回專案：資料庫目前讀不到。你打的路徑保留著，這次派工照樣送得出去。', tone: 'warn' };
            appendLog('存不回專案：資料庫目前讀不到（路徑保留，不影響派工）', 'error');
            bkSyncProjectRoots({ fill: false });   // 🔴 fill:false — 不要動使用者打的字
            return;
        }
        if (data.status === 'not_found') {
            _bkRootFlash[rootId] = { msg: '⚠️ 找不到這個專案（可能剛被刪掉），請重新選擇。', tone: 'warn' };
            appendLog('存不回專案：找不到該專案', 'error');
            bkSyncProjectRoots({ fill: false });
            return;
        }

        // ok：用回傳的最新專案物件取代快取（三根已翻成本機視角）
        if (data.project) {
            // 🔴 合併不是取代：回存端點回的是單筆投影（id／name／三根），
            // 直接蓋掉會把浮層分組要的 client／year／closed／label 弄不見。
            const i = _bkProjects.findIndex(p => String(p.id) === String(data.project.id));
            if (i >= 0) _bkProjects[i] = { ..._bkProjects[i], ...data.project };
            else _bkProjects.push(data.project);
        }
        const saved = Array.isArray(data.saved) ? data.saved : [];
        const skipped = Array.isArray(data.skipped) ? data.skipped : [];
        const label = (k) => BK_ROOT_LABELS[k] || k;

        // 只覆蓋這次牽涉到的那幾根 —— 別的根先前存成功的綠字留著（換案時才整批清）
        for (const k of saved) {
            _bkRootFlash[k] = { msg: `✅ 已存回專案「${esc(proj.name || proj.id)}」，之後這一根就以專案設定為準。`, tone: 'ok' };
        }
        if (saved.length) appendLog(`已存回專案「${proj.name || proj.id}」：${saved.map(label).join('、')}`, 'system');
        if (skipped.length) {
            const reason = data.skipped_reason || '這幾根專案上已經有設定了，要修改請到專案頁';
            for (const k of skipped) _bkRootFlash[k] = { msg: `⚠️ 沒有寫入：${esc(reason)}`, tone: 'warn' };
            appendLog(`未寫入：${skipped.map(label).join('、')} —— ${reason}`, 'system');
        }
        if (data.warning) {
            appendLog(`⚠️ ${data.warning}`, 'system');
            // 警告不是錯誤，值已經存進去了 —— 綠字後面補一句，別讓它只躺在 log 裡
            for (const k of saved) {
                _bkRootFlash[k] = { msg: `${_bkRootFlash[k].msg} ⚠️ ${esc(data.warning)}`, tone: 'warn' };
            }
        }

        // saved / skipped 的根在最新的 project 上都有值了 → 這次重畫會切成唯讀
        bkSyncProjectRoots({ fill: true });
    } catch (err) {
        alert('儲存到專案失敗：' + err.message);
        appendLog('儲存到專案失敗：' + err.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = origText || '儲存到專案'; }
    }
}

async function loadBackupProjects() {
    const inp = _bkProjectInput();
    if (!inp) return;
    _bkOkPlaceholder ||= inp.placeholder;      // html 那句是正本，別在 js 再抄一份
    const base = getComputeBaseUrl();
    try {
        // 不帶 limit＝整份（後端不篩狀態，結案的案照樣列得出來 —— 備份最常發生在結案之後）。
        // 篩選交給浮層自己在前端做，所以清單要是完整的。
        const res = await authFetch(base + '/api/v1/projects/backup-roots');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        _bkProjects = Array.isArray(data?.projects) ? data.projects : [];
        _bkProjectsUnavailable = (data?.status === 'db_offline');
    } catch (_) {
        // 沒登入 / 離線 / 舊 agent 沒這支端點 —— 跟 db_offline 同樣處理，不擋派工
        _bkProjects = [];
        _bkProjectsUnavailable = true;
    }

    // 沒有下拉要重建：浮層每次打開都跟 _bkProjects 要一次（attachProjectPop 的 cfg.options）。
    inp.disabled = _bkProjectsUnavailable;
    inp.classList.toggle('opacity-50', _bkProjectsUnavailable);
    inp.placeholder = _bkProjectsUnavailable ? BK_NO_LIST : _bkOkPlaceholder;

    bkSyncProjectRoots({ fill: true });
    return _bkProjects;
}

/** 解除綁定：三個 input 恢復可編輯，**值保留**。 */
function bkClearProject() {
    const inp = _bkProjectInput();
    if (!inp) return;
    inp.value = '';
    delete inp.dataset.pid;          // 浮層認的就是這一格：沒有 pid＝沒綁
    delete inp.dataset.pname;
    inp.title = '';
    _bkRootFlash = {};
    bkSyncProjectRoots({ fill: false });
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
        // 綁定的 CRM 專案（沒綁就送 ""）。有值時後端以 DB 的三根為準、忽略下面
        // 這三個路徑；仍照實送，讓 log／排程回放看得出當時畫面上是什麼。
        project_id: document.getElementById('bk_project_pick')?.dataset.pid || '',
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

/**
 * 重送上一次失敗的任務（index.html 的「重試」按鈕，任務出錯時才出現）。
 *
 * 2026-08-12 補回：這支在某次重構搬檔時掉了，`app.js` 只留一行
 * 「migrated to backup.js」的註解，但 backup.js 沒有 —— 按鈕的 onclick
 * 指向不存在的函式，點下去是 ReferenceError + 全螢幕紅色錯誤橫幅，
 * 四個 tab 的失敗重試一起斷。
 */
export async function retryLastJob() {
    const job = window._lastJob;
    if (!job || !job.url) { alert('沒有可重試的任務'); return; }
    const btn = document.getElementById('btn_retry');
    if (btn) { btn.disabled = true; btn.textContent = '重送中…'; }
    try {
        const res = await fetch(job.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(job.payload),
        });
        const result = await res.json().catch(() => ({}));
        if (!res.ok) {
            appendLog(`重試失敗: ${result.detail || result.message || ('HTTP ' + res.status)}`, 'error');
            alert(result.detail || ('重試失敗（HTTP ' + res.status + '）'));
            return;
        }
        appendLog(`已重送任務，狀態: ${result.status}, 任務 ID: ${result.job_id || '?'}`, 'system');
        if (result.warning) appendLog(`⚠️ ${result.warning}`, 'system');
        if (btn) btn.style.display = 'none';
    } catch (err) {
        appendLog(`重試發送失敗: ${err.message}`, 'error');
        alert('重試發送失敗：' + err.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '重試'; }
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
    // 優先序：專案設定 > 書籤 > 手打。跟「儲存到專案」同一條原則 —— **只填空、
    // 不覆寫**：專案上已經設定的那一根書籤蓋不掉（那一根本來就是唯讀的），
    // 專案上還沒設定的那幾根書籤可以填進去（填完就長出「儲存到專案」，順手存回去）。
    const _boundProj = bkSelectedProject();
    const _skipped = [];
    for (const [elId, key] of [['local_root', 'local_root'], ['nas_root', 'nas_root'],
                               ['proxy_root', 'proxy_root']]) {
        if (_boundProj && String(_boundProj[key] || '').trim()) { _skipped.push(elId); continue; }
        setVal(elId, r[key]);
    }
    if (_skipped.length) {
        appendLog(`書籤「${bm.name}」的 ${_skipped.join('、')} 已略過 — 專案「${_boundProj.name || _boundProj.id}」已設定這幾根，路徑以專案設定為準`, 'system');
    }
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

// 磁碟對應設定已移至右上角使用者選單（js/shared/drive-map-modal.js，全軟體層級）


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
    // 綁了專案時三個根是唯讀的，但 setupInputDrop 不看 readOnly（丟一個資料夾
    // 進去照樣覆寫）。先掛一道攔截 —— 同一個元素上的監聽依**註冊順序**觸發，
    // 所以這段一定要在下面三行 setupInputDrop 之前。
    for (const _rootId of BK_ROOT_IDS) {
        document.getElementById(_rootId)?.addEventListener('drop', (e) => {
            if (e.currentTarget.readOnly) { e.preventDefault(); e.stopImmediatePropagation(); }
        });
    }
    setupInputDrop('local_root');
    setupInputDrop('nas_root');
    setupInputDrop('proxy_root');
    setupInputDrop('bk_rpt_output');

    // Sync initial visibility of concat/report options panels
    toggleConcatOptions();
    toggleReportOptions();

    // 綁定 CRM 專案：載入可選專案 + 選了就鎖住三個根目錄並帶入
    // 🔴 浮層拿到的是**這一輪抓取的 promise**，不是 `_bkProjects` 那個當下還空著的陣列 ——
    // `attachProjectPop` 的契約本來就收 `Promise<rows>`（工時、週記、零用金三個宿主都是
    // 這樣接的），`_open` 會 await 它、await 完再確認焦點還在才畫。交同步陣列的話，人搶在
    // fetch 回來之前點進去就會看到「進行中（0）」，而且那份空快照連打字重繪也換不掉。
    _bkProjectsReady = loadBackupProjects();
    const _projRow = document.getElementById('bk_project_row');
    attachProjectPop(_projRow, { options: async () => { await _bkProjectsReady; return _bkProjects; } });
    // 🔴 掛在**外層那個 div**、而且要在 attachProjectPop 之後掛：浮層清 data-pid 的那支
    // 監聽器是委派在這個 div 上的（冒泡階段），掛在 input 自己身上的屬於目標階段、**會先跑**
    // —— 那時 pid 還在，等於用上一個案又 fill 一次然後鎖回去，而 pid 隨即被清掉、不會再有
    // 事件把它解開（症狀：把案名整串選起來刪掉，三個根目錄就卡在唯讀）。同一個 div 上的
    // 監聽器照註冊順序跑，所以這行必須排在 attachProjectPop 後面。
    // 選案與手打兩條路都必定派一個 input，而且到達這裡時 pid 已經是最終狀態，一個就夠。
    _projRow?.addEventListener('input', () => {
        _bkRootFlash = {};              // 換案了：上一案的「已存回／沒寫入」小字要收掉
        bkSyncProjectRoots({ fill: true });
    });

    // 路徑書籤：載入清單 + 選了即套用
    loadBookmarks();
    document.getElementById('bk_bookmark_sel')?.addEventListener('change', e => {
        if (e.target.value) bkApplyBookmark(e.target.value);
    });

    // 專案名稱預設今天（原本在 app.js 開機段——分頁點到才載後，這頁的 DOM 開機時不存在）；
    // 轉檔／合併勾選→多機面板的顯示由 html 上的 onchange="renderHostSelector()" 管
    if (!document.getElementById('proj_name')?.value) setTodayName();
    loadReportHistory();   // 「最新備份報表」清單（js/shared/report-history.js，跟報表頁共用）
}


// Bind to window for inline onclick execution (temporary during migration)
window.addSourceRow = addSourceRow;
window.setTodayName = setTodayName;
window.submitJob = submitJob;
window.toggleConcatOptions = toggleConcatOptions;
window.toggleReportOptions = toggleReportOptions;
window.bkSaveBookmark = bkSaveBookmark;
window.bkDeleteBookmark = bkDeleteBookmark;
window.bkClearProject = bkClearProject;
window.bkSaveRootToProject = bkSaveRootToProject;
window.retryLastJob = retryLastJob;
