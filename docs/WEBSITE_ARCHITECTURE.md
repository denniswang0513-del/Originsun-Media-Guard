# Originsun 官方網站 — 架構設計書

> **專案代號**：Phase M
> **網域**：`originsun-studio.com`（沿用舊站、DNS 轉 Cloudflare）
> **時程**：2026-04-20 ~ 2026-07-01（10 週）
> **里程碑**：6/1 Staging 初版 / 7/1 正式切換上線
> **分支**：`feature/website-m`

---

## 1. 專案目的

把 `https://www.originsun-studio.com/` 改版成現代化、SEO 友善、與 CRM 系統整合的官方網站。核心需求：

1. **作品集動態化**：從 `crm_projects` 已結案 + 標記公開的專案自動產生作品頁
2. **集中管理**：所有網站內容在 Originsun Transcode 系統內一個 Tab 管理完
3. **舊站不中斷**：開發期間舊站正常運作，7/1 才切換
4. **AI 友善**：程式結構讓 AI（Claude）一次讀完單檔就理解，不需跨檔推理

---

## 2. 整體架構

```
潛在客戶瀏覽器
       │
       ▼ originsun-studio.com / preview.originsun-studio.com
┌──────────────────────────────────┐
│ Cloudflare                        │
│  • DNS + SSL 自動續               │
│  • CDN 邊緣快取（靜態資源）       │
│  • Tunnel（零 port 暴露）         │
│  • Turnstile 反機器人             │
└──────────┬───────────────────────┘
           │ cloudflared
           ▼
┌──────────────────────────────────┐
│ NAS（QNAP Container Station）     │
│                                   │
│  ┌─────────────┐  ┌────────────┐ │
│  │ cloudflared │─▶│ Nginx      │ │
│  └─────────────┘  └──────┬─────┘ │
│                          │       │
│           ┌──────────────┼─────┐ │
│           ▼              ▼     │ │
│    ┌──────────┐  ┌─────────────┐│
│    │ Astro 站 │  │ FastAPI     ││
│    │（靜態檔）│  │（port 8000）││
│    └──────────┘  └──────┬──────┘│
│                         │        │
│                 ┌───────▼─────┐ │
│                 │ PostgreSQL  │ │
│                 └─────────────┘ │
└──────────────────────────────────┘
```

### 路由分工

| 路徑 | 服務者 | 內容 |
|---|---|---|
| `/` `/about` `/services` `/contact` | Astro SSG | 靜態頁 |
| `/works` `/works/[slug]` | Astro SSG（build 時撈 CRM API） | 作品集 |
| `/news/*` | Astro SSG（build 時撈 Notion API） | 部落格 |
| `/api/website/*` | FastAPI（公開 API） | 前端 runtime 用 |
| `/api/website/admin/*` | FastAPI（需 RBAC） | 官網管理 Tab 用 |

---

## 3. 技術選型

| 層 | 選擇 | 為什麼 |
|---|---|---|
| 前端框架 | **Astro 4** | 靜態優先、Islands、SEO 最佳、內建 Image optimization |
| CSS | **Tailwind CSS** | 與 Astro 整合佳、維護成本低 |
| 後端 API | **複用現有 FastAPI** | 不另起 service、共用 DB + notifier |
| CMS（部落格） | **Notion as CMS** | 非工程師可寫文章、Astro build 時同步 |
| 影片播放 | **YouTube 嵌入（youtube-nocookie.com）** | 免費、SEO、使用者已有 @OriginsunStudio 頻道 |
| Reverse Proxy | **Nginx**（Docker） | 標準方案、之後可加 WAF |
| Tunnel | **cloudflared**（Docker） | QNAP Container Station 可跑 |
| 反機器人 | **Cloudflare Turnstile** | 免費、取代 reCAPTCHA |
| Build CI | **GitHub Actions**（之後規劃） | 內容變動自動 rebuild |

---

## 4. AI 友善程式結構原則（6 條硬規則）

為了讓未來 Claude / Copilot 等 AI 協作者讀程式時**一次到位、不幻覺**，整個 Phase M 必須遵守：

| # | 規則 | 理由 |
|---|---|---|
| 1 | **每個檔案 ≤ 400 行** | AI 單檔讀取上限 2000 行，留 buffer |
| 2 | **單一職責** | 一檔一功能，檔名即功能 |
| 3 | **按「功能」拆目錄**（非按檔案類型） | AI 可從目錄名直接定位 |
| 4 | **完整 type hints / TS strict mode** | AI 不用讀實作就知道 I/O |
| 5 | **每個目錄一個 `INDEX.md`**（≤30 行） | 目錄索引，AI 一眼知道誰做什麼 |
| 6 | **Schema-first**（Pydantic / TS interfaces 集中） | 資料結構是 single source of truth |

