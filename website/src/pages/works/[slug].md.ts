/**
 * works/[slug].md — 作品 markdown 鏡像，AI 爬蟲拿純文字版比解析 HTML 快得多。
 *
 * Astro SSG 會用 getStaticPaths 為每個 work 預生成。
 */
import type { APIRoute, GetStaticPaths } from "astro";
import { fetchMeta, fetchWorkBySlug, getWorkSlugPaths } from "../../lib/crm-client";
import { resolveSiteUrl, textResponse } from "../../lib/seo";
import { normalizeCredits } from "../../lib/credits";

export const getStaticPaths: GetStaticPaths = getWorkSlugPaths;

export const GET: APIRoute = async ({ params, site }) => {
    const slug = params.slug as string;
    const [meta, work] = await Promise.all([fetchMeta(), fetchWorkBySlug(slug)]);
    if (!work) {
        return new Response("Not found", { status: 404 });
    }
    const siteUrl = resolveSiteUrl(site);

    const lines: string[] = [
        `# ${work.title}`,
        "",
    ];
    if (work.year || work.client || work.categories.length) {
        const parts: string[] = [];
        if (work.year) parts.push(`年份：${work.year}`);
        if (work.client) parts.push(`客戶：${work.client}`);
        if (work.categories.length) parts.push(`類別：${work.categories.join(", ")}`);
        lines.push(`> ${parts.join(" · ")}`, "");
    }
    if (work.description) {
        lines.push(work.description.trim(), "");
    }
    const blocks = normalizeCredits(work.credits);
    const hasCredits = blocks.some(b => (b.entries || []).some(e => e.name));
    if (hasCredits) {
        lines.push("## Credits");
        for (const block of blocks) {
            const valid = (block.entries || []).filter(e => e.name);
            if (!valid.length) continue;
            const heading = block.name_zh || "其他";
            const en = block.name_en ? ` (${block.name_en})` : "";
            const items = valid.map(e => e.duty ? `${e.duty} ${e.name}` : e.name).join(" · ");
            lines.push(`- **${heading}${en}**: ${items}`);
        }
        lines.push("");
    }
    if (work.youtube_id) {
        lines.push(`## 影片`, `https://www.youtube.com/watch?v=${work.youtube_id}`, "");
    }
    // 曾用 URL — 給 LLM 知道此作品的歷史路徑變遷（SEO 301 來源）
    if (work.old_urls && work.old_urls.length) {
        lines.push("## 曾用 URL");
        for (const u of work.old_urls) {
            lines.push(`- ${siteUrl}${u}`);
        }
        lines.push("");
    }
    lines.push(
        "---",
        `[完整頁面 →](${siteUrl}/works/${work.slug})`,
        `來源：${meta.company_name_zh}（${siteUrl}）`,
    );

    return textResponse(lines.join("\n"), "text/markdown");
};
