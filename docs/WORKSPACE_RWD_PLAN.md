# 員工工作台手機版（RWD）規劃

> owner 2026-09-17（手機截圖：團隊的一週五欄擠成直排字）：「幫我整理與規劃員工網站的 RWD」。
> 狀態：**規劃中，未動工**。示範畫面見 artifact「工作台手機版示範」；owner 點頭後照 §4 分三批做。

## 1. 現況（2026-09-17，tester_staff 在 390px 真機走一遍）

頁面本身沒有橫向捲動（每個表都包在 `overflow-x:auto` 裡），但**內容比手機寬**，只能在卡片裡左右拖，字被擠成直排：

| 畫面 | 手機上的問題 | 內容寬度 | 原因（檔案） |
|---|---|---|---|
| 團隊的一週 | 5–7 個日欄擠進 390px，每格「外出 10:00–12:00」變直排字；週末有內容時再多兩欄 | 每欄 ≈ 55px | `js/shared/ts-zone/team-week.js:91` 人×日 `<table class="week">`，`table-layout:fixed`，人員欄 84px |
| 今天的專案紀錄 | 11 欄可編輯表格（列號／專案／分類／階段／做了什麼／備註／時數／起／訖／狀態／刪），只看得到前 4 欄，要左右拖著填 | 535px 起跳 | `js/shared/ts-sheet.js:58-82` 各欄固定 px（type 96、stage 96、起訖各 64…） |
| 我的一週 | 5 欄卡片各 ≈ 50px，「加一項」變三行、案名直排 | 每欄 50px | `js/shared/ts-zone/plan.js:83` `.pboard { repeat(N, minmax(0,1fr)) }` |
| 專案查詢 | 10 欄（管理者 14 欄）表，只看到客戶／專案／狀態三欄，317 案一路往下 3 萬 px | 797px | `js/shared/ts-projects.js:156` `BURN_THEAD`；`find.js:136` 包 `overflow-x:auto` |
| 首頁「要補填」條 | 20 個日期連結一整面牆，把下面的分頁鈕推到第二屏 | — | `js/shared/ts-zone/remind.js:11` |
| 假勤 /leave.html | 頁頭「ORIGINSUN 假勤／個人工作台／登出」擠成兩行；休假總表 8 欄橫拖 | 591px | `leave.html:168-171` `table.hist` 全部 `nowrap` |
| 里程碑設定彈窗 | 6 欄 grid 最窄 470px | 470px | `my.html:385,389` `.msm-cols` |

目前的手機規則只有一條：`my.html:413` `@media (max-width:640px)` 把三欄卡片改一欄、Zone 1 的視圖加 `overflow-x:auto`。**其他全部靠橫向拖**。

已經有的東西（可沿用，不必重做）：
- 手機 CRM（`frontend/m/`）已經有同一批資料的手機版畫面：`m/views/worklog.js`（今天的專案紀錄，一列一卡＋底部抽屜）、`m/views/leave.js`、`calendar`、`petty`。同一支 API、同一套 `ts-sheet.js` 算式。
- `m/m.css` 的元件（卡、KPI 條、可橫捲的篩選 chips、44px 按鈕、`details.m-fold` 手風琴、底部抽屜 `#m-sheet`、分段鈕）——但它是**深色**主題，工作台是白底官網語彙，要重新配色不能直接套。
- `tabs/petty/petty-view.js:137` 的 `.pc-narrow`：用**容器寬**不是視窗寬決定排版的先例（卡片在桌機也可能很窄）。

## 2. 兩條路，選第二條

| | A. 手機一律導到 /m/crm.html | B. 同一頁、手機排版（建議） |
|---|---|---|
| 做法 | 390px 以下自動跳到手機 CRM 的對應分頁 | `/my.html`、`/leave.html` 不變，加一層手機排版：表格在窄螢幕改成卡片／日分頁 |
| 好處 | 立刻能用，已經有 worklog／leave／calendar | 同一個網址、同一套權限與鑰匙、同一份測試釘的東西不動；桌機同事看到的跟手機同事講的是同一頁 |
| 缺點 | 深色主題跟官網不搭；團隊的一週、專案查詢、里程碑、兼職排班在 /m/ **沒有**；兩套要一直對齊 | 要寫 6 段手機排版（見 §3） |

選 B。原則：**資料與算式一份、排版兩種**——渲染函式只多一個「窄」分支，資料端點與 `ts-sheet.js` 的算式不碰。

## 3. 每一段手機上長什麼樣（示範畫面照這個畫）

窄的判定：`matchMedia('(max-width: 640px)')` 或容器寬 < 560px（同 `.pc-narrow` 做法，卡片被夾窄時桌機也用卡片排版）。字級 15px、按鈕高 44px、頁邊 16px；配色沿用工作台的 `--red/--ink/--sub/--line`，白底。

