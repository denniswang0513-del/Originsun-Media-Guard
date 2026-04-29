/**
 * works/[slug].md — 作品 markdown 鏡像，AI 爬蟲拿純文字版比解析 HTML 快得多。
 *
 * Astro SSG 會用 getStaticPaths 為每個 work 預生成。
 */
import type { APIRoute, GetStaticPaths } from "astro";
import { fetchMeta, fetchWorkBySlug, getWorkSlugPaths } from "../../lib/crm-client";
import { resolveSiteUrl, textResponse } from "../../lib/seo";

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
    const credits = Object.entries(work.credits || {});
    if (credits.length) {
        lines.push("## Credits");
        for (const [role, name] of credits) {
            const value = Array.isArray(name) ? name.join(" · ") : name;
            lines.push(`- **${role}**: ${value}`);
        }
        lines.push("");
    }
    if (work.youtube_id) {
        lines.push(`## 影片`, `https://www.youtube.com/watch?v=${work.youtube_id}`, "");
    }
    lines.push(
        "---",
        `[完整頁面 →](${siteUrl}/works/${work.slug})`,
        `來源：${meta.company_name_zh}（${siteUrl}）`,
    );

    return textResponse(lines.join("\n"), "text/markdown");
};
