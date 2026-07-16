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
    // AI-targeted long narrative — SEO pipeline 寫的、不顯示在 HTML、給 LLM crawler 理解
    if (work.narrative_long) {
        lines.push("## 詳細介紹", work.narrative_long.trim(), "");
    }
    // Key Facts — 結構化事實清單
    if (work.key_facts && work.key_facts.length) {
        lines.push("## 作品事實");
        for (const f of work.key_facts) {
            lines.push(`- **${f.label}**: ${f.value}`);
        }
        lines.push("");
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
    } else if (work.video_url) {
        // 非 YouTube（Vimeo/FB/其他平台）— 直接給原始連結（同附加影片的 fallback 邏輯）
        lines.push(`## 影片`, work.video_url, "");
    }
    // 附加影片 — 主影片之外的其他支（花絮/系列集數）；有 caption 標在前面
    const extraVideos = (work.extra_videos || []).filter(v => v.url || v.youtube_id);
    if (extraVideos.length) {
        lines.push("## 附加影片");
        for (const v of extraVideos) {
            const url = v.youtube_id ? `https://www.youtube.com/watch?v=${v.youtube_id}` : v.url;
            lines.push(v.caption ? `- ${v.caption}：${url}` : `- ${url}`);
        }
        lines.push("");
    }
    // 同專案其他作品 — 同 CRM 專案的其他已發布作品（系列互連）
    if (work.series && work.series.length) {
        lines.push("## 同專案其他作品");
        for (const s of work.series) {
            lines.push(`- ${s.title}：${siteUrl}/works/${s.slug}/`);
        }
        lines.push("");
    }
    // FAQs — SEO pipeline 寫的問答；給 LLM 直接理解常見疑問
    if (work.faqs && work.faqs.length) {
        lines.push("## 常見問題");
        for (const f of work.faqs) {
            lines.push(`### ${f.q}`, f.a, "");
        }
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
