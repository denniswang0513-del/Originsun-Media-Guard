// ─── User Management (extracted from app.js) ─── //
// RBAC v2: 權限直接綁帳號（角色層已移除）。每個帳號 = 一組可勾選模組 + 「管理員」開關。
import { _ensureModalStyles, _createFormModal } from '../shared/modal-styles.js';
import { groupModules, ALL_MODULES, TAB_GROUPS, shouldShowTab, tabLabel, expandModules, bundleOf } from '../shared/tab-config.js';
import { createSortable, sortableSpan, esc } from '../../tabs/crm/crm-utils.js';

// key 集合必須 == core/auth.py ALL_MODULES == tab-config.js PERMISSION_GROUPS
// （tests/unit/test_rbac_module_sync.py 三方同步測試把關，漏 key 會 fail）
const MODULE_LABELS = {bulletin:'公布欄',references:'片庫',comfyui:'ComfyUI',projects:'專案',crm_clients:'客戶',crm_projects:'專案管理',crm_quotes:'報價',crm_staff:'人力',crm_invoices:'財務管理',money_view:'金額檢視',finance_approve:'零用金審核',finance_partner:'母公司報表',finance_mine:'我的帳',timesheets:'工時檢核',portal:'審批門戶',media_log:'影像紀錄',website_admin:'官網',me_projects:'我的專案',me_profile:'我的資料',me_todos:'我的待辦',me_finance:'我的工時請款',me_benefits:'我的福委會',journal:'工作日誌',me_leave:'我的請假',me_petty:'我的請款',me_worklog:'今天的專案紀錄',me_team_week:'團隊的一週',me_project_lookup:'專案查詢',me_plan_parttime:'兼職排班',me_today_zone:'今天與這週',me_week_plan:'我的一週',preprod:'前期製作（拍攝企劃／場景庫／提案庫／產業情報／器材庫）',postprod:'後期製作（備份／比對／轉檔／串帶／空拍／報表／逐字稿／語音／素材庫）',hr:'人事（請補修＋福委會管理）'};

// 每把鑰匙的相依說明（階段 3，2026-09-08）：畫面上一行灰字＋滑過的 title，管理員不用記。
// 只寫「勾了會怎樣／還要配什麼」，不寫功能介紹（那是 MODULE_LABELS 的事）。
const MODULE_HINTS = {
    money_view: '看得到金額；帳務、報價都要配它', crm_invoices: '要配「金額檢視」', crm_quotes: '要配「金額檢視」',
    finance_partner: '母公司報表唯讀；不要跟「金額檢視」同給', finance_mine: '指名才有，管理員也要明勾', finance_approve: '零用金審核；要配「金額檢視」',
    crm_projects: '含建案、推進階段、雜支、歸檔；刪除與匯入仍限管理員', crm_staff: '含編輯履歷；刪除與匯入仍限管理員',
    crm_clients: '刪除與匯入仍限管理員', timesheets: '全員工時；私帳對映仍限管理員',
    me_profile: '需綁定人員檔案', me_today_zone: '總開關；下面四把要先有它', me_worklog: '需綁定人員檔案', me_week_plan: '需綁定人員檔案',
    me_team_week: '需綁定人員檔案', me_project_lookup: '需綁定人員檔案', me_leave: '需綁定人員檔案', me_petty: '需綁定人員檔案', me_benefits: '需綁定人員檔案',
    me_plan_parttime: '要綁定在職／合夥人員；幫兼職排他的一週', website_admin: '三個身份都有', portal: '也可由「專案管理」開', references: '也可由提案庫／專案管理開',
    me_todos: '畫面未開', me_finance: '畫面未開',
    postprod: '一把＝後期九個分頁（本機代理免登入）', preprod: '一把＝前期五個分頁；片庫另一把', hr: '看與登記全員假勤、福委會管理；核准仍限管理員',
};
// 哪些鑰匙沒綁人員檔案就等於沒作用（員工工作台整區空白）——列上紅字提醒
const STAFF_BOUND_KEYS = ['me_profile', 'me_today_zone', 'me_worklog', 'me_week_plan', 'me_team_week', 'me_project_lookup', 'me_leave', 'me_petty', 'me_benefits', 'me_plan_parttime', 'me_projects'];

// 有層級的鑰匙：子 → 父。父（總開關）沒勾時子鑰匙灰掉；勾子鑰匙時父自動一起勾（後端 require_zone_staff 兩者都要）。
const PERM_PARENT = { me_worklog: 'me_today_zone', me_week_plan: 'me_today_zone', me_team_week: 'me_today_zone', me_project_lookup: 'me_today_zone',
    crm_invoices: 'money_view', crm_quotes: 'money_view' };   // 第三批一把尺：帳務／報價的讀寫都要金額檢視，勾子鑰匙自動配

// The 4-group structure is identical for every user (it's all modules grouped),
// so compute it once rather than per user row / per modal open.
const _PERM_GROUPS = groupModules(ALL_MODULES);

// ── 點欄頭排序（CSS grid 欄頭；操作欄不排）──
// 預設 key '' = 不排序、維持後端順序，點了才生效。
let _usersCache = [];
let _staffListCache = [];
const _userSorter = createSortable({
    storageKey: 'usermgmt_users_sort',
    defaultSort: { key: '', dir: 'asc' },
    panelId: 'umgmt-list',
    onChange: () => _renderUserList(),
    getters: {
        username: u => u.username || '',
        // 權限欄：管理員視為最大（ALL_MODULES 數 +1），一般帳號按模組數
        perms: u => ((u.access_level || 0) >= 3 ? ALL_MODULES.length + 1 : (u.modules || []).length),
    },
});

