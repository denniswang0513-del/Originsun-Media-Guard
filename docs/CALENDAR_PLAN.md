# 公布欄「行事曆」規劃（2026-09-14 定案版）

> owner 2026-09-14 拍板：**① 專案詳情也放一個只看這一案的行事曆分頁；② 外部人員可以被排；③ 排別人不用通知；
> ④ 所有事件都同步到 Google 日曆。** 本文件是實作依據；跟 Work OS 藍圖 §2「排班日曆→通告→工時預填」（ROADMAP N2）對齊。
> 可點的 demo：https://claude.ai/code/artifact/11afbf61-3bce-47e8-84b8-824497f6b429

## 0. 一句話

行事曆＝**一個讀取層**（把已經存在的拍攝場次、請假、假日、里程碑、我的一週計畫卡放在同一張日曆上）
＋ **一種新的可登記事件「工作登記」**（`crm_schedule`：專案 × 日期 × 人員 × 職務，可排外部人員）
＋ **一條同步線**（四種事件都寫進公司 Google 日曆，沿用場次那套）。三個宿主（公布欄／專案詳情／手機）用同一份元件。

## 1. 現況（不重複建的東西）

| 已有 | 在哪 | 行事曆怎麼用它 |
|---|---|---|
| 拍攝場次 `crm_shoots`＋Google 同步 | `routers/api_shoots.py`、`core/shoot_logic.event_body`、`services/google_calendar.py`、手機 `m/views/calendar.js` | 直接畫；「登記拍攝」開既有表單；同步機制**抽成共用** |
| 我的一週計畫卡（`timesheets` status=plan） | `ts-zone/plan.js`、`/timesheets/mine/rows` | 一期：登記自己的單日工作時**同時**建一張卡（帶 schedule_id）；卡仍是工作台那邊的正本 |
| 團隊的一週 `/me/team_week` | `routers/api_me.py` | 週檢視「人員列」的骨架（人×日）；行事曆多疊工作登記 |
| 里程碑 `crm_project_milestones` | `services/milestone_service.py` | 到期日畫成小旗；**加 Google 同步** |
| 已核准的假 `hr_leave_requests`、假日表 `hr_holidays` | 假勤 | 假畫灰條（**加 Google 同步**）；假日整格淡紅（不同步——Google 自己有台灣假日曆） |
| 人員檔案 `crm_staff`（在職／合夥／兼職／離職，`employment_type` 正職/兼職/約聘/freelance） | 人力庫 | 排人的下拉；**外部人員不建檔**（§2） |

## 2. 資料

### 2.1 新表 `crm_schedule`（工作登記／排班；藍圖 §2 的 `crm_project_schedule`）— `db/models/_workos.py`，create_all 建
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | VARCHAR(32) PK | uuid4 hex（也是 Google 事件 id 的來源，同場次：base32hex 合法） |
| kind | VARCHAR(16) | `work` 一般工作／`meeting` 會議・看片／`out` 外出・勘景／`other`。**拍攝不在這張表**（那是 `crm_shoots`） |
| project_id | VARCHAR(32) idx | soft FK → crm_projects.id，**可空**（行政庶務、內部會議） |
| title | VARCHAR(255) | 做什麼（必填） |
| date / end_date | DATE idx / DATE | 起／訖；單日 end_date 空 |
| start_time / end_time | VARCHAR(5) | 'HH:MM'；空＝全天。上午＝09:00–13:00、下午＝13:00–18:00（快捷鈕只是填值，存的仍是時間） |
| attendees | TEXT(JSON) | `[{staff_id, name, role, external, contact}]`：內部人 `staff_id`＋`name`；**外部人員 `staff_id` 空、`external: true`、`name` 必填、`contact` 選填**（電話／LINE）。`role` 自由文字（攝影／燈光／剪接…），可空 |
| location_text | VARCHAR(255) | 自由文字（要場景庫再說） |
| notes | TEXT | |
| status | VARCHAR(16) | `planned`／`done`／`cancelled`（字彙住 `core/schedule_logic.py`，前端從 options 拿） |
| timesheet_ids | TEXT(JSON) | 「做了 ✓」產生的 timesheets 列 id（每人一列；防重複） |
| plan_row_id | VARCHAR(32) | 一期：登記自己的單日工作時同時建的計畫卡 id（改／刪連動；空＝沒建） |
| google_event_id / synced_at / sync_error | VARCHAR(255) / TIMESTAMPTZ / TEXT | 同場次 |
| created_by / created_at / updated_at | | |

