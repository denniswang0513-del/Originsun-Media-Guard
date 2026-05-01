/**
 * credits.ts — 演職員 block 結構正規化
 *
 * 對外作品的 credits 欄位過渡期容忍三種格式：
 *   1. 新 block list `[{role_id, name_zh, name_en, entries: [...]}]`
 *   2. 舊 flat array `[{role, name, resume_url}]`（v1.10.118 以前）
 *   3. 舊 dict `{role: name | name[]}`（更早期 public_credits 格式）
 *
 * `_MIGRATE_FLAT_CREDITS_*` 跑完後 DB 都是 block list，但前端仍保留三格式偵測
 * 1-2 版 release 後可砍掉舊兩種 fallback。
 */
import type { ICreditBlock, ICreditEntry } from "../types/project";

export function normalizeCredits(raw: unknown): ICreditBlock[] {
    if (Array.isArray(raw) && raw.length > 0) {
        const first = raw[0] as Record<string, unknown> | null;
        if (first && typeof first === "object" && "entries" in first) {
            return raw as ICreditBlock[];
        }
        // flat array `[{role, name}]` → 單一 block
        const entries: ICreditEntry[] = (raw as any[])
            .filter(c => c && c.name)
            .map(c => ({
                duty: c.role || "",
                name: String(c.name),
                resume_url: c.resume_url || "",
            }));
        return entries.length
            ? [{ role_id: null, name_zh: "", name_en: "", entries }]
            : [];
    }
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        // 舊 dict `{role: name}` → 單一 block
        const entries: ICreditEntry[] = Object.entries(raw as Record<string, unknown>).map(([role, name]) => ({
            duty: role,
            name: Array.isArray(name) ? name.join(" · ") : String(name),
        }));
        return entries.length
            ? [{ role_id: null, name_zh: "", name_en: "", entries }]
            : [];
    }
    return [];
}
