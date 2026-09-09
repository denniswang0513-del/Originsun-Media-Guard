# 發票影像分享 `/e/{code}` —— 預覽頁 ＋ master 關機也拿得到

> owner 2026-09-10：「這個也希望可以 master 關機也拿得到，然後連結可以先像報價單那樣
> 可以有個預覽圖，以及下載按鈕」
> 狀態：**已實作（2026-09-10，只在 dev）**。拍板紀錄在 §8。
> 相關：[`docs/OFFLINE_MASTER_PLAN.md`](OFFLINE_MASTER_PLAN.md)（報價那條已完成的同型工作）

---

## 0. 一句話

比報價那條**簡單**：發票影像本來就是一個已經存在的檔案，不用生成、不用快照、不用等
master 產什麼東西。而且它就放在 NAS 上兩個容器都掛得到的地方（我實測過）。

真正要做的是兩件事：**把端點搬到對外容器**，以及**把「直接下載」換成「一頁預覽＋下載鈕」**。

---

## 1. 現況（實測，不是照文件抄）

| 項目 | 現況 |
|------|------|
| `/e/{code}` 的行為 | **直接回檔案**（`no_store_file`＋filename ＝ 瀏覽器下載），沒有頁面 |
| 端點位置 | `serve_invoice_by_share_token()` 由 `main.py` 的 `/e/` 直接呼叫；舊長網址掛 `token_router`（**master 限定**） |
| 檔案實際位置 | `invoices_root` ＝ `\\192.168.1.132\Archive\00_電子發票` |
| NAS 容器看得到嗎 | ✅ **看得到而且可寫** —— 容器內翻譯成 `/share/Archive/00_電子發票`（`/share/Archive` 兩個容器都有掛） |
| 公開區登記 | ✅ 已在 `core/public_access.PUBLIC_SURFACES` 的 `invoice_file`，前綴 `/e/` 與 `/api/v1/crm/public/invoice-file/` |
| 建立連結的入口 | 只有桌機 `crm-invoices.js:670`（`POST /invoices/{id}/share`）；**手機發票分頁沒有這個功能** |
| 檔案型別 | 上傳時副檔名沿用原檔（預設 `.pdf`），黑名單是 `core.project_folders.BLOCKED_UPLOAD_EXTS` |

**現成的先例**：`frontend/media-log.html` —— 獨立 HTML、token 從網址參數拿、純 `fetch` 打
public API、沒有 build step。這頁照抄它的骨架就好（它 697 行是因為含整套上傳牆，我們這頁小很多）。

---

## 2. 要做的兩件事

### A. 端點搬到對外容器（＝ master 關機也拿得到）

跟報價同一招：把公開那幾支從 `token_router` 移到 `public_router`（`main_website.py` 掛的就是它），
nginx 加 `/e/` 與 `/api/v1/crm/public/invoice-file/` 兩條 location。

**為什麼不做「快照」**：報價要快照是因為它本來是「客戶點開才即時算出來的」。發票影像本來
就是一個檔，放在 NAS 上，兩個容器都讀得到 —— 沒有東西需要事先生成。這條路比報價短。

### B. `/e/{code}` 從「下載」變成「一頁預覽 ＋ 下載鈕」

新增靜態頁 `frontend/invoice-file.html`（照 `media-log.html` 的形狀），配三支公開端點：

```
GET /api/v1/crm/public/invoice-file/{token}/meta      → {filename, kind, size}
GET /api/v1/crm/public/invoice-file/{token}/raw       → 檔案本體（inline，給 <img>／<embed> 用）
GET /api/v1/crm/public/invoice-file/{token}/download   → 檔案本體（attachment，下載鈕用）
```

頁面依 `kind` 決定怎麼畫：`image` → `<img>`；`pdf` → `<embed>`（手機瀏覽器對內嵌 PDF
支援不一，退路是「這個裝置不能預覽，請按下載」）；其他 → 不預覽，只給下載鈕。

### C. 頁上顯示發票資訊（owner 2026-09-10 追加）

> 「我如果改成顯示發票資訊呢，然後做良好的排版」

