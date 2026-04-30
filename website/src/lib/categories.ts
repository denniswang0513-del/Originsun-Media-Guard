/**
 * categories.ts — 從 DB API 撈部落格分類
 *
 * 資料來源：NAS website-api `/api/website/post_categories`（DB 為真，取代
 * 舊 categories.json）。前端額外加 id="all" pseudo-category 給篩選器當「全部」。
 *
 * 走 crm-client.ts 的 _memoizeList → build 期共用 1 次 HTTP。
 */
import { fetchRawPostCategories } from "./crm-client";
import type { IPostCategoryOption } from "../types/post";


const ALL_OPTION: IPostCategoryOption = {
    id: "all", name: "All", label_zh: "全部", label_en: "All",
};


export async function fetchCategories(): Promise<IPostCategoryOption[]> {
    const raw = await fetchRawPostCategories();
    return [
        ALL_OPTION,
        ...raw
            .filter(c => c.visible)
            .map(c => ({
                id: c.slug,
                name: c.label_en || c.label_zh,
                label_zh: c.label_zh,
                label_en: c.label_en || c.label_zh,
                color: c.color || undefined,
            })),
    ];
}
