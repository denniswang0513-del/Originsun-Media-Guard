/**
 * initiative.ts — 公益合作 / 創作計畫 案例卡片
 * 資料源：/api/website/initiatives（後端已解析作品連動，回可直接渲染的卡片）
 */
export interface IInitiativeCard {
    id: number;
    title: string;
    summary?: string | null;
    cover_url?: string | null;
    link_url?: string | null;     // 連動作品 → /works/{slug}；獨立案例 → 外連；可空
    year?: number | null;
    is_work: boolean;             // 是否連動作品集的作品
}
