/**
 * blog.js — 部落格管理（DB-as-truth + Notion 是匯入器）
 *
 * 4 個 sub-tab：
 *   📰 文章        — list + filters + metadata Modal CRUD
 *   📚 分類        — CRUD 多對多分類（inline table）
 *   📥 從 Notion   — 匯入器（Phase A 後 Notion 只是 seed，不再是 truth）
 *   🌐 SEO 移轉    — 軟+硬 301 計數 + 強制同步 + 跨域遷移指引
 *
 * Modal 含完整 block 編輯器（6 種 block：paragraph / heading / image / video /
 * quote / list）+ 圖片上傳 + YouTube 解析 + 即時預覽 + SEO health widget。
 */
// 2026-07 拆分：本檔僅為入口（保留原路徑 + default export 合約），實作搬至 ./blog/
// 子模組。editor.js 為副作用 import（註冊文章編輯 Modal / block 編輯器的
// window._blog.* handlers）；posts-list / categories / notion-seo 由 shell.js 引入。
export { default } from './blog/shell.js';
import './blog/editor.js';
