# frontend/tabs/website/

Phase M「🌐 官網管理」Tab 前端。**跑在 Windows 既有 FastAPI 上**，但 fetch 跨機呼叫 NAS website-api（`http://192.168.1.132:8001/api/website/admin/*`）。

**AI 友善規則**：每檔 ≤ 400 行、每個子視圖獨立一檔。

## 主殼
| 檔案 | 職責 |
|---|---|
| `website.html` | Tab 主殼（子視圖切換導覽 + 容器） |
| `website.js` | 子視圖路由 + 共用 API client（指向 NAS）+ 統一錯誤處理 |

## 9 子視圖（`subviews/`）
| 檔案 | 內容 |
|---|---|
| `dashboard.html` / `dashboard.js` | 儀表板：本月瀏覽/詢問/熱門作品/同步狀態 |
| `home.html` / `home.js` | 首頁設定：Hero YouTube ID / 標語 / CTA / 精選置頂 |
| `works.html` / `works.js` | 作品集管理：公開切換 + 排序 + 置頂 |
| `categories.html` / `categories.js` | 作品分類 CRUD（名稱 zh/en、slug、排序） |
| `services.html` / `services.js` | 服務項目 CRUD + 關聯分類 |
| `about.html` / `about.js` | 公司文案 + 團隊成員開關 + 地址 |
| `inquiries.html` / `inquiries.js` | 聯絡詢問收件箱 + 轉 CRM client |
| `blog.html` / `blog.js` | Notion 連線狀態 + 立即同步 |
| `settings.html` / `settings.js` | SEO、GA4、Turnstile key、通知管道 |

**RBAC**：模組 key 為 `website_admin`（admin + producer 預設擁有，4 處 RBAC 更新見 CLAUDE.md checklist）。

**跨機 fetch**：`website.js` 統一封裝 `fetchNAS()`，自動加 JWT header + CORS 處理。
