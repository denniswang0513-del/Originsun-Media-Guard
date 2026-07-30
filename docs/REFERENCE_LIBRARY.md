# 參考影片庫 v2 — 每支片一個小頁面（規劃，2026-07-30）

> 狀態：**規劃中，尚未實作**（owner：「先規劃就好」）
> 前身：[`PROPOSAL_PLANNER.md`](PROPOSAL_PLANNER.md) §9.8 已讓提案內的參考影片可加/可改/可移除。
> 這份是把「一列清單項」升級成「一個可研究的小頁面」+ 跨專案共用。

## 0. 需求（owner 原話，2026-07-30）

> 「我希望參考影片他自己可以是一個小頁面，裡頭可以做一些註記、截圖標示等，
> 也可以被其他專案引用」

並附上團隊過去在 Notion 用的 **🛠️ 參考影片資料庫**（`提案與開發` 底下）一筆實例
（靖娟基金會〈神奇的OK繃〉），要求「依據這個架構來優化」。

## 1. 現況（2026-07-30，v2.4.33）

| 面向 | 現在有什麼 |
|---|---|
| DB | `preprod_references`（url / title / note / tags / thumb_url / created_at）＋ `preprod_proposal_refs`（提案 ↔ 片 M:N） |
| 端點 | `GET/POST/PUT/DELETE /api/v1/proposals/references[/{rid}]`、`POST/DELETE /{pid}/refs`、公開 `POST/PATCH/DELETE /shared/{token}/refs` |
| UI | ① 提案庫 overlay 的「參考片單」卡（掛上/解除/入庫）② `/proposal-plan.html` 基本資料側欄可編清單（標題/備註/加入/移除） |
| 掛載點 | **只有提案**（`grep PreprodReference` 全 repo 僅 `api_proposals.py` + `models.py`）|

缺口：沒有詳情頁、沒有截圖、沒有研究欄、沒有結構化分類（類別/品牌/技巧…）、
沒有跨專案引用、沒有片庫總覽與篩選、沒有反向連結（這支片被誰用過）。

## 2. Notion 既有架構（實抓，2026-07-30）

### 2.1 資料庫屬性（15 欄）

| Notion 欄位 | 型別 | 既有選項樣本 | 本系統對策 |
|---|---|---|---|
| 項目 | title | — | 沿用 `title` |
| 連結 | url | YouTube 為主 | 沿用 `url`（`parse_video_url` 已支援 YT/Vimeo/FB）|
| 類別 | multi_select | 商業廣告/影視服務/活動紀錄/動態設計/企業形象/劇情短片/劇情長片/紀實短片/紀錄長片/宣傳影片/音樂MV/短影音/節目/VR | `facets.category[]` |
| 品牌 | multi_select | IKEA、老虎證券、台灣啤酒、SUBARU、7-11、國家兩廳院… | `facets.brand[]` |
| 製作單位 | select | 簡訊設計、Bito Studio、Tom Speers、張藝謀… | `facets.studio`（單選）|
| 典範 | multi_select | 魏斯安德森、羅景壬、龍希武、沈可尚、楊力州… | `facets.paragon[]` |
| 技巧 | multi_select | 驚奇再驚奇、用堆疊製造意外、平行剪接、分割畫面、多事件剪輯、反轉、對決 | `facets.technique[]` |
| 情感取向 | multi_select | （目前空）| `facets.emotion[]` |
| 關鍵字 | multi_select | 匹配剪輯、多場景、前後對比、老人、汽車、旅遊、人情、在地… | `facets.keyword[]`（＝現有 `tags` 升級）|
| 內部專案 | multi_select | 剪輯研究/動畫分享/活動紀錄研究/企業形象影片研究/BTS研究/創意研究 | `facets.study[]`（研究用途，非 CRM 專案）|
| 專案 | relation | → 提案頁 | **引用表**（見 §6）|
| 說明 | text | — | `description`（長文）|
| 備註 | text | 「OK BONE案例以具體物件(商品)帶出概念,訊息明確且簡潔」| 沿用 `note`（一句話心得）|
| 建檔完成 | checkbox | — | `curated`（建檔完成旗標，總覽可篩「待建檔」）|
| 編輯時間 | last_edited_time | — | 新增 `updated_at` |

