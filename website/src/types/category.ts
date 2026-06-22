/**
 * category.ts — 作品分類 TS interface
 * 對應 core/schemas_website.py 的 CategoryPublicResponse
 */
export interface ICategory {
    slug: string;
    name_zh: string;
    name_en?: string | null;
    description?: string | null;
    count: number;
    kind?: "category" | "tag";  // 'category' 為製作類型；'tag' 為使用場景（展覽/講座…）
}
