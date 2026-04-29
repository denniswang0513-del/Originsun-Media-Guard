/**
 * seo.ts — Schema.org 結構化資料工廠 + SEO 共用 helper
 *
 * 集中入口 `pageSchemas`：每種 schema 一個 typed factory。
 * 共用 helper：canonicalUrl / resolveSiteUrl / ensureMinDescription / buildBasicPageSeo。
 */

import { SITE_URL_FALLBACK } from "./config";
import type { IPublicProjectDetail } from "../types/project";
import type { IService } from "../types/service";
import type { IWebsiteMeta } from "../types/meta";
import type { IPost } from "../types/post";
import type {
    SchemaObject, BreadcrumbItem, FAQItem, ReviewItem, RatingStats,
} from "../types/seo";

const SCHEMA_CTX = "https://schema.org";


// === 共用 SEO helper ===

/** 從 Astro.site 推導站台 URL，缺值時 fallback 到 config 預設 */
export function resolveSiteUrl(astroSite?: URL): string {
    return astroSite?.toString() || SITE_URL_FALLBACK;
}

/** 將 pathname 組成完整 canonical URL */
export function canonicalUrl(astroSite: URL | undefined, pathname: string): string {
    return new URL(pathname, resolveSiteUrl(astroSite)).toString();
}

/** 確保 description 至少 minLen 字（過短時附加 fallback 字串） */
export function ensureMinDescription(
    primary: string | null | undefined,
    fallback: string,
    minLen = 30,
): string {
    const trimmed = (primary || "").trim();
    if (trimmed.length >= minLen) return trimmed;
    return trimmed ? `${trimmed} — ${fallback}` : fallback;
}

/**
 * 通用頁面 SEO baseline — 給沒有更專屬 schema 的頁面用
 *
 * 回傳 { title, description, schemaData } 可直接展開到 BaseLayout：
 *   const seo = buildBasicPageSeo(Astro, { title, description });
 *   <BaseLayout {...seo} meta={meta}>
 */
export function buildBasicPageSeo(
    astro: { url: URL; site: URL | undefined },
    opts: { title: string; description: string },
): { title: string; description: string; schemaData: SchemaObject[] } {
    const url = canonicalUrl(astro.site, astro.url.pathname);
    return {
        title: opts.title,
        description: opts.description,
        schemaData: [pageSchemas.webPage({ title: opts.title, description: opts.description, url })],
    };
}


// === Schema.org 工廠 ===

