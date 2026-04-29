/**
 * crm-client.ts — 封裝到 NAS website-api 的 public endpoints 呼叫
 *
 * 用法：在 Astro page 的 frontmatter（---）區塊呼叫
 * const works = await fetchWorks({ limit: 12 })
 *
 * 這些呼叫發生在 BUILD TIME（SSG）或 SSR（未來若啟用）。
 * Runtime 的只有 POST /api/website/contact，那個用 client-side fetch。
 */

import { WEBSITE_API_BASE, LIMITS } from "./config";
import type {
    IPublicProject,
    IPublicProjectDetail,
    IWorksListResponse,
} from "../types/project";
import type { ICategory } from "../types/category";
import type { IService } from "../types/service";
import type { ITeamMember, IWebsiteMeta } from "../types/meta";
import type { IFAQ, ITestimonial, IQuickFact } from "../types/seo";


async function _get<T>(path: string): Promise<T> {
    const url = `${WEBSITE_API_BASE}${path}`;
    const res = await fetch(url, {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(10000),  // 10s 一根吊死端點不拖垮整個 build
    });
    if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Fetch ${path} failed (${res.status}): ${text.slice(0, 200)}`);
    }
    return res.json() as Promise<T>;
}


/** 非關鍵資料：API 離線時用 fallback，build 不中斷但會顯示空狀態 */
async function _safeGet<T>(path: string, fallback: T, label = path): Promise<T> {
    try {
        return await _get<T>(path);
    } catch (e) {
        console.warn(`[crm-client] ${label} 取用 fallback:`, (e as Error).message);
        return fallback;
    }
}


/** 模組級 cache：build 期間一次撈全量，related / getStaticPaths 共用，避免 N+1。
 * 空結果不 cache（避免 API 短暫離線後 dev server 永遠顯示空資料）。
 */
let _worksCachePromise: Promise<IPublicProject[]> | null = null;

export function clearWorksCache(): void { _worksCachePromise = null; }

export async function fetchAllWorksCached(): Promise<IPublicProject[]> {
    if (_worksCachePromise) {
        const cached = await _worksCachePromise;
        if (cached.length > 0) return cached;
        _worksCachePromise = null;  // 空陣列不 cache，下次重試
    }
    _worksCachePromise = fetchWorks({ limit: LIMITS.BUILD_MAX_WORKS }).then(r => r.items);
    return _worksCachePromise;
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
    // API offline → empty list. Page renders the no-items empty state.
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


/**
 * 對 SSG build 期間多頁共用的 list endpoint memoize：N 頁需要 = 1 次 HTTP。
 * 空陣列不 cache（API 短暫離線後重試能拿到真資料）。
 */
function _memoizeList<T>(loader: () => Promise<{ items: T[] }>): () => Promise<T[]> {
    let cache: Promise<T[]> | null = null;
    return async () => {
        if (cache) {
            const v = await cache;
            if (v.length > 0) return v;
            cache = null;
        }
        cache = loader().then(d => d.items);
        return cache;
    };
}

export const fetchFaqs = _memoizeList<IFAQ>(() =>
    _safeGet<{ items: IFAQ[] }>("/api/website/faqs", { items: [] }, "fetchFaqs"));

export const fetchTestimonials = _memoizeList<ITestimonial>(() =>
    _safeGet<{ items: ITestimonial[] }>("/api/website/testimonials", { items: [] }, "fetchTestimonials"));

export const fetchQuickFacts = _memoizeList<IQuickFact>(() =>
    _safeGet<{ items: IQuickFact[] }>("/api/website/quick_facts", { items: [] }, "fetchQuickFacts"));


/**
 * fetchMeta() — 模組級 memoize（同 fetchAllWorksCached 模式），避免多頁重複打 API。
 * API 離線時走 fallback 但不 cache（以便後續重試成功後能切回真實資料）。
 */
let _metaCachePromise: Promise<{ meta: IWebsiteMeta; ok: boolean }> | null = null;

export function clearMetaCache(): void { _metaCachePromise = null; }

export async function fetchMeta(): Promise<IWebsiteMeta> {
    if (_metaCachePromise) {
        const cached = await _metaCachePromise;
        if (cached.ok) return cached.meta;
        _metaCachePromise = null;  // 上次抓失敗，下次重試
    }
    _metaCachePromise = _doFetchMeta();
    const { meta } = await _metaCachePromise;
    return meta;
}

async function _doFetchMeta(): Promise<{ meta: IWebsiteMeta; ok: boolean }> {
    // 離線預覽用：補齊 contact 資訊讓首頁 CTA / 關於頁看起來完整
    const fallback: IWebsiteMeta = {
        company_name_zh: "源日影像",
        company_name_en: "OriginsunStudio",
        tagline: "Best Story, Best Production",
        subtitle: "影像製作 | 行銷規劃",
        address: "台北市中山區南京東路二段 1 號 3 樓",
        phone: "+886 2 1234 5678",
        email: "hello@originsun-studio.com",
        social: {
            instagram: "https://instagram.com/originsun_studio",
            youtube: "https://youtube.com/@originsun_studio",
        },
        seo_default_title: "源日影像 OriginsunStudio",
        seo_default_description: "Best Story, Best Production.",
        categories: [],
    };
    try {
        const data = await _get<IWebsiteMeta>("/api/website/meta");
        if (!data.company_name_zh && !data.company_name_en) {
            console.error("[crm-client] fetchMeta 回空資料 — DB settings 未 seed？走 fallback");
            return { meta: fallback, ok: false };
        }
        return { meta: data, ok: true };
    } catch (e) {
        console.error(`[crm-client] fetchMeta 失敗（API 離線？）走 fallback:`, (e as Error).message);
        return { meta: fallback, ok: false };
    }
}
