/**
 * fake-data.ts — API 離線時的 fallback 假資料
 *
 * 這些資料只在 crm-client 的 _safeGet 抓 API 失敗時才會送出，
 * 正式環境 API 正常時不會影響（API 回空陣列也不走 fake，交給真實 CMS 管）。
 *
 * 等 M-F NAS 部署完成、真實資料進 DB 後，可以把這個檔案縮成只保留 icon/meta，
 * 或整個 import 刪除。
 */
import type { IPublicProject } from "../types/project";
import type { IService } from "../types/service";
import type { ICategory } from "../types/category";
import type { ITeamMember } from "../types/meta";
import { placeholderImage } from "./youtube";

/** 假作品：6 件分佈在 4 個分類 */
export const FAKE_FEATURED: IPublicProject[] = [
    {
        slug: "fake-xyz-brand-tvc-2025",
        title: "XYZ 品牌 2025 年度形象片",
        client: "XYZ 集團",
        youtube_id: null,
        description: "為 XYZ 集團打造的品牌形象片，從策略發想到最終交付歷時 4 個月。",
        year: 2025,
        categories: ["commercial"],
        thumbnail_url: placeholderImage("fake_xyz_brand_tvc", 1600, 900),
        featured: true,
    },
    {
        slug: "fake-potter-documentary-2025",
        title: "陶藝家・土與手的紀錄",
        client: "文化部",
        youtube_id: null,
        description: "跟拍台灣資深陶藝家一整年，記錄土與手交會的靜默美學。",
        year: 2025,
        categories: ["documentary"],
        thumbnail_url: placeholderImage("fake_potter_doc", 1600, 900),
        featured: true,
    },
    {
        slug: "fake-tech-summit-recap-2024",
        title: "科技高峰會年度精華",
        client: "台灣科技協會",
        youtube_id: null,
        description: "三日活動、八個主舞台、超過 60 位講者——濃縮成一支 3 分鐘的精華。",
        year: 2024,
        categories: ["event"],
        thumbnail_url: placeholderImage("fake_tech_summit", 1600, 900),
        featured: true,
    },
    {
        slug: "fake-def-motion-brand-2024",
        title: "DEF 科技品牌動畫",
        client: "DEF Tech",
        youtube_id: null,
        description: "2D / 3D 混合動畫，為 DEF 打造具識別度的品牌動態語彙。",
        year: 2024,
        categories: ["animation"],
        thumbnail_url: placeholderImage("fake_def_motion", 1600, 900),
        featured: true,
    },
    {
        slug: "fake-functional-food-product-2024",
        title: "機能食品產品形象影片",
        client: "好食品牌",
        youtube_id: null,
        description: "從產品故事到消費情境，一支打動健康族群的短片。",
        year: 2024,
        categories: ["commercial"],
        thumbnail_url: placeholderImage("fake_functional_food", 1600, 900),
        featured: false,
    },
    {
        slug: "fake-film-festival-opening-2024",
        title: "影展開幕短片",
        client: "台北影展",
        youtube_id: null,
        description: "為影展開幕設計的 90 秒視覺短片，結合光影與城市意象。",
        year: 2024,
        categories: ["event"],
        thumbnail_url: placeholderImage("fake_film_festival", 1600, 900),
        featured: false,
    },
    {
        slug: "fake-hospitality-brand-2023",
        title: "精品旅宿品牌影片",
        client: "雲山會館",
        youtube_id: null,
        description: "山景與室內設計交錯，呈現精品旅宿的慢活哲學。",
        year: 2023,
        categories: ["commercial"],
        thumbnail_url: placeholderImage("fake_hospitality_brand", 1600, 900),
        featured: false,
    },
    {
        slug: "fake-musician-portrait-2023",
        title: "音樂人紀實肖像",
        client: "獨立廠牌 Kaki",
        youtube_id: null,
        description: "跟拍獨立音樂人一年，從錄音室到巡演的真實側寫。",
        year: 2023,
        categories: ["documentary"],
        thumbnail_url: placeholderImage("fake_musician_portrait", 1600, 900),
        featured: false,
    },
    {
        slug: "fake-ngo-campaign-2023",
        title: "NGO 公益短片",
        client: "兒童福利聯盟",
        youtube_id: null,
        description: "用 3 分鐘讓社會大眾看見被忽略的孩子，募款效果翻倍。",
        year: 2023,
        categories: ["commercial"],
        thumbnail_url: placeholderImage("fake_ngo_campaign", 1600, 900),
        featured: false,
    },
];


