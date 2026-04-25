/**
 * posts.ts — 從 src/content/posts.json 讀取已同步的文章
 *
 * 資料來源：services/website/notion_service.py 透過管理 Tab「📝 部落格」的
 * 「同步 Notion」按鈕產生。檔案不存在時（fresh clone、未同步、build 環境隔離）
 * 回傳空陣列讓網站正常 build —— Insight 列表頁會顯示「尚無文章」。
 *
 * 兩個 entry 都同步：fetchPosts() 取全部，fetchPostBySlug(slug) 用列表查詢。
 * 一次讀檔後 module-level cache，多個 Astro page 共用。
 */
import fs from "node:fs/promises";
import path from "node:path";
import type { IPost } from "../types/post";
import { placeholderImage } from "./youtube";

const POSTS_JSON = path.join(process.cwd(), "src", "content", "posts.json");

let _cache: IPost[] | null = null;
let _cacheMtime = -1;

// mtime-based cache invalidation：dev server 模組長壽，sync 後 JSON 換新需要重讀。
// 沒這個檢查時 module-level cache 會卡住空陣列（dev 啟動時 JSON 還不存在的情況）。
async function _loadAll(): Promise<IPost[]> {
    try {
        const stat = await fs.stat(POSTS_JSON);
        if (_cache !== null && stat.mtimeMs === _cacheMtime) return _cache;
        const raw = await fs.readFile(POSTS_JSON, "utf-8");
        const arr = JSON.parse(raw) as IPost[];
        _cache = arr.map(p => ({
            ...p,
            cover_url: p.cover_url || placeholderImage(`post_${p.slug}`, 1200, 675),
        }));
        _cacheMtime = stat.mtimeMs;
    } catch {
        _cache = [];
        _cacheMtime = 0;
    }
    return _cache;
}

export async function fetchPosts(): Promise<IPost[]> {
    return _loadAll();
}

export async function fetchPostBySlug(slug: string): Promise<IPost | null> {
    const posts = await _loadAll();
    return posts.find(p => p.slug === slug) ?? null;
}
