/**
 * services.md — markdown 鏡像：AI 爬蟲拿純文字版比解析 HTML 快 10×
 */
import type { APIRoute } from "astro";
import { fetchMeta, fetchServices } from "../lib/crm-client";
import { resolveSiteUrl, textResponse } from "../lib/seo";

export const GET: APIRoute = async ({ site }) => {
    const [meta, services] = await Promise.all([fetchMeta(), fetchServices()]);
    const siteUrl = resolveSiteUrl(site);

    const lines: string[] = [
        `# ${meta.company_name_zh} 服務項目`,
        "",
        `> ${meta.seo_default_description || meta.tagline || ""}`,
        "",
    ];
    for (const s of services) {
        lines.push(`## ${s.title}`);
        if (s.short_desc) lines.push(s.short_desc);
        lines.push(`[詳情頁 →](${siteUrl}/services#${s.slug})`, "");
    }

    return textResponse(lines.join("\n"), "text/markdown");
};
