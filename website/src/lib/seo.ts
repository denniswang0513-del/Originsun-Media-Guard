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
    SchemaObject, BreadcrumbItem, FAQItem, ReviewItem, RatingStats, ITestimonial,
} from "../types/seo";

const SCHEMA_CTX = "https://schema.org";


// === 共用 SEO helper ===

/**
 * 從 Astro.site 推導站台 URL，缺值時 fallback 到 config 預設。
 * 一律 strip 尾斜線：endpoints 用 `${siteUrl}/path` 拼接才不會出現 `//`，
 * canonicalUrl 用 new URL() 不受影響。
 */
export function resolveSiteUrl(astroSite?: URL): string {
    const raw = astroSite?.toString() || SITE_URL_FALLBACK;
    return raw.replace(/\/$/, "");
}

/** 將 pathname 組成完整 canonical URL */
export function canonicalUrl(astroSite: URL | undefined, pathname: string): string {
    return new URL(pathname, resolveSiteUrl(astroSite)).toString();
}

/**
 * 動態 endpoint 共用 Response builder（robots.txt / llms*.txt / *.md / feed.json / rss.xml）。
 * 預設 1h cache：admin 寫入後 60s 觸發 rebuild 覆蓋 dist/，所以最差 1h 後對外一定看到新版。
 */
export function textResponse(body: string, contentType: string, maxAge = 3600): Response {
    return new Response(body, {
        headers: {
            "Content-Type": `${contentType}; charset=utf-8`,
            "Cache-Control": `public, max-age=${maxAge}`,
        },
    });
}

/**
 * 公司資訊 markdown block — llms.txt / llms-full.txt / about.md 共用。
 * heading="聯絡資訊" 時用「地址」字眼，其他用「地點」。
 */
export function companyInfoMd(meta: IWebsiteMeta, siteUrl: string, heading = "公司資訊"): string[] {
    const out = [`## ${heading}`];
    if (meta.about_founded_year) out.push(`- 成立：${meta.about_founded_year}`);
    if (meta.address) out.push(`- ${heading === "聯絡資訊" ? "地址" : "地點"}：${meta.address}`);
    if (meta.phone) out.push(`- 電話：${meta.phone}`);
    if (meta.email) out.push(`- Email：${meta.email}`);
    out.push(`- 網址：${siteUrl}`, "");
    return out;
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
 *   const seo = buildBasicPageSeo(Astro, {
 *       title, description,
 *       breadcrumbs: breadcrumb2('作品集', '/works'),
 *   });
 *   <BaseLayout {...seo} meta={meta}>
 *
 * 傳 breadcrumbs 自動加 BreadcrumbList schema 給 SERP 麵包屑使用。
 */
export function buildBasicPageSeo(
    astro: { url: URL; site: URL | undefined },
    opts: {
        title: string;
        description: string;
        breadcrumbs?: BreadcrumbItem[];
    },
): { title: string; description: string; schemaData: SchemaObject[] } {
    const url = canonicalUrl(astro.site, astro.url.pathname);
    const schemaData: SchemaObject[] = [
        pageSchemas.webPage({ title: opts.title, description: opts.description, url }),
    ];
    if (opts.breadcrumbs?.length) {
        schemaData.push(pageSchemas.breadcrumb(opts.breadcrumbs, resolveSiteUrl(astro.site)));
    }
    return { title: opts.title, description: opts.description, schemaData };
}


// ── BreadcrumbList 預設「首頁 → ...」起點，集中字串避免 6+ 頁重複 ──

const HOME_CRUMB: BreadcrumbItem = { name: "首頁", url: "/" };

export function breadcrumb2(name: string, url: string): BreadcrumbItem[] {
    return [HOME_CRUMB, { name, url }];
}

export function breadcrumb3(midName: string, midUrl: string, leafName: string, leafUrl: string): BreadcrumbItem[] {
    return [HOME_CRUMB, { name: midName, url: midUrl }, { name: leafName, url: leafUrl }];
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
    webPage(opts: {
        title: string; description: string; url: string;
        sameAs?: string[]; keywords?: string;
    }): SchemaObject {
        return {
            "@context": SCHEMA_CTX,
            "@type": "WebPage",
            name: opts.title,
            description: opts.description,
            url: opts.url,
            sameAs: opts.sameAs?.length ? opts.sameAs : undefined,
            keywords: opts.keywords || undefined,
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

    /**
     * Testimonial 套組 — 從 admin Tab 的 testimonials 表生成 Review + AggregateRating。
     * `includeReviews=N` 額外輸出前 N 則 Review schema（首頁通常只要 AggregateRating
     * 摘要，about 頁可以鋪 5 則完整 Review）。空 list 直接回 [] 不汙染 schemaData。
     */
    testimonialBundle(testimonials: ITestimonial[], opts: { includeReviews?: number } = {}): SchemaObject[] {
        const visible = testimonials.filter(t => t.visible);
        if (!visible.length) return [];
        const avg = Number((visible.reduce((s, t) => s + t.rating, 0) / visible.length).toFixed(1));
        const out: SchemaObject[] = [];
        if (opts.includeReviews) {
            out.push(...visible.slice(0, opts.includeReviews).map(t => pageSchemas.review({
                author: t.author_zh,
                rating: t.rating,
                body: t.content_zh ?? undefined,
                datePublished: t.date_published ?? undefined,
            })));
        }
        out.push(pageSchemas.aggregateRating({ ratingValue: avg, reviewCount: visible.length }));
        return out;
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
        // sameAs：列出舊 URL（slug 變動歷史），讓 Google 認知 PageRank 合併
        const sameAs = (work.old_urls || []).map(u => `${siteUrl}${u}`);
        // keywords：tag slug + category slug 一起塞，給搜尋引擎 / AI 抓主題
        const keywords = [...(work.categories || []), ...(work.tags || [])]
            .map(s => s.replace(/^tag-/, ""))
            .join(", ");

        return {
            "@context": SCHEMA_CTX,
            "@type": "VideoObject",
            name: work.title,
            description: work.description || work.title,
            thumbnailUrl: thumb ? [thumb] : undefined,
            uploadDate: work.published_at || undefined,
            embedUrl: embedUrl || undefined,
            contentUrl: work.youtube_id ? `https://www.youtube.com/watch?v=${work.youtube_id}` : undefined,
            // canonical 優先（跨站發布時 PM 設定）— 否則本站 /works/{slug}
            url: work.canonical_url || `${siteUrl}/works/${work.slug}`,
            sameAs: sameAs.length ? sameAs : undefined,
            keywords: keywords || undefined,
        };
    },

    /** NewsArticle — /news/[slug] 部落格文章 */
    newsArticle(post: IPost, meta: IWebsiteMeta, siteUrl: string): SchemaObject {
        // 個人作者 → Person schema（E-E-A-T 加分）；空 → 公司
        const author = post.author_name
            ? {
                "@type": "Person",
                name: post.author_name,
                url: post.author_url || siteUrl,
            }
            : {
                "@type": "Organization",
                name: meta.company_name_zh,
                url: siteUrl,
            };
        const img = post.og_image_url || post.cover_url;

        return {
            "@context": SCHEMA_CTX,
            "@type": "NewsArticle",
            headline: post.title,
            description: post.seo_description || post.excerpt,
            image: img ? [img] : undefined,
            datePublished: post.published_at,
            // 內容新鮮度信號（Google E-E-A-T）— admin 編過就比 published_at 新
            dateModified: post.date_modified || post.published_at,
            author,
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
