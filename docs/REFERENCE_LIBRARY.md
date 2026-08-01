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

---

## 實作紀錄

### 階段 1 已完成（2026-07-30，commit `cd66d3f`）

`routers/api_references.py`（新 router，`/api/v1/references`）+ `frontend/reference.html`
（片庫總覽 + 詳情，登入/公開共用一條路徑）+ `reference-page.js` 可換膚元件（`--rfc-*`）。
提案側欄與 SPA 卡片加「研究頁 ↗」入口。

與規劃的差異（刻意）：
- `provider/video_id` 改「**讀時推導 + 寫時同步**」—— 建片的三條路都在 api_proposals，
  只在新 router 填等於所有既有與新建的片都播不了；讀時推導讓全部既有列免遷移即可播。
- 公開放行範圍集中成一張 `_PUBLIC_ALLOW`（kind × 欄位），並與 api_proposals 共用
  `assert_public_ref_writable()`（被別的提案共用的片，免登入連結不給改）。
- 清單走 `defer(research)`；facet 篩選在 Python 但**不下 SQL limit**（否則只在最新 N 筆裡找）。

### 階段 2 已完成（2026-07-30）

`preprod_reference_shots` 表 + 截圖端點（登入 4 / 公開 3）+ `annotate.js` 標示編輯器
（框／箭頭／文字／手繪、顏色線寬、復原、Esc/Ctrl+Z）+ 兩欄截圖牆（時間碼角標、
說明自動儲存、← → 排序、刪除）。

與規劃的差異（刻意）：
- 圖**不走 NAS paste 圖床**，落地 `uploads/references/{rid}/`（照 deck 上傳慣例）：
  這頁只由 master serve，放 NAS 換不到可用性，只多一個離線失敗點。
- 上傳端點收**多檔**（`List[UploadFile]`，比照 api_locations 照片牆）—— 一次 auth／
  一次上限檢查／一次 commit，前端不用寫進度與中斷樣板。
- 公開刪圖的憑證是瀏覽器產的不可見 `guest_key`（localStorage），**不是** created_by 署名 ——
  署名會印在截圖卡上（就在刪除鈕旁），拿它當憑證等於把鑰匙貼在門上。舊列無 key 時退回比署名。
- 標示有總量上限（單張 64KB / 200 shapes，超過回 413 不靜默截斷）；手繪點位移 < 0.004 不記點。

踩過並留下守衛的坑：
- **重畫後重綁監聽 → 同一張圖上傳 N 份**：`_paintShots` 只換牆內節點，但工具列/拖放/貼上
  在牆外。現在拆成 `_wireShotUploads`（整頁 render 才綁一次，paste 用 AbortController 收）
  與 `_wireShotItems`（牆內走事件委派）。
- 截圖說明/時間碼**必須**走委派：牆每次重畫都換新節點，逐顆綁會漏（實測整段沒存到）。

### 階段 3 已完成（2026-07-30）

`preprod_reference_links` 多型引用表（proposal / crm_project）+ startup 一次性冪等搬遷
（舊 `preprod_proposal_refs` 標記 LEGACY 停寫，保留一版當回滾餘裕）。
**所有讀寫都改走 links**，舊 URL 一律不變。CRM 專案詳情新增「參考影片」分頁
（掛片／本案用途備註自動儲存／解除／跳研究頁），研究頁的「被引用」卡同時列提案與專案並可解除，
片庫總覽加「只看沒被引用的」。

與規劃的差異（刻意）：
- **權限依「引用對象」分別把關**，不是給一張通行證：`_check_target_auth(request, target_type)`
  —— proposal 要 preprod 模組、crm_project 要 crm_projects 模組。片庫本身的**讀取**放寬到
  三者任一（CRM 分頁要挑片）。`DELETE /links/{id}` 先讀出那筆掛在什麼上再把關，
  否則只有 CRM 權限的人拿一個 link id 就能解掉提案的引用。
- `unused=1` 用 SQL `NOT EXISTS` 下推 —— 在 Python 端過濾會變成「只看最新 N 筆裡的孤兒」，
  而清理要找的正是舊的那些。
- 搬遷失敗**印警告不靜默**：讀取端已全改 links，搬遷失敗＝每個提案的參考片清單憑空變空，
  而且與「使用者自己移除」無法區分。

### 階段 4 已完成（2026-07-30）

選配包全做：**CSV 匯入**（Notion 匯出檔，以 url 去重、只新增不更新）、**抓封面**
（Vimeo/FB oEmbed）、**AI 建議分類**（claude CLI，只回建議由人按下才套用）、
**複製 Markdown**（研究四欄 + 分類 + 截圖清單，貼進簡報/會議記錄）、
**標示壓平下載**（標示編輯器工具列「下載」，交付客戶用）。

