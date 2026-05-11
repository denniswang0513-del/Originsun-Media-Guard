/**
 * crm-projects-state.js — 共享狀態 + 回呼登記
 * 所有 crm-projects-* 模組 import 此檔案存取共享狀態
 */

// 工作流順序 — 點欄頭以「狀態」排序時走這個 index,而非字串比較。
// 未列出的狀態(自訂)排在最後。新增狀態時請同步更新 crm-projects.html 的下拉選單。
export const STATUS_ORDER = ['洽談中', '報價中', '進行中', '已結案', '已取消'];

// ── Shared State ────────────────────────────────────────────
export const state = {
    projects: [],
    projectsLoaded: false,    // false until the first /projects fetch resolves
                              // (success or error). Lets renderList distinguish
                              // "still loading" from "really empty" — without
                              // this users see "找不到專案" during the boot
                              // window and assume their data is gone.
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
// Keep in sync with _EXPENSE_CATEGORY_DEFAULTS in routers/api_crm.py.
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
};
