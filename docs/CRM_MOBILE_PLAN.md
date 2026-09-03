# CRM 手機版規劃（2026-09-03 定案版）

> 目標讀者：owner（驗收）＋接手實作的 Claude session。
> 依據：2026-09-03 對 CRM 前端（20,392 行／39 檔）、既有手機頁家族、CRM 後端 300+ 端點的盤點。
> 上位決定：`docs/WORK_OS_BLUEPRINT.md` §H「行動端不是選配——獨立輕頁模式照抄，主 UI 的 RWD 可以再等」。
> owner 拍板（2026-09-03）：**定位＝現場輸入＋快看，跟桌機版分開**；**發票登記是核心輸入**；**風格深色**
> （跟後台／`/expense.html` 同一套）；一期寫入只給 Lv3（owner＋財務），助理之後再開。

## 0. 結論

手機版不是桌機版的縮小，是另一個工具：**在外面當下要記的事，一顆按鈕記完**；整理、編輯、對帳、匯入留在桌機。
一個獨立頁 `/m/crm.html`、底部五個分頁（**發票／零用金**／專案／報價／付款；owner 2026-09-03：發票與零用金最常用，排最前面；零用金直接掛既有 `tabs/petty/petty-view.js`，不另做），共用一個殼模組；
後端只加六支手機用端點與一支 token 續期，其餘寫入直接打既有端點（狀態規則在後端已經定案，手機頁不自己寫死）。

## 1. 為什麼不是 RWD

| 事實 | 影響 |
|---|---|
| CRM 前端 20,392 行，全部 media query 只有 1 條 | 沒有可以「補一補」的基礎 |
| `setupResizeHandle` 只綁 mouse；`.crm-row` 七欄最小寬 ≈385px、成本格 9 欄 | 375px 必溢出，觸控上分欄是死的 |
| 168 個 `window._*` handler 靠 HTML 字串 `onclick` | 任何 DOM 改寫都得保住名字，等於重寫 |
| 13 檔 CSV 匯入、17 檔拖放排序、資料夾路徑靠主機端 `pick_folder` | 手機上本來就不存在 |
| 實務版面下限 1,100–1,280px | 「縮小一點」不是選項 |

反例：`tabs/petty/petty-view.js:110` 那條 720px 以下「表格塌成兩欄」——唯讀清單可以低成本 RWD，留給三期。

## 2. 手機上要記的五件事（一期）

| 動作 | 在哪 | 打哪支端點 | 備註 |
|---|---|---|---|
| 登記發票 | 發票分頁 | 既有 `POST /api/v1/crm/invoices` | 狀態由後端 `initial_invoice_status` 決定；取代 `/invoice.html` |
| 專案推階段 | 專案詳情 | 既有 `PATCH /projects/{id}/status` | 未成案要填原因（既有 `outcome_reason`） |
| 專案加一則備註 | 專案詳情 | 新 `POST /crm/m/projects/{id}/note` | 前插一行「日期 時間 使用者：內容」到 `notes` |
| 報價改狀態（寄出／簽回／拒絕） | 報價分頁 | 新 `POST /crm/m/quotations/{id}/status` | 簽回可勾「啟動專案」→ 專案進製作 |
| 請款標記已付／取消 | 付款分頁 | 既有 `PATCH /payments/batch-pay` `batch-unpay`（單筆 id） | 月結鎖帳 409 原樣顯示 |
| 新增案子的殼 | 新增分頁 | 既有 `POST /projects`（name／client_id／project_type／status） | 不含資料夾路徑 |

快看（進來輸入前順便看）：專案詳情一頁打包——收款摘要、報價、請款、發票、最近雜支、工時 burn、備註。
雜支照片、零用金、工時已有各自手機頁，專案詳情放連結帶專案 id 過去。

## 3. 後端（新檔 `routers/api_crm_mobile.py`，prefix `/api/v1/crm/m`）

讀：`check_logged_in`；寫：`check_admin`（一期只給 Lv3；要開給助理時改成 `check_admin_or_module(request, "crm_projects")` 一行）。
`route_class=MoneyRedactRoute`——金額鍵名照 `core/money.py` 的 `MONEY_FIELDS` 命名，沒 `money_view` 的帳號自動抹掉。

