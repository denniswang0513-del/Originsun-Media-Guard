/**
 * tab-config.js — Tab permission constants (single source of truth)
 */

export const TAB_MAP = {
    projects: 'tab-projects', backup: 'tab_main', verify: 'tab_verify',
    transcode: 'tab_transcode', concat: 'tab_concat', report: 'tab_report',
    transcribe: 'tab_transcribe', tts: 'tab_tts', drone_meta: 'tab_drone_meta',
    crm_clients: 'tab_crm_clients', crm_projects: 'tab_crm_projects',
    crm_quotes: 'tab_crm_quotes', crm_staff: 'tab_crm_staff',
    crm_invoices: 'tab_crm_invoices',
    website_admin: 'tab_website',
};

export const NAV_MAP = {
    backup: '備份', verify: '比對', transcode: 'Proxy', concat: '串帶',
    report: '報表', transcribe: '逐字', tts: '語音', drone_meta: '空拍寫入', projects: '專案總覽',
    crm_clients: '客戶', crm_projects: '專案管理', crm_quotes: '報價',
    crm_staff: '人力', crm_invoices: '帳務',
    website_admin: '官網',
};

export const MEDIA_TABS = ['projects', 'backup', 'verify', 'transcode', 'concat', 'report', 'transcribe', 'tts', 'drone_meta'];

// Tab loader registry — paths and init function names cannot be derived from
// keys by convention (crm tabs share a folder; init names are inconsistent).
// Section IDs deliberately mirror TAB_MAP so loadTabs and _applyModuleTabs
// stay in sync without duplication.
export const TAB_LOADERS = [
    ['projects',      './tabs/projects/projects.html',       './tabs/projects/projects.js',       'initTab'],
    ['backup',        './tabs/backup/backup.html',           './tabs/backup/backup.js',           'initBackupTab'],
    ['verify',        './tabs/verify/verify.html',           './tabs/verify/verify.js',           'initVerifyTab'],
    ['transcode',     './tabs/transcode/transcode.html',     './tabs/transcode/transcode.js',     'initTranscodeTab'],
    ['concat',        './tabs/concat/concat.html',           './tabs/concat/concat.js',           'initConcatTab'],
    ['report',        './tabs/report/report.html',           './tabs/report/report.js',           'initReportTab'],
    ['transcribe',    './tabs/transcribe/transcribe.html',   './tabs/transcribe/transcribe.js',   'initTranscribeTab'],
    ['tts',           './tabs/tts/tts.html',                 './tabs/tts/tts.js',                 'initTtsTab'],
    ['drone_meta',    './tabs/drone_meta/drone_meta.html',   './tabs/drone_meta/drone_meta.js',   'initDroneMetaTab'],
    ['crm_clients',   './tabs/crm/crm.html',                 './tabs/crm/crm.js',                 'initCrmTab'],
    ['crm_projects',  './tabs/crm/crm-projects.html',        './tabs/crm/crm-projects.js',        'initCrmProjectsTab'],
    ['crm_staff',     './tabs/crm/crm-staff.html',           './tabs/crm/crm-staff.js',           'initCrmStaffTab'],
    ['crm_invoices',  './tabs/crm/crm-invoices.html',        './tabs/crm/crm-invoices.js',        'initCrmInvoicesTab'],
    ['website_admin', './tabs/website/website.html',         './tabs/website/website.js',         'initWebsiteTab'],
];

export function shouldShowTab(key, authUser, modules) {
    const loggedIn = !!authUser;
    const hasModules = loggedIn && modules && modules.length > 0;
    return hasModules ? modules.includes(key) : loggedIn ? true : MEDIA_TABS.includes(key);
}
