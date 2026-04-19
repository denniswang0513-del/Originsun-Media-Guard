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
