/**
 * crm-client.ts — 封裝到 NAS website-api 的 public endpoints 呼叫
 *
 * 用法：在 Astro page 的 frontmatter（---）區塊呼叫
 * const works = await fetchWorks({ limit: 12 })
 *
 * 這些呼叫發生在 BUILD TIME（SSG）或 SSR（未來若啟用）。
 * Runtime 的只有 POST /api/website/contact，那個用 client-side fetch。
 */

import { WEBSITE_API_BASE } from "./config";
import type {
    IPublicProject,
    IPublicProjectDetail,
    IWorksListResponse,
} from "../types/project";
import type { ICategory } from "../types/category";
import type { IService } from "../types/service";
import type { ITeamMember, IWebsiteMeta } from "../types/meta";


async function _get<T>(path: string): Promise<T> {
    const url = `${WEBSITE_API_BASE}${path}`;
    const res = await fetch(url, {
        headers: { Accept: "application/json" },
    });
    if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Fetch ${path} failed (${res.status}): ${text.slice(0, 200)}`);
    }
    return res.json() as Promise<T>;
}


/** 處理 API 連不上時用的 fallback（build 不中斷，顯示空狀態） */
async function _safeGet<T>(path: string, fallback: T, label = path): Promise<T> {
    try {
        return await _get<T>(path);
    } catch (e) {
        console.warn(`[crm-client] ${label} 取用 fallback:`, (e as Error).message);
        return fallback;
    }
}


export async function fetchWorks(opts: {
    category?: string;
    page?: number;
    limit?: number;
} = {}): Promise<IWorksListResponse> {
    const q = new URLSearchParams();
    if (opts.category) q.set("category", opts.category);
    if (opts.page) q.set("page", String(opts.page));
    if (opts.limit) q.set("limit", String(opts.limit));
    const qs = q.toString();
    return _safeGet<IWorksListResponse>(
        `/api/website/works${qs ? `?${qs}` : ""}`,
        { items: [], total: 0, page: opts.page || 1, limit: opts.limit || 12 },
        "fetchWorks",
    );
}


export async function fetchWorkBySlug(slug: string): Promise<IPublicProjectDetail | null> {
    try {
        return await _get<IPublicProjectDetail>(`/api/website/works/${slug}`);
    } catch (e) {
        console.warn(`[crm-client] fetchWorkBySlug(${slug}) 失敗:`, (e as Error).message);
        return null;
    }
}


export async function fetchFeatured(limit = 6): Promise<IPublicProject[]> {
    const data = await _safeGet<{ items: IPublicProject[] }>(
        `/api/website/featured?limit=${limit}`,
        { items: [] },
        "fetchFeatured",
    );
    return data.items;
}


export async function fetchCategories(): Promise<ICategory[]> {
    const data = await _safeGet<{ items: ICategory[] }>(
        "/api/website/categories",
        { items: [] },
        "fetchCategories",
    );
    return data.items;
}


export async function fetchServices(): Promise<IService[]> {
    const data = await _safeGet<{ items: IService[] }>(
        "/api/website/services",
        { items: [] },
        "fetchServices",
    );
    return data.items;
}


export async function fetchTeam(): Promise<ITeamMember[]> {
    const data = await _safeGet<{ items: ITeamMember[] }>(
        "/api/website/team",
        { items: [] },
        "fetchTeam",
    );
    return data.items;
}


export async function fetchMeta(): Promise<IWebsiteMeta> {
    const fallback: IWebsiteMeta = {
        company_name_zh: "源日影像",
        company_name_en: "OriginsunStudio",
        tagline: "Best Story, Best Production",
        subtitle: "影像製作 | 行銷規劃",
        address: "",
        phone: "",
        email: "",
        social: {},
        seo_default_title: "源日影像 OriginsunStudio",
        seo_default_description: "Best Story, Best Production.",
        categories: [],
    };
    return _safeGet<IWebsiteMeta>("/api/website/meta", fallback, "fetchMeta");
}
