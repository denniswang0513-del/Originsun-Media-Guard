# 工時：員工在系統裡自己填（規劃 2026-09-03）

> owner：「我要建立一個員工也可以在這裡填寫的系統，請規劃」「不單只是分析而已」。
> 目標：團隊從 Google Sheet 填工時 → 改在系統裡填（手機也能），資料直接進 `timesheets`，
> burn 表／人員月視圖／成本回寫都不用再等同步。Sheet 不立刻廢，兩條路並行到切換日。

## 0. 已經有的（不重做）

| 東西 | 位置 | 狀態 |
|------|------|------|
| `timesheets` 表（小時粒度、source=sheet/manual、status、staff_id） | db/models/_workos.py | ✅ 生產 9,828 筆 |
| Sheet 每小時自動拉 → 同一條 ingest | services/timesheet_puller + timesheet_ingest | ✅ 生產已開 |
| 手填優先：同 (人, 日, 專案) 有手填列 → Sheet 那列跳過 | core.hr_logic.manual_dup_key | ✅ |
| 管理員代填「快速補登」grid | 專案工時 tab | ✅ |
| 員工「補一筆工時」（單筆） | /my.html 工時卡 → POST /api/v1/me/timesheets | ✅ 但只能加、看不到自己填了什麼、不能改 |
| 帳號 ↔ 人員檔案綁定（users.staff_id）、me_* 細粒度 key | N0 個人帳號化 | ✅ 目前 7 個帳號綁了 staff_id |
| 專案對映（對映表→精確→去前綴→撞案不猜） | core.hr_logic.resolve_project | ✅ |

## 1. 設計決定

- **D1 填寫的地方＝/my.html「我的工時」**，不是後台 tab。員工只看得到自己的；手機優先
  （現場人員九成用手機，藍圖 §7-H）。後台 tab 的「快速補登」留給管理員代填。
- **D2 一天可以填多列**（同 Sheet 實務：一天分好幾個案），每列＝日期／專案／內容／時數。
  專案下拉＝進行中案＋本人最近填過的（既有 `timesheet_options`）；找不到的案名照打，
  對映規則跟 Sheet 一樣（撞案不猜、留給管理員指定）。
- **D3 自己填的可以改、可以刪，Sheet 同步進來的不行**（改 Sheet 那邊再拉）。
  可改的條件寫成純函式 `core.hr_logic.can_edit_timesheet(row, staff_id)`：
  本人 ＋ source=manual ＋ status 在 draft／confirmed（approved／locked 之後不能動）。
- **D4 兩條路並行時的規則**：同一天同一案**只填一邊**。系統裡填了，Sheet 那列會被擋
  （手填優先）；但 Sheet 已經拉進來的舊列不會自動消失 —— 所以「我的工時」把 Sheet 列
  也列出來（唯讀、標「Sheet」），員工一眼看得到今天已經有什麼，不會重複填。
- **D5 不做審核**（owner 2026-09-03「員工填了就 ok，我不用審核」）：填了就是最終，
  本人隨時可改可刪；不做 confirmed／approved 狀態機。`status` 欄留著（手填列＝draft）只是
  資料事實，沒有任何流程掛在上面。日後若要月結鎖帳，只加一個 locked，不加核可。
- **D6 權限**：階段 1 沿用 `me_finance`（工時＋請款卡本來就是它）。等全員推行再拆
  `me_timesheets`（新 key 要同步五處，見 memory）—— 不為了一個 MVP 先付那個成本。

## 2. 階段

### 階段 1 —— 員工自助填寫 MVP（**本次做**）

後端（routers/api_me.py，own-scope 一律經 `resolve_current_staff`，絕不吃 client 的 staff_id）：

- `GET  /api/v1/me/timesheets?month=YYYY-MM` — 本人該月所有列（Sheet＋手填），含
  `editable`、本月合計、各案小計
- `POST /api/v1/me/timesheets/batch` — 一次多列（走既有 `insert_manual_rows`）
- `PUT  /api/v1/me/timesheets/{id}` — 改自己的手填列（日期／專案／內容／時數，專案重新對映）
- `DELETE /api/v1/me/timesheets/{id}` — 刪自己的手填列
- 既有單筆 `POST /api/v1/me/timesheets` 保留（相容）

