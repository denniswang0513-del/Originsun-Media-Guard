/**
 * awardLabels.ts — 榮譽牆「作品類型」中→英固定對照 + 標題組字。
 *
 * work_type 是後台下拉的固定選項（劇情短片/紀錄短片/紀錄片/MV…），非自由文字，
 * 所以用固定對照表在渲染時英文化即可 —— DB 不需存英文、後台不需編。
 * work_title 是作品原名（專有名詞）→ 中英一致、不翻。
 * 獎項行本身（name_zh / name_en）才是人工逐行填的英文。
 */
export const WORK_TYPE_EN: Record<string, string> = {
    劇情短片: "Narrative Short",
    劇情長片: "Narrative Feature",
    紀錄短片: "Documentary Short",
    紀錄片: "Documentary",
    動畫: "Animation",
    動畫短片: "Animation Short",
    實驗片: "Experimental",
    實驗短片: "Experimental Short",
    音樂錄影帶: "Music Video",
    MV: "Music Video",
    廣告: "Commercial",
    微電影: "Short Film",
    品牌影片: "Brand Film",
    形象影片: "Brand Film",
    活動紀錄: "Event Coverage",
};

export function workTypeEn(zh: string | null | undefined): string {
    const t = (zh || "").trim();
    return WORK_TYPE_EN[t] || t;
}

/** 回中/英兩種作品標題字串（work_title 專有名詞不翻，中英同字）。 */
export function filmHeadingParts(g: { workType: string; workTitle: string }): { zh: string; en: string } {
    const title = g.workTitle ? `《${g.workTitle}》` : "—";
    return {
        zh: g.workType ? `${g.workType}${title}` : title,
        en: g.workType ? `${workTypeEn(g.workType)} ${title}` : title,
    };
}
