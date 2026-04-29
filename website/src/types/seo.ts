/**
 * seo.ts — SEO 共用 TypeScript 型別
 *
 * Schema.org JSON-LD 物件的通用型別 + 各頁面共用的 SEO 結構介面。
 * src/lib/seo.ts 的 pageSchemas 工廠回傳 SchemaObject，
 * BaseLayout 的 schemaData prop 也是 SchemaObject[]。
 */

/** Schema.org JSON-LD 物件（純 JSON-serializable record） */
export type SchemaObject = Record<string, unknown>;

/**
 * Breadcrumb 項目（按順序傳入 pageSchemas.breadcrumb）。
 * url 可為相對路徑（如 "/works"）或絕對 URL；factory 用 siteUrl 補完成絕對。
 */
export interface BreadcrumbItem {
    name: string;
    url: string;
}

/** FAQPage 條目（pageSchemas.faqPage） */
export interface FAQItem {
    question: string;
    answer: string;
}

/** Review 條目（pageSchemas.review） */
export interface ReviewItem {
    author: string;
    rating: number;       // 1-5
    body?: string;
    datePublished?: string;
}

/** AggregateRating 統計（pageSchemas.aggregateRating） */
export interface RatingStats {
    ratingValue: number;
    reviewCount: number;
    bestRating?: number;  // 預設 5
    worstRating?: number; // 預設 1
}


// ── API 回傳形狀（對應 db/models_website/seo.py + services/website/seo_service.py） ──

export interface IFAQ {
    id: number;
    question_zh: string;
    question_en: string | null;
    answer_zh: string;
    answer_en: string | null;
    sort_order: number;
    visible: boolean;
}

export interface ITestimonial {
    id: number;
    author_zh: string;
    author_en: string | null;
    role_zh: string | null;
    role_en: string | null;
    company: string | null;
    rating: number;
    content_zh: string | null;
    content_en: string | null;
    sort_order: number;
    visible: boolean;
    date_published: string | null;
}

export interface IQuickFact {
    id: number;
    label_zh: string;
    label_en: string | null;
    value: string;
    sort_order: number;
    visible: boolean;
}
