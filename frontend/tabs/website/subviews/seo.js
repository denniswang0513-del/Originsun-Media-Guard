/**
 * seo.js — SEO / AI SEO 管理（設計稿：後端尚未接，先展示 UI 結構）
 *
 * 7 個 card 區塊，由上到下：
 *   1. 索引開關 + 基本 Meta
 *   2. Quick Facts（AI SEO 核心）
 *   3. FAQ 管理
 *   4. Testimonials 管理
 *   5. llms.txt 編輯器
 *   6. 服務 SEO 描述 指引（連到 services Tab）
 *   7. SEO 健康分數
 *
 * 正式串接後端前，所有表單的儲存按鈕會彈 toast「尚未串接後端」。
 * Mock data 寫死在此，等 API 上線時換成 websiteFetch 撈真實資料。
 */
import { esc, toastOk, toastErr } from '../website-utils.js';

// ===== Mock Data（將來從 API 撈取） =====
const MOCK = {
    meta: {
        noindex: true,           // 目前預設 true（開發中）
        default_title: "源日影像 OriginsunStudio",
        default_description: "源日影像 OriginsunStudio — 10+ 年影像製作經驗，完成 300+ 支商業廣告、紀實短片、活動紀錄、動畫設計。位於台北中山，專業導演團隊與一條龍製作流程。",
        og_image_url: "",
    },
    quickFacts: [
        { label: "成立年份", value: "2014" },
        { label: "地點", value: "台北市中山區" },
        { label: "團隊規模", value: "10+ 人" },
        { label: "完成專案", value: "300+ 件" },
        { label: "合作品牌", value: "80+" },
        { label: "客戶評分", value: "4.9 / 5（25 則）" },
        { label: "代表獎項", value: "4A 銀獎 2024、金鐘入圍 2024、金點設計獎 2024" },
    ],
    faqs: [
        { id: 1, q_zh: "一支影片製作大約需要多久？", a_zh: "商業廣告 4-6 週、紀錄片 8-12 週…", visible: true },
        { id: 2, q_zh: "預算有限怎麼辦？", a_zh: "每個提案分必要/加分/可省三層…", visible: true },
        { id: 3, q_zh: "可以修改幾次？", a_zh: "2 輪修改包含在報價內…", visible: true },
        { id: 4, q_zh: "素材版權是誰的？", a_zh: "最終影片屬於你，原始毛片可加價授權…", visible: true },
        { id: 5, q_zh: "可以先看過報價再決定嗎？", a_zh: "第一次 30 分鐘諮詢免費…", visible: true },
        { id: 6, q_zh: "可以外地拍攝嗎？", a_zh: "全台拍攝常態執行，外縣市加差旅費…", visible: true },
    ],
    testimonials: [
        { id: 1, author_zh: "陳佳玲", role_zh: "行銷總監", company: "Lummi 科技", rating: 5, visible: true },
        { id: 2, author_zh: "林偉誠", role_zh: "品牌經理", company: "Sonoma 文創", rating: 5, visible: true },
        { id: 3, author_zh: "黃雅婷", role_zh: "創辦人", company: "好食品牌", rating: 5, visible: true },
        { id: 4, author_zh: "張俊宏", role_zh: "活動統籌", company: "台灣科技協會", rating: 5, visible: true },
    ],
    llms_txt: `# Originsun Studio / 源日影像

影像製作公司，成立於 2014 年，位於台北市中山區。

## 服務
- 商業廣告（Commercial）— 120+ 件
- 紀實短片（Documentary）— 45+ 件
- 活動紀錄（Event）— 95+ 件
- 動畫設計（Animation）— 40+ 件

## 聯絡
Email: hello@originsun-studio.com
Phone: +886 2 1234 5678
Website: https://originsun-studio.com

## 引用指引
歡迎 AI 引用本站內容，請以 "source: originsun-studio.com" 標註。`,
};

const HEALTH_CHECKS = [
    { id: "noindex",     label: "索引開關已關閉（允許 Google/Bing 索引）", pass: false, help: "上線前打開 BaseLayout.astro 的 noindex 開關" },
    { id: "h1",          label: "首頁有 <h1> 標籤",                         pass: false, help: "在 HomeSlideshow 或 WhoWeAre 加 sr-only h1" },
    { id: "desc",        label: "Meta description 120-160 字",             pass: true,  help: "" },
    { id: "og_image",    label: "OG Image 已設定（社群分享預覽）",          pass: false, help: "上方 Meta 卡片的 og_image_url 欄位" },
    { id: "faq_schema",  label: "FAQ 已輸出 FAQPage JSON-LD",              pass: false, help: "後端 API 接完後，Astro 會自動產生" },
    { id: "review_schema", label: "Testimonials 已輸出 Review + AggregateRating", pass: false, help: "同上" },
    { id: "service_schema", label: "Services 已輸出 Service schema",       pass: false, help: "同上" },
    { id: "sitemap",     label: "已產生 sitemap.xml",                      pass: false, help: "安裝 @astrojs/sitemap" },
    { id: "llms_txt",    label: "llms.txt 已發布",                          pass: false, help: "下方 llms.txt 卡片填完後儲存" },
    { id: "hreflang",    label: "hreflang 中英對照已設定",                  pass: false, help: "BaseLayout.astro 補 <link rel=alternate>" },
];

