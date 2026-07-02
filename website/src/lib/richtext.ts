/**
 * richtext.ts — 內文 inline markdown → HTML。
 *
 * notion_service 把 paragraph/quote/list/FAQ 內的 inline 連結存成 markdown `[text](url)`。
 * 這個 helper 還原成 <a>。content 是可信來源，但仍 HTML-escape 文字與 URL，避免奇怪字元
 * 意外破壞 markup。給 PostBody.astro（內文）與 news/[slug].astro（FAQ 答案）共用。
 */
const _ESC = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const _MD_LINK = /\[([^\]]+)\]\(([^)\s]+)\)/g;

export function renderInline(text: string): string {
    if (!text) return "";
    const escaped = _ESC(text);
    return escaped.replace(_MD_LINK, (_, label, href) =>
        `<a href="${_ESC(href)}" target="_blank" rel="noopener noreferrer" class="text-[#c9372c] underline hover:text-[#a02a23]">${label}</a>`,
    );
}