索引：`(date)`, `(project_id)`；查「某人某天」用 JSON 掃（量小：一天幾十筆），不另建關聯表。

### 2.2 舊表加欄（`db/migrations.CRM_COLUMNS` 末端，`ALTER … ADD COLUMN IF NOT EXISTS`）
- `crm_project_milestones`：`google_event_id VARCHAR(255)`、`synced_at TIMESTAMPTZ`、`sync_error TEXT`
- `hr_leave_requests`：同上三欄
- `crm_shoots.crew` 的元素**允許** `{name, role, external: true, contact}`（沒 staff_id）——拍攝也能排外部人員；讀取端本來就只用 name。

### 2.3 外部人員（owner：可以被排）
- **不建人員檔案**、不進 `crm_staff`：只存在事件的 `attendees`／`crew` 裡。理由：freelancer 一年幾十個、大多一次性；建檔會汙染人力庫與假勤。
- 表單：「＋外部人員」→ 名字（必填）＋聯絡（選填）；最近用過的外部名字下拉可選（從近 90 天事件彙整，不落表）。
- 衝突檢查對外部人員**用名字比**（同名視同一人）；不產工時、不進負載熱力圖、不進團隊的一週。
- 之後真的常合作 → 人力庫建成「兼職」，表單選人時就變內部人（名字一樣即可手動換）。

## 3. Google 日曆同步（owner：全部都同步）

### 3.1 一條線四種事件（`services/calendar_sync.py`，從 `api_shoots._sync_calendar` 抽出來）
| 事件 | 觸發 | Google 事件 | 取消／刪除 |
|---|---|---|---|
| 拍攝場次 | 既有（建／改／狀態） | `拍攝｜案名（客戶）`，橘（colorId 6），description＝人員／器材／備註／系統連結 | 取消＝刪事件（既有） |
| **工作登記** | POST／PUT／status | `工作｜標題（案名）`，藍（9）；會議 `會議｜…`、外出 `外出｜…`；description＝人員（含外部＋聯絡）／地點／備註／系統連結 | cancelled＝刪事件；done＝標題前加 `✓ `（留著，日曆上看得到做完了） |
| **里程碑** | `save_week`／`set_done`／`defer`／刪除 | 全天 `里程碑｜案名：標題`，紫（3）；description＝負責人／備註 | 刪＝刪事件；done＝`✓ ` 前綴；defer＝改日期（PATCH） |
| **請假** | 核准／撤回／退回 | 全天 `休假｜姓名（假別）`，灰（8）；0.5 天標「（半天）」 | 撤回或退回＝刪事件；只有**已核准**才上日曆 |

- 幂等：事件 id＝我們的 uuid（`upsert_event` 已處理 insert 409→PATCH）；`extendedProperties.private` 的鍵改成通用 `originsun_kind`＋`originsun_id`（`upsert_event` 讀鍵的那段改成認 `originsun_id`，舊的 `originsun_shoot_id` 保留相容）。
- best-effort：同步失敗只寫 `sync_error`，不擋操作（同場次）；每種事件都有「重新同步」（單筆）＋ 管理員的「全部重新同步」（`POST /api/v1/calendar/resync-all`：掃未來 180 天內 sync_error 非空或從沒同步過的，逐筆補；阻塞 I/O 一律 `asyncio.to_thread`）。
- 一次性回填：上線後管理員按「全部重新同步」把既有的未來里程碑與已核准的假補上去（不自動跑：老闆先確認共用日曆要不要看到假）。
- 憑證／日曆 ID 沿用 settings `google_calendar.*`（手機行事曆分頁的「行事曆設定」卡）；沒設就整條線中性不動。
- **通知**：owner 說排別人不用通知 → 不做 Chat 推播、不做前一晚 digest。Google 日曆本身就是通知（同事訂閱公司共用日曆即可）。

