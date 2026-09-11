# 工作追蹤 v2 —— CRM 分頁改成「員工介面 ＋ 管理層」（規劃）

> 狀態：規劃完成、owner 已拍板（2026-09-11），待 P1。示範頁：[`/demo/work-tracking-v2.html`](../frontend/demo/work-tracking-v2.html)
> 前身：`docs/WORK_TRACKING_UI_PLAN.md`（P1–P4，現行 CRM 七分頁）、`docs/STAFF_PROJECT_RECORD_PLAN.md`（員工端）

## 1. owner 要什麼（原話）

> 「我希望我可以有員工的所有介面，但是可以看到的更多與管理相關的細節」——「我指的是 CRM 裡的頁面」

拆開來是兩件事：

1. **介面 ＝ 員工那套**：`/my.html` 第一區「今天與這週」的四個視圖（今天的專案紀錄／我的一週／團隊的一週／專案查詢）＋兼職排班，
   原封不動搬進 CRM 的工作追蹤分頁，取代現行的「今日／我的一天／專案／人員」四個管理分頁。
2. **細節 ＝ 管理層疊上去**：同一個畫面，管理員／`timesheets` 鑰匙的人多看到員工端刻意抹掉的東西（錢、預算、別人的、資料治理）。
   員工端**一個位元都不變**（後端照樣抹，管理欄位只在 CRM 分頁打管理端點）。

## 2. 現況對照（為什麼要合）

| 員工端視圖（`js/my/`） | CRM 現行分頁（`tabs/timesheets/`） | 資料 | 差在哪 |
|---|---|---|---|
| 今天的專案紀錄（ts-sheet 格子） | 我的一天（同一份 ts-sheet） | `/timesheets/mine*` | 幾乎一樣；CRM 多時數快捷鈕、複製昨天 |
| 我的一週（一天一欄的計畫板） | —— | `/timesheets/mine`（plan 列） | CRM 沒有 |
| 團隊的一週（人×日＋場次／休假／里程碑） | 今日看板（一天、每個人） | `/me/team_week` vs `/timesheets/board` | 員工端是一週、CRM 是一天；CRM 沒里程碑 |
| 專案查詢（burn 表，抹錢） | 專案（burn 表＋建議預算＋比較） | `/me/projects_burn` vs `/timesheets/summary` | 同一張表，CRM 多 `suggested_hours`／未對映診斷／類似專案 |
| 兼職排班（獨立視窗） | 設定 › 代填 | `/timesheets/plan-for/*` vs `/manual` | 兩條路做同一件事 |

兩邊格子與專案表**已經是共用元件**（`js/shared/ts-sheet.js`、`ts-projects.js`），差的只是外面那層視圖邏輯住在 `js/my/*.js`（傳統 script、全域詞法環境），CRM 分頁是 ES module，**搬不動才各寫一份**。

## 3. 新分頁的資訊架構

```
工作追蹤（CRM › 人事管理）
├─ 今天的專案紀錄   ← 員工視圖 1 ＋ 管理：看誰的（人員切換）、代填、案子燒率 chip、今天還沒填的人
├─ 我的一週         ← 員工視圖 2 ＋ 管理：看誰的（兼職排班維持獨立視窗，不併）
├─ 團隊的一週       ← 員工視圖 3 ＋ 管理：每人週合計、未填標紅、Sheet 衝突待決、未對映案名
├─ 專案查詢         ← 員工視圖 4 ＋ 管理：合約未稅／預期毛利／建議預算／人力成本／超標警示、未對映區塊、類似專案並排
└─ 管理 ▾           ← 現行 總表／儀表板／設定 原樣保留（次級 tab，只有管理員／timesheets 看得到）
```

頂欄兩顆管理專屬控制：**「看誰的」**（預設**全部**；今天與一週兩視圖用）、**「管理視角」開關**（預設開；關掉＝「以員工的角度看」，用來檢查員工看到什麼）。分頁**跟 CRM 深色**。

## 4. 每個視圖的管理層（demo 頁編號對應）