與規劃的差異（刻意）：
- **AI 閘門問「這台有沒有 claude CLI」（`_resolve_claude_exe`），不是 `is_master_machine()`**
  —— 後者是排程用的「機隊只跑一次」閘，會把裝了 CLI 的 dev 機也擋掉。三個互動式 AI 端點
  現在同一個慣例。
- CSV 匯入**只新增不更新**：匯入是一次性搬家，之後正本在這裡；允許覆蓋的話，第二次匯入
  會把大家在系統裡寫的研究/分類洗回 Notion 舊值。UI 的完成訊息明講這件事。
- 分類值清洗（去空白/截長/去重/上限）收斂成 `_clean_facet_values`，PATCH／CSV 匯入／
  AI 建議三處共用 —— 原本三份已經分岔（只有 PATCH 會去重）。
- 抓封面**不鎖著資料列做對外網路 I/O**：先短查拿 url → 出去打 3 秒 → 再開第二個 session 寫入。
  oEmbed 的 host 用 `urlparse` 判定（子字串比對會把任意網址送去 vimeo API），回應讀取設上限。
- AI prompt 用 f-string 直組（原本 `.replace()` 串接有順序耦合：標題含 `{url}` 會被二次展開）。

## 四階段全部完成 — 現況總表

| 能力 | 入口 |
|---|---|
| 片庫總覽（搜尋／分類篩選／建檔狀態／只看沒被引用的／CSV 匯入）| `/reference.html` |
| 單片研究頁（影片嵌入・八族分類・研究四欄・截圖標示・被引用）| `/reference.html?id=` |
| 提案內掛片／改備註／開研究頁 | 提案庫 overlay、`/proposal-plan.html` 基本資料側欄 |
| CRM 專案引用片單（本案用途備註）| 專案詳情「參考影片」分頁 |
| 免登入共編（填研究・貼截圖・標示・改標題備註）| 提案共編連結 `?t=` → 研究頁 |

### 收尾（2026-07-30）

- **抽 `frontend/js/shared/autosave.js`**：「停手 800ms 或離開欄位就送、沒改過不送」的機制
  原本被抄了好幾份且開始分岔（有的有 change flush、有的沒 dirty-check）。共用模組提供
  `autosave(el, send)`（單一欄位）、`autosaveDelegated(host, selector, send)`（會重畫的清單 ——
  逐顆綁會漏、重畫時重綁會疊加，兩個坑都踩過）、`syncBaseline()`。
  本次改用它的是截圖說明/時間碼與 CRM 引用備註；**企劃矩陣與提案基本資料頁沿用舊寫法**
  （那是已上生產的功能，不在這條線的 diff 裡，之後有動到再一起收）。
- CRM 專案分頁掛上/解除後**不再重抓整個片庫**（最多 500 支、每支帶 note/description/facets），
  只重抓「已引用」那份，片庫清單沿用第一次的結果。
- 上生產前檢查搬遷完整性（dev 實測）：舊表列數＝新表 proposal 列數、沒有漏搬、沒有重複、
  沒有指向不存在片子/提案的孤兒 link。⚠ dev 樣本只有 1 列，生產列數較多但走同一條
  冪等 `NOT EXISTS` SQL。

### 修正：Vimeo 未公開影片播不了（2026-07-30，v2.4.34 後）

owner 回報研究頁的 Vimeo 影片顯示「抱歉，我們遇到了一點麻煩」。根因在
`services/website/video_utils.py`：**未公開（unlisted）影片的網址帶一段私密雜湊**
（`vimeo.com/960087402/71d3a3c405`），舊 pattern 只抓數字 id 把雜湊丟掉，
嵌入時少了 `?h=` → Vimeo 播放器拒播。

- pattern 改成同時吃 `vimeo.com/<id>/<雜湊>`、`player.vimeo.com/video/<id>?h=<雜湊>`，
  雜湊只放進 `embed_url`（**不另開欄位** —— 呼叫端一律用 embed_url）。
- `ref_dict` 開始回 `embed_url`（即時從 url 推導，不需要新欄位或資料遷移），
  `reference-page.js` 改用它嵌入 —— **前端不要自己拿 video_id 拼**，那正是漏掉雜湊的原因。
- 這條修正同時修好**對外官網**：`website/src` 的作品頁本來就吃 `video_embed_url`，
  之前未公開的 Vimeo 作品同樣播不了。

### 追加：SPA 側欄「🎬 片庫」tab（2026-07-30）