### 3.2 顏色與時區
- 全天：`start.date`／`end.date`＝訖日＋1（Google 開區間）；有時間：`dateTime`＋`Asia/Taipei`；跨午夜訖日隔天（沿用 `event_body` 那條）。
- 顏色只在 Google 端（colorId）；系統內的顏色由前端 token 決定（§5）。

## 4. 後端

### 4.1 純規則 `core/schedule_logic.py`（無 I/O，單元測試釘住）
- `KINDS`／`STATUSES`／`SLOTS`（上午／下午時段）；`attendee_norm(raw)`（清洗 attendees，外部人員名字必填、去重）；
- `conflicts(target, leaves, shoots, schedules)` → `[{name, kind: leave|shoot|schedule, what}]`（同人同日：已核准的假／已在別場 crew／已有全天工作）；
- `event_body_schedule(row, link)`／`event_body_milestone`／`event_body_leave`（Google 事件形狀，同 `shoot_logic.event_body` 的規矩）；
- `timesheet_rows_for_done(row, who, hours)` → `TimesheetManualRow` 形狀（work_date／project_id／project_name／task_note＝title／start_time／end_time／hours；全天＝`HOURS_PER_WORKDAY`、半天＝一半）。

### 4.2 路由 `routers/api_calendar.py`（prefix `/api/v1/calendar`）
| 端點 | 回什麼 | 守衛 |
|---|---|---|
| `GET /events?from=&to=&scope=all\|me\|project:<id>` | 統一事件流，一趟撈五種、後端合併排序：`[{kind: shoot|schedule|leave|holiday|milestone|plan, id, date, end_date, start_time, end_time, title, project_id, project_name, client, people:[{name, role, external}], location, status, mine, sync:{ok, error}}]`。`plan`＝我的一週計畫卡（只在 scope=me，且只有沒 schedule_id 的——有的話已經被 schedule 代表） | 登入＋`bulletin`（公布欄那把）；專案詳情宿主改帶 `crm_projects` 也放行 |
| `GET /options` | kind／status 字彙、時段快捷、人員（在職／合夥／兼職，含 id／name／role）、進行中專案、最近 90 天用過的外部人員名單、職務建議清單 | 同上 |
| `GET /conflicts?date=&end_date=&attendees=<json>&exclude=<id>` | §4.1 `conflicts` 的結果（存之前前端問一次） | 同上 |
| `POST /schedule`／`PUT /schedule/{id}` | 登記／改（含 attendees 差異）→ 一期連動計畫卡 → 同步 Google → 回最新列 | 自己（attendees 只有本人）＝綁定人員檔案；含別人或外部人員＝`crm_projects` 鑰匙（跟場次寫入同一條） |
| `POST /schedule/{id}/status {status}` | done／cancelled／回 planned；cancelled 刪 Google 事件 | 同上；建立者或 `crm_projects` |
| `DELETE /schedule/{id}` | 刪（連動計畫卡與 Google 事件） | 同上 |
| `POST /schedule/{id}/done {hours?}` | 「做了 ✓」：對呼叫者本人產一列 timesheets（`add_rows`，同 /timesheets/mine/rows），記進 `timesheet_ids`；已有就回同一列；本人不在 attendees → 409 | 綁定人員檔案 |
| `POST /schedule/{id}/resync`、`POST /resync-all` | 重新同步（單筆／全部，後者 admin） | admin |
| `GET /day?date=`（手機通告） | 本人那天：拍攝（含器材／地點／crew）＋被排的工作＋本週到期、我負責的里程碑 | 綁定人員檔案 |