export const pageSchemas = {

    /** Organization — 識別品牌實體（首頁、about 用） */
    organization(meta: IWebsiteMeta, siteUrl: string): SchemaObject {
        const sameAs = Object.values(meta.social).filter(Boolean) as string[];
        return {
            "@context": SCHEMA_CTX,
            "@type": "Organization",
            "@id": `${siteUrl}/#organization`,
            name: meta.company_name_en || meta.company_name_zh,
            alternateName: meta.company_name_zh,
            url: siteUrl,
            email: meta.email || undefined,
            telephone: meta.phone || undefined,
            address: meta.address ? {
                "@type": "PostalAddress",
                streetAddress: meta.address,
                addressCountry: "TW",
            } : undefined,
            sameAs: sameAs.length ? sameAs : undefined,
            logo: meta.seo_og_image ? {
                "@type": "ImageObject",
                url: meta.seo_og_image,
            } : undefined,
        };
    },

    /** LocalBusiness — Google Maps / 在地搜尋 */
    localBusiness(meta: IWebsiteMeta, siteUrl: string): SchemaObject {
        const sameAs = Object.values(meta.social).filter(Boolean) as string[];
        return {
            "@context": SCHEMA_CTX,
            "@type": "LocalBusiness",
            "@id": `${siteUrl}/#localbusiness`,
            name: meta.company_name_zh,
            alternateName: meta.company_name_en || undefined,
            url: siteUrl,
            telephone: meta.phone || undefined,
            email: meta.email || undefined,
            address: meta.address ? {
                "@type": "PostalAddress",
                streetAddress: meta.address,
                addressCountry: "TW",
            } : undefined,
            image: meta.seo_og_image || undefined,
            sameAs: sameAs.length ? sameAs : undefined,
        };
    },

    /** WebSite + SearchAction — 觸發 Google Sitelinks Search Box */
    website(meta: IWebsiteMeta, siteUrl: string): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "WebSite",
            "@id": `${siteUrl}/#website`,
            name: meta.company_name_zh,
            alternateName: meta.company_name_en || undefined,
            url: siteUrl,
            potentialAction: {
                "@type": "SearchAction",
                target: {
                    "@type": "EntryPoint",
                    urlTemplate: `${siteUrl}/works?q={search_term_string}`,
                },
                "query-input": "required name=search_term_string",
            },
        };
    },

    /** WebPage — 任何頁面的最低基線 schema */
    webPage(opts: { title: string; description: string; url: string }): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "WebPage",
            name: opts.title,
            description: opts.description,
            url: opts.url,
        };
    },

    /** BreadcrumbList — SERP 麵包屑 */
    breadcrumb(items: BreadcrumbItem[], siteUrl: string): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "BreadcrumbList",
            itemListElement: items.map((it, i) => ({
                "@type": "ListItem",
                position: i + 1,
                name: it.name,
                item: it.url.startsWith("http") ? it.url : `${siteUrl}${it.url}`,
            })),
        };
    },

    /** FAQPage — rich snippet 摺疊問答 */
    faqPage(items: FAQItem[]): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "FAQPage",
            mainEntity: items.map(it => ({
                "@type": "Question",
                name: it.question,
                acceptedAnswer: { "@type": "Answer", text: it.answer },
            })),
        };
    },

    /** Service — 單一服務項目 */
    service(s: IService, meta: IWebsiteMeta, siteUrl: string): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "Service",
            name: s.title,
            description: s.short_desc || s.title,
            url: `${siteUrl}/services#${s.slug}`,
            image: s.cover_image || undefined,
            provider: {
                "@type": "Organization",
                name: meta.company_name_zh,
                url: siteUrl,
            },
            ...(s.related_category_slug ? { category: s.related_category_slug } : {}),
        };
    },

    /** AggregateRating — 整體評分（嵌入 Organization 或 LocalBusiness） */
    aggregateRating(stats: RatingStats): SchemaObject {
        return {
            "@type": "AggregateRating",
            ratingValue: stats.ratingValue,
            reviewCount: stats.reviewCount,
            bestRating: stats.bestRating ?? 5,
            worstRating: stats.worstRating ?? 1,
        };
    },

    /** Review — 單則客戶證言 */
    review(r: ReviewItem): SchemaObject {
        return {
            "@type": "Review",
            author: { "@type": "Person", name: r.author },
            reviewRating: {
                "@type": "Rating",
                ratingValue: r.rating,
                bestRating: 5,
                worstRating: 1,
            },
            reviewBody: r.body || undefined,
            datePublished: r.datePublished || undefined,
        };
    },

    /** VideoObject — 作品詳情頁（YouTube 影片） */
    videoObject(work: IPublicProjectDetail, siteUrl: string): SchemaObject {
        const thumb = work.youtube_id
            ? `https://img.youtube.com/vi/${work.youtube_id}/maxresdefault.jpg`
            : work.thumbnail_url;
        const embedUrl = work.youtube_id
            ? `https://www.youtube-nocookie.com/embed/${work.youtube_id}`
            : null;

        return {
            "@context": SCHEMA_CTX,
            "@type": "VideoObject",
            name: work.title,
            description: work.description || work.title,
            thumbnailUrl: thumb ? [thumb] : undefined,
            uploadDate: work.published_at || undefined,
            embedUrl: embedUrl || undefined,
            contentUrl: work.youtube_id ? `https://www.youtube.com/watch?v=${work.youtube_id}` : undefined,
            url: `${siteUrl}/works/${work.slug}`,
        };
    },

    /** NewsArticle — /news/[slug] 部落格文章 */
    newsArticle(post: IPost, meta: IWebsiteMeta, siteUrl: string): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "NewsArticle",
            headline: post.title,
            description: post.excerpt,
            image: post.cover_url ? [post.cover_url] : undefined,
            datePublished: post.published_at,
            author: {
                "@type": "Organization",
                name: meta.company_name_zh,
                url: siteUrl,
            },
            publisher: {
                "@type": "Organization",
                name: meta.company_name_zh,
                logo: meta.seo_og_image ? {
                    "@type": "ImageObject",
                    url: meta.seo_og_image,
                } : undefined,
            },
            mainEntityOfPage: {
                "@type": "WebPage",
                "@id": `${siteUrl}/news/${post.slug}`,
            },
        };
    },
};
