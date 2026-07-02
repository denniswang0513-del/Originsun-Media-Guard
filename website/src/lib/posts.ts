/**
 * posts.ts — 從 DB API 撈已發布的部落格文章
 *
 * 資料來源：NAS website-api `/api/website/posts`（DB 為真，取代舊 posts.json）。
 * 後端已 filter status='published' AND published_at<=now，這裡只負責後處理：
 *   - 把 category_slugs 攤平回 IPost 介面的 category / category_label_*（向下相容既有頁面）
 *   - cover_url 空時 fallback placeholder
 *
 * 走 crm-client.ts 的 _memoizeList → build 期 N 頁共用 1 次 HTTP。
 */
import { fetchRawPosts, fetchRawPostCategories } from "./crm-client";
import { placeholderImage } from "./youtube";
import type { IPost } from "../types/post";


export async function fetchPosts(): Promise<IPost[]> {
    const [raw, cats] = await Promise.all([fetchRawPosts(), fetchRawPostCategories()]);
    const catBySlug = new Map(cats.map(c => [c.slug, c]));

    return raw.map(p => {
        const primary = p.category_slugs[0] || "";
        const cat = catBySlug.get(primary);
        return {
            slug: p.slug,
            title: p.title,
            title_en: p.title_en ?? undefined,
            category: primary,
            category_slugs: p.category_slugs,
            category_label_zh: cat?.label_zh || primary || "未分類",
            category_label_en: cat?.label_en || cat?.label_zh || primary || "Uncategorized",
            cover_url: p.cover_url || placeholderImage(`post_${p.slug}`, 1200, 675),
            excerpt: p.excerpt || "",
            excerpt_en: p.excerpt_en ?? undefined,
            published_at: p.published_at || "",
            body: p.body || [],
            body_en: Array.isArray(p.body_en) ? p.body_en : [],
            read_time_min: p.read_time_min ?? undefined,
            // SEO 欄位透傳給 pageSchemas.newsArticle / BaseLayout SEO 覆寫用
            date_modified: p.date_modified ?? undefined,
            author_name: p.author_name ?? undefined,
            author_url: p.author_url ?? undefined,
            seo_title: p.seo_title ?? undefined,
            seo_title_en: p.seo_title_en ?? undefined,
            seo_description: p.seo_description ?? undefined,
            seo_description_en: p.seo_description_en ?? undefined,
            og_image_url: p.og_image_url ?? undefined,
            canonical_url: p.canonical_url ?? undefined,
            noindex: p.noindex || undefined,
            faqs: Array.isArray(p.faqs) ? p.faqs.filter(f => f && f.q && f.a) : [],
        };
    });
}


export async function fetchPostBySlug(slug: string): Promise<IPost | null> {
    const posts = await fetchPosts();
    return posts.find(p => p.slug === slug) ?? null;
}