這把那頁的性質換掉了：從「一個檔的下載點」變成**一份寄給外面的人的憑證頁**。可以做，
但要先處理下面 §3.5、§3.6 兩件事 —— 不是排版問題，是「哪些欄位能出現」與「顯示的數字
會不會跟他下載的那張對不起來」。

原本的註解寫著「不回發票的其他欄位 —— 順手多給金額／統編／客戶名等於把不必要的東西一起
寄出去」。那個顧慮**沒有作廢，只是講得太粗**：金額、統編、抬頭本來就印在他手上那張發票上，
給他看不算外洩；真正不能上的是**只有我們系統裡才知道的東西**。新的規則見 §3.5。

---

## 3. 🔴 要先決定的幾件事（不是實作細節，是設計決定）

### 3.1 路徑翻譯 —— 不處理的話在 NAS 上一律 404

`serve_invoice_by_share_token()` 現在拿 DB 裡的 `file_url`（**master 視角的 UNC**）直接
`os.path.isfile()`，而且白名單比對也是拿未翻譯的 root：

```python
if not os.path.abspath(path).startswith(os.path.abspath(_invoices_root())):
```

在 NAS 容器上這兩個都要先過 `core.drive_map.to_local_path`。而且 **白名單比對要在翻譯之後**
做 —— 翻譯前比對等於拿兩個不同視角的字串比，可能誤放行、也可能把好的擋掉。

（報價那條沒踩到這個坑，是因為它走 `core.assets_host`，那支自己會翻譯。）

### 3.2 🔴 inline 預覽是**這個需求新引進的**攻擊面

現在檔案是以 attachment 送出的 —— 瀏覽器只會下載，不會執行它。改成 inline 預覽之後就不同了：

- `BLOCKED_UPLOAD_EXTS` **沒有擋** `.svg`、`.html`、`.htm`
- 這條連結跑在 `www.originsun-studio.com` 上，是**寄給客戶與會計師**的
- 一個 SVG 裡可以放 `<script>` —— inline 打開就是官網網域上的儲存型 XSS

上傳者是內部同仁不是匿名，所以這不是「隨時會被打」的洞；但它會從「不可能」變成「可能」，
而且是我們自己動手造成的。三件都做，成本都很低：

1. **只對確定安全的型別 inline**：`.pdf` `.jpg` `.jpeg` `.png` `.webp`。其他一律不預覽，只給下載鈕。
2. **Content-Type 由白名單決定，不靠副檔名猜**；`/raw` 與 `/download` 都不吃使用者給的型別。
3. **`.svg` `.html` `.htm` 加進 `BLOCKED_UPLOAD_EXTS`**（同時保護提案資產夾那條路 —— 它共用同一份黑名單）。

nginx 那側 `X-Content-Type-Options: nosniff` 已經在 server 層有了，別在新 location 裡放
`add_header`（會把整組安全標頭吃掉，那個坑檔頭有寫）。

### 3.3 路由順序 —— `meta` 會被當成 token 吃掉

舊的長網址是 `/public/invoice-file/{token}`，新的是 `/public/invoice-file/{token}/meta`。
FastAPI 先註冊先贏，`{token}` 那條若排在前面，`/{token}/meta` 仍然對得上它自己的形狀所以沒事，
但**要確認**新增的三支排在舊那支之前或路徑不重疊。`routers/crm/__init__.py` 檔頭已經寫著
「import 順序＝註冊順序，不可重排」。

### 3.4 舊連結不要變行為

已經寄給客戶／會計師的 `/e/{code}` 現在是「點了就下載」。改成頁面之後他們會拿到一頁 ——
**這是 owner 要的**，不是回歸。但舊的長網址 `/api/v1/crm/public/invoice-file/{token}`
（v1 的 JWT 版）維持「直接下載」語意，別一起改，那條的存在意義就是不要動它。

### 3.5 🔴 哪些欄位能上那頁 —— 一條可執行的規則

**只顯示「那張發票上本來就印著的東西」。任何只有我們系統裡才知道的，一律不上。**