| 端點 | 回什麼 |
|---|---|
| `GET /options` | 表單字彙一次給：`phases`（`core.project_flow` 的順序）、`project_types`（`project_type_vocab()`）、`clients`（id／short_name）、`users`（AM 下拉）、`quote_statuses`、`invoice`（payment_types／kinds／categories／最近 20 個 item_type）、`me`（username／can_write／money_view） |
| `GET /home` | 進行中專案數、待回覆報價數、本月應收／應付合計、最近更新 5 案 |
| `GET /projects?phase=&q=&limit=30&offset=0` | 卡片瘦欄位：id／name／client_short_name／status／project_type／am_username／contract_amount／amount_received／shoot_date／updated_at；`total` |
| `GET /projects/{id}` | 打包：`project`（既有 `_to_project_dict`）、`summary`（既有 `project_financial_summary` 的子集）、`quotes`、`payments`、`invoices`、`expenses_recent`（5 筆）、`burn`（hours_used／budget_hours／pct，沿用 `services/timesheet_lookup`）、`notes` |
| `POST /projects/{id}/note` `{text}` | 前插一行到 `notes`，回新的 `notes` |
| `POST /quotations/{id}/status` `{status, activate?}` | 只改 `status`（既有 PUT 要整包 items，手機不該重送）；`activate` 走專案狀態同一支內部 helper |
| `POST /api/v1/auth/refresh`（`routers/api_auth.py`） | 拿還沒過期的 token 換一顆新的，權限從 DB 重讀；預設 7 天不動 |

分頁只在 `/crm/m/projects` 內做（`limit` 上限 100）；既有六支清單端點不動，桌機零改動。

## 4. 前端（新目錄 `frontend/m/`，不進 `core/public_assets.MODULE_DIRS`）

- `shell.js`（ES module，唯一的殼）：登入三視圖（密碼＋Google）、`mfetch`（帶 Bearer；401 → 清 token 回登入；剩 < 2 天靜默 `refresh`）、toast、`esc`、**本地日期** `todayLocal()`（`toISOString` 在 08:00 前會寫成昨天）、`money()` 千分位。
- `crm.html` + `crm.js`：深色（`#1a1a1a`／`#2a2a2a` 卡片／`#3b82f6` 主色，同 `/expense.html`），`maximum-scale=1`，底部 tab bar：
  - **專案**：頂端兩個數字（進行中／待回覆報價）、階段 chips、搜尋、卡片（30 筆一頁，往下捲再抓）→ 詳情底部抽屜：快看＋四顆動作（推階段／加備註／記雜支→`/expense.html?project=`／開發票→切到發票分頁預填）。
  - **發票**：登記表單（款項、種類、名稱、編號、日期、未稅、含稅、抬頭、統編、專案、品項、備註；未稅↔含稅互算）＋最近 10 張。表單字彙全部來自 `/options`。
  - **報價**：依狀態分組清單；每張三顆按鈕（寄出／簽回／拒絕），簽回時問「順便啟動專案？」。
  - **付款**：未付／應付清單（摘要、金額、收款人、預計月）＋「標已付」；已付最近 10 筆可「取消」。
  - **零用金**：掛既有 `renderMine`（`/petty-cash.html` 同一個模組，含拍照收據），CSS 變數改成深色值；閘門 `me_petty`。
  - 新增案子：專案分頁頂端「＋ 新案子」抽屜，四欄（名稱、客戶、案型、階段）。
- `manifest.webmanifest` + iOS meta：加到主畫面像 app；**不做 service worker**。
- 驗收用 Playwright iPhone viewport 打 dev，再真手機打 foundry。

## 5. 明確不做

成本格、收支明細、對帳、CSV 匯入、拖放排序、報價項目編輯、資料夾路徑、上傳影像／作品、金額類的修改（手機打錯的代價比省的時間高）。

## 6. 分期與估時

| 期 | 內容 | 工時 |
|---|---|---|
| 一期 | §3 後端＋§4 五分頁；取代 `/invoice.html` | 4–5 天 |
| 二期 | 客戶聯絡紀錄、應收應付總覽、儀表板；助理權限開放（守衛一行＋決定端點） | 2–3 天 |
| 三期（選配） | 主 UI 三個唯讀分頁 RWD；通知走 Google Chat／LINE；service worker 只快取殼 | 各 0.5–1 天 |

## 7. 兩個要守住的坑

1. **手機頁不寫死任何字彙**：狀態、案型、款項類型全部從 `/options` 拿。`/invoice.html` 曾把 `payment_status` 寫死，桌機改名後手機送出的票落在沒人認得的狀態。
2. **殼只有一份**：現有五個獨立頁各抄一份登入／fetch／日期，已經漂過兩次；新頁只准 `import './shell.js'`。