// Render the editable, 4-group permission cell for one user. `locked` disables
// everything (built-in admin: prevent self-lockout). When 管理員 is on, modules
// are implied (full access) so the grid is dimmed.
function _renderUserPermCell(username, userModules, isAdminUser, locked, opts = {}) {
    const groups = _PERM_GROUPS;
    const dis = locked ? 'disabled' : '';
    // 兼職排班：幫「兼職」排我的一週，所以綁定人員是兼職（或沒綁）的帳號不能勾（兼職不能排兼職）
    const boxDis = (m) => (m === 'me_plan_parttime' && !opts.canPlanParttime) ? 'disabled title="要綁定在職人員才能勾（兼職不能排兼職）"' : dis;
    const adminRow = `
        <label class="_fm-chk" style="padding:3px 6px;font-weight:600;color:${isAdminUser ? '#a78bfa' : '#999'};">
            <input type="checkbox" data-uadmin-user="${username}" ${isAdminUser ? 'checked' : ''} ${dis}
                   onchange="window._onUserAdminToggle('${username}', this.checked)">
            👑 管理員（完整權限：使用者 / 設定 / 發版）
        </label>`;
    const groupsHtml = groups.map(g => {
        const total = g.modules.length;
        const checkedN = g.modules.filter(m => userModules.includes(m)).length;
        const allOn = checkedN === total && total > 0;
        const boxes = g.modules.map(m => {
            const parent = PERM_PARENT[m];
            const parentOff = parent && !userModules.includes(parent);
            const hint = MODULE_HINTS[m] || (parent ? `要先開「${MODULE_LABELS[parent] || parent}」` : '');
            return `
            <label class="_fm-chk" style="min-width:auto;padding:2px 6px;${parent ? 'margin-left:18px;' : ''}"${hint ? ` title="${hint}"` : ''}>
                <input type="checkbox" data-umod-user="${username}" data-group="${g.id}" value="${m}"${parent ? ` data-parent="${parent}"` : ''}
                       ${userModules.includes(m) ? 'checked' : ''} ${boxDis(m) || (parentOff ? 'disabled' : '')}
                       onchange="window._syncUserGroupMaster('${username}','${g.id}'); window._syncPermParent('${username}','${m}')"> ${parent ? '└ ' : ''}${MODULE_LABELS[m] || m}${hint ? `<span style="color:#555;font-size:10px;margin-left:4px;font-weight:400;">${hint}</span>` : ''}
            </label>`; }).join('');
        return `
            <div style="margin-bottom:4px;">
                <label class="_fm-chk" style="font-weight:600;color:#bbb;padding:2px 6px;">
                    <input type="checkbox" data-umaster-user="${username}" data-group="${g.id}" ${allOn ? 'checked' : ''} ${dis}
                           onchange="window._toggleUserGroup('${username}','${g.id}',this.checked)"> ${g.label}
                    <span data-ucount-user="${username}" data-group="${g.id}" style="color:#666;font-size:10px;font-weight:400;margin-left:4px;">${checkedN}/${total}</span>
                </label>
                <div style="display:flex;flex-wrap:wrap;gap:1px;padding-left:18px;">${boxes}</div>
            </div>`;
    }).join('');
    return `${adminRow}
        <div data-uperm-user="${username}" style="margin-top:2px;opacity:${isAdminUser ? '0.45' : '1'};pointer-events:${isAdminUser ? 'none' : 'auto'};">${groupsHtml}</div>`;
}

