/**
 * tab-config.js — Tab permission constants (single source of truth)
 */

export const TAB_MAP = {
    bulletin: 'tab_bulletin',
    projects: 'tab-projects',
    preprod_plan: 'tab_preprod_plan',
    preprod_locations: 'tab_preprod_locations',
    preprod_proposals: 'tab_preprod_proposals',
    intel: 'tab_intel',
    equipment: 'tab_equipment',
    backup: 'tab_main', verify: 'tab_verify',
    transcode: 'tab_transcode', concat: 'tab_concat', report: 'tab_report',
    transcribe: 'tab_transcribe', tts: 'tab_tts', footage: 'tab_footage', drone_meta: 'tab_drone_meta',
    crm_clients: 'tab_crm_clients', crm_projects: 'tab_crm_projects',
    crm_quotes: 'tab_crm_quotes', crm_staff: 'tab_crm_staff',
    crm_invoices: 'tab_crm_invoices', timesheets: 'tab_timesheets',
    portal: 'tab_portal',
    hr_leave: 'tab_hr_leave',
    journal: 'tab_journal',
    website_admin: 'tab_website',
};

export const MEDIA_TABS = ['projects', 'backup', 'verify', 'transcode', 'concat', 'report', 'transcribe', 'tts', 'drone_meta'];

// Tab loader registry — paths and init function names cannot be derived from
// keys by convention (crm tabs share a folder; init names are inconsistent).
// Section IDs deliberately mirror TAB_MAP so loadTabs and _applyModuleTabs
// stay in sync without duplication.
export const TAB_LOADERS = [
    ['bulletin',      './tabs/bulletin/bulletin.html',       './tabs/bulletin/bulletin.js',       'initBulletinTab'],
    ['projects',      './tabs/projects/projects.html',       './tabs/projects/projects.js',       'initTab'],
    ['preprod_plan',  './tabs/preprod/preprod.html',         './tabs/preprod/preprod.js',         'initPreprodTab'],
    ['preprod_locations', './tabs/locations/locations.html', './tabs/locations/locations.js',     'initLocationsTab'],
    ['preprod_proposals', './tabs/proposals/proposals.html', './tabs/proposals/proposals.js',     'initProposalsTab'],
    ['intel',         './tabs/intel/intel.html',             './tabs/intel/intel.js',             'initIntelTab'],
    ['equipment',     './tabs/equipment/equipment.html',     './tabs/equipment/equipment.js',     'initEquipmentTab'],
    ['backup',        './tabs/backup/backup.html',           './tabs/backup/backup.js',           'initBackupTab'],
    ['verify',        './tabs/verify/verify.html',           './tabs/verify/verify.js',           'initVerifyTab'],
    ['transcode',     './tabs/transcode/transcode.html',     './tabs/transcode/transcode.js',     'initTranscodeTab'],
    ['concat',        './tabs/concat/concat.html',           './tabs/concat/concat.js',           'initConcatTab'],
    ['report',        './tabs/report/report.html',           './tabs/report/report.js',           'initReportTab'],
    ['transcribe',    './tabs/transcribe/transcribe.html',   './tabs/transcribe/transcribe.js',   'initTranscribeTab'],
    ['tts',           './tabs/tts/tts.html',                 './tabs/tts/tts.js',                 'initTtsTab'],
    ['footage',       './tabs/footage/footage.html',         './tabs/footage/footage.js',         'initFootageTab'],
    ['drone_meta',    './tabs/drone_meta/drone_meta.html',   './tabs/drone_meta/drone_meta.js',   'initDroneMetaTab'],
    ['crm_clients',   './tabs/crm/crm.html',                 './tabs/crm/crm.js',                 'initCrmTab'],
    ['crm_projects',  './tabs/crm/crm-projects.html',        './tabs/crm/crm-projects.js',        'initCrmProjectsTab'],
    ['crm_quotes',    './tabs/crm/crm-quotes.html',          './tabs/crm/crm-quotes.js',          'initCrmQuotesTab'],
    ['crm_staff',     './tabs/crm/crm-staff.html',           './tabs/crm/crm-staff.js',           'initCrmStaffTab'],
    // 財務管理殼（階段一）：內嵌既有 crm-invoices 六視圖 + 左側導覽代理其 view bar
    ['crm_invoices',  './tabs/finance/finance.html',         './tabs/finance/finance.js',         'initFinanceTab'],
    ['timesheets',    './tabs/timesheets/timesheets.html',   './tabs/timesheets/timesheets.js',   'initTimesheetsTab'],
    ['hr_leave',      './tabs/hr_leave/hr_leave.html',       './tabs/hr_leave/hr_leave.js',       'initHrLeaveTab'],
    ['journal',       './tabs/journal/journal.html',         './tabs/journal/journal.js',         'initJournalTab'],
    ['portal',        './tabs/portal/portal.html',           './tabs/portal/portal.js',           'initPortalTab'],
    ['website_admin', './tabs/website/website.html',         './tabs/website/website.js',         'initWebsiteTab'],
];

