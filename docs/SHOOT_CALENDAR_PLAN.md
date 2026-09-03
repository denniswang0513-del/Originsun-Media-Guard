# 行事曆（拍攝排程＋器材登記）規劃（2026-09-03 定案版）

> owner 拍板（2026-09-03）：手機版「付款」分頁改成「**行事曆**」——主要登記**拍攝日**與**使用器材**，
> 跟**專案**、**Google 日曆**連動（做法 B：系統直接寫進公司 Google 日曆）。付款分頁留成隱藏頁（從專案抽屜進）。
> 依據：器材庫 B4 已有器材主檔＋領用／歸還紀錄（`equipment` / `equipment_checkouts`，可掛專案、有應還日）；
> 專案有單一 `shoot_date`、成本子表各有 `shoot_date`；行事曆串接原本**沒有**；服務帳號機制已有
> （`services/google_oauth.py`，GA／GSC 用，鑰匙存 website_settings `analytics.ga_service_account_json`）。

## 0. 結論

一個新實體「**拍攝場次**」（`crm_shoots`）：哪一天、哪個案子、去哪裡、誰去、帶什麼器材。
器材不另建表，**沿用器材庫的領用紀錄**（`equipment_checkouts` 加 `shoot_id`）：登記一場拍攝勾器材＝開出預約，
拍攝日撞到別場已勾走的器材會標「衝突」；「已領」「已還」直接動同一列。
專案的「拍攝日」改成**從場次算**（最近一場未來的；沒有就最後一場），不再手填。
每個場次新增／改期／取消時**同步寫進 Google 日曆**（服務帳號＋公司共用日曆），失敗不擋操作、記在場次上重試。

## 1. 資料

### `crm_shoots`（`db/models/_workos.py`）
| 欄位 | 說明 |
|---|---|
| id | uuid4 hex |
| project_id | soft FK → crm_projects.id（必填） |
| title | 空＝用案名 |
| date / end_date | 拍攝日（必填）／結束日（多日拍攝才填；空＝當天） |
| start_time / end_time | 'HH:MM'，空＝全天 |
| location_id / location_text | 場景庫 soft FK／自由文字（二選一或都有） |
| crew | JSON 陣列 `[{staff_id, name}]`（人員，crm_staff） |
| notes | 備註 |
| status | 排定／完成／取消（字彙住後端 `SHOOT_STATUSES`，前端從 options 拿） |
| cost_group_id | 可連成本子表（那一天的雜支） |
| google_event_id / synced_at / sync_error | 日曆同步狀態 |
| created_by / created_at / updated_at | |

### `equipment_checkouts` 加欄
- `shoot_id`（soft FK → crm_shoots.id，`ALTER TABLE … ADD COLUMN IF NOT EXISTS`，走 main.py startup 那個迴圈）。
- 預約列：`out_at`＝拍攝日、`due_at`＝結束日＋1、`returned_at` 空。「已領」把 `out_at` 改成當下並把器材狀態轉「出勤」；
  「已還」填 `returned_at`、狀態轉「在庫」（跟 `api_equipment` 的 checkout／return 同一套規則，抽成共用 helper）。
- 衝突＝同一器材另一列未歸還、且日期區間重疊。

## 2. 後端（新檔 `routers/api_shoots.py`，prefix `/api/v1/shoots`）

讀：`check_logged_in`；寫：`check_admin_or_module(request, "crm_projects")`（拍攝排程屬專案管理，不開新模組 key）。