規則：
- 私帳案（entity=mine）的案名對非私帳讀者照 `_team_row` 那條線：只給案名不給 id（同團隊的一週）。
- 里程碑／假／假日在行事曆**唯讀**，點了連到原本的地方改；它們的 Google 同步掛在各自的寫入端點（`milestone_service.save_week/set_done/defer`、假勤核准／撤回），不經 calendar 路由。
- 衝突**只提醒不擋**（人可以硬排）。
- 一期的計畫卡連動：`attendees` 只有本人且單日 → `add_rows([{work_date, project_id, project_name, task_note: title, plan: true}])`，把 id 存 `plan_row_id`；改日期／標題／案子 → 更新那張卡；取消／刪 → 刪卡。多人或多日**不**建卡（工作台的卡是「我的」）。

### 4.3 里程碑與假勤的改動（小）
- `services/milestone_service.py`：`save_week`（新增／改到期日）、`set_done`、`defer`、刪除各加一句 `await calendar_sync.sync("milestone", id)`。
- 假勤核准端點（`routers/api_hr.py` 的 approve／cancel／reject）：核准後 `sync("leave", id)`；撤回／退回 `unsync("leave", id)`。

### 4.4 migration／部署
- 新表 create_all；三欄 × 2 表進 `CRM_COLUMNS`；`ota_manifest` 不用動（都在既有目錄）。
- 發版順序：先發（欄位是 `IF NOT EXISTS`，舊碼看不到新欄也不會壞）→ 管理員按「全部重新同步」回填。
- NAS office-api：`main_office._ROUTER_MODULES` **不**掛 api_calendar（手機行事曆走主控；主控關機時只是看不到日曆，可接受）—— 加東西前跑 `test_office_surface`。

## 5. 前端：一份元件、三個宿主

### 5.1 共用元件 `frontend/js/shared/calendar/`（ES module，模式同 `ts-zone/`）
- `ctx.js`：狀態（view／scope／day／events）、端點表、宿主 hooks（`openShoot(preset)`、`openProject(id)`、`can(key)`）。
- `month.js`：月格（週一起）；每格最多 3 條＋「+N」；假日整格淡紅；今天描邊；點格→日檢視。
- `week.js`：兩種排法——「時間軸」（7 欄×時段，全天事件置頂）／「人員列」（人×日，跟團隊的一週同骨架；每人小計「N 天有排・請假 M 天」，連續 4 天以上標「負載高」）。外部人員不進人員列。
- `day.js`：那天所有事依時間排；拍攝展開器材／地點／crew；工作旁「改」；本人的工作旁「做了 ✓」（手機與桌機日檢視都有）。
- `form.js`：登記工作彈窗——做什麼／案子（打字找，可空）／日期＋結束日／全天・上午・下午・自訂／人員（預設自己；有 `crm_projects` 才能加別人；「＋外部人員」名字＋聯絡；每人一格職務）／地點／備註。存之前打 `/conflicts`，有衝突在表單裡列出來再按一次「照排」。
- `index.js`：`mountCalendar({host, scope, hooks})`；顏色 token：拍攝橘、工作藍、里程碑紫、假灰、假日淡紅、跟我有關深藍底（demo 那套）。
- 沒有拖拉改期（一期）；改期＝點事件→表單改日期。