### 檔案頂部標準 docstring

```python
"""
routers/website/admin_works.py
---
管理端：作品集排序與置頂。

Endpoints:
- POST /api/website/admin/works/reorder      → 拖曳排序
- POST /api/website/admin/works/{id}/featured → 切換首頁精選

Depends on:
- services.website.project_service
- db.models.website_project_category
"""
```

AI 讀第一個 docstring 就知道**整個檔案做什麼、有哪些 endpoint、依賴什麼**，不用讀完整檔。

### 命名慣例

| 類型 | 規則 | 範例 |
|---|---|---|
| Python 檔名 | 動詞或名詞明確 | `admin_works.py` |
| API endpoint | URL 讀得出用途 | `/api/website/admin/works/reorder` |
| DB 欄位前綴 | `public_*` 對外、`admin_*` 後台 | `public_youtube_id` |
| 前端模組 | Tab + subview 兩層前綴 | `website-inquiries.js` |
| TS interface | `I` 開頭 | `IPublicProject` |
| Pydantic | 用途後綴 | `WorkPublicResponse` / `WorkAdminUpdate` |

---

## 5. 目錄結構全貌

```
d:\Antigravity\OriginsunTranscode\
│
├── routers/website/                      🆕 對外網站 + 管理路由（拆 7 檔）
│   ├── INDEX.md
│   ├── __init__.py                       組合 router
│   ├── public.py                         對外 API
│   ├── admin_works.py                    管理：作品排序置頂
│   ├── admin_categories.py               管理：分類 CRUD
│   ├── admin_services.py                 管理：服務項目
│   ├── admin_inquiries.py                管理：聯絡詢問
│   ├── admin_settings.py                 管理：網站設定
│   └── admin_rebuild.py                  管理：Rebuild + Notion
│
├── services/website/                     🆕 業務邏輯層
│   ├── INDEX.md
│   ├── project_service.py                作品查詢/排序邏輯
│   ├── category_service.py               分類邏輯
│   ├── inquiry_service.py                聯絡表單處理
│   ├── notify_service.py                 4 通道通知組合
│   └── rebuild_service.py                觸發 Astro build
│
├── db/models/                            🆕 DB Model 拆多檔（現有 models.py 不動）
│   ├── INDEX.md
│   ├── website_category.py
│   ├── website_setting.py
│   ├── website_service.py
│   ├── website_inquiry.py
│   └── website_project_category.py
│
├── core/schemas/                         🆕 Pydantic 拆多檔
│   ├── INDEX.md
│   └── website.py
│
├── frontend/tabs/website/                🆕 官網管理 Tab
│   ├── INDEX.md
│   ├── website.html                      主殼
│   ├── website.js                        子視圖路由
│   └── subviews/
│       ├── dashboard.html / dashboard.js
│       ├── home.html / home.js
│       ├── works.html / works.js
│       ├── categories.html / categories.js
│       ├── services.html / services.js
│       ├── about.html / about.js
│       ├── inquiries.html / inquiries.js
│       ├── blog.html / blog.js
│       └── settings.html / settings.js
│
├── website/                              🆕 Astro 對外站
│   ├── INDEX.md
│   ├── astro.config.mjs
│   ├── package.json
│   ├── tailwind.config.mjs
│   ├── src/
│   │   ├── pages/                        每頁一檔 ≤ 200 行
│   │   ├── components/
│   │   │   ├── INDEX.md
│   │   │   ├── layout/
│   │   │   ├── home/
│   │   │   ├── works/
│   │   │   ├── about/
│   │   │   └── contact/
│   │   ├── lib/                          業務邏輯 ≤ 200 行
│   │   │   ├── INDEX.md
│   │   │   ├── crm-client.ts
│   │   │   ├── notion-client.ts
│   │   │   ├── youtube.ts
│   │   │   └── i18n.ts
│   │   ├── types/                        TS interfaces 集中
│   │   │   ├── INDEX.md
│   │   │   ├── project.ts
│   │   │   ├── category.ts
│   │   │   └── api.ts
│   │   └── styles/global.css
│   └── public/
│       └── robots.txt                    開發期 Disallow: /
│
└── docker/                               🆕 Website 部署
    ├── INDEX.md
    ├── docker-compose.website.yml
    ├── cloudflared/config.yml
    └── nginx/originsun.conf
```

---

## 6. 資料庫設計

### 6.1 `crm_projects` 擴充欄位（11 個）

`ALTER TABLE ADD COLUMN IF NOT EXISTS` 在 `main.py` startup 執行：

