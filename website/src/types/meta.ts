/**
 * meta.ts — 全站 metadata TS interface
 * 對應 core/schemas_website.py 的 WebsiteMeta
 */
import type { ICategory } from "./category";

export interface IWebsiteMeta {
    company_name_zh: string;
    company_name_en: string;
    tagline: string;
    subtitle: string;
    address: string;
    phone: string;
    email: string;
    social: {
        youtube?: string;
        instagram?: string;
        facebook?: string;
        [k: string]: string | undefined;
    };
    seo_default_title: string;
    seo_default_description: string;
    seo_og_image?: string;  // 全站預設 OG image（個別頁未指定時 fallback 用）
    brand_logo_url?: string;     // 品牌 Logo（header；空則用預設 SVG 標記）
    brand_favicon_url?: string;  // favicon（空則用 /favicon.svg）
    categories: ICategory[];
    // Admin 可編輯（website_settings.about.* / home.*）
    about_intro_zh?: string;
    about_intro_en?: string;
    about_founded_year?: string;
    about_team_intro_zh?: string;
    home_hero_youtube_id?: string;
    // 首頁 WhoWeAre 段 Showreel（admin「🏠 首頁設定」可編；空則沿用 home_hero_youtube_id）
    home_showreel_id?: string;
    // 首頁 WhoWeAre 段 4 欄 stats（admin「🏠 首頁設定」可編；空則前端用預設）
    home_stats?: { value: string; label_zh: string; label_en: string }[];
    // 首頁 Testimonials 段整體評分 badge（admin「🏠 首頁設定」可編；空則前端用預設）
    home_rating_value?: string;
    home_rating_count?: string;
    // 即時真實數據（文案 {years}/{works} 佔位符替換）— 年資 "13"、完成作品 "150+"
    stats_years?: string;
    stats_works?: string;
    // SEO 索引控制（admin 在「網站設定」打開後 BaseLayout 移除 noindex meta，
    // robots.txt endpoint 改 Allow:/）
    indexable?: boolean;
    // 是否允許 AI 爬蟲（GPTBot/ClaudeBot/PerplexityBot/Google-Extended）讀取
    ai_allow?: boolean;
    // admin 自填的 llms.txt 內容；空則 /llms.txt endpoint 走自動生成
    llms_txt_body?: string;
    // GA4 追蹤 ID（G-XXXXXXXXXX）；admin 在「⚙️ 網站設定 › 📊 分析追蹤」填。
    // 空 → BaseLayout 不注入 gtag。
    analytics_ga_id?: string;
    // /portfolio 頁面頂部「下載作品集 PDF」按鈕連結；空則隱藏按鈕
    portfolio_pdf_url?: string;
    // 頁面行銷文案覆寫（admin 在各子視圖「📝 頁面文案」卡編輯 website_settings.copy.*）。
    // 巢狀結構：copy[page][block_lang]，如 copy.services.hero_title_zh。
    // 各 .astro 用 meta.copy?.<page>?.<block>_zh ?? "<硬寫 fallback>" 渲染。
    copy?: Record<string, Record<string, string>>;
    // 頂部導覽選單（visible=true ORDER BY sort_order）。空 / undefined → Header.astro
    // fallback 到硬寫 7 筆 navItems（對外網站零變化）。
    nav?: INavItem[];
    // 聯絡表單選項清單（forms.contact.service_types / budget_ranges）。
    // value 穩定不可變（後端 / CRM 存這個），只 label 可編；空 → ContactForm 用硬寫 fallback。
    forms?: {
        contact?: {
            service_types?: IFormOption[];
            budget_ranges?: IFormOption[];
        };
    };
}

export interface INavItem {
    label_zh: string;
    label_en?: string | null;
    href: string;
    sort_order?: number;
}

export interface IFormOption {
    value: string;
    label_zh: string;
    label_en: string;
}

export interface ITeamMember {
    id: string;
    name: string;
    role?: string | null;
    bio?: string | null;
    photo_url?: string | null;
}
