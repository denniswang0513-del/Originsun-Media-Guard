/**
 * tab-config.js — Tab permission constants (single source of truth)
 */

export const TAB_MAP = {
    projects: 'tab-projects', backup: 'tab_main', verify: 'tab_verify',
    transcode: 'tab_transcode', concat: 'tab_concat', report: 'tab_report',
    transcribe: 'tab_transcribe', tts: 'tab_tts',
    crm_clients: 'tab_crm_clients', crm_projects: 'tab_crm_projects',
    crm_quotes: 'tab_crm_quotes', crm_staff: 'tab_crm_staff',
    crm_invoices: 'tab_crm_invoices',
};

export const NAV_MAP = {
    backup: '備份', verify: '比對', transcode: 'Proxy', concat: '串帶',
    report: '報表', transcribe: '逐字', tts: '語音', projects: '專案總覽',
    crm_clients: '客戶', crm_projects: '專案管理', crm_quotes: '報價',
    crm_staff: '人力', crm_invoices: '帳務',
};

export const MEDIA_TABS = ['projects', 'backup', 'verify', 'transcode', 'concat', 'report', 'transcribe', 'tts'];

export function shouldShowTab(key, authUser, modules) {
    const loggedIn = !!authUser;
    const hasModules = loggedIn && modules && modules.length > 0;
    return hasModules ? modules.includes(key) : loggedIn ? true : MEDIA_TABS.includes(key);
}
