# 新增對外網站頁面 SEO Checklist

每加一個 `.astro` 頁面到 [`website/src/pages/`](../website/src/pages/)，依序過 10 關。
SEO 鐵閘會擋掉常見漏項，但有些細節仍要人工確認。

## 1. URL 永久承諾

- slug 一旦 publish **就不能改**（會破壞 Google 反向連結 + AI 引用）
- `/works/[slug]` 已用 `public_number` 自動編號避免改名

## 2. BaseLayout SEO props 必填（TS 強制）

[`BaseLayout`](../website/src/layouts/BaseLayout.astro) 的 props：
- `description`：30-200 字，每頁獨立寫
- `schemaData`：至少 1 個 schema

缺任一 → TypeScript 編譯失敗。

## 3. 挑對的 schema.org 類型

| 頁面類型 | Schema 工廠 |
|---------|-----------|
| 通用頁面 | `pageSchemas.webPage` |
| 服務項目 | `pageSchemas.service` per item |
| 作品詳情 | `pageSchemas.videoObject` |
| 部落格詳情 | `pageSchemas.newsArticle` |
| 任何詳情頁 | + `pageSchemas.breadcrumb` |
| 首頁 | `organization + localBusiness + website` 三件套 |
| FAQ 頁 | `pageSchemas.faqPage` |
| 證言頁 | `pageSchemas.testimonialBundle({includeReviews: 5})` |

`testimonialBundle` 內部會吐 `review` × N + `aggregateRating`，這兩個通常不直接呼叫；
`aggregateRating` 也可手動嵌入 Organization/LocalBusiness 物件內當 `aggregateRating` 欄位。

全套 12 個 factory 定義在 [`website/src/lib/seo.ts`](../website/src/lib/seo.ts)。

## 4. Breadcrumb 用 helper

```ts
import { breadcrumb2, breadcrumb3 } from "../lib/seo";

breadcrumb2("作品集", "/works")
// → 首頁 → 作品集

breadcrumb3("作品集", "/works", work.title, `/works/${work.slug}`)
// → 首頁 → 作品集 → 影片名
```

## 5. Sitemap

`.astro` 頁面自動進 sitemap（`@astrojs/sitemap` 掃 `src/pages/`）。
**不該進 sitemap** 的端點：`.txt / .json / .xml / .md` —
已在 [`astro.config.mjs`](../website/astro.config.mjs) filter 排除。

## 6. AI SEO 鏡像

需要被 ChatGPT/Claude/Perplexity 引用的內容 → 加 markdown 鏡像端點：
- 範例：[`services.md.ts`](../website/src/pages/services.md.ts) /
  [`about.md.ts`](../website/src/pages/about.md.ts) /
  [`works/[slug].md.ts`](../website/src/pages/works/[slug].md.ts)
- 用 [`textResponse / companyInfoMd`](../website/src/lib/seo.ts) helpers

## 7. 內部連結（內鏈密度）

- 從首頁或 nav link 到新頁，否則 SEO 為零
- 主要頁面：`Header.astro` nav 加 entry
- 詳情頁：相關卡片區塊互相 link

## 8. 圖片 alt + width/height

- 所有 `<img>` 必須有 `alt=` 屬性（裝飾圖用 `alt=""`）
- 主視覺圖片補 `width` `height` 防 CLS：
  ```astro
  <img src={thumb} alt={title} width="1280" height="720" loading="lazy" />
  ```
- 容器 aspect-video → 1280×720（YouTube hq/maxres 比例）

## 9. 多語

- 內文用 `<Tr zh="..." en="..." />` 元件 或 `data-lang-zh / data-lang-en` 屬性
- Stage 4 i18n 路由切割（`/zh/...` `/en/...` + hreflang）尚未啟用

## 10. URL 改動 = 加 301

任何 URL 變動（slug / 路徑）：在 NAS nginx config 加 301 redirect。
`docker/nginx/originsun.conf` 已預留位置。

---

## 後端新欄位 8 步流程

如果新功能含 admin 可編輯的 SEO 內容：

1. **DB model** → [`db/models_website/`](../db/models_website/)（id + sort_order + visible + timestamps 慣例）
2. **Migration** → [`db/migrations_website.py`](../db/migrations_website.py) 加 `CREATE TABLE IF NOT EXISTS` + 索引（visible+sort_order）
3. **Pydantic schema** → [`core/schemas_website.py`](../core/schemas_website.py)（Create/Update/Response 三件套）
4. **Service** → [`services/website/seo_service.py`](../services/website/seo_service.py) 用 `_create/_update/_delete/_list` 泛型
5. **Router** → [`routers/website/admin_seo.py`](../routers/website/admin_seo.py) 用 `_register_crud(...)` 工廠
6. **Public endpoint** → [`routers/website/public.py`](../routers/website/public.py)（visible_only=True）
7. **前端 admin card** → [`frontend/tabs/website/subviews/seo.js`](../frontend/tabs/website/subviews/seo.js) 加 card
8. **Astro page** → fetch + 套 `pageSchemas` 工廠

任一寫入觸發 `rebuild_service.mark_dirty()` → 60s debounce → rebuild 對外網站。

---

## SEO 鐵閘執行檢查

build 時 [`integrations/seo-audit.mjs`](../website/integrations/seo-audit.mjs) 自動掃 `dist/*.html`：
- `<title>` 存在
- `<meta description>` 30-200 字
- `<link canonical>`
- 至少 1 個 JSON-LD schema
- `<h1>` 存在
- 所有 `<img>` 有 alt

任一缺 → build fail。本機驗：
```bash
cd website && npm run build
```

---

## 對外可見性檢查清單（上線前）

| 項目 | 位置 | 狀態 |
|------|------|------|
| `seo.indexable` 設 true | admin Tab → SEO 子視圖 → Card 1 | 預設 false（staging 期） |
| `robots.txt` 移除 `Disallow: /` | 自動由 indexable 控制 | 動態 |
| `meta name=robots` 移除 noindex | BaseLayout 自動由 meta.indexable 控制 | 動態 |
| `astro.config.mjs site` 切正式網域 | `https://originsun-studio.com` | DNS 切換時手動改 |
| Google Search Console 驗證 | 加 `<meta name="google-site-verification">` | 上線當天設定 |
| Bing Webmaster Tools 驗證 | 同上 | 上線當天 |
| `seo.ai_allow` 設 true | admin Tab → Card 1 | 視 AI SEO 策略決定 |
| `seo.llms_txt_body` 設值 | admin Tab → Card 6 | 自動 fallback OK，但建議手動寫 |

---

## 監測（上線後）

| 工具 | 用途 |
|------|------|
| [Google Rich Results Test](https://search.google.com/test/rich-results) | 驗證 JSON-LD schema 被 Google 識別 |
| [PageSpeed Insights](https://pagespeed.web.dev/) | Core Web Vitals + Lighthouse 分數 |
| [Schema.org Validator](https://validator.schema.org/) | 驗證 schema 結構 |
| Google Search Console | 索引狀態 + 點擊率 + 關鍵字 |
| `curl /llms.txt /llms-full.txt /feed.json /rss.xml` | 動態端點輸出檢查 |
