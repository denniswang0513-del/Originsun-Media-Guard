/**
 * robots.txt.ts — 動態 robots.txt 端點
 *
 * 內容由 admin「網站設定」即時控制：
 * - seo.indexable=false（預設）：Disallow: /  → 全站不被索引
 * - seo.indexable=true        ：Allow:    /  → 公開索引
 * - seo.ai_allow=true         ：明確 allow GPTBot/ClaudeBot/PerplexityBot/Google-Extended
 *
 * sitemap-index.xml 一律 reference（即使現在禁索引，未來打開時 Google 會撿）。
 */
import type { APIRoute } from "astro";
import { fetchMeta } from "../lib/crm-client";
import { resolveSiteUrl, textResponse } from "../lib/seo";

const AI_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"] as const;

export const GET: APIRoute = async ({ site }) => {
    const meta = await fetchMeta();
    const indexable = meta.indexable === true;
    const aiAllow = meta.ai_allow === true;
    const siteUrl = resolveSiteUrl(site);

    const lines: string[] = [
        "User-agent: *",
        indexable ? "Allow: /" : "Disallow: /",
        // showcase-edit.html 是 token-based 編輯頁面，永不可索引
        "Disallow: /showcase-edit.html",
        "",
    ];

    if (aiAllow) {
        for (const bot of AI_BOTS) {
            lines.push(`User-agent: ${bot}`, "Allow: /", "");
        }
    }

    lines.push(`Sitemap: ${siteUrl}/sitemap-index.xml`);

    return textResponse(lines.join("\n"), "text/plain");
};
