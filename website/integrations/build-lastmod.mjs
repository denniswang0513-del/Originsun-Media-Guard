/**
 * build-lastmod.mjs — Astro build init 時抓每頁的「最後更新時間」，供 sitemap <lastmod>
 *
 * 只給「有真實時間可依據」的頁面：
 *   /works/{slug}/  → 作品的 public_published_at（後端 date_modified）
 *   /news/{slug}/   → 文章的 date_modified（無則 published_at）
 *   /、/works/、/news/ → 其子項的最新時間（這三頁的內容就是列出那些子項）
 * 其餘靜態頁（/about、/contact…）一律不給 lastmod。
 *
 * 為什麼不乾脆全站塞 build 時間：rebuild 是內容異動觸發的，但一次 rebuild 會重產
 * 所有頁面。若讓 /about 每次 rebuild 都宣稱「剛更新」，Google 會判定此站 lastmod
 * 不可信 → 整個忽略。寧可少給，不可謊報。
 *
 * API 離線時回 {} → build 不中斷，sitemap 就沒有 lastmod（無害）。
 */
import { apiGet } from "./_api.mjs";

/** ISO 字串正規化；無效值回 null（絕不讓壞資料進 sitemap）。 */
function _iso(v) {
    if (!v) return null;
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** ISO 字串的字典序 == 時序，直接取最大值即可（O(n)，不必排序）。 */
function _max(dates) {
    return dates.reduce((m, d) => (d && (!m || d > m) ? d : m), null);
}

export async function fetchLastmod() {
    try {
        // limit=500 → 一次撈全部作品（後端上限 500，目前約 170 筆）
        const [works, posts] = await Promise.all([
            apiGet("/api/website/works?limit=500"),
            apiGet("/api/website/posts"),
        ]);

        const map = {};
        const workDates = [];
        const postDates = [];

        for (const w of works?.items || []) {
            const iso = _iso(w.date_modified);
            if (!w.slug || !iso) continue;
            map[`/works/${w.slug}/`] = iso;
            workDates.push(iso);
        }

        for (const p of posts?.items || []) {
            const iso = _iso(p.date_modified) || _iso(p.published_at);
            if (!p.slug || !iso) continue;
            map[`/news/${p.slug}/`] = iso;
            postDates.push(iso);
        }

        // 索引頁 = 其子項的最新時間（子項變動時這幾頁的內容確實跟著變）
        const newestWork = _max(workDates);
        const newestPost = _max(postDates);
        if (newestWork) map["/works/"] = newestWork;
        if (newestPost) map["/news/"] = newestPost;
        const newestAny = _max([newestWork, newestPost]);
        if (newestAny) map["/"] = newestAny;

        console.log(`[build-lastmod] ${Object.keys(map).length} pages with lastmod `
            + `(${workDates.length} works, ${postDates.length} posts)`);
        return map;
    } catch (e) {
        console.warn(`[build-lastmod] fetch failed: ${e.message}, fallback {}`);
        return {};
    }
}
