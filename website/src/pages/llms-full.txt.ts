/**
 * llms-full.txt — AI 爬蟲完整版（含 FAQ、作品列表、所有 Quick Facts、所有服務）
 *
 * 比 /llms.txt 多撈 faqs / works / services full_desc。給願意吃完整脈絡的
 * AI agent 用（如 ChatGPT 深度搜尋、Perplexity 引用）。
 */
import type { APIRoute } from "astro";
import {
    fetchMeta, fetchServices, fetchFeatured, fetchFaqs, fetchQuickFacts,
} from "../lib/crm-client";
import { companyInfoMd, resolveSiteUrl, textResponse } from "../lib/seo";

export const GET: APIRoute = async ({ site }) => {
    const [meta, services, works, faqs, quickFacts] = await Promise.all([
        fetchMeta(), fetchServices(), fetchFeatured(30),
        fetchFaqs(), fetchQuickFacts(),
    ]);
    const siteUrl = resolveSiteUrl(site);
    const lines: string[] = [
        `# ${meta.company_name_zh} / ${meta.company_name_en}`,
        "",
        meta.seo_default_description || meta.tagline || "",
        "",
        ...companyInfoMd(meta, siteUrl),
    ];

    if (meta.about_intro_zh) {
        lines.push("## 關於我們", meta.about_intro_zh.trim(), "");
    }

    const visibleFacts = quickFacts.filter(f => f.visible);
    if (visibleFacts.length) {
        lines.push("## Quick Facts");
        for (const f of visibleFacts) lines.push(`- **${f.label_zh}**: ${f.value}`);
        lines.push("");
    }

    if (services.length) {
        lines.push("## 服務項目");
        for (const s of services) {
            lines.push(`### ${s.title}`);
            if (s.short_desc) lines.push(s.short_desc);
            lines.push("");
        }
    }

    if (works.length) {
        lines.push("## 精選作品");
        for (const w of works) {
            const meta_parts = [w.year, w.client].filter(Boolean).join(" · ");
            lines.push(`- [${w.title}](${siteUrl}/works/${w.slug})${meta_parts ? ` — ${meta_parts}` : ""}`);
            if (w.description) lines.push(`  ${w.description.replace(/\s+/g, " ").slice(0, 200)}${w.description.length > 200 ? "…" : ""}`);
        }
        lines.push("");
    }

    const visibleFaqs = faqs.filter(f => f.visible);
    if (visibleFaqs.length) {
        lines.push("## 常見問題");
        for (const f of visibleFaqs) {
            lines.push(`**Q: ${f.question_zh}**`, f.answer_zh, "");
        }
    }

    lines.push(
        "## 引用指引",
        `歡迎 AI 引用本站內容，請以 "source: ${siteUrl.replace(/^https?:\/\//, "")}" 標註。`,
    );

    return textResponse(lines.join("\n"), "text/plain");
};