前端（/my.html 工時卡改成「我的工時」）：月份切換 → 列表（日期／專案／內容／時數／來源，
手填列有「改」「刪」）→ 底部「新增」多列 grid（預設今天；手機直式也排得下）→ 送出。

驗收：dev 用綁定帳號填 3 列 → 列表看到 → 改 1 列 → 刪 1 列 → 專案工時 tab 的 burn 表
與人員月視圖跟著變；Sheet 拉取跑一次，同人同日同案的 Sheet 列被擋（手填優先）。

### 階段 2 —— 推行與切換（1–2 週，owner 決定切換日）

- 週一 digest：每人上週工時摘要推 Google Chat（漏填自見；藍圖 §3.6「同儕可見性 > 催促」）
- 「我的工時」頂部提示：本週還沒填的日子
- 切換日：Sheet 改唯讀（owner 在試算表設）、拉取排程關掉（tab「設定」關）、
  之後只剩系統填；Sheet 留作歷史備份
- 影子運行：切換前一週兩邊都填的人，對帳一致才切（藍圖 §7-L）

### ~~階段 3 —— 確認與核可~~（owner 2026-09-03：不審核，取消）

### 階段 3 —— 排班預填（N2 主線）

- 排班日曆排了誰，當天工時預填好（專案／日期／人／整天），本人手機「一鍵確認」或改半天
- 沒排班的臨時工時才手動新增（就是階段 1 的表單）

## 5. 團隊互看＋匯總＋幫大家算好（2026-09-03 追加，owner：「大家可以看到彼此的工時、
有匯總頁、專案工時匯總頁，也可以幫他們計算工時」）

**決定**
- D7 閘門＝「看得到自己就看得到大家」：能填工時的人（me_finance＋綁定）就能看團隊頁。
  之後若要只給部分人看，再拆一把 `me_team_hours`。
- D8 團隊頁回的是 **Sheet 案名與時數**，沒有金額、沒有 CRM 專案 id 的連結 ——
  私帳「專案列」那條可見性線守的是錢，這裡是團隊自己的工時表（他們本來就在 Sheet 上互看）。
  預算小時（budget_hours）不是錢，顯示。
- D9 「幫他們計算」＝系統算好：每人本月合計、填了幾天、平均每天、每週小計、各案小計；
  全體各案合計；**參考工時＝週一到週五天數 × 8**（只是對照，不扣國定假日、不是打卡標準）。
  超過參考的標紅 pill，不做任何加班／扣薪推論 —— 那是 N3 獎金與 owner 政策的事。

**做了什麼（階段 1 同批上線）**
- 端點（routers/api_me.py，純計算在 core.hr_logic.hours_rollup）：
  `GET /me/team/hours?month=`、`GET /me/team/projects?months=12`、`GET /me/team/project?name=`
- 頁面 `/hours.html`（同 my.html 設計語彙，手機可）：團隊月表（每人一列、每週欄、點開各案）、
  專案匯總（總時數／人數／預算／剩餘／消耗條）、專案明細（各人／各月走勢／最近 60 列）
- 「我的工時」多一顆「團隊工時 →」

**之後可加**：週一 digest 引用同一支 rollup 推 Google Chat；人力負載熱力圖（藍圖 §3.5-3）
直接吃 `weeks`；專案明細加「報價人日 vs 排班 vs 實際」三條 bar（§3.5-1）。

## 3. 風險

- **綁定**：沒綁 staff_id 的帳號填不了（409 提示找管理員）。目前 14 個填 Sheet 的人裡只有
  部分有帳號 —— 推行前要先開帳號＋綁定（使用者管理已有 UI）。
- **重複填**：D4 已擋一半（Sheet 列被手填擋）；另一半靠「我的工時」把 Sheet 列列出來給人看。
- **時區**：work_date 是 timestamptz，寫 naive／讀 aware 差一天的坑已在 manual_dup_key 處理；
  列表顯示一律 `astimezone().date()`。
- **法律定位**：若日後當出勤紀錄用，保存年限與不可竄改要另議（藍圖 §7-D）—— 階段 1 不碰。
