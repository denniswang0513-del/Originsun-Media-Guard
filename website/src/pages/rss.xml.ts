/**
 * rss.xml — RSS 2.0 feed for /news posts（給傳統 RSS reader / Feedly / Inoreader）
 *
 * 比 /feed.json 老但相容性更好。AI 爬蟲多半兩個都吃。
 */
import type { APIRoute } from "astro";
import { fetchMeta } from "../lib/crm-client";
import { fetchPosts } from "../lib/posts";
import { resolveSiteUrl, textResponse } from "../lib/seo";

const _esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
     .replace(/"/g, "&quot;").replace(/'/g, "&apos;");

export const GET: APIRoute = async ({ site }) => {
    const [meta, posts] = await Promise.all([fetchMeta(), fetchPosts()]);
    const siteUrl = resolveSiteUrl(site);

    const items = posts.map(p => `
    <item>
      <title>${_esc(p.title)}</title>
      <link>${siteUrl}/news/${p.slug}</link>
      <guid isPermaLink="true">${siteUrl}/news/${p.slug}</guid>
      <pubDate>${p.published_at ? new Date(p.published_at).toUTCString() : ""}</pubDate>
      <description>${_esc(p.excerpt || p.title)}</description>
      ${p.category_label_en ? `<category>${_esc(p.category_label_en)}</category>` : ""}
    </item>`).join("");

    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${_esc(meta.company_name_zh)} 影像專欄 Insight</title>
    <link>${siteUrl}/news</link>
    <description>${_esc(meta.seo_default_description || meta.tagline || "")}</description>
    <language>zh-Hant</language>
    <atom:link href="${siteUrl}/rss.xml" rel="self" type="application/rss+xml" />${items}
  </channel>
</rss>`;

    return textResponse(xml, "application/rss+xml");
};
