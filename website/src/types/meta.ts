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
    categories: ICategory[];
    // Admin 可編輯（website_settings.about.* / home.*）
    about_intro_zh?: string;
    about_intro_en?: string;
    about_founded_year?: string;
    about_team_intro_zh?: string;
    home_hero_youtube_id?: string;
    // SEO 索引控制（admin 在「網站設定」打開後 BaseLayout 移除 noindex meta，
    // robots.txt endpoint 改 Allow:/）
    indexable?: boolean;
    // 是否允許 AI 爬蟲（GPTBot/ClaudeBot/PerplexityBot/Google-Extended）讀取
    ai_allow?: boolean;
    // admin 自填的 llms.txt 內容；空則 /llms.txt endpoint 走自動生成
    llms_txt_body?: string;
    // /portfolio 頁面頂部「下載作品集 PDF」按鈕連結；空則隱藏按鈕
    portfolio_pdf_url?: string;
}

export interface ITeamMember {
    id: string;
    name: string;
    role?: string | null;
    bio?: string | null;
    photo_url?: string | null;
}
