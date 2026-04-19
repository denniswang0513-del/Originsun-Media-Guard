# website/src/components/

Astro 元件依「功能」分目錄。每檔 ≤ 200 行、單一職責。

| 目錄 | 用途 |
|---|---|
| `layout/` | Header、Footer、Navigation、LanguageToggle、SEO |
| `home/` | 首頁專用（HeroReel、FeaturedWorks、ServicesGrid） |
| `works/` | 作品集（WorkCard、CategoryFilter、VideoPlayer） |
| `about/` | 關於我們（TeamGrid、CompanyInfo、Map） |
| `contact/` | 聯絡頁（ContactForm + Turnstile） |

**命名慣例**：PascalCase `.astro` 檔名。

**Islands 原則**：預設零 JS（.astro 純靜態），只有需要互動的元件（如 ContactForm）才加 `client:load` 或 `client:visible`。