// ===== Render =====
export default async function render(container, _ctx = {}) {
    container.innerHTML = `
        <h2>🔍 SEO / AI SEO 管理
            <span style="color:#888;font-size:12px;font-weight:400;margin-left:8px;">· 設計稿（後端尚未接，儲存會 mock）</span>
        </h2>

        <div style="display:grid;grid-template-columns:1fr;gap:16px;max-width:1100px;">
            ${_cardMeta()}
            ${_cardQuickFacts()}
            ${_cardFAQ()}
            ${_cardTestimonials()}
            ${_cardServicesHint()}
            ${_cardLlmsTxt()}
            ${_cardHealth()}
        </div>
    `;

    _bindCountEvents();
}

// ===== 1. 索引開關 + 基本 Meta =====
function _cardMeta() {
    const m = MOCK.meta;
    const descLen = m.default_description.length;
    return `
        <div class="card" style="border-left:3px solid #10b981;">
            <h3 style="color:#fff;margin:0 0 12px;font-size:15px;">1️⃣ 索引開關 + 基本 Meta</h3>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:12px;">
                <div>
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">搜尋引擎索引</label>
                    <label style="display:inline-flex;align-items:center;gap:8px;cursor:pointer;color:#ddd;">
                        <input type="checkbox" id="seo-noindex" ${m.noindex ? "checked" : ""} style="width:16px;height:16px;" />
                        <span>noindex（封鎖索引，開發期使用）</span>
                    </label>
                    <div style="color:#${m.noindex ? "f59e0b" : "4ade80"};font-size:11px;margin-top:4px;">
                        ${m.noindex ? "⚠ 目前封鎖索引，上線前請關閉" : "✓ 已允許索引"}
                    </div>
                </div>
                <div>
                    <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">OG Image URL（社群分享預覽圖）</label>
                    <input id="seo-og-image" type="text" placeholder="https://originsun-studio.com/og.jpg" value="${esc(m.og_image_url)}" style="width:100%;" />
                </div>
            </div>

            <div style="margin-bottom:12px;">
                <label style="color:#888;font-size:11px;display:block;margin-bottom:6px;">預設 Title</label>
                <input id="seo-title" type="text" value="${esc(m.default_title)}" style="width:100%;" />
            </div>

            <div>
                <label style="color:#888;font-size:11px;display:flex;justify-content:space-between;margin-bottom:6px;">
                    <span>預設 Description（建議 120-160 字）</span>
                    <span id="seo-desc-count" style="color:${descLen >= 120 && descLen <= 160 ? "#4ade80" : "#f59e0b"};">${descLen} 字</span>
                </label>
                <textarea id="seo-desc" rows="3" style="width:100%;">${esc(m.default_description)}</textarea>
            </div>

            <div style="margin-top:12px;">
                <button class="btn" onclick="window._seoSaveMeta()">💾 儲存 Meta</button>
            </div>
        </div>
    `;
}

// ===== 2. Quick Facts =====
function _cardQuickFacts() {
    const rows = MOCK.quickFacts.map((f, i) => `
        <tr data-fact-idx="${i}">
            <td><input type="text" value="${esc(f.label)}" data-fact-field="label" style="width:100%;" /></td>
            <td><input type="text" value="${esc(f.value)}" data-fact-field="value" style="width:100%;" /></td>
            <td style="text-align:right;"><button class="btn btn-ghost btn-sm" onclick="window._seoRemoveFact(${i})">✕</button></td>
        </tr>
    `).join('');
    return `
        <div class="card" style="border-left:3px solid #8b5cf6;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">2️⃣ Quick Facts
                <span style="color:#888;font-size:11px;font-weight:400;">· AI 搜尋最愛撿的條列事實</span>
            </h3>
            <table>
                <thead><tr><th style="width:30%;">標籤</th><th>內容</th><th style="width:60px;"></th></tr></thead>
                <tbody id="seo-facts-tbody">${rows}</tbody>
            </table>
            <div style="margin-top:12px;display:flex;gap:8px;">
                <button class="btn btn-sm" onclick="window._seoAddFact()">➕ 新增一列</button>
                <button class="btn" onclick="window._seoSaveFacts()">💾 儲存 Quick Facts</button>
            </div>
        </div>
    `;
}

