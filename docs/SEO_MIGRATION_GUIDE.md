# 文章遷移到 originsun-studio.com — SEO 完整手冊

> 適用情境：把舊文章（同域改 URL、或從另一個網域）搬到目前的部落格系統，
> 希望舊連結的 Google 排名權重轉移到新 URL，不掉。

## TL;DR

| 情境 | 你要做的事 | 系統會自動做的事 |
|---|---|---|
| **同域 slug 變動** | admin Tab 編輯文章 → SEO 區「舊網址轉址」貼舊路徑 → 儲存 | 軟 301（Astro 靜態 redirect 頁）+ 硬 301（NAS nginx）雙保險 |
| **跨域遷移**（如 `old.originsun.com.tw` → `originsun-studio.com`） | 上面 4 步＋舊網域 nginx 加 301 + Google Search Console 設「Change of Address」 | 同上 |

---

## 1. 同域 SEO 權重保留（90% 情況用這個）

### 何時觸發

- 文章 slug 改了（如 `/news/old-name` → `/news/11`）
- 文章換分類路徑（如 `/blog/...` → `/news/...`）
- 文章從 root path 進到 sub-path

### 操作步驟

1. admin Tab → **📝 部落格** → 點要編的文章 → **編輯**
2. 滾到 SEO 區塊 **🔗 舊網址轉址（SEO 301）** 折疊面板
3. 一行貼一個舊路徑：
   ```
   /blog/2020/photographer-tips
   /old-news/45
   /lessons/how-to-shoot
   ```
   - 自動 strip 域名（`https://originsun-studio.com/foo` → `/foo`）
   - 自動 strip 結尾 `/`
   - 衝突偵測：如果其他文章已用同一舊 URL，會跳警告
4. 點 **💾 儲存**
5. 60 秒後系統自動：
   - **軟 301**：Astro build 為每個舊 URL 生成 redirect 頁（meta refresh + canonical + noindex）
   - **硬 301**：NAS nginx config 加 `location = /old/path { return 301 /new; }` 並 reload

### 為什麼 SEO 權重不掉

雙保險架構：

```
請求 /blog/2020/photographer-tips
        ↓
NAS nginx：return 301 /news/11    ← 硬 301（瀏覽器/Google 看的第一順位）
        ↓ （若 nginx config 沒同步到，這裡才會失效）
Astro 靜態頁：meta refresh + canonical + noindex   ← 軟 301 fallback
        ↓
最終到達 /news/11
```

Google 把舊 URL 排名信號（PageRank、anchor text、CTR 歷史）轉移到新 URL：
- 硬 HTTP 301 → ~95-100% 權重轉移（Google 官方數據）
- 軟 301（meta refresh） → ~95% 權重轉移（Google 公開說等同處理）

### SEO 權重連續性檢查清單

編輯 Modal 的 **🎯 SEO 健康度** widget 即時掃描，確保：

- ✓ Title 30-60 字（match SERP 顯示寬度）
- ✓ Description 60-160 字
- ✓ 已填舊網址轉址 → SEO 權重連續性
- ✓ Author 已填（E-E-A-T 個人作者比公司加分多）
- ✓ published_at 不是未來時間（避免「Page indexed but not yet served」）
- ✓ 內文有內鏈到其他文章/作品/服務（內鏈密度）

---

## 2. 跨域遷移（從另一個網域搬過來）

例如把 `old.originsun.com.tw` 的內容遷到 `originsun-studio.com`。除了上面同域流程，還要：

### Step 1：本系統設定（同上）

舊路徑寫**不含網域**：
```
✓ /blog/2020/cool          ← 對
✗ https://old.originsun.com.tw/blog/2020/cool   ← 錯（系統會 strip 但顯示時不直觀）
```

### Step 2：舊網域 nginx 加 301 重導

舊網站的 nginx server config（不是這個系統的 NAS）加：
```nginx
server {
    server_name old.originsun.com.tw;

    # 整批文章一條一條 301 → 新網域對應 URL
    location = /blog/2020/cool {
        return 301 https://originsun-studio.com/news/11;
    }
    location = /old-news/45 {
        return 301 https://originsun-studio.com/news/22;
    }
    # ... 更多 ...

    # 沒明確指定的所有路徑統一導到新網站首頁（保險）
    location / {
        return 301 https://originsun-studio.com$request_uri;
    }
}
```

reload 舊站 nginx。

### Step 3：Google Search Console「Change of Address」

GSC 有專門的整體網域搬遷工具，告訴 Google「這整個網域搬家了」。比逐 URL 301 更快觸發索引重建。

1. 兩個網域都加進 Search Console 並通過所有權驗證
2. 進**舊網域**的 console
3. 設定 → **Change of Address** → 選新網域 → 確認
4. GSC 會驗證 301 設置正確 + sitemap 健全
5. 按下 **Submit**