收件人手上就有那張證明聯，所以把上面已經有的欄位排版出來，不增加他知道的事；
而系統裡另外那半邊是我們的帳務與內部流程，跟他無關。

`CrmInvoice` 26 個欄位分類：

| 可以顯示（發票上本來就有） | 🔴 絕對不能上（只有系統裡知道） |
|---|---|
| `invoice_number` 發票號碼 | `commission` **代開費** —— 我們跟開票方之間的事 |
| `invoice_date` 開立日期 | `category` 專案／**內部代開** —— 四個字直接揭露這是代開的 |
| `company_name` 抬頭（就是他自己） | `payment_status` 未收款／已收款 —— **我們的催收狀態** |
| `tax_id` 統編（同上） | `paid_date` 收款日 |
| `amount_ex_tax` 未稅 | `entity` 母帳／私帳 —— 內部帳務結構 |
| `tax_amount` 稅額 | `applicant` 申請人 —— 我方同事姓名 |
| `amount_total` 含稅總額 | `project_id` / `project_ids` 內部案號 |
| `item_type` 品項 | `notes` 內部備註 —— 什麼都可能寫在裡面 |
| `invoice_kind` 紙本／電子 | `id` / `share_token` / `file_url` / timestamps |

三個要個別決定的：

- **`title` 名稱（案件／項目）**：欄位註解寫「案件/項目」。如果它就是印在發票品名欄的字，
  可以上；如果實務上有人拿它記內部案名，就不能上。**要先看幾筆真實資料再決定。**
- **`recipient` / `recipient_phone` / `recipient_address`**（紙本收件資訊）：那是**對方自己的**
  聯絡資料，給他看沒問題 —— 但連結一被轉發就等於外洩了對方的地址電話。**建議不放**（他不需要）。
- **`issue_status` 作廢**：這個對收件人**有意義**（作廢了他該知道）。建議做成一條明顯的
  提示帶（或直接讓連結失效），不是當成一般欄位塞進表格。

**實作上要釘死**：欄位投影寫成一支純函式（例如 `core/invoice_share.public_view(inv)`），
配一條測試逐欄斷言「這些在、那些不在」。理由跟報價的 `quote_snapshot` 一樣 —— 靠「記得
不要加」是撐不住的，下一個人往 API 多回一個欄位不會有任何徵兆，而錯誤只有客戶看得到。

### 3.6 🔴 顯示的數字會跟他下載的那張對不起來

檔案是固定的，**DB 那筆是活的**。有人事後在系統裡改了金額（打錯字、補統編、改抬頭），
客戶那頁就跟著變 —— 而他下載到的 PDF 還是舊的那張。**畫面與附件自相矛盾，而且只有他看得到。**

這不是假想：`core/invoice_pdf.compare_invoice_pdf()` 存在的理由就是「PDF 上的」與
「系統裡的」會不一致，上傳時就在比對了。

處置（跟報價那條同一個思路，而且機制剛做好可以直接抄）：

**建立／更新分享連結時，把要顯示的那幾個欄位快照起來**（`crm_invoices.share_snapshot`
JSONB），那頁只讀快照。之後改系統不影響已經寄出去的連結；要更新就重新產生連結
（或加一顆「更新分享內容」）。

比較便宜的替代：直接讀 DB 活值，頁尾加一句「以下載的發票證明聯為準」。**不建議** ——
那句話等於把「我們知道可能不一致」寫在客戶面前，而不是把它解決掉。

### 3.7 排版怎麼做（照這個 repo 既有的方式）

報價單版面是 owner 逐項拍板的，做法是**先做示範頁、看過再接線**
（`frontend/demo/quotation-pdf.html` 是視覺正本）。這頁照同一套：

1. 先做 `frontend/demo/invoice-share.html` —— 假資料、三種情境（PDF／圖／已作廢）
2. owner 看過拍板（欄位順序、要不要 logo、金額怎麼排、手機上怎麼折）
3. 才接真資料

視覺語言沿用報價的線上檢視頁（同一個網域、同一批收件人，長得像兩家公司很奇怪）。

---

## 4. 網址要用哪個網域