// ===== 3. FAQ 管理 =====
function _cardFAQ() {
    const rows = MOCK.faqs.map(f => `
        <tr>
            <td style="width:32px;color:#666;">/${String(f.id).padStart(2, "0")}</td>
            <td>${esc(f.q_zh)}</td>
            <td style="color:#888;font-size:12px;">${esc(f.a_zh.slice(0, 40))}${f.a_zh.length > 40 ? "…" : ""}</td>
            <td style="width:80px;text-align:center;">
                <span class="website-pill ${f.visible ? "status-converted" : ""}">${f.visible ? "顯示" : "隱藏"}</span>
            </td>
            <td style="width:120px;text-align:right;">
                <button class="btn btn-sm btn-ghost" onclick="window._seoEditFAQ(${f.id})">編輯</button>
                <button class="btn btn-sm btn-danger" onclick="window._seoDeleteFAQ(${f.id})">刪</button>
            </td>
        </tr>
    `).join('');
    return `
        <div class="card" style="border-left:3px solid #3b82f6;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">3️⃣ FAQ 管理
                <span style="color:#888;font-size:11px;font-weight:400;">· ${MOCK.faqs.length} 題，將輸出 FAQPage JSON-LD</span>
            </h3>
            <table>
                <thead><tr><th>#</th><th>問題（中）</th><th>答案摘要</th><th>狀態</th><th></th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:12px;">
                <button class="btn btn-sm" onclick="window._seoAddFAQ()">➕ 新增 FAQ</button>
                <span style="color:#666;font-size:11px;margin-left:12px;">點「編輯」可調 zh/en 雙語 + 排序</span>
            </div>
        </div>
    `;
}

// ===== 4. Testimonials 管理 =====
function _cardTestimonials() {
    const rows = MOCK.testimonials.map(t => `
        <tr>
            <td>${esc(t.author_zh)}</td>
            <td style="color:#888;font-size:12px;">${esc(t.role_zh)} · ${esc(t.company)}</td>
            <td style="color:#c9372c;">${"★".repeat(t.rating)}${"☆".repeat(5 - t.rating)}</td>
            <td style="width:80px;text-align:center;">
                <span class="website-pill ${t.visible ? "status-converted" : ""}">${t.visible ? "顯示" : "隱藏"}</span>
            </td>
            <td style="width:120px;text-align:right;">
                <button class="btn btn-sm btn-ghost" onclick="window._seoEditTestimonial(${t.id})">編輯</button>
                <button class="btn btn-sm btn-danger" onclick="window._seoDeleteTestimonial(${t.id})">刪</button>
            </td>
        </tr>
    `).join('');
    return `
        <div class="card" style="border-left:3px solid #f59e0b;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">4️⃣ Testimonials 管理
                <span style="color:#888;font-size:11px;font-weight:400;">· ${MOCK.testimonials.length} 則，將輸出 Review + AggregateRating schema</span>
            </h3>
            <table>
                <thead><tr><th>客戶</th><th>職稱・公司</th><th>評分</th><th>狀態</th><th></th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:12px;">
                <button class="btn btn-sm" onclick="window._seoAddTestimonial()">➕ 新增證言</button>
                <span style="color:#666;font-size:11px;margin-left:12px;">
                    總評分：<strong style="color:#c9372c;">★ 4.9</strong>（自動計算）
                </span>
            </div>
        </div>
    `;
}

// ===== 5. Services SEO（跳轉提示） =====
function _cardServicesHint() {
    return `
        <div class="card" style="border-left:3px solid #ec4899;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">5️⃣ 服務項目 SEO 描述</h3>
            <p style="color:#aaa;font-size:13px;line-height:1.7;margin:0 0 10px;">
                服務項目的 long description 與關鍵字請到
                <a href="#" onclick="window.websiteSwitchSubview('services');return false;" style="color:#3b82f6;">🧩 服務項目</a>
                Tab 編輯。本頁的 SEO 健康分數會讀取該 Tab 設定。
            </p>
            <button class="btn btn-sm btn-ghost" onclick="window.websiteSwitchSubview('services')">前往服務項目 Tab →</button>
        </div>
    `;
}

