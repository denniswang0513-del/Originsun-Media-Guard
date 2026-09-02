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
- **D5 狀態機留給階段 3**：draft（自己填）→ confirmed（本人週末確認）→ approved（主管核可）
  → locked（月結）。階段 1 全部 draft，不擋任何人。
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

### 階段 3 —— 確認與核可（N0 共用審核模組落地後）

- 本人每週確認（draft → confirmed）；主管（製片／owner）核可（→ approved）；月結鎖（→ locked）
- 核可人是誰：**owner 要決定**（藍圖 §8-4 未答）
- 「我的工時」顯示狀態 pill；後台 tab 多「待核」清單

### 階段 4 —— 排班預填（N2 主線）

- 排班日曆排了誰，當天工時預填好（專案／日期／人／整天），本人手機「一鍵確認」或改半天
- 沒排班的臨時工時才手動新增（就是階段 1 的表單）

## 3. 風險

- **綁定**：沒綁 staff_id 的帳號填不了（409 提示找管理員）。目前 14 個填 Sheet 的人裡只有
  部分有帳號 —— 推行前要先開帳號＋綁定（使用者管理已有 UI）。
- **重複填**：D4 已擋一半（Sheet 列被手填擋）；另一半靠「我的工時」把 Sheet 列列出來給人看。
- **時區**：work_date 是 timestamptz，寫 naive／讀 aware 差一天的坑已在 manual_dup_key 處理；
  列表顯示一律 `astimezone().date()`。
- **法律定位**：若日後當出勤紀錄用，保存年限與不可竄改要另議（藍圖 §7-D）—— 階段 1 不碰。