| 欄位 | 型別 | 用途 |
|---|---|---|
| `public` | `BOOLEAN DEFAULT FALSE` | 是否對外展示 |
| `public_slug` | `VARCHAR(100) UNIQUE` | SEO URL |
| `public_title` | `VARCHAR(200)` | 對外標題 |
| `public_client` | `VARCHAR(100)` | 對外客戶名（可不同於內部） |
| `public_youtube_id` | `VARCHAR(20)` | YouTube video ID |
| `public_description` | `TEXT` | 對外文案 |
| `public_credits` | `JSONB` | 職員表 |
| `public_year` | `INT` | 年份 |
| `public_featured` | `BOOLEAN DEFAULT FALSE` | 首頁精選 |
| `public_sort_order` | `INT DEFAULT 0` | 排序 |
| `public_published_at` | `TIMESTAMP` | 上架時間 |

### 6.2 新增 5 張表

```sql
-- 作品分類主檔（可 CRUD）
CREATE TABLE website_categories (
  id SERIAL PRIMARY KEY,
  slug VARCHAR(50) UNIQUE,
  name_zh VARCHAR(100),
  name_en VARCHAR(100),
  description TEXT,
  cover_image TEXT,
  sort_order INT DEFAULT 0,
  visible BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT NOW()
);

-- 作品分類多對多關聯
CREATE TABLE website_project_categories (
  project_id INT REFERENCES crm_projects(id) ON DELETE CASCADE,
  category_id INT REFERENCES website_categories(id) ON DELETE CASCADE,
  PRIMARY KEY (project_id, category_id)
);

-- 網站全站設定 key-value
CREATE TABLE website_settings (
  key VARCHAR(100) PRIMARY KEY,
  value JSONB,
  updated_at TIMESTAMP,
  updated_by VARCHAR(100)
);

-- 服務項目
CREATE TABLE website_services (
  id SERIAL PRIMARY KEY,
  title VARCHAR(100),
  slug VARCHAR(100) UNIQUE,
  icon VARCHAR(50),
  short_desc VARCHAR(300),
  full_desc TEXT,
  cover_image TEXT,
  related_category_id INT REFERENCES website_categories(id),
  sort_order INT DEFAULT 0,
  visible BOOLEAN DEFAULT TRUE
);

-- 聯絡表單收件箱
CREATE TABLE website_contact_inquiries (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100),
  email VARCHAR(200),
  phone VARCHAR(50),
  company VARCHAR(200),
  service_type VARCHAR(50),
  budget_range VARCHAR(50),
  message TEXT,
  source VARCHAR(50),
  status VARCHAR(20) DEFAULT 'new',
  converted_client_id INT,
  ip_address VARCHAR(50),
  user_agent TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  handled_at TIMESTAMP,
  handled_by VARCHAR(100),
  notes TEXT
);
```

### 6.3 `crm_staff` 擴充

`show_on_website BOOLEAN DEFAULT FALSE`（對外團隊頁是否顯示該成員）。

---

## 7. API 設計

### 7.1 對外公開 API（`routers/website/public.py`）

| 方法 | 端點 | 說明 |
|---|---|---|
| GET | `/api/website/works` | 列出公開作品（分頁 + 分類篩選） |
| GET | `/api/website/works/{slug}` | 單一作品詳情 |
| GET | `/api/website/featured` | 首頁精選（最新 6 + 手動置頂） |
| GET | `/api/website/categories` | 分類清單（含計數） |
| GET | `/api/website/services` | 服務項目 |
| GET | `/api/website/team` | 對外展示團隊成員 |
| GET | `/api/website/meta` | 全站 SEO metadata |
| POST | `/api/website/contact` | 聯絡表單（Turnstile 驗證） |

**安全層**：
- Rate limit：每 IP 每分鐘 30 次
- CORS：只允許 `originsun-studio.com` + `preview.originsun-studio.com`
- `public=false` 的專案絕不露出（DB 層過濾）

### 7.2 管理 API（`routers/website/admin_*.py`）

| 模組 | 端點數 |
|---|---|
| admin_works.py | 4（reorder、featured on/off） |
| admin_categories.py | 5（CRUD + reorder） |
| admin_services.py | 5（CRUD + reorder） |
| admin_inquiries.py | 4（list、update、convert、delete） |
| admin_settings.py | 2（get、update） |
| admin_rebuild.py | 3（rebuild、notion-status、notion-sync） |

**全部**需 `website_admin` 模組權限（RBAC guard）。

---

## 8. 官網管理 Tab（9 子視圖）