/** 假服務：對齊 4 個主要分類 */
export const FAKE_SERVICES: IService[] = [
    { slug: "commercial",  title: "廣告行銷", icon: "megaphone",
      short_desc: "從品牌策略到鏡頭語言，打造能打動人的商業影片。",
      related_category_slug: "commercial" },
    { slug: "documentary", title: "紀實短片", icon: "camera",
      short_desc: "長時間訪談與觀察，紀錄每一段獨特的真實故事。",
      related_category_slug: "documentary" },
    { slug: "event",       title: "活動紀實", icon: "live",
      short_desc: "多機位現場紀錄 + 當日精華交付，高品質低延遲。",
      related_category_slug: "event" },
    { slug: "animation",   title: "動畫設計", icon: "motion",
      short_desc: "2D / 3D 動畫與動態設計，為品牌提供生動視覺表現。",
      related_category_slug: "animation" },
];


/** 假分類 */
export const FAKE_CATEGORIES: ICategory[] = [
    { slug: "commercial",  name_zh: "廣告行銷", name_en: "Commercial",  count: 2 },
    { slug: "documentary", name_zh: "紀實短片", name_en: "Documentary", count: 1 },
    { slug: "event",       name_zh: "活動紀實", name_en: "Event",       count: 2 },
    { slug: "animation",   name_zh: "動畫設計", name_en: "Animation",   count: 1 },
];


/** 假：公司信任數字（Hero 下方 stats 條 + About 段共用） */
export const FAKE_STATS = [
    { value: "10+",  label_zh: "年製作經驗", label_en: "Years" },
    { value: "300+", label_zh: "完成作品",   label_en: "Projects" },
    { value: "80+",  label_zh: "合作品牌",   label_en: "Brands" },
    { value: "4.9",  label_zh: "平均評分",   label_en: "Rating" },
];


/** 假：服務分類 + 專案數（取代 home ServicesGrid） */
export const FAKE_SERVICES_WITH_COUNTS = [
    { slug: "commercial",  name_zh: "廣告行銷", name_en: "Commercial",
      count: 120,  short_zh: "從品牌策略到鏡頭語言，打造能打動人的商業影片。",
      short_en: "Brand strategy to visual language — commercials that move." },
    { slug: "documentary", name_zh: "紀實短片", name_en: "Documentary",
      count: 45,   short_zh: "長時間訪談與觀察，紀錄每一段獨特的真實故事。",
      short_en: "Deep interviews and field observation — true stories." },
    { slug: "event",       name_zh: "活動紀實", name_en: "Event",
      count: 95,   short_zh: "多機位現場紀錄 + 當日精華交付，高品質低延遲。",
      short_en: "Multi-cam live coverage with same-day highlights." },
    { slug: "animation",   name_zh: "動畫設計", name_en: "Animation",
      count: 40,   short_zh: "2D / 3D 動畫與動態設計，為品牌提供生動視覺表現。",
      short_en: "2D/3D animation and motion design for brands." },
];


/** 假：創辦人（About 段） */
export const FAKE_FOUNDER = {
    name_zh: "王士源",
    name_en: "Alex Wang",
    role_zh: "創辦人 · 導演",
    role_en: "Founder · Director",
    photo_url: placeholderImage("fake_founder_alex", 800, 1000),
    years: "2014",
    quote_zh: "影像的本質是人。這 10 年我只專心做一件事——讓被拍攝的人覺得被理解，讓看影片的人覺得被打動。技術會變，這件事不會。",
    quote_en: "At its core, filmmaking is about people. For 10 years I've focused on one thing — making subjects feel understood and audiences feel moved. Techniques change, this doesn't.",
};


/** 假：客戶證言（Testimonials 段） */
export const FAKE_TESTIMONIALS = [
    { author_zh: "陳佳玲", author_en: "Carolyn Chen",
      role_zh: "行銷總監", role_en: "Marketing Director",
      company: "Lummi 科技",
      quote_zh: "源日團隊最讓我驚訝的是——我們只給他們半天的訪綱準備，他們當天就讓執行長卸下心防，講出連我都沒聽過的創業故事。",
      quote_en: "What surprised me most — we gave them only half a day of prep, yet by shoot day our CEO was telling stories even I hadn't heard.",
      avatar: placeholderImage("fake_testimonial_01", 200, 200), rating: 5 },
    { author_zh: "林偉誠", author_en: "David Lin",
      role_zh: "品牌經理", role_en: "Brand Manager",
      company: "Sonoma 文創",
      quote_zh: "從腳本到交片 28 天，中間沒有一次延遲。拍攝現場他們自己解決所有問題，我們幾乎不用操心。下次還會再合作。",
      quote_en: "Script to final cut in 28 days, zero delays. They solved everything on set — we barely had to worry. Working with them again.",
      avatar: placeholderImage("fake_testimonial_02", 200, 200), rating: 5 },
    { author_zh: "黃雅婷", author_en: "Emily Huang",
      role_zh: "創辦人", role_en: "Founder",
      company: "好食品牌",
      quote_zh: "我們是新創，預算有限但希望品質不打折。源日的提案直接告訴我們哪些錢該花、哪些可以省——這種顧問級的對話在業界很少見。",
      quote_en: "We're a startup with tight budget but high standards. Their proposal told us exactly where to invest and where to save — rare in this industry.",
      avatar: placeholderImage("fake_testimonial_03", 200, 200), rating: 5 },
    { author_zh: "張俊宏", author_en: "Brian Chang",
      role_zh: "活動統籌", role_en: "Event Producer",
      company: "台灣科技協會",
      quote_zh: "三天活動、8 個舞台，他們當天下午就能交當日精華。速度快而不粗糙，這 10 年我接觸過最專業的製作團隊。",
      quote_en: "Three-day event, eight stages, same-day highlights delivered. Fast without being rough — the most professional team I've worked with in 10 years.",
      avatar: placeholderImage("fake_testimonial_04", 200, 200), rating: 5 },
];


