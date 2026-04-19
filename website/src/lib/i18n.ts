/**
 * i18n.ts — 精簡雙語（繁中/英文）字串對照
 *
 * 設計取捨：不用 astro-i18next 之類框架（動態路由 + dep 肥）。
 * 採 client-side switch：根元素 data-lang=zh|en，CSS 隱藏另一語言。
 * 元件用 <span data-lang-zh>中文</span><span data-lang-en>English</span>
 * LanguageToggle 切換 data-lang + localStorage 持久化。
 */

export type Lang = "zh" | "en";

export const DEFAULT_LANG: Lang = "zh";


/** 通用字串對照表。每一組 {zh, en} 直接在 .astro 取用，不經過 t() 函式。 */
export const STR = {
    nav_home:     { zh: "首頁",     en: "Home" },
    nav_works:    { zh: "作品集",   en: "Works" },
    nav_services: { zh: "服務項目", en: "Services" },
    nav_about:    { zh: "關於我們", en: "About" },
    nav_news:     { zh: "部落格",   en: "Insight" },
    nav_contact:  { zh: "聯絡",     en: "Contact" },

    hero_cta:       { zh: "了解我們的作品", en: "Explore Our Work" },
    hero_scroll:    { zh: "向下探索",       en: "Scroll" },

    featured_title:    { zh: "精選作品",      en: "Featured Work" },
    featured_viewall:  { zh: "看全部作品 →",  en: "View all works →" },

    services_title:    { zh: "我們提供的服務", en: "What We Do" },
    services_viewall:  { zh: "查看服務細節 →", en: "All Services →" },

    works_title:       { zh: "作品集",         en: "Works" },
    works_filter_all:  { zh: "全部",           en: "All" },
    works_no_items:    { zh: "此分類尚無作品", en: "No works in this category yet" },

    work_year:         { zh: "年份",     en: "Year" },
    work_client:       { zh: "客戶",     en: "Client" },
    work_credits:      { zh: "職員表",   en: "Credits" },
    work_related:      { zh: "相關作品", en: "Related Work" },
    work_watch_yt:     { zh: "在 YouTube 觀看", en: "Watch on YouTube" },

    copyright:  { zh: "© {year} 源日影像 OriginsunStudio. All rights reserved.",
                  en: "© {year} OriginsunStudio. All rights reserved." },
} as const;

export type StrKey = keyof typeof STR;


/** 渲染單一語言的文字（for server 端 render／Astro frontmatter） */
export function t(key: StrKey, lang: Lang = DEFAULT_LANG, vars: Record<string, string | number> = {}): string {
    // 顯式標 string 避免 TS 把字面量聯合型別當 readonly
    let str: string = STR[key][lang] || STR[key][DEFAULT_LANG] || key;
    for (const [k, v] of Object.entries(vars)) {
        str = str.replace(`{${k}}`, String(v));
    }
    return str;
}