| # | 視圖 | 管理層加什麼 | 資料來源（既有端點，不新開） |
|---|---|---|---|
| 1 | 今天的專案紀錄 | 人員切換器；替別人填＝總表那把（`PUT/POST /timesheets/rows`，列上標「代填：誰」`planned_by`／`filled_by`） | `/timesheets/rows?date=&staff=` |
| 2 | 今天的專案紀錄 | 每列案名旁 **燒率 chip**（已投入／預算／剩餘），超 100% 紅 | `/timesheets/summary`（管理版帶 budget） |
| 3 | 今天的專案紀錄 | 頂端一條「今天還沒填：A、B」（點名字切過去） | `/timesheets/board?date=` |
| 4 | 我的一週 | 人員切換器（在職／合夥）；兼職不在切換器裡，**兼職排班仍是獨立視窗**（`plan-for` 那條不動） | `/timesheets/rows` |
| 5 | 團隊的一週 | 每人右側 **週合計 h**、低於門檻標黃、0 標紅「未填」 | `/me/team_week`＋`/timesheets/by_staff` |
| 6 | 團隊的一週 | 格子角落 **衝突待決** 標記（改過的列撞到 Sheet 新版），點開三選一 | `/timesheets/conflicts` |
| 7 | 專案查詢 | 多四欄：**合約未稅、預期毛利、建議預算(h)、人力成本**；建議預算可一鍵套用 | `/timesheets/summary`（`suggested_hours`）、`/crm/projects/{id}/financial-summary` |
| 8 | 專案查詢 | 下方 **未對映 Sheet 案名** 區塊（candidates／一鍵指定） | `/timesheets/summary.unmatched`（完整鍵）、`PUT /project_map` |
| 9 | 專案檔案彈窗 | 員工版（分類組成／各人／各月／里程碑／時間軸）＋ 管理：**類似專案並排**、預算調整、連到 CRM 專案頁 | `/timesheets/project`、`/compare`、`PUT /project_budget` |
| — | 管理 ▾ | 總表／儀表板／設定原樣 | 不動 |

**錢的界線**：合約額／毛利／人力成本只在管理視角、只給 `money_view`（同 CRM 其他地方一把尺）；只有 `timesheets` 沒 `money_view` 的人看得到建議預算（小時）但看不到它怎麼算出來的（同現行 `_has_ts_module` 那條）。

## 5. 做法（技術）

1. **把 `js/my/zone1.js`／`week-plan.js`／`team-week.js` 的視圖邏輯抽成 ES module** `js/shared/ts-views/{log,plan,team-week,find}.js`，
   每支 export `mount(host, ctx)`，`ctx = { api, staff, manage: bool, money: bool, onStale }`。
   - `manage=false`＝今天的員工端（一個位元都不變，`api` 指 `/me/*`＋`/timesheets/mine*`）。
   - `manage=true`＝CRM 分頁（`api` 指管理端點，多畫 §4 那些）。
   - `my.html` 仍是傳統 script 殼：頁尾 `<script type="module">` import 這四支後掛到 `window.TS`（跟 ts-sheet 今天的做法一樣，`TS_READY` 等它）。
2. CRM `tabs/timesheets/timesheets.js` 的 today／mine／projects／staff 四個 view 換成 import 這四支；ledger／dash／settings 原樣。
3. 後端只補小的：`/timesheets/rows` 收 `date`／`from`＋`to_day`／`staff_id`；`/timesheets/people`（切換器）；`/board` 每天多 `absent`；
   `/manual` 改走 `add_rows`（階段／待辦處理跟員工自填一致）；`/me/team_week` 有 timesheets 鑰匙不必綁定人員、管理視角多 `project_id`。
4. 錢的欄位走 `MoneyRedactRoute`＋`money_view`，不另做抹除。
5. 測試：`test_ts_shared_components`（列 html 只有一份）擴成「四個視圖各只有一份」；`test_money_visibility` 釘 `manage=false` 的 api 表不含任何管理端點。

## 6. 分期

- **P0（demo）**：本文件＋示範頁，owner 逐項拍板 §4。✅ 2026-09-11
- **P1**：抽四支視圖模組、`my.html` 改吃它（員工端零視覺變化，Playwright 對照截圖）。✅ 2026-09-12（`js/shared/ts-zone/`，四視圖逐像素相同）
- **P2**：CRM 分頁換上四視圖＋人員切換＋管理視角開關（§4 的 1、3、4、5）。✅ 2026-09-12
  （看誰的＝全部：每人一段唯讀格子＋「還沒填」；某人：替他填走 `/manual`／`/rows/{id}`、替他排一週；團隊的一週多週合計＋「未填」；
  專案查詢已改打 `/summary`，錢四欄與未對映區塊留 P3）
- **P3**：衝突待決、未對映、類似專案、建議預算套用（§4 的 2、6、8、9）。
- **P4**：拿掉舊的 today（今日看板）／mine／projects／staff 四個 view；兼職排班獨立視窗保留。

## 7. owner 拍板（2026-09-11）

1. 主題：**跟 CRM 深色**。
2. 「看誰的」預設：**全部**（進來先看團隊；今天的專案紀錄在「全部」時＝每個人一段格子，唯讀＋點名字進去改）。
3. 兼職排班：**保留獨立視窗**，不併進「我的一週」。
4. 專案查詢的錢欄位：**四欄全開**（合約未稅／預期毛利／建議預算／人力成本，仍要 `money_view`）。
5. 舊「今日看板」：**不留**（團隊的一週切到今天那欄就是它）。