/** 假：FAQ（常見問題） */
export const FAKE_FAQ = [
    { q_zh: "一支影片製作大約需要多久？",
      q_en: "How long does a typical video take?",
      a_zh: "商業廣告約 4-6 週、紀錄片 8-12 週、活動紀實 72 小時內可交當日精華 + 2 週完整版。具體時程我們會在第一次諮詢後給你確切估計。",
      a_en: "Commercials take 4-6 weeks, documentaries 8-12 weeks. Event highlights delivered same-day, full edit in 2 weeks. We'll give you an exact timeline after our first call." },
    { q_zh: "預算有限怎麼辦？你們會幫我規劃嗎？",
      q_en: "What if my budget is tight? Will you help me plan?",
      a_zh: "會。我們每個提案都會分「必要」、「加分」、「可省」三層，告訴你錢花在哪最有效。預算低不代表品質低，關鍵是把錢花對地方。",
      a_en: "Yes. Every proposal breaks costs into essential / bonus / skippable, so you know where each dollar goes. Low budget doesn't mean low quality — it means smart spending." },
    { q_zh: "可以修改幾次？會額外收費嗎？",
      q_en: "How many revisions are included?",
      a_zh: "2 輪修改包含在報價內，第 3 輪起會另計。大部分專案 2 輪以內就能定稿，因為我們在拍攝前就把腳本 / 分鏡講清楚。",
      a_en: "Two rounds of revision are included. Third round and beyond are billed separately. Most projects wrap within two rounds because we lock the script and storyboard before shoot." },
    { q_zh: "素材版權是誰的？",
      q_en: "Who owns the footage?",
      a_zh: "最終影片版權屬於你。原始毛片可選擇加價授權或交付。所有音樂 / 字體 / 素材我們都採用合法授權，不會留下版權風險。",
      a_en: "You own the final video. Raw footage can be licensed or delivered for an additional fee. All music, fonts, and assets we use are properly licensed — no legal risk." },
    { q_zh: "可以先看過你們的報價再決定嗎？",
      q_en: "Can I see a quote before committing?",
      a_zh: "當然。第一次 30 分鐘諮詢 100% 免費，我們會給你一份含範圍、時程、預算的建議書，完全沒有義務要繼續合作。",
      a_en: "Of course. First 30-min consultation is free. You'll get a full scope / timeline / budget proposal with zero obligation." },
    { q_zh: "我們在其他城市，你們可以外地拍攝嗎？",
      q_en: "Do you shoot outside Taipei?",
      a_zh: "可以。全台拍攝我們常態執行，外縣市僅收一次性差旅費。若案件在境外（香港、日本、東南亞等）我們也有過經驗，歡迎詢問。",
      a_en: "Yes. We regularly shoot across Taiwan with flat-rate travel fees. Overseas projects (HK, Japan, SEA) — we've done them, reach out." },
];


/** 假團隊成員 */
export const FAKE_TEAM: ITeamMember[] = [
    { id: "fake-01", name: "王大明", role: "Creative Director",
      bio: "10 年廣告創意經驗，曾任職 Ogilvy。",
      photo_url: placeholderImage("fake_team_01", 600, 600) },
    { id: "fake-02", name: "陳小美", role: "Producer",
      bio: "統籌過百檔商業案件，掌握節奏與預算。",
      photo_url: placeholderImage("fake_team_02", 600, 600) },
    { id: "fake-03", name: "李攝影", role: "Cinematographer",
      bio: "擅長光線與構圖，為作品注入電影感。",
      photo_url: placeholderImage("fake_team_03", 600, 600) },
    { id: "fake-04", name: "林剪輯", role: "Editor / Colorist",
      bio: "剪輯與調色一條龍，精準掌握敘事節奏。",
      photo_url: placeholderImage("fake_team_04", 600, 600) },
];