```
🌐 官網管理
├── 📊 儀表板        本月瀏覽/詢問/熱門作品/同步狀態
├── 🏠 首頁設定       Hero YouTube ID / 標語 / CTA / 精選置頂
├── 🎬 作品集管理     公開切換 + 排序 + 置頂（列表視圖）
├── 🏷️ 作品分類       CRUD（名稱 zh/en、slug、排序、可見）
├── 🧩 服務項目       CRUD + 關聯分類
├── 👥 關於我們       公司文案 + 團隊成員開關 + 地址
├── 📬 聯絡詢問       收件箱、狀態、轉 CRM client
├── 📝 部落格同步     Notion 連線狀態 + 立即同步
└── ⚙️ 網站設定       SEO、GA4、Turnstile key、通知管道
```

**RBAC 新模組**：`website_admin`（admin + producer 預設擁有）。

---

## 9. 發布策略（舊站不中斷）

### 9.1 DNS 轉移流程

```
Step 1  遠振 DNS 記錄稽核（使用者執行）
Step 2  Cloudflare 帳號建立 + add site（CF 自動掃 DNS）
Step 3  手動比對 CF 自動掃到的記錄 vs 遠振稽核表 → 補齊差異
Step 4  所有記錄設為「DNS only」（灰雲，不走 CF proxy）
Step 5  遠振後台改 NS 指向 Cloudflare
Step 6  等 24-48 小時 propagation，驗證舊站運作正常
Step 7  新增 preview.originsun-studio.com A record → Tunnel
        並設為 proxied（橘雲）
```

### 9.2 Staging 期間（開發到 6/1）

- `preview.originsun-studio.com` → 新站（Tunnel）
- `www.originsun-studio.com` + root → 舊站（原 IP 不動）
- 新站 `robots.txt` 全 Disallow、HTML meta noindex

### 9.3 正式切換（7/1）

- 6/30：DNS TTL 降到 300 秒
- 7/1：改 root + `www.` A record 從舊 IP 改指向 Tunnel
- 7/1：拔 `robots.txt` Disallow + meta noindex
- 7/1：送 Google Search Console 新 sitemap
- 7/1 ~ 7/7：舊站內容保留為 fallback（Cloudflare Workers redirect 或另存 git branch）

---

## 10. 安全考量

| 面向 | 措施 |
|---|---|
| 聯絡表單濫用 | Cloudflare Turnstile + rate limit（每 IP 每分鐘 3 次） |
| API 濫用 | Rate limit 30/分鐘、CORS 白名單 |
| 未公開資料外洩 | DB 層查詢強制 `public=true` |
| Admin API | RBAC + JWT（複用既有認證） |
| XSS | Astro 自動 escape、Pydantic 驗證所有輸入 |
| DB injection | SQLAlchemy ORM（無 raw SQL） |
| CF Tunnel | 零 port 暴露、NAS 不開 80/443 |

---

## 11. 時程與里程碑

| 週 | 日期 | 階段 | 里程碑 |
|---|---|---|---|
| 1 | 04/20-04/26 | A 基礎設施 | Tunnel 通、Astro Hello World |
| 2 | 04/27-05/03 | B 資料層 | DB + Seed |
| 3 | 05/04-05/10 | C 後端 API + D Tab 啟動 | 管理 API 可操作 |
| 4 | 05/11-05/17 | D Tab 完成 + E-1 前端骨架 | Tab 可用 |
| 5 | 05/18-05/24 | E-2/3 首頁 + 作品列表 | 主視覺完成 |
| 6 | 05/25-05/31 | E-4/5/6/7 內頁 + 聯絡 | 🚀 **6/1 Staging 初版** |
| 7 | 06/01-06/07 | E-8 部落格 | Notion 同步 |
| 8 | 06/08-06/14 | F-1 SEO + 分析 | GSC、sitemap、schema |
| 9 | 06/15-06/21 | F-1 效能 | Lighthouse ≥ 95 |
| 10 | 06/22-06/30 | F-2 切換上線 | 🎯 **7/1 正式上線** |

---

## 12. 進度追蹤

- **TodoWrite 追蹤**：本專案所有任務透過 Claude Code TodoWrite 即時管理
- **版本標記**：每完成一階段 bump 版本號（遵循 `/publish` skill 流程）
- **分支策略**：所有開發在 `feature/website-m`，上線前 merge 到 master
- **Session 記錄**：每次對話後更新 `memory/project_session_YYYYMMDD.md`

---

## 13. 參考連結

- 舊站：`https://www.originsun-studio.com/`
- YouTube：`https://www.youtube.com/@OriginsunStudio`
- IG：`https://instagram.com/originsun_studio`
- 此文件：`docs/WEBSITE_ARCHITECTURE.md`
- ROADMAP：參見 `ROADMAP.md` Phase M