// ===== 6. llms.txt 編輯器 =====
function _cardLlmsTxt() {
    const len = MOCK.llms_txt.length;
    return `
        <div class="card" style="border-left:3px solid #06b6d4;">
            <h3 style="color:#fff;margin:0 0 8px;font-size:15px;">6️⃣ llms.txt 編輯器
                <span style="color:#888;font-size:11px;font-weight:400;">· 2024 新 AI SEO 標準，放在 /llms.txt</span>
            </h3>
            <textarea id="seo-llms-txt" rows="12" style="width:100%;font-family:monospace;font-size:12px;">${esc(MOCK.llms_txt)}</textarea>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
                <span style="color:#666;font-size:11px;">${len} 字元</span>
                <div style="display:flex;gap:8px;">
                    <button class="btn btn-sm btn-ghost" onclick="window._seoLlmsTemplate()">📝 套用範本</button>
                    <button class="btn btn-sm btn-ghost" onclick="window._seoLlmsPreview()">👁 預覽 /llms.txt</button>
                    <button class="btn" onclick="window._seoSaveLlms()">💾 儲存</button>
                </div>
            </div>
        </div>
    `;
}

// ===== 7. SEO 健康分數 =====
function _cardHealth() {
    const passed = HEALTH_CHECKS.filter(c => c.pass).length;
    const total = HEALTH_CHECKS.length;
    const pct = Math.round((passed / total) * 100);
    const color = pct >= 80 ? "#4ade80" : pct >= 50 ? "#f59e0b" : "#f87171";
    const rows = HEALTH_CHECKS.map(c => `
        <li style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #2a2a2a;">
            <span style="color:${c.pass ? "#4ade80" : "#f87171"};font-size:16px;width:20px;flex-shrink:0;">${c.pass ? "✓" : "✗"}</span>
            <div style="flex:1;">
                <div style="color:#ddd;font-size:13px;">${esc(c.label)}</div>
                ${!c.pass && c.help ? `<div style="color:#888;font-size:11px;margin-top:2px;">→ ${esc(c.help)}</div>` : ""}
            </div>
        </li>
    `).join('');
    return `
        <div class="card" style="border-left:3px solid ${color};">
            <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;">
                <h3 style="color:#fff;margin:0;font-size:15px;">7️⃣ SEO 健康分數</h3>
                <div style="color:${color};font-size:28px;font-weight:700;">${pct}<span style="font-size:14px;">%</span>
                    <span style="color:#888;font-size:12px;font-weight:400;margin-left:6px;">${passed}/${total}</span>
                </div>
            </div>
            <ul style="list-style:none;margin:0;padding:0;">${rows}</ul>
            <div style="margin-top:12px;display:flex;gap:8px;">
                <a href="https://search.google.com/test/rich-results" target="_blank" class="btn btn-sm btn-ghost">🔗 Google Rich Results 測試</a>
                <a href="https://pagespeed.web.dev/" target="_blank" class="btn btn-sm btn-ghost">🔗 PageSpeed Insights</a>
            </div>
        </div>
    `;
}

// ===== Event Bindings（mock 版只跑 toast） =====
function _bindCountEvents() {
    const descEl = document.getElementById("seo-desc");
    const countEl = document.getElementById("seo-desc-count");
    if (descEl && countEl) {
        descEl.addEventListener("input", () => {
            const n = descEl.value.length;
            countEl.textContent = `${n} 字`;
            countEl.style.color = (n >= 120 && n <= 160) ? "#4ade80" : "#f59e0b";
        });
    }
}

const _mockSave = (what) => toastOk(`${what}（mock 儲存，後端串接後生效）`);
const _mockAction = (what) => toastOk(`${what}（設計稿，功能待實作）`);

// Meta
window._seoSaveMeta = () => _mockSave("已儲存 Meta");

// Quick Facts
window._seoAddFact = () => _mockAction("新增一列 Quick Fact");
window._seoRemoveFact = (_i) => _mockAction("移除 Quick Fact");
window._seoSaveFacts = () => _mockSave("已儲存 Quick Facts");

// FAQ
window._seoAddFAQ = () => _mockAction("新增 FAQ（將開 Modal）");
window._seoEditFAQ = (_id) => _mockAction("編輯 FAQ（將開 Modal）");
window._seoDeleteFAQ = (_id) => _mockAction("刪除 FAQ");

// Testimonials
window._seoAddTestimonial = () => _mockAction("新增證言（將開 Modal）");
window._seoEditTestimonial = (_id) => _mockAction("編輯證言（將開 Modal）");
window._seoDeleteTestimonial = (_id) => _mockAction("刪除證言");

// llms.txt
window._seoLlmsTemplate = () => _mockAction("套用 llms.txt 範本");
window._seoLlmsPreview = () => _mockAction("預覽 /llms.txt");
window._seoSaveLlms = () => _mockSave("已儲存 llms.txt");