### 5.2 宿主
| 宿主 | 位置 | 差異 |
|---|---|---|
| 公布欄 | 第三個子視圖「📅 行事曆」（`frontend/tabs/bulletin/`，`SUBVIEWS` 多一項） | 預設月／全公司；篩選：我的／全公司／某一案 |
| **專案詳情**（owner ①） | 專案管理詳情第九個分頁「行事曆」（`frontend/tabs/crm/crm-projects-calendar.js`，掛在既有 `proj-detail-tabs`；詳情現在是寬彈窗，放得下） | scope 鎖 `project:<id>`；「登記工作」「登記拍攝」預設這一案；多一段「這一案接下來」（未來 30 天清單） |
| 手機 | 既有「行事曆」分頁改三段：**今天（通告）／本週／登記**（`m/views/calendar.js`） | 拍攝表單原樣；工作登記表單同桌機（欄位少、預設自己）；「做了 ✓」在今天那段 |

### 5.3 權限（RBAC 三處同步：`core/auth.ALL_MODULES`／`tab-config.js`／`user-mgmt.js`）
- **不開新鑰匙**：讀＝`bulletin`；專案詳情那頁＝`crm_projects`（本來就要）；登記自己＝綁定人員檔案；排別人／外部人員＝`crm_projects`；「全部重新同步」＝admin。
- 守門測試 `test_rbac_module_sync` 不用動（沒新 key）。

## 6. 邊界情況（先寫下來，實作照這個做）
- 跨月的多日事件：月格每一天都畫（只在第一天顯示時間）。
- 事件掛的專案被刪：事件留著、`project_name` 顯示「（已刪除的專案）」，不連動刪。
- 已 done 的工作再改日期：允許（工時列不動，只改事件）；已產工時的工作被取消：事件刪、**工時列留著**（那是實際做過的）。
- 同一人同一天兩件全天工作：`/conflicts` 提醒「那天已排：…」，不擋。
- 請假被退回但事件已同步：退回端點刪事件；退回失敗（Google 掛掉）→ `sync_error`，「全部重新同步」會把「狀態不是已核准但有 google_event_id」的刪掉。
- 外部人員改名：舊事件不跟著改（沒有 id 可追）。
- 憑證沒設：整條同步線中性、畫面照常；options 回 `calendar_configured: false`，表單頂上一行「還沒接 Google 日曆（管理員在手機行事曆分頁設定）」。

## 7. 分期與檔案

| 期 | 內容 | 檔案 |
|---|---|---|
| **一期** | 新表＋`core/schedule_logic.py`＋`routers/api_calendar.py`（events／options／conflicts／schedule CRUD／status／resync）＋`services/calendar_sync.py`（抽共用、四種 event_body、工作登記同步）＋外部人員＋共用元件月／週／日／表單＋公布欄子視圖＋專案詳情分頁＋手機三段 | `db/models/_workos.py`、`db/migrations.py`、`core/schedule_logic.py`、`services/calendar_sync.py`、`routers/api_calendar.py`、`routers/api_shoots.py`（改用共用同步）、`frontend/js/shared/calendar/*`、`frontend/tabs/bulletin/*`、`frontend/tabs/crm/crm-projects-calendar.js`＋`crm-projects.html`、`frontend/m/views/calendar.js` |
| **二期** | 里程碑／請假上 Google（各自寫入端點掛同步＋三欄）＋「全部重新同步」回填＋「做了 ✓」→ 工時列＋人員列負載 | `services/milestone_service.py`、`routers/api_hr.py`、`db/migrations.py` |
| **三期（＝N2）** | 通告單分享連結給外部人員（token 制）、排班→預填→主管週結核可、排班 vs 實際工時對照 | 另開規劃 |

測試：`schedule_logic` 純函式（衝突／attendee 清洗／event_body／done→工時列）全覆蓋；路由用假 session 走一遍（同 `test_reminders`）；前端 `_srcscan` 釘三個宿主都掛同一份元件、權限線、外部人員不進人員列。

## 8. 明確不做
Google 日曆**反向**改回系統；甘特圖、拖拉改期；車輛／棚預約；付款里程碑上日曆；審核流程（登記即生效；N0 落地後再接）；任何推播通知（owner：不用）。
