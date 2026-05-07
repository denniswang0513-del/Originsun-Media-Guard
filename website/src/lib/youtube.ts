/**
 * youtube.ts — YouTube thumbnail / embed URL 組裝
 *
 * YouTube ID 格式：11 字元 [A-Za-z0-9_-]
 * 本站 test_* 開頭的 ID 是測試假 ID，會顯示「影片無法使用」，這是正確行為。
 */

const _YT_ID_RE = /^[A-Za-z0-9_-]{11}$/;

export function isValidYouTubeId(id: string | null | undefined): boolean {
    return !!id && _YT_ID_RE.test(id);
}


/** 取得 YouTube 縮圖，優先用高畫質，無則 fallback default.jpg */
export function youtubeThumbnail(
    videoId: string | null | undefined,
    quality: "default" | "hq" | "maxres" = "maxres",
): string | null {
    if (!videoId) return null;
    const q = { default: "default", hq: "hqdefault", maxres: "maxresdefault" }[quality];
    return `https://img.youtube.com/vi/${videoId}/${q}.jpg`;
}


/** 產生 youtube-nocookie.com 的 iframe embed URL（不送 cookie，GDPR 友善） */
export function youtubeEmbedUrl(
    videoId: string | null | undefined,
    opts: {
        autoplay?: boolean;
        mute?: boolean;
        loop?: boolean;
        controls?: boolean;
        start?: number;
    } = {},
): string | null {
    if (!videoId) return null;
    const q = new URLSearchParams();
    if (opts.autoplay) q.set("autoplay", "1");
    if (opts.mute) q.set("mute", "1");
    if (opts.loop) {
        q.set("loop", "1");
        q.set("playlist", videoId); // loop 需要 playlist 參數帶自己
    }
    if (opts.controls === false) q.set("controls", "0");
    if (opts.start) q.set("start", String(opts.start));
    q.set("rel", "0");   // 結束不推薦別人的影片
    q.set("modestbranding", "1");
    const qs = q.toString();
    return `https://www.youtube-nocookie.com/embed/${videoId}${qs ? `?${qs}` : ""}`;
}


/** YouTube Watch URL（給「在 YouTube 觀看」連結用） */
export function youtubeWatchUrl(videoId: string | null | undefined): string | null {
    return videoId ? `https://www.youtube.com/watch?v=${videoId}` : null;
}


/**
 * placeholderImage() — 圖片 fallback
 * 用 picsum.photos 產 deterministic 圖（seed 基於 slug 所以同作品每次相同）。
 * 使用情境：
 *   1. youtube_id 是測試假 ID（test_vid*）→ YouTube CDN 回 404
 *   2. 管理者還沒設 youtube_id 的作品
 *   3. 縮圖載入失敗的 onerror fallback
 */
export function placeholderImage(seed: string, w = 1600, h = 900): string {
    const safe = encodeURIComponent(seed || "originsun");
    return `https://picsum.photos/seed/${safe}/${w}/${h}`;
}


/**
 * resolveThumbnail() — 解析最終要顯示的圖片 URL
 * 真 YouTube ID → 用 maxresdefault；test_/空值 → picsum placeholder
 */
export function resolveThumbnail(
    videoId: string | null | undefined,
    seed: string,
    quality: "maxres" | "hq" | "default" = "maxres",
): string {
    if (videoId && !videoId.startsWith("test_") && isValidYouTubeId(videoId)) {
        return youtubeThumbnail(videoId, quality)!;
    }
    const dims = quality === "maxres" ? [1600, 900]
                : quality === "hq" ? [800, 450]
                : [320, 180];
    return placeholderImage(seed, dims[0], dims[1]);
}


/**
 * resolveThumbnailAsync() — 同 resolveThumbnail 但 build-time HEAD 檢查 maxresdefault
 * 是否存在(YouTube 對沒上 HD 的舊片不發 maxres,直接 404)。404 → 自動 fallback 到 hq。
 *
 * 為什麼:Astro `<Image>` build 時會 fetch 圖片做 sharp 處理,maxresdefault 404 會直接
 * 中斷 build。homepage hero / featured grid 用 maxres 畫質,在 frontmatter 用這個。
 *
 * 一次 HEAD ~50ms,9 張平行 < 200ms,不影響 build 速度。
 */
const _maxresAvailableCache = new Map<string, Promise<boolean>>();

async function _hasMaxresThumb(videoId: string): Promise<boolean> {
    const cached = _maxresAvailableCache.get(videoId);
    if (cached) return cached;
    const p = (async () => {
        try {
            const r = await fetch(
                `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`,
                { method: "HEAD", signal: AbortSignal.timeout(5000) },
            );
            return r.ok;
        } catch {
            return false;
        }
    })();
    _maxresAvailableCache.set(videoId, p);
    return p;
}

export async function resolveThumbnailAsync(
    videoId: string | null | undefined,
    seed: string,
    quality: "maxres" | "hq" | "default" = "maxres",
): Promise<string> {
    if (quality === "maxres" && videoId && !videoId.startsWith("test_")
        && isValidYouTubeId(videoId) && !(await _hasMaxresThumb(videoId))) {
        return youtubeThumbnail(videoId, "hq")!;
    }
    return resolveThumbnail(videoId, seed, quality);
}
