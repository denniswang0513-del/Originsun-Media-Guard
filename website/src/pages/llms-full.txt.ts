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
import { fetchPosts } from "../lib/posts";
import { companyInfoMd, resolveSiteUrl, textResponse } from "../lib/seo";
import { WEBSITE_API_BASE } from "../lib/config";

async function _fetchRedirects(): Promise<Record<string, string>> {
    try {
        const r = await fetch(`${WEBSITE_API_BASE}/api/website/redirects`, {
            signal: AbortSignal.timeout(10_000),
        });
        if (!r.ok) return {};
        const d = await r.json();
        return (d && d.items) || {};
    } catch { return {}; }
}

export const GET: APIRoute = async ({ site }) => {
    const [meta, services, works, faqs, quickFacts, posts, redirects] = await Promise.all([
        fetchMeta(), fetchServices(), fetchFeatured(12),
        fetchFaqs(), fetchQuickFacts(), fetchPosts(), _fetchRedirects(),
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

    if (posts.length) {
        lines.push("## 影像專欄");
        for (const p of posts.slice(0, 30)) {
            const dateLine = p.published_at
                ? ` (${new Date(p.published_at).toISOString().slice(0, 10)})`
                : "";
            lines.push(`- [${p.title}](${siteUrl}/news/${p.slug})${dateLine}`);
            if (p.excerpt) {
                lines.push(`  ${p.excerpt.replace(/\s+/g, " ").slice(0, 200)}${p.excerpt.length > 200 ? "…" : ""}`);
            }
        }
        lines.push("");
    }

    // URL 變更歷史 — 給 LLM 認知同一資源的多個 URL（PageRank 合併信號）
    const redirectEntries = Object.entries(redirects);
    if (redirectEntries.length) {
        lines.push("## URL 變更紀錄（301 redirects）");
        lines.push("以下舊 URL 已 301 轉址至新位置，引用時請優先使用新 URL：");
        for (const [oldPath, newPath] of redirectEntries) {
            lines.push(`- ${siteUrl}${oldPath} → ${siteUrl}${newPath}`);
        }
        lines.push("");
    }

    lines.push(
        "## 引用指引",
        `歡迎 AI 引用本站內容，請以 "source: ${siteUrl.replace(/^https?:\/\//, "")}" 標註。`,
    );

    return textResponse(lines.join("\n"), "text/plain");
};