跟報價同一個問題：連結目前是前端用 `location.origin` 組的。報價那批已經加了
`quotes_public_base`（設定在「報價單資料夾」那張卡）。

建議：**改成一個共用的鍵**（例如 `share_public_base`），報價與發票都讀它，
舊的 `quotes_public_base` 保留為 fallback 一輪。理由：兩個面都是「寄給外面的人的連結」，
分兩個設定只會有一天其中一個忘了填。

---

## 5. 動到的檔案（預期）

| 檔案 | 動作 |
|------|------|
| `routers/crm/invoice_files.py` | 三支公開端點移到 `public_router`＋各自 `await surface_gate`；路徑翻譯；型別白名單 |
| `core/invoice_share.py`（新，可選） | 純規則：副檔名 → `kind`／Content-Type 白名單（純函式好測） |
| `frontend/invoice-file.html`（新） | 預覽頁，照 `media-log.html` 骨架 |
| `core/public_assets.py` | `PAGES` 加 `invoice-file.html`（同步清單與 nginx location 從這裡對齊） |
| `core/project_folders.py` | `BLOCKED_UPLOAD_EXTS` 加 `.svg` `.html` `.htm` |
| `main.py` | `/e/{code}` 改成回頁面（不是檔案） |
| `docker/nginx/originsun.conf` | `/e/` 與 `/api/v1/crm/public/invoice-file/` 兩條 location |
| `tests/unit/test_media_log_public_router.py`、`test_public_surface.py` | 白名單各加三條 |
| `tests/unit/test_invoice_share.py`（新） | 型別白名單、路徑翻譯、meta 不外洩金額／抬頭 |

---

## 6. 工作量

加了「顯示發票資訊」之後是 **1～1.5 天**（原本只做預覽是半天～一天）。多出來的是
欄位投影＋快照＋示範頁那一輪，不是排版本身。

分三段，每段都可以單獨上：

1. **先搬端點**（不改任何行為）：`/e/{code}` 仍然直接下載，但在 NAS 上也活了。
   風險最低，而且立刻拿到 owner 要的那半件事「master 關機也拿得到」。**建議先上這段。**
2. **示範頁 ＋ 拍板**：`frontend/demo/invoice-share.html`，假資料三種情境，不接後端。
   這段零風險，而且欄位清單要在這裡定下來（§3.5 那三個待決的欄位就在這輪決定）。
3. **接線**：欄位投影純函式 ＋ `share_snapshot` 快照 ＋ 型別白名單 ＋ 黑名單補 svg/html
   ＋ 三支公開端點 ＋ nginx ＋ 兩支白名單測試。

---

## 7. 驗收（要真的拔插頭）

1. **把 master 8000 停掉**，然後：
   - 開一條既有的 `/e/{code}` → 看得到預覽（PDF 或圖）
   - 按下載 → 真的拿到檔，檔名正確
   - 一條被撤銷的連結 → 401「連結已失效」，不是 500 也不是白畫面
2. 公開區把「發票影像分享」關掉 → `/e/{code}` 變 404（`surface_gate` 有生效）
3. 上傳一個 `.svg` → **被擋下來**
4. master 開回來：桌機發票分頁一切如常，`test_public_surface` 仍綠

---

## 8. owner 拍板紀錄（2026-09-10，看過示範頁之後）

| 題目 | 決定 |
|------|------|
| 頁上顯示發票資訊 | ✅ 做。欄位照 §3.5 白名單 |
| `title`（名稱／案件） | ❌ 不放 —— 可能有人拿它記內部案名 |
| 金額左邊的留白 | 維持現況（同報價單的右對齊摘要） |
| 標題 | 中性的「發票」（不是「電子發票證明聯」）—— 紙本拍照上傳的也走這頁 |
| **內嵌預覽** | ❌ **不做**。只留下載區塊 |
| **「在新分頁開啟」** | ❌ **也拿掉** |
| 對外網址設定 | ✅ **報價與發票共用一個** |
| 手機建立分享連結 | ✅ **要做**（目前只有桌機有） |

### 🔴 「不內嵌」把兩條風險整個刪掉了