| 端點 | 回什麼 |
|---|---|
| `GET /options?date=&end_date=` | `statuses`、`equipment`（依 category 分組；帶日期時每件附 `busy`＝那天被哪場拿走）、`locations`（id／name）、`staff`（id／name／role）、`projects`（沿用 `api_crm_mobile._slim_project` 那個瘦欄位） |
| `GET /?from=&to=&project_id=&status=&limit=&offset=` | 場次清單（帶 project_name／client_short_name／location_name／equipment 數／crew 數／sync 狀態），預設 from＝今天－7 天 |
| `GET /{id}` | 單場＋器材明細（每件：name／category／checkout 狀態 預約／已領／已還） |
| `POST /` | 建場次＋器材預約 → 同步日曆（best-effort） |
| `PUT /{id}` | 改欄位＋器材差異（新勾＝加預約、取消勾＝刪預約列；已領的不能取消勾） → 同步 |
| `POST /{id}/status {status}` | 完成／取消／回排定；取消＝刪日曆事件 |
| `POST /{id}/equipment/pickup` / `/return` | 整場器材一次領／還（`equipment_ids` 可指定部分） |
| `GET /calendar/status` | 服務帳號是否備妥（email）、calendar_id 是否設定、最近一次同步結果 |
| `PUT /calendar/config {calendar_id}` | admin；存 settings.json `google_calendar.calendar_id` |
| `POST /calendar/test` | admin；對日曆打一次 list（驗分享權限） |
| `POST /{id}/resync` | 重試同步 |

每次寫入後：重算該專案 `crm_projects.shoot_date`；`api_crm_mobile` 的專案詳情多回 `shoots`（未來 5 場＋最近 3 場）。

### Google 日曆（新檔 `services/google_calendar.py`）
- 憑證：settings.json `google_calendar.service_account_json`（可空）→ 空就用 website_settings `analytics.ga_service_account_json`（同一個服務帳號）。
- scope `https://www.googleapis.com/auth/calendar.events`；REST 直打 `https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events`（沿用 `google_oauth.get_access_token` / `urlopen_json`，不引入 googleapiclient）。
- 事件：全天（沒時間）用 `date`／`end.date`＝結束日＋1；有時間用 `dateTime`＋`Asia/Taipei`。
  `summary`＝「拍攝｜案名（客戶）」、`location`、`description`＝人員／器材／備註／系統連結。
- 阻塞 I/O 一律 `asyncio.to_thread`；任何失敗只寫 `sync_error`，不 raise 給使用者。
- owner 要做的設定：Google 日曆 → 公司共用日曆 → 分享給服務帳號 email（權限「變更活動」）；把日曆 ID 貼進手機行事曆分頁的「行事曆設定」卡。

## 3. 手機版（`frontend/m/`）

- 分頁列：發票／零用金／專案／報價／**行事曆**（取代付款）；`payments` 改成隱藏路由（`ROUTES`），專案抽屜「請款」段有「付款清單」進去。
- `views/calendar.js`：
  - 頂端「登記拍攝」按鈕 → 原地展開表單：專案（打字找，必填）→ 日期 → 結束日期（更多欄位）→ 時間起迄 → 地點（打字找場景庫，可自由填）→ 人員（打字找員工，選成 chip）→ 器材（打字找，依類別顯示，選成 chip；換日期後重抓 busy，衝突的 chip 標紅）→ 備註。
  - 清單：今天／本週／下週／之後 分組；過去的收在「已過」折疊（`renderPaged` 10 筆一頁）。卡片：日期時間、案名（客戶）、地點、人員數、器材數、狀態 pill、日曆同步標記。動作（分段式、同專案抽屜）：修改／器材已領／已歸還／完成／取消。
  - admin 才看得到「行事曆設定」卡：服務帳號 email（要分享日曆給它）、日曆 ID、測試連線、最近同步結果。
- 專案抽屜：多一段「拍攝」（近期場次）＋第五顆動作「登記拍攝」（`embedHost` 把行事曆宿主搬進來、預設專案）。
- 字彙（狀態、類別）全部從 `/shoots/options` 拿；殼只有 shell.js。

## 4. 明確不做（一期）
桌機月曆檢視（二期，放前期製作群）、從 Google 日曆反向改回系統、器材以外的預約（車輛／棚）、多日拍攝的逐日不同器材。

## 5. 分期
| 期 | 內容 |
|---|---|
| 一期 | §1–§3 全部；deploy_to_prod；owner 分享日曆＋貼 ID |
| 二期 | 桌機月曆 tab、通知（拍攝前一天 Google Chat 提醒）、器材庫分頁顯示預約 |
