/**
 * feed.json — JSON Feed 1.1 spec：給 AI agent / RSS reader 訂閱新文章用
 * https://www.jsonfeed.org/version/1.1/
 *
 * 內容來源：lib/posts.ts（Notion 同步來的影像專欄）。
 */
import type { APIRoute } from "astro";
import { fetchMeta } from "../lib/crm-client";
import { fetchPosts } from "../lib/posts";
import { resolveSiteUrl, textResponse } from "../lib/seo";

export const GET: APIRoute = async ({ site }) => {
    const [meta, posts] = await Promise.all([fetchMeta(), fetchPosts()]);
    const siteUrl = resolveSiteUrl(site);

    const feed = {
        version: "https://jsonfeed.org/version/1.1",
        title: `${meta.company_name_zh} 影像專欄 Insight`,
        home_page_url: `${siteUrl}/news`,
        feed_url: `${siteUrl}/feed.json`,
        description: meta.seo_default_description || meta.tagline || "",
        language: "zh-Hant",
        authors: [{ name: meta.company_name_zh, url: siteUrl }],
        items: posts.map(p => ({
            id: `${siteUrl}/news/${p.slug}`,
            url: `${siteUrl}/news/${p.slug}`,
            title: p.title,
            content_text: p.excerpt || p.title,
            summary: p.excerpt || undefined,
            image: p.cover_url || undefined,
            date_published: p.published_at || undefined,
            tags: p.category_label_en ? [p.category_label_en] : undefined,
        })),
    };

    return textResponse(JSON.stringify(feed, null, 2), "application/feed+json");
};