export function shouldShowTab(key, authUser, modules) {
    const loggedIn = !!authUser;
    const hasModules = loggedIn && modules && modules.length > 0;
    return hasModules ? modules.includes(key) : loggedIn ? true : MEDIA_TABS.includes(key);
}

// ── Top-level grouping (官網-style left-sidebar groups) ──────────────────
// Single source of truth for the grouped navigation. A group is either a
// standalone tab (`single`) or a left-sidebar group (`items` = ordered keys).
// `key` values are TAB_MAP keys; sidebar labels live here so nav + RBAC +
// switchTab all derive from one place.
export const TAB_GROUPS = [
    { id: 'bulletin',   label: '📌 公布欄', single: 'bulletin' },
    { id: 'projects',   label: '📊 專案總覽', single: 'projects' },
    { id: 'preprod',    label: '📝 前期製作', items: [
        { key: 'preprod_plan', label: '📋 拍攝企劃' },
        { key: 'preprod_locations', label: '🗺️ 場景庫' },
        { key: 'preprod_proposals', label: '📑 提案庫' },
        { key: 'intel', label: '📡 產業情報' },
        { key: 'equipment', label: '🎥 器材庫' },
    ] },
    { id: 'production', label: '🎬 後期製作', items: [
        { key: 'backup',     label: '📦 備份並轉檔' },
        { key: 'verify',     label: '✔️ 檔案比對' },
        { key: 'transcode',  label: '✂️ 轉 Proxy' },
        { key: 'concat',     label: '🎞️ 製作串帶' },
        { key: 'drone_meta', label: '🛸 空拍寫入' },
        { key: 'report',     label: '📊 檔案視覺報表' },
        { key: 'transcribe', label: '🎙️ AI 逐字稿' },
        { key: 'tts',        label: '🔊 語音生成' },
        { key: 'footage',    label: '🎞️ 素材庫' },
    ] },
    { id: 'business',   label: '💼 業務管理', items: [
        { key: 'crm_clients',  label: '🤝 客戶管理' },
        { key: 'crm_projects', label: '📁 專案管理' },
        { key: 'crm_quotes',   label: '💰 報價管理' },
        { key: 'portal',       label: '🎬 審批門戶' },
    ] },
    // 人事管理（2026-07 N-hr）：員工檔案/工時自業務管理搬入 + 請補修（原名出缺勤）。
    // 設計鐵則（owner 2026-07-17）：emoji 只在頂層 tab 標籤，items 一律純文字。
    { id: 'hr',         label: '👔 人事管理', items: [
        { key: 'crm_staff',    label: '員工檔案' },
        { key: 'timesheets',   label: '專案工時' },
        { key: 'hr_leave',     label: '請補修' },
        { key: 'journal',      label: '工作日誌' },
    ] },
    // 財務管理（2026-07 起）：帳務六視圖自業務管理搬入；沿用 crm_invoices 單一
    // module key（零 RBAC 遷移，既有授權者自動看得到）。內部子視圖自管左側欄。
    { id: 'finance',    label: '💰 財務管理', single: 'crm_invoices' },
    { id: 'website',    label: '🌐 官網管理', single: 'website_admin' },
];

