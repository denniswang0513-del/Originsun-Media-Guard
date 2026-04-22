/**
 * crm-projects-state.js — 共享狀態 + 回呼登記
 * 所有 crm-projects-* 模組 import 此檔案存取共享狀態
 */

// ── Shared State ────────────────────────────────────────────
export const state = {
    projects: [],
    clients: [],
    users: [],
    selectedId: null,
    editingId: null,
    staffList: [],
    filters: { q: '', status: '', client_id: '', am: '' },
    // 多子表狀態
    costGroups: [],           // [{id, name, shoot_date, budget_amount, misc_budget_amount, summary, ...}]
    selectedGroupId: null,    // 當前選中的子表 id
};

// ── Constants ──────────────────────────────────────────────
export const EXPENSE_CATEGORIES = ['交通','住宿','飲食','提案','器材','其他'];

// ── Callbacks (解耦跨模組依賴) ──────────────────────────────
// 下游模組完成操作後呼叫對應 callback，上游模組在 init 時註冊實際函式
export const callbacks = {
    renderDetail: null,      // (project) => void — 重新渲染詳情面板
    renderList: null,        // () => void — 重新渲染列表
    loadProjects: null,      // () => Promise — 重新載入專案資料
    loadFinancialSummary: null, // (projectId) => Promise — 刷新財務摘要
    loadCostStaff: null,     // (projectId) => Promise — 刷新執行人員
    loadAdvances: null,      // (projectId) => Promise — 刷新預支款
    closeDetail: null,       // () => void — 關閉詳情面板
    loadQuotations: null,    // (projectId) => Promise — 刷新報價單
    loadCostGroups: null,    // (projectId) => Promise — 刷新子表列表
    renderGroupSwitcher: null, // () => void — 重新渲染子表切換器（DOM 已備好時）
    renderGroupDashboard: null, // () => void — 重新渲染當前子表儀表板
};