⚠ 「內部專案」在 Notion 是研究用途標籤（剪輯研究/BTS研究…），**不是** CRM 專案關聯 ——
別把它跟 §6 的引用搞混（Notion 的關聯欄叫「專案」）。

### 2.2 頁面內容架構（`創意研究蒐集模板`）

```
### 影片        ← embed 播放器
### 截圖        ← 兩欄 Pic 01 / Pic 02（圖片）
### 研究        ← 四欄表格（可多列）
    ┌ 核心目的（客戶想要什麼？）
    ├ 創意idea（影片概念）& 手法（怎麼說？）
    ├ 創意素材（如何達成概念的元素）
    └ 關鍵橋段（亮眼畫面或情節）
```

**這張四欄表就是這個工具的方法論骨架** —— 和企劃矩陣同一種「固定提問格」思路，
所以實作直接沿用 `plan-matrix.js` 的逐格 PATCH + 樂觀鎖 + 自動儲存模式，不另發明。

## 3. 資料模型（建議）

### 3.1 `preprod_references` 擴充（現有表加欄，全部 nullable，OTA 遷移照 `main.py` _crm_cols）

| 新欄 | 型別 | 說明 |
|---|---|---|
| `description` | TEXT | Notion「說明」，長文 |
| `facets` | JSONB | `{category:[], brand:[], studio:"", paragon:[], technique:[], emotion:[], keyword:[], study:[]}` |
| `research` | JSONB | 研究四欄（逐格 `{answer, updated_at, updated_by}`，多列）|
| `curated` | BOOLEAN | 建檔完成 |
| `provider` / `video_id` | VARCHAR | `parse_video_url()` 解析結果快取（省前端重算）|
| `updated_at` | TIMESTAMPTZ | 編輯時間 |

`tags` 保留不動（既有資料在裡面），前端把它當 `facets.keyword` 的來源合併顯示；
或階段 1 一次性搬進 `facets.keyword` 後 `tags` 停寫（建議後者，少一個雙寫）。

**facets 用單一 JSONB 而非 8 個欄位**：選項本身會長（品牌/典範/技巧都是持續累積），
欄位化每加一族就要 migration + schema + 前端三處同步。JSONB + `GIN index`
（`CREATE INDEX ... USING gin (facets jsonb_path_ops)`）可直接做「含某標籤」篩選。
選項字典不另建表 —— 從既有資料 `DISTINCT` 撈出來當 datalist 建議值（同 CRM 標籤慣例），
使用者可自由新增。

### 3.2 新表 `preprod_reference_shots`（截圖）

```
id             uuid4 hex
reference_id   → preprod_references.id（index）
image_url      圖床網址（api_paste 圖床，見 §4）
timecode       VARCHAR(16)   影片時間碼，如 "0:42"（可空）
caption        VARCHAR(255)  這張要看什麼
annotations    JSONB         標示圖形（見 §4.2），可再編輯
sort_order     INTEGER
created_at / created_by
```

### 3.3 新表 `preprod_reference_links`（多型引用，取代只綁提案的舊關聯）

```
id             uuid4 hex
reference_id   → preprod_references.id（index）
target_type    'proposal' | 'crm_project'（未來可加 'work'）
target_id      對應主鍵（index）
note           這個專案為什麼引用它（可空）— 同一支片在不同案子的用途不同
created_at / created_by
UNIQUE(reference_id, target_type, target_id)
```

**遷移策略**：階段 3 開始寫 `links`，同時把 `preprod_proposal_refs` 既有列
一次性搬進來（`target_type='proposal'`），舊表**保留但停寫**（回滾餘裕），
確認一版沒問題後再刪。舊端點 `POST/DELETE /{pid}/refs` 內部改寫 links，URL 不變
（提案庫與公開共編頁不用動）。

