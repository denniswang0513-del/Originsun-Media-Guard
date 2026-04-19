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
    categories: ICategory[];
    // Admin 可編輯（website_settings.about.* / home.*）
    about_intro_zh?: string;
    about_intro_en?: string;
    about_founded_year?: string;
    about_team_intro_zh?: string;
    home_hero_youtube_id?: string;
}

export interface ITeamMember {
    id: string;
    name: string;
    role?: string | null;
    bio?: string | null;
    photo_url?: string | null;
}
