# website/

Phase M 對外官方網站 (Astro 4 + Tailwind CSS)。

**開發**：Windows 本機 `npm run dev` → `localhost:4321`
**部署**：NAS build → `Website_Nginx` serve 靜態檔（M-F 階段）

## 技術棧 / 指令
- **Astro 4** + **Tailwind CSS 4** + **TypeScript strict** + **Node.js 24 LTS**
- `npm run dev` (localhost:4321) / `npm run build` (dist/) / `npm run preview`

## 目錄結構
| 目錄 | 職責 |
|---|---|
| `src/pages/` | Astro 路由頁面（每檔對應一個 URL） |
| `src/layouts/` | BaseLayout + SEO meta |
| `src/components/` | 依功能分子目錄（layout/home/works/about/contact） |
| `src/lib/` | 業務邏輯（crm-client、notion-client、youtube、i18n） |
| `src/types/` | TypeScript interfaces 集中 |
| `src/styles/` | 全域 CSS（global.css 含 Tailwind directives） |
| `public/` | 靜態資源（favicon、robots.txt、sitemap） |

**AI 友善規則**：每檔 ≤ 200 行、每個 component 獨立檔、完整 TS 型別。
