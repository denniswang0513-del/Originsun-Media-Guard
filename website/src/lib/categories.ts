/**
 * categories.ts — 從 src/content/categories.json 讀取動態分類清單
 *
 * 資料來源：notion_service.py 同步時撈 Notion「分類」multi_select schema 的
 * options 寫入。Notion 加新分類 → 同步 → 官網自動有新分類（含中英對照、顏色、
 * 文章計數）。
 *
 * 自動加入 id="all" 的 pseudo-category 給篩選器當「全部」按鈕。
 */
import fs from "node:fs/promises";
import path from "node:path";
import type { IPostCategoryOption } from "../types/post";

const CATEGORIES_JSON = path.join(process.cwd(), "src", "content", "categories.json");

const ALL_OPTION: IPostCategoryOption = {
    id: "all", name: "All", label_zh: "全部", label_en: "All",
};

let _cache: IPostCategoryOption[] | null = null;
let _cacheMtime = -1;

// 同 posts.ts：mtime-based cache invalidation 讓 dev server 抓得到 sync 後的新內容
async function _loadAll(): Promise<IPostCategoryOption[]> {
    try {
        const stat = await fs.stat(CATEGORIES_JSON);
        if (_cache !== null && stat.mtimeMs === _cacheMtime) return _cache;
        const raw = await fs.readFile(CATEGORIES_JSON, "utf-8");
        const arr = JSON.parse(raw) as IPostCategoryOption[];
        _cache = [ALL_OPTION, ...arr];
        _cacheMtime = stat.mtimeMs;
    } catch {
        _cache = [ALL_OPTION];
        _cacheMtime = 0;
    }
    return _cache;
}

export async function fetchCategories(): Promise<IPostCategoryOption[]> {
    return _loadAll();
}