## 4. 截圖與標示

### 4.1 圖怎麼進來

| 方式 | 作法 | 備註 |
|---|---|---|
| **貼上（主力）** | `Ctrl+V` 貼剪貼簿圖 → `POST /api/v1/paste_upload` | 既有圖床（NAS `Assets_Nginx` 8082，命名空間 `paste`，10MB / 長邊 1600 / 自動 WebP）—— 完全複用，零新基建 |
| 上傳檔 | 同一端點 | 拖放多張 |
| 自動封面 | YouTube 直接組 `img.youtube.com/vi/{id}/hqdefault.jpg` 存 `thumb_url` | 零 API 呼叫；Vimeo/FB 需 oEmbed → 階段 4 再說 |

**不做**：從 YouTube 播放器自動截圖（跨域＋授權問題，技術上不可靠）。
影片看到好畫面 → 系統截圖 → 貼上，這條路最短也最符合現在的實際做法。

### 4.2 標示怎麼存

**存圖形不燒進圖**：`annotations` = `{v:1, shapes:[…]}`，每個 shape 是
`{type:'rect'|'arrow'|'text'|'pen', x,y,w,h, points:[], color, width, text}`，
座標用 **0~1 相對值**（圖片縮放不跑位）。渲染＝圖片上疊一層 SVG。

好處：隨時可改、可刪單一標示、原圖不失真、diff 友善（JSONB 一格一 PATCH）。
需要交付客戶時再由前端 canvas 壓平成 WebP 匯出（階段 4，非必要）。

工具列：框、箭頭、文字、手繪、顏色、Undo、刪除。桌機為主（觸控可用但不最佳化）。

### 4.3 版面

對齊 Notion 的 Pic 01 / Pic 02 → **兩欄縮圖牆**（窄螢幕轉單欄），點開 lightbox
（`media-log` 已有 lightbox 慣例可抄）。每張下方顯示時間碼 + 說明。

## 5. 頁面與網址

| 入口 | 形式 |
|---|---|
| 獨立網址 | `/reference.html?id=<rid>` —— 官網白底、帳號登入，比照 `media-log-workspace.html` / `proposal-plan.html` 家族 |
| 提案內 | 側欄清單每列加「開啟頁面 ↗」；或 overlay 直接掛元件 |
| CRM 專案內 | 專案詳情新增「參考影片」分頁（階段 3）|
| 片庫總覽 | `/reference.html`（不帶 id）＝ 卡片牆 + facets 篩選 + 關鍵字搜尋 + 「待建檔」篩選 |

**元件化**：詳情頁本體寫成 `frontend/tabs/proposals/reference-page.js`
（`renderReference(container, opts)`，主題化 `--rfc-*` 變數，深色/白底雙套），
獨立頁與 SPA overlay 共用同一份 —— 完全照 `plan-matrix.js` 的成功模式。

**公開共編連結（?t=）**：提案的公開連結點進參考片頁時，比照 §9.8 的分級 ——
可加截圖、可填研究、可改標題備註；**不可**刪別人的截圖、不可改 facets 分類、
不可刪片。所有守門在後端（`_PUBLIC_*` 白名單），前端只是隱藏。

## 6. 跨專案引用（owner 需求核心）

1. **掛既有片**：提案 / CRM 專案的「參考影片」區 → 可搜尋下拉（`searchableSelect`，
   ≥8 項自動搜尋的全域元件已存在）→ 挑片 → 建 link（可填「本案為什麼引用它」）。
2. **反向連結**：詳情頁顯示「被 3 個案子引用過」＋清單（提案/專案各自連結）。
   這是把片庫變成**組織記憶**的關鍵：看到一支片就知道我們哪幾次提案用它說服過客戶。
