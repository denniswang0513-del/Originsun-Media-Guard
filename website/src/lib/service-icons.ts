/**
 * service-icons.ts — 服務項目圖示對照表 + helper
 *
 * 單一來源。ServicesGrid.astro（首頁）與 services.astro（總覽頁）共用。
 *
 * 若 admin 在「🧩 服務項目」Tab 設的 icon 不在此表，`iconChar()` 回預設 ◇
 * 並由呼叫方用 `warnUnknownIcons()` 在 build 時印警示。
 */

export const ICON_MAP: Record<string, string> = {
    video:    "◉",
    film:     "◆",
    camera:   "◯",
    sparkles: "✦",
    mic:      "◐",
    default:  "◇",
};

export function iconChar(key?: string | null): string {
    return ICON_MAP[key || "default"] || ICON_MAP.default;
}

/** Build-time：收集 admin 設了但不在 ICON_MAP 的 key，console.warn 提醒擴充 */
export function warnUnknownIcons(icons: (string | null | undefined)[], context = ""): void {
    const unknown = icons.filter((k): k is string => !!k && !(k in ICON_MAP));
    if (unknown.length) {
        const ctx = context ? `[${context}] ` : "";
        console.warn(
            `${ctx}未知 icon key（落回預設 ◇）：${[...new Set(unknown)].join(", ")}。` +
            `支援的 key：${Object.keys(ICON_MAP).filter(k => k !== "default").join(", ")}`,
        );
    }
}