[🔗 開啟 Google Search Console Change of Address](https://search.google.com/search-console)

預期效果：3-6 個月內整體搬遷信號 propagate 完。

### Step 4：Bing Webmaster Tools

[Bing Webmaster Tools 的 Site Move](https://www.bing.com/webmasters) — 同等功能。
台灣 Bing 流量小但仍可順手設定。

### Step 5（選做）：直接通知主流 AI 爬蟲

部分 AI 爬蟲（GPTBot / ClaudeBot）尊重 robots.txt 的 sitemap 提示。
新網域 robots.txt 已自動含 sitemap 連結（系統處理）。

---

## 3. 驗收

### 立刻可驗

```bash
# 軟 301（Astro 靜態頁）
curl -i https://originsun-studio.com/blog/2020/cool | head -20
# 預期：200 OK + body 含 <meta http-equiv="refresh" content="0; url=/news/11">

# 硬 301（NAS nginx）
curl -I https://originsun-studio.com/blog/2020/cool
# 預期：HTTP/1.1 301 Moved Permanently + Location: /news/11
```

### admin Tab 即時看

部落格管理頁 → **🌐 SEO 移轉中心** sub-tab：
- 軟 301 計數（Astro 靜態頁數）
- 硬 301 計數（NAS nginx 條目數）
- 上次同步時間
- 點 **🔄 強制重新同步** 觸發 `sync_redirects_to_nas()`

### 1-2 週後可看

- Google Search Console → 「索引狀態」→ 舊 URL 開始出現「Page with redirect」
- 新 URL 出現舊 URL 過去常拿到的關鍵字流量
- 「網址檢查」工具直接查舊 URL → 顯示「Page is redirected」

### 1-3 個月後

- 舊 URL 退出 Google index
- 新 URL 拿到舊 URL ~95% 排名分數
- backlink 報告顯示舊 URL 的反向連結信號已轉移到新 URL

---

## 4. 常見問題

### Q：軟 301 vs 硬 301 哪個 SEO 比較好？

A：**Google 公開文件說兩者等同**處理 PageRank 轉移。
硬 301 的優勢是「快幾十毫秒」（少 1 個 round-trip），對使用者體驗略好。
本系統兩個都做，雙保險。

### Q：能不能不做「舊網址轉址」？反正 Google 會自己找到新 URL。

A：**不行**。沒 redirect → Google 把舊 URL 當 404 → 反向連結權重歸零、舊排名清空、流量斷崖式下降。

### Q：同一個舊 URL 要轉到兩個新文章怎辦？

A：技術上不行（一個 URL 只能 redirect 到一個目標）。系統會偵測衝突並警告。
做法：選一個主要目標，另一個寫進新文章內文「相關閱讀」連結。

### Q：archived 文章還能保留 SEO 權重嗎？

A：可以但下降。archived 的 URL 仍 200（不破壞反向連結），但加 `<meta noindex>`（不出現在 SERP）。
舊權重靠 internal link 慢慢分散到其他活的文章。
**不推薦** redirect archived → /news 列表（會把單篇權重稀釋掉）。

### Q：published_at 可以填過去日期嗎？

A：**可以也應該**。遷移過來的文章 published_at 應該保留**原始發布日期**，不要寫今天。
理由：Google 用文章「年齡」當權威性信號。寫今天 = Google 把它當「新文章」處理，過去的 backlink/share 信號連續性會斷。

### Q：跨域遷移的舊網域要保留多久？

A：建議**最少 1 年**，最好**永久**。
Google 索引重建 3-6 個月，但偶爾還是有人會點到舊網域連結（社群/Email/PDF/書籤）。
舊網域只要保持 nginx 301 就行，不需要 hosting 應用程式。

### Q：可以一次匯入大量舊文章嗎？

A：**可以但要手寫**。系統當前無自動爬取舊網站的功能（依設計簡化）。
做法：admin Tab「+ 新增空白文章」→ 從舊網站複製內容貼進 block 編輯器 → SEO 區塊填舊 URL → 儲存。
單篇預估 10-20 分鐘。

---

## 5. 監測工具

| 工具 | 用途 | 頻率 |
|---|---|---|
| [Google Search Console](https://search.google.com/search-console) | 索引狀態、Change of Address、查單一 URL 重定向結果 | 每週 |
| [Bing Webmaster Tools](https://www.bing.com/webmasters) | 同上但 Bing | 每月 |
| [Google Rich Results Test](https://search.google.com/test/rich-results) | 驗證新 URL 的 schema.org JSON-LD（NewsArticle 等） | 每篇上線後 |
| `curl -I <old_url>` | 快速確認 301 + Location header | 部署後即時 |
| 部落格管理頁「SEO 移轉中心」 | 軟+硬 301 計數 + 上次同步時間 | 改完設定隨時 |

---

## 6. 工程細節（給維護者）

- `WebsitePost.old_urls` JSONB 陣列存舊路徑
- `services.website.post_service.list_redirects()` 聚合所有 published+archived post 的 old_urls → 新 URL map
- `routers.website.public.list_redirects` 公開 endpoint（給 Astro build + master sync 拉）
- `website/integrations/build-redirects.mjs` Astro build init 時 fetch → `astro.config.mjs` `redirects` field → 軟 301 靜態頁
- `publish_update.sync_redirects_to_nas()` master 端 SSH 進 NAS → 寫 `/etc/nginx/snippets/redirects.conf` → `nginx -s reload` → 硬 301
- `docker/nginx/originsun.conf` `include /etc/nginx/snippets/*.conf` 載入 redirect snippet

詳見 [`CLAUDE.md`](../CLAUDE.md) 規則 G + [`docs/NEW_PAGE_CHECKLIST.md`](NEW_PAGE_CHECKLIST.md)。