3. **移除語意不變**：解除只斷 link，片庫本體保留（現行行為，使用者已熟悉）。
4. 片庫總覽支援「只看沒被任何案子引用的」→ 清理用。

## 7. 匯入既有 Notion 資料

| 內容 | 建議路徑 |
|---|---|
| 15 欄屬性（幾百列）| **Notion 匯出 CSV → 後台匯入**（CRM 已有多個 CSV 匯入前例，欄名對映寫死一張表即可）|
| 頁面內文（截圖/研究）| CSV 帶不出來。① 重要的片人工補（研究四欄本來就要人想）② 或用 MCP 逐頁抓（**只有本機有 Notion 連線的 session 能做**，不是產品功能）|

⚠ 那個 Notion 資料庫是私有的（`app.notion.com`），現有的「貼公開 Notion 連結匯入」
（影像專欄用的 `loadPageChunk` 免 token 路線）**對它無效** —— 別假設可以重用。

## 8. 分階段（各階段獨立可上線，照慣例各自 /simplify + commit + /publish）

| 階段 | 內容 | 產出後可用 | 規模感 |
|---|---|---|---|
| **1. 詳情頁骨架** | DB 擴充（description/facets/research/curated/provider/updated_at）＋ `/reference.html?id=` ＋ 影片 embed ＋ facets 標籤編輯 ＋ **研究四欄**（逐格自動儲存）＋ 提案側欄「開啟頁面 ↗」 | 每支片有頁面可寫研究 | 中（後端 1 檔 + 新前端頁 + 元件）|
| **2. 截圖牆 + 標示** | `preprod_reference_shots` ＋ 貼上/上傳 ＋ 兩欄牆 ＋ lightbox ＋ SVG 標示編輯器 ＋ 時間碼/說明 | 可做視覺筆記 | 中大（標示編輯器是最大單塊）|
| **3. 跨專案引用** | `preprod_reference_links` ＋ 舊關聯遷移 ＋ CRM 專案「參考影片」分頁 ＋ 反向連結 ＋ 片庫總覽/篩選 | 片庫變組織記憶 | 中 |
| **4. 進階（選配）** | CSV 匯入既有資料、標示壓平匯出、Vimeo/FB 封面 oEmbed、AI 摘要建議 facets、研究四欄匯出 Markdown | — | 小～中，各自獨立 |

## 9. 要 owner 拍板的決策（實作前確認，都有預設值可直接跑）

1. **公開連結權限**（預設：可填研究、可加截圖、可改標題備註；不可刪他人截圖、不可改分類、不可刪片）
2. **研究四欄是否固定**（預設：固定四欄照 Notion，可多列；未來要換欄＝模板版本化，同企劃矩陣做法）
3. **`tags` 是否一次搬進 `facets.keyword` 後停寫**（預設：搬，避免雙寫）
4. **`preprod_proposal_refs` 是否遷進 links 表**（預設：階段 3 遷移 + 舊表停寫保留一版）
5. **片庫總覽要不要開給非提案權限的人看**（預設：`preprod_proposals` / `preprod_plan` / admin 才看得到）

## 10. 明確不做

- 不託管影片檔（只存連結）—— 片庫是索引不是儲存
- 不自動抓 YouTube 畫面當截圖（跨域/授權不可靠）
- 不做影片 OCR / 自動場景切分
- 不做 Notion 雙向同步（單向匯入一次就好，之後正本在這裡）

## 11. 風險

- **圖床依賴 NAS**：`Assets_Nginx` 離線時上傳失敗 → 沿用既有 503「圖床目錄不可達」明確提示，不要靜默吞掉
- **標示編輯器是唯一「新東西」**：其餘全是既有模式複用。若時間緊，階段 2 可先只做「截圖 + 說明 + 時間碼」，標示延後 —— 純截圖牆已經解掉大半價值
- **facets JSONB 篩選效能**：資料量到千列以上要記得建 GIN index，別用 LIKE 掃 JSON 字串