owner：「希望這裡可以新增片庫的 tab」（前期製作群組，器材庫下面）。

`frontend/tabs/references/`（html + js）：卡片牆（搜尋／分類篩選／建檔狀態／
只看沒被引用的／匯入 CSV）→ 點卡在同一個 tab 內開研究頁，內容直接掛
`reference-page.js` 元件（**元件深色是預設值**，SPA 不用覆寫；官網白底頁才掛
`html.ref-theme-light`）。右上角保留「獨立網址開啟 ↗」給要分享的情境。

**新增了模組 key `references`**（tab 與模組 key 是一對一，不能共用既有 key），
所以照慣例三處同步：`core/auth.py` ALL_MODULES（append 尾端）、
`tab-config.js`（TAB_MAP / TAB_LOADERS / TAB_GROUPS / PERMISSION_GROUPS）、
`user-mgmt.js` MODULE_LABELS，外加 `index.html` 的 section 容器。
**後端閘門同步放寬**收 `references` —— 否則只給片庫權限的人看得到 tab 卻拿不到資料。

### 追加：CRM 專案分頁可直接新增與就地播放（2026-07-30）

owner：「這裡要可以新增 然後可以直接播」。

- **`POST /api/v1/references`（v2 建檔入口）**：body 可帶 `link {target_type, target_id, note}`
  一次完成「入庫＋掛上本案」。**以 url 去重（idempotent）**：同網址已在庫回既有那支 ——
  片庫是共用資產，兩個實體會讓研究/截圖散在兩處。有 link 時權限依對象把關。
- CRM 分頁多一列「貼新片網址 → ＋入庫並引用」（Enter 也可送）；挑既有片的下拉照舊。
- **就地播放**：縮圖有 ▶ 遮罩，點了在列內展開 16:9 播放器（`embed_url` + autoplay，
  未公開 Vimeo 的 ?h= 雜湊自然帶著）；再點收起；同時只開一支。

---

## 12. 影片封存規劃（NAS 建檔 + 自動下載 + 五張示意圖）— 2026-07-31，尚未實作

> owner 決策：**要自動化執行**（非手動逐支按）、**yt-dlp 失效要自動更新自救**、
> **管理頁面要能填 NAS 資料夾**。下載平台影片的條款風險 owner 已知情
>（自動化＝接受）；工具面仍保留每支片可「排除封存」的開關。

### 12.1 目標

1. 片庫每支片在 NAS 指定資料夾建檔：`{資料夾}/{片id}/` 內含影片檔、五張示意圖、
   `info.json`（標題/網址/分類/研究快照）——**資料夾本身人類可讀**，脫離系統也是完整檔案庫。
2. 影片自動下載（yt-dlp）：連結失效時仍可播（研究頁 fallback `<video>`）。
3. 每支自動抽 **5 張等距截圖**進既有截圖牆（繼承標示/時間碼/說明）。

### 12.2 架構

**封存 runner**（`services/reference_archiver.py`，第五個 runner）：
- 掛進 `core/scheduler.py` 既有 tick（比照 intel/social runner），
  **gate = `is_master_machine()`**（排程性質，正確用法；NAS SMB 與 yt-dlp 只在 master）。
- 每 tick 撈 `archive_status IN ('pending','retry')` 的片，**一次一支、支間 sleep**
  （節流：預設每小時上限 6 支，YouTube 反爬敏感，寧慢勿封 IP）。
- 新片建檔（POST /references / CSV 匯入 / 提案端入庫）一律自動標 `pending`；
  啟用當下把既有片全部 backfill 成 pending（會花數天慢慢消化，正常）。
- 流程：yt-dlp 下載最佳 ≤720p → ffmpeg 轉 H.264 mp4（統一容器，瀏覽器直播）
  → ffmpeg 抽 5 張等距 JPEG → `save_webp_or_none` 進 `preprod_reference_shots`
  （`created_by='系統封存'`、`created_key='auto-archive'` — 重跑時先刪同 key 舊圖，冪等）
  → 寫 `info.json` → 更新 DB。

**yt-dlp 用獨立執行檔不用 pip 套件**（關鍵決策）：
- 生產 8000 跑 `python_embed`，pip 依賴要手動裝＋記 requirements_server.txt（既有坑）；
  單檔 `yt-dlp.exe` 完全繞開，且**內建自我更新**（`yt-dlp.exe -U`）。
- 首次使用自動從 GitHub releases 下載到 `tools/yt-dlp.exe`（不進 git、不進 OTA —
  比照 ffmpeg.exe 是 INSTALL_EXTRA，二進制不得進 OTA ZIP 的鐵則）。

