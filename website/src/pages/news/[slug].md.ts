/**
 * news/[slug].md — 部落格文章 markdown 鏡像
 *
 * AI 爬蟲（GPT/Claude/Perplexity）拿純文字版比解析 HTML 快得多。
 * Astro SSG 為每篇 published 文章預生成。
 */
import type { APIRoute, GetStaticPaths } from "astro";
import { fetchMeta } from "../../lib/crm-client";
import { fetchPosts, fetchPostBySlug } from "../../lib/posts";
import { resolveSiteUrl, textResponse } from "../../lib/seo";
import type { PostBlock } from "../../types/post";


export const getStaticPaths: GetStaticPaths = async () => {
    const posts = await fetchPosts();
    return posts.map(p => ({ params: { slug: p.slug } }));
};


/** PostBlock[] → markdown 字串。圖片只列 alt + URL（讓 AI 知道有圖但不嵌）。 */
function _blocksToMarkdown(blocks: PostBlock[] | undefined): string {
    if (!blocks?.length) return "";
    const lines: string[] = [];
    for (const b of blocks) {
        switch (b.type) {
            case "paragraph":
                lines.push(b.text, "");
                break;
            case "heading":
                lines.push(`${b.level === 2 ? "##" : "###"} ${b.text}`, "");
                break;
            case "image":
                if (b.alt) lines.push(`![${b.alt}](${b.src})`);
                else lines.push(`![](${b.src})`);
                if (b.caption) lines.push(`*${b.caption}*`);
                lines.push("");
                break;
            case "video":
                lines.push(`[影片：https://www.youtube.com/watch?v=${b.youtube_id}]`);
                if (b.caption) lines.push(`*${b.caption}*`);
                lines.push("");
                break;
            case "quote":
                lines.push(`> ${b.text}`);
                if (b.author) lines.push(`> — ${b.author}`);
                lines.push("");
                break;
            case "list": {
                const marker = b.ordered ? "1." : "-";
                for (const it of b.items) lines.push(`${marker} ${it}`);
                lines.push("");
                break;
            }
        }
    }
    return lines.join("\n");
}


export const GET: APIRoute = async ({ params, site }) => {
    const slug = params.slug as string;
    const [meta, post] = await Promise.all([fetchMeta(), fetchPostBySlug(slug)]);
    if (!post) return new Response("Not found", { status: 404 });

    const siteUrl = resolveSiteUrl(site);
    const author = post.author_name || meta.company_name_zh;
    const cat = post.category_label_zh ? ` · ${post.category_label_zh}` : "";
    const dateLine = post.published_at
        ? new Date(post.published_at).toISOString().slice(0, 10)
        : "";
    const readTime = post.read_time_min ? ` · ${post.read_time_min} 分鐘閱讀` : "";

    const lines: string[] = [
        `# ${post.title}`,
        "",
        `> 作者：${author}${cat} · 發布：${dateLine}${readTime}`,
        "",
    ];
    if (post.excerpt) {
        lines.push(`> ${post.excerpt}`, "");
    }
    lines.push(_blocksToMarkdown(post.body));
    lines.push(
        "---",
        `[完整頁面 →](${siteUrl}/news/${post.slug})`,
        `來源：${meta.company_name_zh}（${siteUrl}）`,
    );

    return textResponse(lines.join("\n"), "text/markdown");
};
