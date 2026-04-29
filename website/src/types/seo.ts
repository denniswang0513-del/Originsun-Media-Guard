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