**失效自動自救（owner 要求的核心）— 失敗分類三路**：

| 失敗型 | 判定（yt-dlp stderr） | 處置 |
|---|---|---|
| 影片已死 | `Private video` / `removed` / `unavailable` | 標 `unavailable`，不重試（每月復查一次） |
| 抽取器壞了 | `Unable to extract` / `signature` / HTTP 403 樣式 | **跑 `yt-dlp.exe -U` 自我更新 → 立即重試一次**；仍敗 → `retry`（隔日再試），連敗 3 天 → 告警 |
| 暫時性 | timeout / 網路 | `retry` 退避（1h→4h→隔日） |

另每週日固定跑一次 `-U`（不等壞掉才更新）。連續 3 天有片卡在 retry → 走既有
`alert_webhook` 告警（Google Chat），不靜默。

**播放 fallback**：`ref_dict` 帶 `archive_url`；前端 embed `onerror`（或手動切換鈕）
→ `<video>` 播本地封存檔。**由 master serve（內網限定），刻意不放 Assets_Nginx 公網**
—— 下載回來的平台影片放公開網址是自找的版權風險；代價是 master 關機時無 fallback，可接受。
serving 需支援 HTTP Range（影片拖進度條），Starlette StaticFiles 原生支援。

### 12.3 DB 與設定

`preprod_references` 加欄（nullable，照 _crm_cols 慣例）：
`archive_status`（NULL/pending/downloading/done/unavailable/retry/excluded）、
`archive_path`、`archive_error`、`archived_at`、`archive_tries`。

settings `reference_archive`：`enabled`（**預設 False**，dev 防呆同 social runner）、
`dir`（NAS UNC，建議預設 `\192.168.1.132\Container\AI_Workspace\Originsun_Web\ReferenceArchive`）、
`max_height`（720）、`per_hour`（6）。

### 12.4 管理介面（owner 要求）

片庫 tab（SPA + `/reference.html` 總覽）加一張 **「封存設定」卡（admin 限定）**：
- **NAS 資料夾路徑輸入欄**（寫 settings，存前 master 端驗證資料夾可寫，不可寫回明確錯誤）
- 啟用開關、畫質/每小時上限
- 狀態總覽：已封存 N / 待處理 N / 重試中 N / 已失效 N / 告警中
- 每支片的研究頁顯示封存狀態徽章 + 「排除封存」開關（excluded）+「立即重試」

### 12.5 分階段（各自 /simplify + commit）

1. **封存管線**：DB 欄位 + tools/yt-dlp 自舉 + runner（下載/轉檔/五圖/info.json）+ 節流
2. **自救與告警**：失敗分類、`-U` 自救、退避重試、週更、Chat 告警
3. **介面**：封存設定卡（含資料夾輸入）、狀態徽章、fallback 播放、排除/重試

### 12.6 風險備忘

- yt-dlp 與 YouTube 是軍備競賽：**自我更新大幅降低但不能歸零**維護成本；告警是底線。
- FB 影片成功率低 → 誠實標 `unavailable(來源不支援)`，不無限重試。
- 磁碟：720p 每支約 50–300MB；狀態卡顯示總用量，超過 settings 上限（預設 200GB）暫停並告警。
- runner 絕不進轉檔佇列（不能卡同事備份）；與報表 job 同款獨立 asyncio。

### 12.7 呈現方式定案（2026-07-31，owner 校正）

- 平台播放器永遠第一順位，封存檔只在**原連結失效**（每月復查標記）或**手動切換**時出面；
  跨網域 iframe 偵測不到內部錯誤 → 自動切換靠後端復查標記，非前端猜測。
- **用詞：「已建檔」不是「已封存」**（owner 指定）。⚠ 與既有 curated 旗標的
  「已建檔/待建檔」pill 撞名 → **curated pill 改名「研究完成/研究中」**
  （語意本來就是研究四欄寫完了；三處要同步：卡片牆 pill、總覽篩選下拉、研究頁勾選框文案）。
- **截圖時間碼一律用網頁文字呈現，不壓在圖片上**（owner 指定）：拿掉縮圖角落的
  `.tc` overlay 角標，時間碼顯示在圖下方那排（與說明同列的文字），lightbox/標示編輯器
  的標題列也帶。此規則同時適用五張自動截圖與手動貼的截圖（既有 UI 一併調整）。
- 公開共編（?t=）不提供封存檔播放（版權，只給登入內部成員）。
- 五張自動截圖：時間碼等距（片長/6 取 1~5 段點）、排在手動截圖之後、標「系統封存」。