原本 §3.2 列的「inline 預覽是新引進的攻擊面」與 §8 原本第一題「PDF 在手機上內嵌不了」，
在「永遠是 attachment」之後**都不存在**：

- 型別白名單（只對 PDF/JPG/PNG/WebP inline）—— **不用做了**
- `.svg` `.html` 加進 `BLOCKED_UPLOAD_EXTS` —— **不用做了**（可以另案處理，但不是這件事的相依）
- 手機內嵌 PDF 的退路 —— 不存在這個情境

§3.2 保留在文件裡是為了記住「為什麼最後沒有預覽」，不是待辦。

### 共用對外網址

`quotes_public_base` 改名成 **`share_public_base`**，報價與發票都讀它；
舊鍵保留為 fallback 一輪（CF 給 .js 四小時快取，舊分頁還會送舊鍵）。
設定的入口維持在報價那張卡，標題改成「對外連結網址」。

### 手機建立分享連結

`frontend/m/views/invoice.js` 加一顆「建立連結／複製連結」，走既有的
`POST /invoices/{id}/share`（冪等）。鑰匙鏡射後端 —— 那支的守衛是
`require_entity(request, inv.entity, level="full")`，**不是** `isAdmin()`，
別重蹈報價手機版那次「畫面比後端嚴，有權限的人看不到鈕」。

---

## 9. 實作後補記（2026-09-10）

實作時碰到兩件規劃裡沒想到的事，都已處理：

### 🔴 金額被 `MoneyRedactRoute` 抹掉了

那一層對**匿名**請求會刪掉 `amount_ex_tax`／`tax_amount`／`amount_total` —— 那是它存在的
理由（`public_router` 明確帶著 `route_class=MoneyRedactRoute`）。第一次端對端跑起來時，
`/meta` 回的欄位裡那三個直接不見了。

但這頁的金額是**收件人手上那張發票本來就印著的數字**。抹掉不會讓誰更安全，只會讓那頁
變成一排空格，然後有人為了「修好」它去把整層關掉。

處置：`core/money.py` 加一個**窄的** `MONEY_EXEMPT_PREFIXES`（目前只有一條），
門檻寫在那裡的註解：憑證是逐字比對、可撤銷的一次性連結；回的是白名單投影；
那些數字收件人已經拿在手上 —— **三個都要成立**。
`tests/unit/test_invoice_share.py` 守著「例外不要長大」。

### 頁面註解把內部欄位詞彙抄給客戶看了

第一版的 `invoice-file.html` 檔頭列了「代開費、內部代開、未收款狀態、母帳私帳…一律不上」
—— 用意是好的，但那頁是**寄給客戶**的，原始碼看得到。等於順手告訴對方我們內部在記些什麼。
改成指向後端那支（`core/invoice_share.py`），端對端測試多一條檢查那些詞不在頁面裡。

### 順手改掉的

頁尾的「源日影像製作有限公司　統一編號 90371657」原本是寫死字串 —— 公司資料改了不會跟著動。
改成 `/meta` 回 `seller`（讀 settings 的 `company`）。

---

---

## 10. 舊連結的補齊（一次性）

改版前鑄的分享連結沒有快照，`/meta` 會回 503「這個連結還沒有可顯示的內容」。
生產目前有 **14 張**發票產過連結（2026-09-10 實測），全部需要補。

**`POST /api/v1/crm/invoices/backfill-share-snapshots`**（管理員；`?apply=true` 才真的寫）
—— 效果等同人工去每一張按一次「複製連結」。預設 dry-run、冪等、不動 `updated_at`。

🔴 **發版之後越早跑越好**：補的是「現在這一版的欄位」。對這批舊連結沒有更好的來源
（人工按那顆鈕也是補現在這一版），但拖越久、中間被改過的機會越大。

回應會把「有連結卻找不到檔」的列在 `no_file` —— 那些補了也送不出東西，
要決定是把檔補回去還是把連結撤掉，不要靜默寫一個指向空氣的快照。

順序：`/publish` →（master 重啟時自己補上 `crm_invoices.share_snapshot` 欄位）→ 打這支。
