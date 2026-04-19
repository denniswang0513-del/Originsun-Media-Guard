# frontend/tabs/website/

Phase M「🌐 官網管理」Tab 前端。**跑在 Windows 既有 FastAPI 上**，但 fetch 跨機呼叫 NAS website-api（`http://192.168.1.132:8001/api/website/admin/*`）。

**AI 友善規則**：每檔 ≤ 400 行、每個子視圖獨立一檔。

## 主殼
| 檔案 | 行數 | 職責 |
|---|---|---|
| `website.html` | 87 | Tab 主殼 + 左側 9 子視圖導覽 + CSS |
| `website.js` | 114 | 入口 initWebsiteTab、子視圖 lazy import、API health 輪詢、inquiry badge |
| `website-utils.js` | 122 | 跨機 websiteFetch、esc/fmtNum（reuse CRM utils）、toast、fmtDt |

## 9 子視圖（`subviews/*.js`，無單獨 .html — HTML 在 render() 動態產生）
| 檔案 | 行數 | 內容 |
|---|---|---|
| `dashboard.js` | 78 | 4 張 stat card + 最新詢問 + 熱門分類 |
| `home.js` | 72 | Hero YouTube 設定 + 預覽 + 精選作品引導 |
| `works.js` | 146 | 列表 + 分類/公開篩選 + toggle 公開/精選 |
| `categories.js` | 109 | 分類 CRUD（inline edit + 新增列） |
| `services.js` | 128 | 服務 CRUD + 關聯分類下拉 |
| `about.js` | 75 | 公司文案 key-value + 團隊成員預覽 |
| `inquiries.js` | 178 | 收件箱列表 + 詳情面板 + 狀態更新 + 轉 CRM client + 刪除 |
| `blog.js` | 94 | Notion 連線狀態 + 觸發 rebuild/sync + log tail |
| `settings.js` | 109 | 分群 key-value 編輯（6 群 + 其他） |

**RBAC**：模組 key 為 `website_admin`（admin + producer 預設擁有，4 處 RBAC 更新見 CLAUDE.md checklist）。

**跨機 fetch**：`website.js` 統一封裝 `fetchNAS()`，自動加 JWT header + CORS 處理。
