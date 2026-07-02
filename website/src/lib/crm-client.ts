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
import type { IFAQ, ITestimonial, IQuickFact, IAward } from "../types/seo";
import type { IInitiativeCard } from "../types/initiative";


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


// SSG build 期間 /works/[slug].astro + /works/[slug].md.ts 各自 fetch 同一個 slug
// = 200 works × 2 = 400 HTTP；memoize 砍半到 200。失敗不 cache 讓重試有機會。
const _slugCache = new Map<string, Promise<IPublicProjectDetail | null>>();

export function fetchWorkBySlug(slug: string): Promise<IPublicProjectDetail | null> {
    const cached = _slugCache.get(slug);
    if (cached) return cached;
    const p = _get<IPublicProjectDetail>(`/api/website/works/${slug}`).catch(e => {
        console.warn(`[crm-client] fetchWorkBySlug(${slug}) 失敗:`, (e as Error).message);
        _slugCache.delete(slug);
        return null;
    });
    _slugCache.set(slug, p);
    return p;
}

/** 共用 getStaticPaths 來源：works/[slug].astro + .md.ts 都用這個 */
export async function getWorkSlugPaths(): Promise<{ params: { slug: string } }[]> {
    const items = await fetchAllWorksCached();
    return items.filter(w => w.slug).map(w => ({ params: { slug: w.slug } }));
}


export async function fetchFeatured(limit = 6): Promise<IPublicProject[]> {
    // Build-critical: 用 _get 而非 _safeGet,fetch error 會 throw 中斷 build。
    // 避免 master 重啟期間 build 抓到空就部署 placeholder。HTTP 200 但空陣列
    // 是合法狀態(剛上線沒精選),不會 throw。
    const data = await _get<{ items: IPublicProject[] }>(
        `/api/website/featured?limit=${limit}`,
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


// 這三個被很多頁面用,build 不 memoize 會 35 頁 × 3 endpoint = 105 個 hit,
// 觸發 master /api/website/* 的 60/min rate limit → 假性 build 失敗。
export const fetchCategories = _memoizeList<ICategory>(() =>
    _safeGet<{ items: ICategory[] }>(
        "/api/website/categories", { items: [] }, "fetchCategories"));

export const fetchServices = _memoizeList<IService>(() =>
    _safeGet<{ items: IService[] }>(
        "/api/website/services", { items: [] }, "fetchServices"));

export const fetchTeam = _memoizeList<ITeamMember>(() =>
    _safeGet<{ items: ITeamMember[] }>(
        "/api/website/team", { items: [] }, "fetchTeam"));

export const fetchFaqs = _memoizeList<IFAQ>(() =>
    _safeGet<{ items: IFAQ[] }>("/api/website/faqs", { items: [] }, "fetchFaqs"));

export const fetchTestimonials = _memoizeList<ITestimonial>(() =>
    _safeGet<{ items: ITestimonial[] }>("/api/website/testimonials", { items: [] }, "fetchTestimonials"));

export const fetchQuickFacts = _memoizeList<IQuickFact>(() =>
    _safeGet<{ items: IQuickFact[] }>("/api/website/quick_facts", { items: [] }, "fetchQuickFacts"));

export const fetchAwards = _memoizeList<IAward>(() =>
    _safeGet<{ items: IAward[] }>("/api/website/awards", { items: [] }, "fetchAwards"));


// ── Blog Posts (DB-as-truth；取代舊 src/content/posts.json) ──

/** API 回傳的 post 形狀（對應 PostPublicResponse），給 lib/posts.ts 後處理 */
export interface IRawPublicPost {
    slug: string;
    title: string;
    excerpt: string | null;
    cover_url: string | null;
    category_slugs: string[];
    body: any[];
    published_at: string | null;
    date_modified: string | null;
    read_time_min: number | null;
    seo_title: string | null;
    seo_description: string | null;
    og_image_url: string | null;
    canonical_url: string | null;
    noindex: boolean;
    author_name: string | null;
    author_url: string | null;
    ai_allow_override: boolean | null;
    faqs: { q: string; a: string }[];
}

export interface IRawPostCategory {
    id: number;
    slug: string;
    label_zh: string;
    label_en: string | null;
    color: string | null;
    sort_order: number;
    visible: boolean;
}

export const fetchRawPosts = _memoizeList<IRawPublicPost>(() =>
    _safeGet<{ items: IRawPublicPost[] }>("/api/website/posts", { items: [] }, "fetchRawPosts"));

export const fetchRawPostCategories = _memoizeList<IRawPostCategory>(() =>
    _safeGet<{ items: IRawPostCategory[] }>("/api/website/post_categories", { items: [] }, "fetchRawPostCategories"));


// ── 公益合作 / 創作計畫 案例（後端已解析作品連動）──

export const fetchImpactInitiatives = _memoizeList<IInitiativeCard>(() =>
    _safeGet<{ items: IInitiativeCard[] }>("/api/website/initiatives?line=impact", { items: [] }, "fetchImpact"));

export const fetchLabInitiatives = _memoizeList<IInitiativeCard>(() =>
    _safeGet<{ items: IInitiativeCard[] }>("/api/website/initiatives?line=lab", { items: [] }, "fetchLab"));


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
