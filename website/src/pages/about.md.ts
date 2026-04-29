/**
 * about.md — 公司資訊 markdown 鏡像
 */
import type { APIRoute } from "astro";
import { fetchMeta, fetchTeam } from "../lib/crm-client";
import { companyInfoMd, resolveSiteUrl, textResponse } from "../lib/seo";

export const GET: APIRoute = async ({ site }) => {
    const [meta, team] = await Promise.all([fetchMeta(), fetchTeam()]);
    const siteUrl = resolveSiteUrl(site);

    const lines: string[] = [
        `# ${meta.company_name_zh}（${meta.company_name_en}）`,
        "",
        `> ${meta.tagline || meta.seo_default_description || ""}`,
        "",
    ];
    if (meta.about_intro_zh) {
        lines.push("## 關於我們", meta.about_intro_zh.trim(), "");
    }
    lines.push(...companyInfoMd(meta, siteUrl, "聯絡資訊"));

    if (team.length) {
        lines.push("## 團隊");
        for (const m of team) {
            const role = m.role ? ` · ${m.role}` : "";
            lines.push(`- **${m.name}**${role}${m.bio ? ` — ${m.bio}` : ""}`);
        }
        lines.push("");
    }

    return textResponse(lines.join("\n"), "text/markdown");
};