window._openUserMgmt = async function() {
    _ensureModalStyles();
    document.getElementById('user-mgmt-modal')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'user-mgmt-modal';
    overlay.className = '_fm-overlay';
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.addEventListener('keydown', function _esc(e) { if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', _esc); } });

    const modal = document.createElement('div');
    modal.className = '_fm-modal';
    modal.style.width = '780px'; modal.style.maxWidth = '92%';
    modal.innerHTML = `
        <div class="_fm-header" style="padding:14px 24px;border-bottom:none;">
            <div style="display:flex;align-items:center;gap:0;">
                <button id="umgmt-tab-users" class="_umgmt-tab _umgmt-tab-active" onclick="window._switchMgmtTab('users')">使用者</button>
                <button id="umgmt-tab-keys" class="_umgmt-tab" onclick="window._switchMgmtTab('keys')">API Keys</button>
                <button id="umgmt-tab-tpl" class="_umgmt-tab" onclick="window._switchMgmtTab('tpl')">身份範本</button>
                <button id="umgmt-tab-pub" class="_umgmt-tab" onclick="window._switchMgmtTab('pub')">公開區</button>
            </div>
            <div style="display:flex;gap:8px;align-items:center;">
                <button id="umgmt-action-btn" class="_fm-btn-submit" style="padding:5px 16px;font-size:12px;">+ 新增使用者</button>
                <span class="_fm-close" onclick="document.getElementById('user-mgmt-modal')?.remove()">&#x2715;</span>
            </div>
        </div>
        <div style="height:1px;background:#333;margin:0;"></div>
        <div class="_fm-body" style="padding:16px 24px;min-height:280px;">
            <div id="umgmt-panel-users" style="font-size:12px;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
            <div id="umgmt-panel-keys" style="font-size:12px;display:none;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
            <div id="umgmt-panel-tpl" style="font-size:12px;display:none;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
            <div id="umgmt-panel-pub" style="font-size:12px;display:none;">
                <div style="text-align:center;color:#666;padding:20px;">載入中...</div>
            </div>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Inject tab styles (once)
    if (!document.getElementById('_umgmtTabStyles')) {
        const s = document.createElement('style');
        s.id = '_umgmtTabStyles';
        s.textContent = `
            ._umgmt-tab { background:transparent;border:none;color:#666;font-size:13px;font-weight:500;padding:10px 20px;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;letter-spacing:0.3px; }
            ._umgmt-tab:hover { color:#bbb; }
            ._umgmt-tab-active { color:#f0f0f0;border-bottom-color:#7c3aed; }
        `;
        document.head.appendChild(s);
    }

    // Wire up action button and tabs
    document.getElementById('umgmt-action-btn').onclick = () => window._addUserPrompt();

    // Remap panel IDs to match what _loadUserList / _loadApiKeyList expect
    document.getElementById('umgmt-panel-users').id = 'umgmt-list';
    document.getElementById('umgmt-panel-keys').id = 'apikey-list';
    document.getElementById('umgmt-panel-tpl').id = 'umgmt-tpl';
    document.getElementById('umgmt-panel-pub').id = 'umgmt-pub';

    window._switchMgmtTab = function(tab) {
        const panels = { users: 'umgmt-list', keys: 'apikey-list', tpl: 'umgmt-tpl', pub: 'umgmt-pub' };
        Object.entries(panels).forEach(([k, id]) => {
            const el = document.getElementById(id); if (el) el.style.display = k === tab ? '' : 'none';
            document.getElementById('umgmt-tab-' + k)?.classList.toggle('_umgmt-tab-active', k === tab);
        });
        const actionBtn = document.getElementById('umgmt-action-btn');
        if (tab === 'users') {
            actionBtn.textContent = '+ 新增使用者'; actionBtn.onclick = () => window._addUserPrompt();
        } else if (tab === 'keys') {
            actionBtn.textContent = '+ 產生新 Key'; actionBtn.onclick = () => window._createApiKey();
            if (typeof window._loadApiKeyList === 'function') window._loadApiKeyList();
        } else if (tab === 'tpl') {
            actionBtn.textContent = '儲存範本'; actionBtn.onclick = () => window._saveRbacTemplates();
            _loadTemplates();
        } else {
            actionBtn.textContent = '儲存公開區'; actionBtn.onclick = () => window._savePublicAccess();
            _loadPublicAccess();
        }
    };

    await _loadUserList();
};

async function _loadUserList() {
    const container = document.getElementById('umgmt-list');
    if (!container) return;
    try {
        const r = await fetch('/api/v1/auth/users', { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') } });
        if (!r.ok) { container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗（需要管理員權限）</div>'; return; }
        _usersCache = await r.json();

        // N0: 人員清單（綁定下拉的資料來源）。載入失敗不擋使用者列表。
        _staffListCache = [];
        try {
            const rs = await fetch('/api/v1/crm/staff', { headers: { 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || '') } });
            if (rs.ok) _staffListCache = (await rs.json()).staff || [];
        } catch (_) {}

        await _fetchTemplates();
        await _fetchDenials();
        _renderUserList();
    } catch (_) {
        container.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">載入失敗</div>';
    }
}

// 渲染使用者列表（初次載入與點欄頭排序共用，讀 _usersCache / _staffListCache）
function _renderUserList() {
    const container = document.getElementById('umgmt-list');
    if (!container) return;
    // Table header（grid 欄頭非 table：sortableSpan 標記；操作欄不排）
    let html = _denialsHtml() + `<div style="display:grid;grid-template-columns:170px 1fr auto;gap:0;font-size:11px;color:#666;padding:0 16px 8px;font-weight:600;letter-spacing:0.5px;text-transform:uppercase;">
        ${sortableSpan('username', '帳號')}${sortableSpan('perms', '權限')}<span>操作</span>
    </div>`;
    html += _userSorter.sorted(_usersCache).map(u => {
        const modules = u.modules || [];
        const isAdminUser = (u.access_level || 0) >= 3;
        const locked = (u.username === 'admin');   // 內建超級帳號鎖定，避免把自己鎖在外
        const am = u.auth_method || 'password';
        const authBadge = am === 'google'
            ? '<span style="display:inline-block;background:#4285f422;color:#8ab4f8;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">G</span>'
            : am === 'both'
            ? '<span style="display:inline-block;background:#4285f422;color:#8ab4f8;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">G+</span>'
            : '';
        const avatarImg = u.avatar_url
            ? `<img src="${u.avatar_url}" style="width:20px;height:20px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:4px;">`
            : '';
        const emailLine = u.email
            ? `<div style="font-size:10px;color:#666;margin-top:1px;">${u.email}</div>`
            : '';
        // N0: 綁定人員檔案（帳號 ↔ crm_staff，個人工作台 /my.html 的資料鍵）
        const staffOpts = ['<option value="">— 未綁定人員 —</option>']
            .concat(_staffListCache.map(s => `<option value="${s.id}" ${s.id === u.staff_id ? 'selected' : ''}>${s.name}${s.role ? '（' + s.role + '）' : ''}</option>`))
            .join('');
        const staffSelect = `<select data-ustaff-user="${u.username}" ${locked ? 'disabled' : ''} title="綁定人員檔案 — 個人工作台（/my.html）的資料來源"
            style="margin-top:6px;width:100%;max-width:150px;background:#252525;color:#ccc;border:1px solid #333;border-radius:6px;padding:2px 4px;font-size:11px;">${staffOpts}</select>`;
        // 綁定人員的在職／兼職（分配權限時一眼看得到誰是兼職；空白視同在職，同 core.hr_logic）
        const boundStaff = _staffListCache.find(s => s.id === u.staff_id) || null;
        const staffStatus = boundStaff ? ((boundStaff.status || '').trim() || '在職') : '';
        // 「算在職」＝在職／合夥／空白（正本 core.hr_logic.ACTIVE_STATUSES）；兼職橘、合夥藍、其餘綠
        const pillColor = staffStatus === '兼職' ? ['#b4530922', '#f59e0b'] : staffStatus === '合夥' ? ['#1d4ed822', '#93c5fd'] : ['#15803d22', '#6ee7b7'];
        const statusPill = boundStaff
            ? `<span style="display:inline-block;font-size:9px;padding:1px 5px;border-radius:3px;margin-top:4px;background:${pillColor[0]};color:${pillColor[1]};">${staffStatus}</span>`
            : '';
        const canPlanParttime = !!boundStaff && ['在職', '合夥'].includes(staffStatus);
        const unboundWarn = (!boundStaff && !isAdminUser && modules.some(k => STAFF_BOUND_KEYS.includes(k)))
            ? '<div style="color:#f87171;font-size:10px;margin-top:4px;line-height:1.4;">未綁定人員檔案：員工工作台的區塊會是空的</div>' : '';
        return `
        <div style="display:grid;grid-template-columns:170px 1fr auto;gap:12px;align-items:start;padding:12px 16px;margin-bottom:1px;background:#1e1e1e;border:1px solid #2e2e2e;border-radius:8px;transition:border-color .15s;" onmouseenter="this.style.borderColor='#444'" onmouseleave="this.style.borderColor='#2e2e2e'">
            <div style="padding-top:4px;">
                <div>${avatarImg}<span style="color:#f0f0f0;font-weight:600;font-size:13px;">${u.username}</span>${u.username === 'admin' ? '<span style="display:inline-block;background:#7c3aed22;color:#a78bfa;font-size:9px;padding:1px 5px;border-radius:3px;margin-left:4px;vertical-align:middle;">SUPER</span>' : ''}${authBadge}</div>
                ${emailLine}
                ${staffSelect}${statusPill}${unboundWarn}
                <button type="button" onclick="window._previewAs('${u.username}')" class="_fm-btn-cancel" style="margin-top:6px;padding:2px 8px;font-size:10px;" title="照目前勾的（還沒儲存也算）列出他會看到哪些分頁與區塊">以他的角度看</button>
            </div>
            <div style="min-width:0;">${_renderUserPermCell(u.username, modules, isAdminUser, locked, { canPlanParttime })}</div>
            <div style="display:flex;gap:6px;align-items:center;padding-top:4px;">
                <button onclick="window._changeUserPwd('${u.username}')" class="_fm-btn-cancel" style="padding:3px 10px;font-size:11px;">改密碼</button>
                ${(!locked && !isAdminUser && _tplCache && _tplCache[staffStatus]) ? `<button onclick="window._applyTemplateToUser('${u.username}','${staffStatus}')" class="_fm-btn-cancel" style="padding:3px 10px;font-size:11px;" title="把勾選換成「${staffStatus}」範本（換完還是要按儲存）">套${staffStatus}範本</button>` : ''}
                ${locked ? '' : `<button onclick="window._saveUserSettings('${u.username}')" class="_fm-btn-submit" style="padding:3px 12px;font-size:11px;font-weight:500;">儲存</button>`}
                ${u.username !== 'admin' ? `<button onclick="window._deleteUser('${u.username}')" style="background:transparent;border:1px solid rgba(239,68,68,0.3);color:#f87171;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;transition:all .15s;" onmouseenter="this.style.borderColor='#ef4444';this.style.background='rgba(239,68,68,0.08)'" onmouseleave="this.style.borderColor='rgba(239,68,68,0.3)';this.style.background='transparent'">刪除</button>` : ''}
            </div>
        </div>`;
    }).join('');
    container.innerHTML = html;

    // Reflect partial-group state (indeterminate can't be set via HTML attr).
    container.querySelectorAll('input[data-umaster-user]').forEach(master => {
        const u = master.getAttribute('data-umaster-user');
        const g = master.getAttribute('data-group');
        const boxes = [...container.querySelectorAll(`input[data-umod-user="${u}"][data-group="${g}"]`)];
        const checked = boxes.filter(b => b.checked).length;
        master.indeterminate = checked > 0 && checked < boxes.length;
    });
    _userSorter.attach();
}

// ─── 以他的角度看（階段 3）：照目前勾選（未儲存也算）列出這個帳號會看到的分頁／員工頁區塊／手機版／財務分頁 ─── //
window._previewAs = function(username) {
    const u = _usersCache.find(x => x.username === username); if (!u) return;
    const adminEl = document.querySelector(`input[data-uadmin-user="${username}"]`);
    const isAdmin = adminEl ? adminEl.checked : (u.access_level || 0) >= 3;
    // 格子是捆鍵（postprod／preprod／hr），shouldShowTab 的 TAB_MAP 是成員級 —— 跟 token 一樣先展開，不然後期／前期／人事整組預覽不到
    const mods = expandModules(isAdmin ? ALL_MODULES.slice() : [...document.querySelectorAll(`input[data-umod-user="${username}"]:checked`)].map(cb => cb.value));
    const has = (k) => isAdmin || mods.includes(k);
    const staffSel = document.querySelector(`select[data-ustaff-user="${username}"]`);
    const bound = _staffListCache.find(s => s.id === (staffSel ? staffSel.value : u.staff_id)) || null;
    const status = bound ? ((bound.status || '').trim() || '在職') : '';
    const authUser = { access_level: isAdmin ? 3 : 1 };
    // 頂層分頁
    const groups = TAB_GROUPS.map(g => {
        const keys = g.single ? [g.single] : (g.items || []).map(i => i.key);
        const vis = keys.filter(k => shouldShowTab(k, authUser, mods));
        const glabel = String(g.label || '').replace(/^[\p{Extended_Pictographic}️‍\s]+/u, '');   // 群組標籤帶頂層 tab 的圖示，卡片裡不畫 emoji
        return vis.length ? `<div style="margin:2px 0;"><b style="color:#ddd;">${glabel}</b>${g.single ? '' : ` <span style="color:#aaa;">${vis.map(k => tabLabel(k) || k).join('、')}</span>`}</div>` : '';   // 單頁群組（公布欄／專案總覽／財務）只印一次名字
    }).filter(Boolean).join('') || '<div style="color:#f87171;">沒有任何分頁（只剩本機免登入的後期流程）</div>';
    // 員工工作台
    const z1 = has('me_today_zone') && ['me_worklog', 'me_week_plan', 'me_team_week', 'me_project_lookup'].some(has);
    const meRows = [];
    if (!bound && ['me_profile', 'me_today_zone', 'me_leave', 'me_petty', 'me_benefits', 'me_projects'].some(has)) meRows.push('<span style="color:#f87171;">沒綁人員檔案：下面這些區塊都會是空的</span>');
    if (has('me_profile')) meRows.push('基本資料');
    if (z1) meRows.push('今天與這週：' + [['me_worklog', '今天的專案紀錄'], ['me_week_plan', '我的一週'], ['me_team_week', '團隊的一週'], ['me_project_lookup', '專案查詢']].filter(([k]) => has(k)).map(([, l]) => l).join('、')
        + (has('me_plan_parttime') && bound && ['在職', '合夥'].includes(status) ? '、兼職排班' : ''));
    if (has('me_leave')) meRows.push('我的請假'); if (has('me_petty')) meRows.push('零用金'); if (has('me_benefits')) meRows.push('我的福委會');
    // 財務與手機
    const finRows = [];
    if (has('crm_invoices')) finRows.push(has('money_view') ? '財務分頁：完整（帳務＋金額檢視）' : '<span style="color:#f87171;">財務分頁：有帳務但缺「金額檢視」，每一頁都會擋</span>');
    else if (has('finance_partner')) finRows.push('財務分頁：母公司報表唯讀');
    if (has('crm_quotes') && !has('money_view')) finRows.push('<span style="color:#f87171;">報價分頁：缺「金額檢視」，清單會擋</span>');
    if (has('finance_mine')) finRows.push('我的帳（/my-ledger.html）');
    const mobile = has('crm_projects')
        ? '手機版 /m/crm.html：進得去；' + [has('crm_invoices') && has('money_view') ? '發票／付款可寫' : '發票／付款只能看', '雜支可登', isAdmin ? '加備註' : ''].filter(Boolean).join('、')
        : '手機版 /m/crm.html：進不去（要「專案管理」）';
    const adminNote = isAdmin ? '管理員：使用者管理、設定、發版、刪除、匯入、根目錄、假勤核准全部可做' : '管理員限定（他做不到）：刪除、CSV 匯入、根目錄設定、假勤核准、使用者管理、設定、發版';
    _ensureModalStyles();
    document.getElementById('perm-preview-modal')?.remove();
    const ov = document.createElement('div'); ov.id = 'perm-preview-modal'; ov.className = '_fm-overlay';
    ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
    ov.innerHTML = `<div class="_fm-modal" style="width:560px;max-width:92%;">
        <div class="_fm-header" style="padding:14px 20px;"><div style="font-size:14px;font-weight:600;color:#f0f0f0;">以 ${esc(username)} 的角度看${bound ? `<span style="color:#888;font-weight:400;font-size:12px;margin-left:8px;">${esc(bound.name)}（${esc(status)}）</span>` : ''}</div>
            <span class="_fm-close" onclick="document.getElementById('perm-preview-modal')?.remove()">&#x2715;</span></div>
        <div class="_fm-body" style="padding:14px 20px;font-size:12px;line-height:1.7;">
            <div style="color:#666;font-size:10.5px;margin-bottom:8px;">照目前勾的算（還沒儲存也算）。${isAdmin ? '管理員＝全部。' : `${mods.length} 把鑰匙。`}</div>
            <div style="color:#888;font-size:11px;letter-spacing:.1em;margin-top:6px;">頂層分頁</div>${groups}
            <div style="color:#888;font-size:11px;letter-spacing:.1em;margin-top:10px;">員工工作台 /my.html</div><div style="color:#aaa;">${meRows.length ? meRows.join('<br>') : '沒有任何區塊'}</div>
            <div style="color:#888;font-size:11px;letter-spacing:.1em;margin-top:10px;">財務與手機</div><div style="color:#aaa;">${[...finRows, mobile].join('<br>')}</div>
            <div style="color:#666;font-size:11px;margin-top:10px;">${adminNote}</div>
        </div></div>`;
    document.body.appendChild(ov);
};

// ─── 最近授權不足（階段 0）：後端 403 環形緩衝；一鍵「開通」＝把那把鑰匙勾到該帳號那一列，管理員再按儲存 ─── //
let _denialsCache = [];
async function _fetchDenials() {
    try { const r = await fetch('/api/v1/auth/denials', { headers: _hdr() }); if (r.ok) _denialsCache = (await r.json()).items || []; } catch (_) {}
}
function _denialsHtml() {
    if (!_denialsCache.length) return '';
    const seen = new Set(); const rows = [];
    for (const d of _denialsCache) {                       // 同一人同一把只列一次（最近的）
        const k = d.username + '|' + (d.missing || []).join(',');
        if (seen.has(k) || !d.username) continue; seen.add(k); rows.push(d);
        if (rows.length >= 8) break;
    }
    if (!rows.length) return '';
    const fmt = (iso) => { try { return new Date(iso).toLocaleString('zh-TW', { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }); } catch (_) { return iso; } };
    return `<div style="margin:0 0 12px;padding:10px 14px;background:#1e1a14;border:1px solid #4a3a1a;border-radius:8px;font-size:11.5px;">
        <div style="color:#f59e0b;font-weight:600;margin-bottom:6px;">最近授權不足（重啟後重算）</div>
        ${rows.map(d => `<div style="display:flex;gap:10px;align-items:center;padding:3px 0;color:#bbb;flex-wrap:wrap;">
            <span style="color:#666;font-variant-numeric:tabular-nums;">${fmt(d.at)}</span>
            <b style="color:#e5e5e5;">${esc(d.username)}</b>
            <span>缺 ${(d.labels || d.missing || []).map(esc).join('／') || '管理員'}</span>
            <span style="color:#555;font-family:ui-monospace,Menlo,monospace;font-size:10px;">${esc(d.method)} ${esc(d.path)}</span>
            ${(d.missing || []).length ? `<button type="button" onclick="window._grantFromDenial('${esc(d.username)}','${esc(d.missing[0])}')" class="_fm-btn-cancel" style="padding:1px 8px;font-size:11px;margin-left:auto;">開通「${esc((d.labels || d.missing)[0])}」</button>` : ''}
        </div>`).join('')}</div>`;
}
window._grantFromDenial = function(username, key) {
    key = bundleOf(key);   // 守衛記的是成員鍵（hr_leave／footage…），畫面只有捆的格子
    const box = document.querySelector(`input[data-umod-user="${username}"][value="${key}"]`);
    if (!box) { alert('找不到這個帳號的列（可能是已刪除的帳號）'); return; }
    box.checked = true; box.disabled = false;
    _PERM_GROUPS.forEach(g => window._syncUserGroupMaster(username, g.id));
    window._syncPermParent(username, key);
    box.closest('[data-uperm-user]')?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const save = [...document.querySelectorAll('#umgmt-list button')].find(b => b.textContent.includes('儲存') && (b.onclick?.toString() || '').includes(username));
    if (save) { save.style.outline = '2px solid #f59e0b'; setTimeout(() => { save.style.outline = ''; }, 2500); }
};

// ─── 身份範本（owner 2026-09-08）：合夥／在職／兼職各一組「預設就有」的鑰匙 ─── //
// 範本存 settings.json rbac.templates；只是「填勾選的捷徑」，帳號上仍是自己那份 modules（守衛與 token 不看範本）。
const TPL_IDENTITIES = ['合夥', '在職', '兼職'];
const TPL_HIDDEN = new Set(['me_todos', 'me_finance']);   // 員工頁還沒放回的卡，範本不勾
let _tplCache = null;      // {合夥:[...], 在職:[...], 兼職:[...]}（存過的或預設）
let _tplDefaults = null;
const _hdr = () => ({ 'Authorization': 'Bearer ' + (localStorage.getItem('auth_token') || ''), 'Content-Type': 'application/json' });

async function _fetchTemplates() {
    try {
        const r = await fetch('/api/v1/auth/rbac/templates', { headers: _hdr() });
        if (!r.ok) return;
        const d = await r.json(); _tplCache = d.templates || null; _tplDefaults = d.defaults || null;
    } catch (_) {}
}

async function _loadTemplates() {
    const host = document.getElementById('umgmt-tpl'); if (!host) return;
    if (!_tplCache) await _fetchTemplates();
    if (!_tplCache) { host.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">範本載入失敗</div>'; return; }
    _renderTemplates();
}

// 「現在誰有」：Lv1 帳號依綁定人員的身份分組，數這把鑰匙幾個人有
function _tplHolders() {
    const by = {}; TPL_IDENTITIES.forEach(i => { by[i] = []; });
    _usersCache.forEach(u => {
        if ((u.access_level || 0) >= 3) return;
        const st = _staffListCache.find(s => s.id === u.staff_id); if (!st) return;
        const k = (st.status || '').trim() || '在職';
        if (by[k]) by[k].push(new Set(u.modules || []));
    });
    return by;
}

function _renderTemplates() {
    const host = document.getElementById('umgmt-tpl'); if (!host) return;
    const holders = _tplHolders();
    const cell = (id, m) => {
        const on = _tplCache[id].includes(m);
        const hidden = TPL_HIDDEN.has(m);
        const parent = PERM_PARENT[m];
        return `<td style="text-align:center;padding:3px 6px;"><input type="checkbox" data-tpl-id="${id}" data-tpl-key="${m}"${parent ? ` data-tpl-parent="${parent}"` : ''} ${on ? 'checked' : ''} ${hidden ? 'disabled title="員工頁還沒放回這張卡"' : ''} style="width:15px;height:15px;cursor:pointer;"></td>`;
    };
    const counts = TPL_IDENTITIES.map(id => `<span data-tpl-count="${id}">${_tplCache[id].length}</span>`);
    let html = `
        <div style="display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin-bottom:10px;">
            <div style="color:#999;line-height:1.5;max-width:520px;">每一列是一把鑰匙，勾的是「這個身份預設就有」。管理員固定全部有、不套範本。
                改完按右上「儲存範本」；要套到某個帳號，回「使用者」分頁按那一列的「套範本」再儲存。</div>
            <button type="button" onclick="window._resetRbacTemplates()" class="_fm-btn-cancel" style="padding:3px 10px;font-size:11px;white-space:nowrap;">還原成建議值</button>
        </div>
        <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr style="color:#888;font-size:11px;">
                <th style="text-align:left;padding:4px 6px;border-bottom:1px solid #333;">鑰匙</th>
                <th style="text-align:left;padding:4px 6px;border-bottom:1px solid #333;white-space:nowrap;">現在誰有</th>
                <th style="padding:4px 6px;border-bottom:1px solid #333;color:#666;">管理員</th>
                ${TPL_IDENTITIES.map((id, i) => `<th style="padding:4px 6px;border-bottom:1px solid #333;color:#ddd;">${id}<div style="font-weight:400;color:#666;">${counts[i]} 把</div></th>`).join('')}
            </tr></thead><tbody>`;
    _PERM_GROUPS.forEach(g => {
        html += `<tr><td colspan="3" style="padding:8px 6px 3px;color:#bbb;font-weight:600;">${g.label}</td>
            ${TPL_IDENTITIES.map(id => `<td style="text-align:center;padding:6px 2px 2px;white-space:nowrap;">
                <button type="button" onclick="window._tplBulk('${g.id}','${id}',true)" style="background:transparent;border:1px solid #333;color:#888;border-radius:4px;font-size:10px;padding:0 5px;cursor:pointer;">全給</button>
                <button type="button" onclick="window._tplBulk('${g.id}','${id}',false)" style="background:transparent;border:1px solid #333;color:#888;border-radius:4px;font-size:10px;padding:0 5px;cursor:pointer;">全收</button></td>`).join('')}</tr>`;
        g.modules.forEach(m => {
            const parent = PERM_PARENT[m];
            const who = TPL_IDENTITIES.map(id => `${id} ${holders[id].filter(s => s.has(m)).length}/${holders[id].length}`).join(' · ');
            html += `<tr data-tpl-group="${g.id}" style="border-top:1px solid #222;">
                <td style="padding:3px 6px;${parent ? 'padding-left:22px;' : ''}color:#e5e5e5;">${parent ? '└ ' : ''}${MODULE_LABELS[m] || m}<span style="color:#555;font-family:ui-monospace,Menlo,monospace;font-size:10px;margin-left:6px;">${m}</span></td>
                <td style="padding:3px 6px;color:#666;font-size:10.5px;white-space:nowrap;">${who}</td>
                <td style="text-align:center;color:#555;">✓</td>
                ${TPL_IDENTITIES.map(id => cell(id, m)).join('')}
            </tr>`;
        });
    });
    html += `</tbody></table></div>`;
    host.innerHTML = html;
    host.onchange = (e) => {
        const cb = e.target; if (!cb.matches || !cb.matches('input[data-tpl-id]')) return;
        const id = cb.dataset.tplId, m = cb.dataset.tplKey;
        const set = new Set(_tplCache[id]);
        if (cb.checked) { set.add(m); if (cb.dataset.tplParent) set.add(cb.dataset.tplParent); } else set.delete(m);
        _tplCache[id] = ALL_MODULES.filter(k => set.has(k));
        if (cb.checked && cb.dataset.tplParent) { const p = host.querySelector(`input[data-tpl-id="${id}"][data-tpl-key="${cb.dataset.tplParent}"]`); if (p) p.checked = true; }
        const c = host.querySelector(`[data-tpl-count="${id}"]`); if (c) c.textContent = _tplCache[id].length;
    };
}

window._tplBulk = function(groupId, id, on) {
    const g = _PERM_GROUPS.find(x => x.id === groupId); if (!g || !_tplCache) return;
    const set = new Set(_tplCache[id]);
    g.modules.forEach(m => { if (TPL_HIDDEN.has(m)) return; if (on) set.add(m); else set.delete(m); });
    _tplCache[id] = ALL_MODULES.filter(k => set.has(k));
    _renderTemplates();
};

window._resetRbacTemplates = function() {
    if (!_tplDefaults || !confirm('把三欄放回建議值？（還沒儲存，可以再改）')) return;
    _tplCache = {}; TPL_IDENTITIES.forEach(id => { _tplCache[id] = (_tplDefaults[id] || []).slice(); });
    _renderTemplates();
};

window._saveRbacTemplates = async function() {
    if (!_tplCache) return;
    const btn = document.getElementById('umgmt-action-btn');
    try {
        const r = await fetch('/api/v1/auth/rbac/templates', { method: 'PUT', headers: _hdr(), body: JSON.stringify({ templates: _tplCache }) });
        if (!r.ok) { alert('儲存失敗'); return; }
        _tplCache = (await r.json()).templates; _renderTemplates();
        if (btn) { btn.textContent = '已儲存'; setTimeout(() => { btn.textContent = '儲存範本'; }, 1500); }
    } catch (_) { alert('連線失敗'); }
};

// 使用者列的「套範本」：把這個帳號的勾選換成他身份的範本（例外要自己再勾；換完還是要按儲存）
window._applyTemplateToUser = function(username, status) {
    const tpl = _tplCache && _tplCache[status]; if (!tpl) return;
    const want = new Set(tpl);
    document.querySelectorAll(`input[data-umod-user="${username}"]`).forEach(cb => { cb.checked = want.has(cb.value); });
    _PERM_GROUPS.forEach(g => window._syncUserGroupMaster(username, g.id));
    Object.keys(PERM_PARENT).forEach(m => window._syncPermParent(username, m));
};

// ─── 公開區（owner 2026-09-08）：對外免登入的面集中一份登記表，這裡開關；規則在 core/public_access.py ─── //
let _pubCache = null;   // { surfaces:[{key,label,who,how,pages,modes,default,mode,links}], modes:{}, mode_labels:{} }

async function _loadPublicAccess() {
    const host = document.getElementById('umgmt-pub'); if (!host) return;
    try {
        const r = await fetch('/api/v1/auth/public-access', { headers: _hdr() });
        if (!r.ok) { host.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">公開區載入失敗</div>'; return; }
        _pubCache = await r.json();
    } catch (_) { host.innerHTML = '<div style="text-align:center;color:#f87171;padding:20px;">連線失敗</div>'; return; }
    _renderPublicAccess();
}

function _renderPublicAccess() {
    const host = document.getElementById('umgmt-pub'); if (!host || !_pubCache) return;
    const L = _pubCache.mode_labels || { off: '關閉', link: '連結', open: '公開' };
    let html = `
        <div style="color:#999;line-height:1.5;margin-bottom:10px;max-width:640px;">這些是<b>不用登入</b>就能從外面進來的功能，每一面一列。
            「連結」＝憑證是網址裡的連結（可撤銷）；「關閉」＝整面 404，連結全部失效；「公開」＝連連結都不用（只有履歷與註冊有）。
            改完按右上「儲存公開區」，立即生效。</div>
        <div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="color:#888;font-size:11px;">
            <th style="text-align:left;padding:4px 6px;border-bottom:1px solid #333;">功能</th>
            <th style="text-align:left;padding:4px 6px;border-bottom:1px solid #333;">誰在用</th>
            <th style="text-align:left;padding:4px 6px;border-bottom:1px solid #333;">怎麼進</th>
            <th style="text-align:right;padding:4px 6px;border-bottom:1px solid #333;white-space:nowrap;">有效連結</th>
            <th style="text-align:left;padding:4px 6px;border-bottom:1px solid #333;">模式</th>
        </tr></thead><tbody>`;
    _pubCache.surfaces.forEach(sf => {
        const cur = _pubCache.modes[sf.key] || sf.default;
        html += `<tr style="border-top:1px solid #222;${cur === 'off' ? 'opacity:.6;' : ''}">
            <td style="padding:6px;color:#e5e5e5;white-space:nowrap;">${esc(sf.label)}<div style="color:#555;font-family:ui-monospace,Menlo,monospace;font-size:10px;">${esc(sf.key)}</div></td>
            <td style="padding:6px;color:#aaa;">${esc(sf.who)}</td>
            <td style="padding:6px;color:#888;max-width:360px;">${esc(sf.how)}</td>
            <td style="padding:6px;color:#aaa;text-align:right;font-variant-numeric:tabular-nums;">${sf.links == null ? '—' : sf.links}</td>
            <td style="padding:6px;white-space:nowrap;">${sf.modes.map(m => `<label class="_fm-chk" style="min-width:auto;padding:2px 6px;display:inline-flex;">
                <input type="radio" name="pub-${esc(sf.key)}" value="${m}" data-pub-key="${esc(sf.key)}" ${cur === m ? 'checked' : ''}> ${L[m] || m}</label>`).join('')}</td>
        </tr>`;
    });
    html += `</tbody></table></div>`;
    host.innerHTML = html;
    host.onchange = (e) => { const el = e.target; if (el.matches && el.matches('input[data-pub-key]')) { _pubCache.modes[el.dataset.pubKey] = el.value; } };
}

window._savePublicAccess = async function() {
    if (!_pubCache) return;
    const btn = document.getElementById('umgmt-action-btn');
    try {
        const r = await fetch('/api/v1/auth/public-access', { method: 'PUT', headers: _hdr(), body: JSON.stringify({ modes: _pubCache.modes }) });
        if (!r.ok) { alert('儲存失敗'); return; }
        _pubCache.modes = (await r.json()).modes; _renderPublicAccess();
        if (btn) { btn.textContent = '已儲存'; setTimeout(() => { btn.textContent = '儲存公開區'; }, 1500); }
    } catch (_) { alert('連線失敗'); }
};

// ─── Add User (styled modal) ─── //
window._addUserPrompt = async function() {
    const moduleGroups = _PERM_GROUPS.map(g => ({
        label: g.label,
        options: g.modules.map(m => ({ value: m, label: MODULE_LABELS[m] || m, checked: false })),
    }));
    _createFormModal({
        id: 'add-user-modal',
        title: '新增使用者',
        submitLabel: '建立使用者',
        fields: [
            { type: 'section', label: '帳號資訊' },
            { key: 'username', label: '帳號', type: 'text', required: true, autofocus: true, placeholder: '輸入英文帳號名稱' },
            { key: 'password', label: '密碼', type: 'password', required: true, placeholder: '設定密碼' },
            { key: 'password2', label: '確認密碼', type: 'password', required: true, placeholder: '再次輸入密碼' },
            { type: 'divider' },
            { type: 'section', label: '管理身分' },
            { key: 'is_admin', type: 'checkboxes', options: [{ value: 'admin', label: '👑 管理員（完整權限：使用者 / 設定 / 發版）' }] },
            { type: 'section', label: '可用模組（非管理員才需勾選）' },
            { key: 'modules', type: 'checkboxes', groups: moduleGroups },
        ],
        onSubmit: async (vals, setError, close) => {
            if (!vals.username) { setError('請輸入帳號'); return; }
            if (!vals.password) { setError('請輸入密碼'); return; }
            if (vals.password !== vals.password2) { setError('兩次密碼不一致'); return; }
            if (vals.password.length < 3) { setError('密碼至少需要 3 個字元'); return; }
            const isAdmin = (vals.is_admin || []).includes('admin');
            const modules = isAdmin ? ALL_MODULES.slice() : (vals.modules || []);
            const access_level = isAdmin ? 3 : 1;
            try {
                const r = await fetch('/api/v1/auth/users', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: vals.username, password: vals.password, modules, access_level }),
                });
                const d = await r.json();
                if (!r.ok) { setError(d.detail || '新增失敗'); return; }
                close();
                _loadUserList();
            } catch (_) { setError('連線失敗，請稍後再試'); }
        },
    });
};

// ─── Per-user permission editing (group master + admin toggle) ─── //
window._toggleUserGroup = function(username, groupId, checked) {
    document.querySelectorAll(`input[data-umod-user="${username}"][data-group="${groupId}"]`)
        .forEach(cb => { cb.checked = checked; });
    window._syncUserGroupMaster(username, groupId);
};

window._syncUserGroupMaster = function(username, groupId) {
    const boxes = [...document.querySelectorAll(`input[data-umod-user="${username}"][data-group="${groupId}"]`)];
    const checked = boxes.filter(b => b.checked).length;
    const master = document.querySelector(`input[data-umaster-user="${username}"][data-group="${groupId}"]`);
    if (master) {
        master.checked = checked === boxes.length && boxes.length > 0;
        master.indeterminate = checked > 0 && checked < boxes.length;
    }
    const count = document.querySelector(`span[data-ucount-user="${username}"][data-group="${groupId}"]`);
    if (count) count.textContent = `${checked}/${boxes.length}`;
};

// 父子鑰匙：勾了子 → 父跟著勾；父的勾／不勾 → 子鑰匙開／灰（灰掉的仍保留勾選，只是提醒「總開關沒開」）。
window._syncPermParent = function(username, m) {
    const box = (k) => document.querySelector(`input[data-umod-user="${username}"][value="${k}"]`);
    const parent = PERM_PARENT[m];
    if (parent && box(m)?.checked) {
        const p = box(parent);
        // 母公司報表（finance_partner）絕不可與金額檢視同給（tab-config 註記／core/ledger）：這種帳號不自動勾 money_view，交給管理員決定
        if (p && !p.checked && !(parent === 'money_view' && box('finance_partner')?.checked)) { p.checked = true; }
    }
    const pKey = parent || m;
    const pBox = box(pKey);
    if (!pBox) return;
    document.querySelectorAll(`input[data-umod-user="${username}"][data-parent="${pKey}"]`)
        .forEach(cb => { cb.disabled = !pBox.checked; });
};

// 管理員 on = full access (modules implied) → dim the module grid. off = re-enable.
window._onUserAdminToggle = function(username, checked) {
    const perm = document.querySelector(`[data-uperm-user="${username}"]`);
    if (!perm) return;
    perm.style.opacity = checked ? '0.45' : '1';
    perm.style.pointerEvents = checked ? 'none' : 'auto';
};

window._saveUserSettings = async function(username) {
    const adminEl = document.querySelector(`input[data-uadmin-user="${username}"]`);
    const isAdmin = !!(adminEl && adminEl.checked);
    let modules;
    if (isAdmin) modules = ALL_MODULES.slice();
    else {
        const boxes = [...document.querySelectorAll(`input[data-umod-user="${username}"]`)];
        modules = boxes.filter(cb => cb.checked).map(cb => cb.value);
        // 格子只有捆鍵：只拿到部分成員的舊帳號（例如只有 hr_leave、只有 transcode）在畫面上沒有格子，
        // 不帶回去就等於這次儲存悄悄收掉他的分頁（「放寬守衛不能收回原本的鑰匙」）。
        const rendered = new Set(boxes.map(cb => cb.value));
        const u = _usersCache.find(x => x.username === username);
        const had = new Set(u?.modules || []);
        const uncheckedBundles = new Set(boxes.filter(cb => !cb.checked).map(cb => cb.value));
        had.forEach(k => {
            if (rendered.has(k) || ALL_MODULES.includes(k) || modules.includes(k)) return;
            const b = bundleOf(k);
            // 原本整捆都有、管理員這次把捆取消 → 成員一起收；原本就只有部分成員（捆格從來沒勾過）→ 保留
            if (b !== k && had.has(b) && uncheckedBundles.has(b)) return;
            modules.push(k);
        });
    }
    const access_level = isAdmin ? 3 : 1;
    // N0: 綁定人員檔案 — "" 表示解綁（後端哨兵語意），欄位不存在則不送（不變）
    const staffSel = document.querySelector(`select[data-ustaff-user="${username}"]`);
    try {
        const r = await fetch('/api/v1/auth/users/' + username, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ modules, access_level, ...(staffSel ? { staff_id: staffSel.value } : {}) }),
        });
        if (r.ok) {
            const btns = document.querySelectorAll('#umgmt-list button');
            btns.forEach(b => {
                if (b.textContent.includes('儲存') && b.onclick?.toString().includes(username)) {
                    b.textContent = '✅ 已儲存'; b.style.background = '#22c55e';
                    setTimeout(() => { b.textContent = '儲存'; b.style.background = ''; }, 1500);
                }
            });
        } else { alert('儲存失敗'); }
    } catch (_) { alert('連線失敗'); }
};

window._changeUserPwd = async function(username) {
    const password = prompt(`設定 ${username} 的新密碼：`);
    if (!password) return;
    const r = await fetch('/api/v1/auth/users/' + username, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
    });
    if (r.ok) alert('密碼已更新'); else alert('修改失敗');
};

window._deleteUser = async function(username) {
    if (!confirm(`確定要刪除使用者 "${username}"？`)) return;
    const r = await fetch('/api/v1/auth/users/' + username, { method: 'DELETE' });
    if (r.ok) _loadUserList(); else alert('刪除失敗');
};

// Expose _loadUserList for cross-module calls
window._loadUserList = _loadUserList;