// Keys belonging to a group — filtered to keys that actually have a section in
// TAB_MAP so any orphan RBAC key (a module with no loader/section) can't
// create a phantom sidebar member.
export function groupKeys(group) {
    const keys = group.single ? [group.single] : group.items.map((i) => i.key);
    return keys.filter((k) => TAB_MAP[k]);
}

// Reverse lookup: a section id (e.g. 'tab_main') → its TAB_GROUPS entry.
export function groupForSection(sectionId) {
    return TAB_GROUPS.find((g) => groupKeys(g).some((k) => TAB_MAP[k] === sectionId)) || null;
}

// Section ids whose group shows the shared 執行控制與日誌 panel (media tasks).
// Derived from MEDIA_TABS so the hardcoded list in switchTab can be retired.
export function isMediaSection(sectionId) {
    return MEDIA_TABS.some((k) => TAB_MAP[k] === sectionId);
}

// ── Permission grouping (RBAC editor / display) ─────────────────────────
// Single source of truth that maps EVERY assignable RBAC module to one of the
// 4 top-level groups, mirroring the grouped navigation. Unlike TAB_GROUPS
// (nav-only — `groupKeys` filters out keys without a section), this covers ALL
// modules so the permission editor never silently hides one — including any
// module that lacks its own top-level tab.
export const PERMISSION_GROUPS = [
    { id: 'bulletin',   label: '📌 公布欄', modules: ['bulletin'] },
    { id: 'projects',   label: '📊 專案總覽', modules: ['projects'] },
    { id: 'preprod',    label: '📝 前期製作', modules: ['preprod_plan', 'preprod_locations', 'preprod_proposals', 'intel', 'equipment'] },
    { id: 'production', label: '🎬 後期製作', modules: ['backup', 'verify', 'transcode', 'concat', 'drone_meta', 'report', 'transcribe', 'tts', 'footage'] },
    { id: 'business',   label: '💼 業務管理', modules: ['crm_clients', 'crm_projects', 'crm_quotes', 'portal'] },
    { id: 'hr',         label: '人事管理', modules: ['crm_staff', 'timesheets', 'hr_leave', 'journal'] },
    { id: 'finance',    label: '💰 財務管理', modules: ['crm_invoices'] },
    { id: 'website',    label: '🌐 官網管理', modules: ['website_admin'] },
    // N0 個人工作台 — 獨立頁 /my.html 的卡片（無 SPA tab，僅權限編輯器用；
    // groupKeys 會因 TAB_MAP 無此 key 而自動不進側欄）。
    { id: 'me',         label: '🙋 個人工作台', modules: ['me_projects', 'me_profile', 'me_todos', 'me_finance', 'me_leave'] },
];

// Flat list of every assignable RBAC module key — derived from PERMISSION_GROUPS
// so there is one source of truth (order follows group order). Consumed by the
// user-management permission editor.
export const ALL_MODULES = PERMISSION_GROUPS.flatMap((g) => g.modules);

// Group a module list into PERMISSION_GROUPS order, keeping only modules present
// in the input. Any module not listed in any group (future-proofing if the
// module set grows but PERMISSION_GROUPS isn't updated) falls into a trailing
// '其他' group so it can never be silently dropped. Empty groups are omitted.
export function groupModules(modules) {
    const want = new Set(modules || []);
    const seen = new Set();
    const out = PERMISSION_GROUPS.map((g) => {
        const mods = g.modules.filter((m) => want.has(m));
        mods.forEach((m) => seen.add(m));
        return { id: g.id, label: g.label, modules: mods };
    });
    const leftover = [...want].filter((m) => !seen.has(m));
    if (leftover.length) out.push({ id: 'other', label: '🔧 其他', modules: leftover });
    return out.filter((g) => g.modules.length);
}
