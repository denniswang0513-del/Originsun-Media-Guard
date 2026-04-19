/**
 * posts.ts — 專欄文章資料（目前為佔位，之後接 Notion as CMS）
 *
 * fetchPosts() / fetchPostBySlug() 的介面設計成 async，未來改接 Notion API
 * 時只換實作不動呼叫端。
 */
import type { IPost } from "../types/post";
import { placeholderImage } from "./youtube";

const _PLACEHOLDER_POSTS: IPost[] = [
    {
        slug: "interview-surprise-moment",
        title: "訪談拍攝技巧　用驚喜製造感動瞬間",
        category: "documentary",
        category_label_zh: "紀錄片",
        category_label_en: "Documentary",
        cover_url: placeholderImage("post_interview_surprise", 1200, 675),
        excerpt: "受訪者面對鏡頭常有設防反應。透過精心設計的場景與時機，讓真實情感自然流露，創造出打動人心的訪談片段。",
        published_at: "2026-04-15",
        read_time_min: 6,
        body: `訪談是紀錄片最核心的環節。受訪者面對鏡頭常會自動進入「受訪模式」——字斟句酌、語調平穩、表情收斂。但真實情感的火花，往往在意料之外的瞬間才會浮現。

## 1. 從側面切入

不要一開始就問大問題。先聊天氣、聊今天來的路上、聊他手上拿的東西，讓受訪者的身體姿態放鬆下來。

## 2. 預備一個驚喜

如果你知道受訪者的重要親友、舊物、某段歷史影片，準備好在訪談中途拿出來。那一刻的反應往往是整支片的黃金 3 秒。

## 3. 停下來等他說完

專業訪談者最容易犯的錯是急著問下一題。每個回答後空出 3 秒，讓受訪者有空間再補上他真正想說的話。`,
    },
    {
        slug: "questionnaire-touching-interview",
        title: "如何利用問卷　讓訪談影片觸動人心",
        category: "documentary",
        category_label_zh: "紀錄片",
        category_label_en: "Documentary",
        cover_url: placeholderImage("post_questionnaire", 1200, 675),
        excerpt: "前期問卷不只是蒐集資料的工具。設計得當，它能幫你在正式拍攝前就讀懂受訪者的情感脈絡。",
        published_at: "2026-04-08",
        read_time_min: 5,
    },
    {
        slug: "documentary-sound-magic",
        title: "紀錄片聲音的魔力：讓故事鮮活的五大元素",
        category: "post-production",
        category_label_zh: "後期",
        category_label_en: "Post-production",
        cover_url: placeholderImage("post_sound_magic", 1200, 675),
        excerpt: "好的聲音設計能讓紀錄片從「看到」變成「身歷其境」。本文拆解現場音、氛圍音、音效、配樂與無聲 5 大元素。",
        published_at: "2026-04-01",
        read_time_min: 8,
    },
    {
        slug: "editing-rhythm-first-minute",
        title: "剪輯節奏　一分鐘留住觀眾的秘訣",
        category: "post-production",
        category_label_zh: "後期",
        category_label_en: "Post-production",
        cover_url: placeholderImage("post_editing_rhythm", 1200, 675),
        excerpt: "YouTube 平均觀看時長僅 52%。前 60 秒的剪輯節奏決定了大多數觀眾會不會繼續看下去。",
        published_at: "2026-03-25",
        read_time_min: 7,
    },
    {
        slug: "xyz-brand-project-review",
        title: "案件回顧：XYZ 品牌年度形象片的拍攝挑戰",
        category: "project-review",
        category_label_zh: "案件回顧",
        category_label_en: "Project Review",
        cover_url: placeholderImage("post_xyz_review", 1200, 675),
        excerpt: "從腳本發想到最終交付，4 個月的製作期我們踩過哪些坑、學到了什麼，完整覆盤給您參考。",
        published_at: "2026-03-18",
        read_time_min: 10,
    },
    {
        slug: "production-workflow-optimization",
        title: "影像製作工作流程優化：從小團隊到中型專案",
        category: "workflow",
        category_label_zh: "工作流程",
        category_label_en: "Workflow",
        cover_url: placeholderImage("post_workflow", 1200, 675),
        excerpt: "當專案規模從 3 人擴展到 15 人，流程該怎麼升級？分享我們內部使用的工具鏈與溝通節奏。",
        published_at: "2026-03-10",
        read_time_min: 6,
    },
];


export async function fetchPosts(): Promise<IPost[]> {
    // TODO(M-E-5.2): 接 Notion API — 從 admin 設的 notion.database_id 撈
    return _PLACEHOLDER_POSTS;
}


export async function fetchPostBySlug(slug: string): Promise<IPost | null> {
    const posts = await fetchPosts();
    return posts.find(p => p.slug === slug) || null;
}