1. **團隊的一週 → 一天一頁**：上方五顆日分段鈕（週末有內容才出現），預設今天；底下一人一列：名字＋當天的卡（案名粗、時數、內容灰；場次藍底、休假灰底、計畫淡字，同桌機）。「本週里程碑」帶留在最上面，改成一行一顆可展開。左右滑換天。管理者的週合計放在名字右邊。
2. **今天的專案紀錄 → 一列一卡＋底部抽屜**：卡上三行（專案、分類・階段、時數 起–訖），點卡開抽屜編輯（欄位跟桌機同 11 個，一欄一行）。「加五列」改「加一列」；「儲存草稿」「合併同案」固定在底部。直接沿用 `m/views/worklog.js` 的卡與抽屜結構，換成白底。
3. **我的一週 → 一天一列**：五（七）天直排，今天展開、其他天收合成「9/15（二）2 項・4.16h」；每天底下「加一項」。「從里程碑帶入」「複製上週」兩顆放頂端一列。
4. **專案查詢 → 一案一卡**：客戶小字、案名粗、狀態 pill、案型；右邊消耗率條（顏色同桌機）與最後填報日；點卡開專案檔案彈窗（既有）。篩選列收成一顆「篩選」鈕開抽屜（搜尋、狀態、案型、消耗率、日期都在裡面），排序改成分段鈕；一次只畫 30 案，往下再載（`renderPaged` 的做法）。
5. **首頁「要補填」條 → 一行摘要**：「專案紀錄沒填 17 天、週記沒送 6 週 → 補最近的 9/16」，點開才列全部日期。
6. **假勤**：頁頭三顆縮成「假勤」＋回工作台的圖示鈕＋登出；休假總表一列一卡（日期＋假別＋時段、右邊小時、狀態 pill、事由一行）。
7. **里程碑設定彈窗**：一欄直排；**兼職排班**彈窗同「我的一週」的一天一列。

## 4. 分三批做（每批都先 8001 真機截圖、owner 看圖再推）

| 批 | 內容 | 估 | 動到的檔 |
|---|---|---|---|
| 1（最痛） | 團隊的一週日分頁；今天的專案紀錄卡＋抽屜 | 2 天 | 新 `frontend/css/ws-mobile.css`（my.html 與 leave.html 都掛）、`ts-zone/team-week.js`、`ts-zone/log.js`＋新 `ts-zone/log-cards.js`（抽屜） |
| 2 | 我的一週一天一列；專案查詢一案一卡＋篩選抽屜；要補填摘要 | 1.5 天 | `ts-zone/plan.js`、`ts-zone/find.js`、`ts-zone/remind.js` |
| 3 | 假勤頁頭與休假總表；里程碑彈窗；兼職排班 | 1 天 | `leave.html`、`js/my/leave-host.js`、`my.html`（彈窗 CSS）、`js/my/parttime.js` |

每批的驗收：tester_staff 在 390px 走一遍，`document.documentElement.scrollWidth == clientWidth` 且**沒有任何元素比視窗寬**（走查腳本 `walk_my_rwd.py` 現在就能量）；桌機 1366 畫面零變化。

## 5. 不動的地方（測試釘著）

- `tests/unit/test_my_workspace_layout.py`：四顆 `view-btn` 的字面模板、`#ws-actions` 三顆功能鍵的字面、`z1-find-*` 篩選欄的 id、`id="ws-journal"`／`ws-zone1`／`ws-grid`、**my.html 不能有 emoji**、個人工時不能出現「合計」。手機排版加在渲染函式的分支裡，不改這些字面。
- `tests/unit/test_ts_shared_components.py`：`ts-sheet-num`／`ts-mine-row` 只能在 `ts-sheet.js`；卡片版不重定義。
- `tests/unit/test_week_plan.py`：`<span class="hrs plan">草稿</span>`；my.html 只能 import `ts-sheet.js` 已有的 export（Cloudflare 4 小時 JS 快取，新 export 要一起發）。
- `tests/unit/test_files_stay_readable.py`：`cards-hr.js` 已 49KB，手機層放新檔。
- 新增的測試：每批補一支「窄螢幕沒有元素比視窗寬」的規則測試（照 `test_my_ledger_layout.py:24` 的寫法釘 CSS），加「手機層不出現 emoji」。

## 6. 之後（不在這三批）

- `/hours.html`（團隊工時）與 `/calendar.html` 週視圖（`min-width:640px`）同樣的手法。
- `leave.html` 把跟 `my.html` 重複的 80 行卡片 CSS 抽成共用檔（順手，不然手機層要改兩份）。
