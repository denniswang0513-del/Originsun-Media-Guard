# 社群平台帳號準備指南（N-soc 階段二前置）

> 給 owner 的照做清單：把 FB / IG / Threads 的發文權限接進 n8n，
> 讓「核准後自動發佈」能動。**階段一（產文稿）不需要這些**，可以先跑。
> 平台後台 UI 常改版，以下寫「目標」為主；卡住時把畫面丟給 Claude 帶你走。
> 撰於 2026-07-07。

## 0. 前置盤點（5 分鐘）

- [ ] 公司 FB 粉絲專頁存在，且你的個人帳號是**管理員**（不是編輯）
- [ ] 公司 IG 帳號存在；若還是個人帳號 → IG 設定內「切換為商業帳號」（免費）
- [ ] **IG 商業帳號綁定 FB 粉專**（IG 設定 → 分享到其他應用程式 / 帳號中心連結粉專
      — 這是 IG API 發文的硬性前提）
- [ ] Threads 帳號存在（用 IG 帳號登入即有）
- [ ] NAS n8n 容器能登入（http://192.168.1.132 的 n8n port，帳密在誰手上確認一下）

## 1. Meta 開發者 App（FB + IG 共用，~20 分鐘）

1. 到 developers.facebook.com → My Apps → Create App → 類型選 **Business**
2. App 名稱隨意（例：Originsun Publisher）；**App 保持 Development mode 即可**
   —— 只對自家粉專發文不需要送 App Review（你的帳號是 app 管理員 + 粉專管理員就能用）
3. App 內 Add Product：**Facebook Login for Business** 與 **Instagram Graph API**
4. 產生 token（目標：**長效 Page Access Token**）：
   - Graph API Explorer → 選你的 app → User Token → 勾權限：
     `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
     `instagram_basic`, `instagram_content_publish`
   - Generate → 換成長效 user token（Access Token Debugger 有 Extend 按鈕）
   - 用長效 user token 打 `GET /me/accounts` → 拿到**粉專的 Page Token**
     （由長效 user token 換得的 page token 實務上不過期，除非改密碼/資安事件）
5. 記下三樣：**Page ID**、**Page Access Token**、**IG Business Account ID**
   （`GET /{page-id}?fields=instagram_business_account` 可查）

## 2. Threads API（~10 分鐘）

1. 同一個 Meta app → Add Product：**Threads API**
2. 用 Threads 帳號完成 OAuth 授權（權限 `threads_basic`, `threads_content_publish`）
3. 記下：**Threads User ID** 與 **長效 Threads Token**（60 天效期，
   n8n flow 會做自動 refresh；token 看門狗到期前也會提醒）

## 3. n8n 憑證設定（~10 分鐘）

1. 登入 NAS n8n → Credentials → 新增：
   - Facebook Graph API credential（貼 Page Access Token）
   - Threads 用 HTTP Header credential（Bearer token）
2. 匯入 workflow（階段二實作時 Claude 會給 `docs/n8n/social_publisher.json`）：
   Webhook 入口 → 平台分流 → FB `/{page-id}/feed` 或 `/photos`、
   IG 兩段式（media container → publish）、Threads 兩段式 → 回呼 master
3. 把 webhook URL 貼回後台「社群工作台 → 設定」

## 4. 驗證（跟 Claude 一起做）

- [ ] n8n 手動執行 workflow 各平台發一篇測試文（發完可刪）
- [ ] 後台核准一篇 → 黃金時段自動出現在三平台 → 佇列回寫貼文連結
- [ ] token 看門狗出現在監控（到期前 7 天會推播）

## 注意事項

- **IG 硬性要圖**且圖片 URL 必須公開可抓 — 系統會用官網上的 OG 圖，不用另傳
- IG API 上限每 24h 25 篇（我們日上限 1，遠低於）
- 所有 token 只存 n8n 憑證庫；備份 bundle 不含 n8n 資料 —— token 遺失就照本文重走一次（~30 分鐘），不是災難
- LinkedIn 公司頁要過 Marketing Developer Platform 審核，等前三個平台跑順再議
