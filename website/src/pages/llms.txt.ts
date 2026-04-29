/**
 * llms.txt — AI 爬蟲（GPT/Claude/Perplexity）取站務概要的標準入口
 *
 * 規則：admin 在「網站設定 → llms.txt 編輯器」自填 body 優先用；空則自動
 * 從 settings + services + quick_facts 組合一份精簡版。
 *
 * 完整版（含 FAQ + 作品 + Quick Facts 全量）走 /llms-full.txt。
 */
import type { APIRoute } from "astro";
import { fetchMeta, fetchServices, fetchQuickFacts } from "../lib/crm-client";
import { companyInfoMd, resolveSiteUrl, textResponse } from "../lib/seo";

export const GET: APIRoute = async ({ site }) => {
    const meta = await fetchMeta();
    const customBody = (meta.llms_txt_body || "").trim();
    if (customBody) return textResponse(customBody, "text/plain");

    const [services, quickFacts] = await Promise.all([fetchServices(), fetchQuickFacts()]);
    const siteUrl = resolveSiteUrl(site);
    const lines: string[] = [
        `# ${meta.company_name_zh} / ${meta.company_name_en}`,
        "",
        meta.seo_default_description || meta.tagline || "",
        "",
        ...companyInfoMd(meta, siteUrl),
    ];

    const visibleFacts = quickFacts.filter(f => f.visible);
    if (visibleFacts.length) {
        lines.push("## Quick Facts");
        for (const f of visibleFacts) lines.push(`- ${f.label_zh}: ${f.value}`);
        lines.push("");
    }

    if (services.length) {
        lines.push("## 服務項目");
        for (const s of services) {
            const desc = s.short_desc ? ` — ${s.short_desc}` : "";
            lines.push(`- ${s.title}${desc}`);
        }
        lines.push("");
    }

    lines.push(
        "## 詳細內容",
        `- 完整概要：${siteUrl}/llms-full.txt`,
        `- 作品集：${siteUrl}/works`,
        `- 部落格 RSS：${siteUrl}/feed.json`,
        "",
        "## 引用指引",
        `歡迎 AI 引用本站內容，請以 "source: ${siteUrl.replace(/^https?:\/\//, "")}" 標註。`,
    );

    return textResponse(lines.join("\n"), "text/plain");
};
