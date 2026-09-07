# 員工假勤重整規劃（請假 ＋ 補休時數帳 ＋ Google 日曆）

> 2026-09-07 盤點；owner 指令「我要來做員工的休假登記了…現在用的（Notion）不太好用，而且希望員工的登記可以和 Google Calendar 連結」。
> 現況正本：Notion「個人工作區 › 每人一頁 › 😍 時數表」＋「請補修規章」＋ Google 表單「延長工時申請表」；
> repo 內已有 N-hr H2 極簡請假（`hr_leave_requests`），但員工端入口已關、沒有時數帳、沒有日曆同步。

---

## 0. 結論（先講）

1. **現在 Notion 那張表不是「請假單」，是每人一本「時數帳」**：一列＝一筆貸方（特休一天 8 小時一列、拍攝加班 16 小時一列、病假也記一列），
   在「補修日期」欄用掉（使用時數），公式算剩餘。這個模型是對的（他們的假是**小時**在算、補休來自拍攝加班），
   但用 Notion 做的代價是：每人一個資料庫要各自維護、特休要手動一列一列開、用掉要人手對到哪一列、沒有審核流、
   看不到誰哪天休、跟拍攝場次對不上。
2. **repo 的請假表是另一個模型（一張申請單：起迄／天數／狀態）**，沒有時數帳、天數手填、不扣假日、不能半天以下、核准不通知、
   員工端卡片 2026-09-05 被收掉了。兩邊都不能直接用。
3. **要做的是「時數帳 ＋ 申請單」二合一**：帳（credits）記進來的時數，申請單（requests）花出去，系統自動對帳（FIFO），
   核准後自動寫進公司 Google 日曆。日曆那條線**已經有現成的**（拍攝場次用服務帳號寫日曆，`services/google_calendar.py`），請假直接沿用。
4. 分三期：**一期＝帳本＋申請＋核准＋通知（員工可以開始登記）**；二期＝Google 日曆＋月曆檢視；三期＝補休申請取代 Google 表單、特休依年資自動發、一月結算、Notion 歷史匯入。

---

## 1. 現況盤點

### 1.1 Notion（現用）

- 結構：`個人工作區` 底下每人一頁（蘇家弘、黃聖鈞、劉禮瑜、連婕妤、蔡念栩、陳陵、李宜庭、Archive），每頁一個時數資料庫（例：`😍 蔡念栩`）。
- 欄位：`人員`(title)、`核准日`、`時數`（貸方）、`事由`（就職五年特休／coding101 拍攝／病假…）、`核准與否`（核准／拒絕）、
  `補修日期`（用掉的那天，可含時間）、`使用時數`（借方）、`狀態`（待休假／核准／拒絕／結算／彈藥／展延）、`備註`、`剩餘時數`（公式＝時數−使用時數）。
- 實際用法（蔡念栩 25 列樣本）：特休按「一天 8 小時一列」預開（就職五年特休 ×N、就職六年特休 ×N）；拍攝加班開一列（4h／16h／40h）；
  用掉時把「補修日期＋使用時數」填進**某一列**，狀態改核准；病假也記一列（時數 8／使用 8）。
- 規章（Notion `請補修規章`，2026-09-07 讀，9 條；每條只有表格欄位、頁面無正文）→ 對系統的意義：

  | 條 | 規章原文（摘） | 系統怎麼接 |
  |---|---|---|
  | 特休 | 6 月～1 年 3 日；1～2 年 7 日；2～3 年 10 日；3～5 年每年 14 日；5～10 年每年 15 日；10 年以上每年加 1 日至 30 日。當年未休完可協議延後一年或折薪 | `annual_hours_for(hire_date)` 級距表（×8 小時）；到期時「展延一年」或「折薪」兩種結算 |
  | 加班（補休） | 當年未使用完，可協議延後一年或折薪；**每年 1 月結算** | 補休 credit 到期日＝當年 12/31；一月結算精靈 |
  | 病假 | 未住院 1 年合計最多 30 天；住院 2 年最多 1 年；兩者並存 2 年最多 1 年。第 1 天給薪、1～30 天半薪、30 天後不給薪（留職停薪）；須證明 | 不進時數帳，記年度累計＋給薪比例欄；超過 30 天／需證明 → 送單時提醒 |
  | 事假 | 不給薪 | 記事實、標不給薪 |
  | 喪假 | 8 日：父母／養父母／繼父母／配偶；6 日：祖父母／外祖父母／配偶父母；3 日：曾祖父母／兄弟姊妹／配偶祖父母 | 假別「喪假」帶「關係」選項 → 自動給天數上限；給薪；須證明 |
  | 婚嫁 | 8 日，登記後生效；登記前 10 天起 3 個月內請完（雇主同意可延至 1 年）；給薪；須證明 | 假別「婚假」帶登記日 → 期限提醒 |
  | 颱風假 | 颱風假如果有工作：比照假日加班 | 假日表要能標「颱風假」（臨時、當天公告）；當天有工作 → 產生補休 credit（假日加班費率，見 §5 待拍板） |
  | 請假時間點 | 理想 1 個月前，最晚 1 週前提出並簡述理由；例外另與主管討論 | 送單時算「距開始幾天」：<7 天黃字提醒（不擋），事由必填 |
  | 消假 | 休假前兩天消假；臨時消假要跟主管會報討論。遇颱風假可在颱風假前兩天評估是否消假；颱風假公告當日無法消假 | 已核准的單：開始前 ≥2 天可自己撤回；<2 天只能「申請消假」進待審由主管決定；颱風假公告當日鎖住不可消 |
- 旁邊還掛：Google 表單「延長工時申請表」（加班申請）、一份 Google Sheet。

### 1.2 repo（已有）

| 面向 | 現況 | 缺口 |
|---|---|---|
| 資料 | `hr_leave_requests`（staff、假別、起迄日、days 手填 0.5 步進、待審／已核准／已退回、approved_by/at） | 無時數帳、無半天以下、無撤回狀態（撤回＝硬刪）、無日曆同步欄位 |
| 額度 | `crm_staff.annual_leave_days` 一個整數，餘額＝整數 − 當年已核准特休 SUM | 無年資自動算、無年度切分／展延、補休／病假無額度 |
| 規則 | `core/hr_logic.validate_leave`（假別白名單、迄≥起、0.5 倍數） | 天數不對起迄、不扣週末與國定假日、無重疊檢查、無假日表 |
| API | 管理端 `/api/v1/hr/leave*`（列／代登／PUT 含核准／刪／額度）、員工端 `/api/v1/me/leave`（送單、撤回） | 核准與編輯同一支、無審核歷程、核准／退回不通知申請人 |
| 通知 | 送單 → Google Chat 群（`notifier` 模板 `leave_request`） | 只有這一條 |
| 前端 | 管理 tab「請補修」（待核佇列／紀錄／額度）；員工頁「我的請假」卡**存在但沒畫出來**（`ME_ZONE_ON` 只剩零用金）；團隊的一週有「休假」灰格 | 員工實際上沒有送單入口；手機版無請假；無月曆 |
| Google 日曆 | `services/google_calendar.py`：服務帳號寫公司共用日曆、單向、冪等 upsert／delete；拍攝場次已接（`api_shoots._sync_calendar`） | 請假零接線；`settings.google_calendar.calendar_id / service_account_json` 目前兩者皆空 |

---

## 2. 目標模型：時數帳 ＋ 申請單

單位一律**小時**（8 小時＝1 天；顯示時同時給「天」）。理由：Notion 已經是小時、補休天生是零碎小時、拍攝加班 4h／16h 才記得下。

### 2.1 三張表

**`hr_leave_credits`（貸方：進來的時數）**

| 欄位 | 說明 |
|---|---|
| id, staff_id, staff_name | |
| kind | `特休` / `補休` / `其他`（婚假／喪假等法定給假也可用它發） |
| hours | 貸方時數 |
| granted_on | 生效日（特休＝週年日；補休＝加班日） |
| expires_on | 到期日（特休＝隔年週年日；補休＝當年 12/31；展延＝再一年） |
| source | `annual_auto`（年資自動發）／`overtime`（加班申請）／`manual`（管理員手開）／`import`（Notion 匯入） |
| reason | 事由（「coding101 拍攝」「就職六年特休」） |
| shoot_id | 可選：對到 `crm_shoots`（從場次帶入） |
| status | `待審`（加班申請中）／`可用`／`結算`（折薪）／`展延`（換成一筆新的 credit）／`拒絕` |
| approved_by / approved_at, note, created_* | |

**`hr_leave_requests`（借方：花出去）— 既有表擴充**

| 新增／修改 | 說明 |
|---|---|
| hours | 取代 days 當正本（days 保留為 hours/8 的鏡射，舊資料 days×8 回填） |
| start_time / end_time | 可選；半天（上午／下午）或時段（14:00 後外出看診）；沒填＝整天 |
| leave_type | 加 `補休`；`特休`／`補休` 走時數帳，`病假`／`事假`／`公假`／`婚假`／`喪假` 走年度上限（規章的天數）不扣帳 |
| status | 加 `已撤回`（不再硬刪）；`已退回` 帶 `reject_note` |
| google_event_id / synced_at / sync_error | 同 `crm_shoots` 三欄 |
| paid | 給薪比例（病假 30 日內半薪等）— 三期再算，一期先存事實 |

**`hr_leave_allocations`（對帳：這張申請單吃了哪幾筆 credit 多少小時）**

| 欄位 | 說明 |
|---|---|
| request_id, credit_id, hours | 核准時系統 FIFO 自動分配（先到期先扣）；撤回／退回時釋放 |

→ 這就是 Notion「把使用時數填到某一列」那件事，只是**系統自己對**，人不用挑列。
餘額＝Σ 可用 credit.hours − Σ allocations.hours，任何時候都能重算，不存快照。

**`hr_holidays`（假日表）**：date、name、kind（`國定假日`／`補班日`）。從行政院人事總處的年度行事曆匯入（政府開放資料 CSV），管理員可改。
時數自動算：起迄 × 工作日（扣週末、扣國定假日、補班日算上班）× 8，半天／時段照填的算。

### 2.2 純函式（`core/leave_logic.py`，全部可單元測試）

- `annual_hours_for(hire_date, on) -> hours`：勞基法級距（規章那張表）。
- `working_hours(start, end, start_time, end_time, holidays) -> hours`。
- `allocate(credits, hours) -> [(credit_id, hours)]`：FIFO 先到期先扣，不夠 → 422「特休／補休不足」。
- `balance(credits, allocations, kind, on)`。
- `settle_year(credits, allocations, year) -> [展延 / 折薪 建議]`（三期）。
- `leave_event_body(request, staff)`：Google 日曆事件（同 `core/shoot_logic.event_body` 形狀）。

---

## 3. 流程

### 3.1 員工登記（桌機 `/my.html` ＋ 手機 `/m/`）

- 「我的假勤」卡回來（`ME_ZONE_ON` 加回）：三個數字 **特休剩餘 X h（Y 天）／補休剩餘 Z h／病假已用 N 天**，下面兩顆鈕。
- **請假**：假別 → 起日／迄日 → 整天／上午／下午／時段 → 系統算出「共 12 小時（1.5 天），扣特休後剩 …」→ 事由 → 送出。
  - 送出前檢查：與自己已送／已核准的期間重疊 → 擋；期間內是某場次 crew → **黃字警告不擋**（「9/14 你在『思沙龍 EP02』拍攝名單」）；
    特休／補休不足 → 擋並說差幾小時；晚於「最晚一週前」→ 黃字提醒（規章）。
- **申請補休時數**（三期）：加班日／時數／事由（可從場次帶入：日期、案名自動填），取代 Google 表單。
- 清單：待審可撤回；已核准顯示「已進公司日曆」。
- 手機版 `/m/` 加「假勤」分頁：同三個數字＋請假表單＋清單（跟報價一樣走 BFF）。

### 3.2 核准（管理 tab「請補修」重整）

- 待核佇列：一張卡＝一單，顯示 期間／時數／餘額夠不夠／同期間還有誰休／有沒有撞場次；**核准／退回（必填理由）** 兩顆鈕，各自專屬端點
  （`POST /hr/leave/{id}/approve`、`/reject`），不再與欄位編輯共用 PUT。
- 核准當下：FIFO 分配寫 `hr_leave_allocations` → 寫 Google 日曆 → 通知申請人。
- 月曆檢視：整月格子，每天列出休假的人（已核准實色、待審虛線）＋ 拍攝場次，一眼看人力。
- 時數帳頁：每人一列（特休／補休 可用／已用／到期），點開看 credit 明細（＝Notion 那張表的替代品，但全公司一頁）。
- 代登、手開 credit（管理員補時數）、假日表維護。

### 3.3 通知

| 事件 | 收件人 | 管道 |
|---|---|---|
| 送單 | 管理群 | Google Chat webhook（既有） |
| 核准／退回 | 申請人 | **新增**：Google Chat 群貼一條 @姓名（現有 webhook 只能貼群）；若要私訊需另接 Chat API／LINE，一期先貼群 |
| 前一天提醒「明天 X 休假」 | 管理群 | 排程（既有 scheduler），二期 |

### 3.4 Google 日曆（二期）

- 沿用 `services/google_calendar.py`（服務帳號寫公司共用日曆）。事件：`休假｜蔡念栩（特休 1 天）`，整天用 date、時段用 dateTime；
  description 放假別／時數／事由／系統連結；`extendedProperties.private.originsun_leave_id`。
- 時機：**核准才寫**（待審不寫），退回／撤回 → delete，改期 → patch。單向（系統→日曆），跟場次一樣「明確不做反向」。
- 設定：可與場次同一本日曆，或另設 `google_calendar.leave_calendar_id`（預設沿用 `calendar_id`）。日曆要分享給全員（唯讀）。
- **前置（owner 動作）**：`settings.google_calendar.calendar_id` 與 `service_account_json` 目前是空的——把公司日曆分享給服務帳號（可編輯）並貼日曆 ID，
  這一步做完，拍攝場次和請假就同時上日曆。

---

## 4. 分期與工作量

| 期 | 內容 | 估計 |
|---|---|---|
| **一期：帳本＋申請＋核准＋通知** ✅ 2026-09-07 落地（v2.4.378；三個介面 Playwright 全流程驗過） | credits／allocations／holidays 三表＋遷移；`core/leave_logic` 純函式＋測試；時數自動算；申請單擴充（小時、時段、撤回）；專屬核准／退回端點；FIFO 分配；員工卡回歸＋手機分頁；管理佇列重整；核准／退回通知；特休額度先由管理員手開 credit（年資自動發放三期） | 2 個 session |
| **二期：日曆** | `leave_event_body`；核准／退回／撤回接 `google_calendar`；設定頁加日曆 ID；管理 tab 月曆檢視；手機行事曆分頁疊休假；前一天提醒 | 0.5～1 session |
| **三期：來源與結算** | 補休申請取代 Google 表單（可從場次帶入）；特休依到職日自動發（需 7 人 `hire_date` 齊）；一月結算精靈（展延 → 新 credit／折薪 → 產一筆應付）；病假半薪標記；Notion 匯入（每人資料庫 CSV → credits＋allocations，含預覽） | 1.5 session |

---

## 5. 需要 owner 拍板

1. **單位用小時**（8h＝1 天，畫面同時顯示天）— 建議是，跟 Notion 與拍攝加班一致。
2. **病假／事假／婚喪假**：只記事實＋年度上限提醒，不進時數帳 — 建議是。
3. **核准通知**：一期先貼 Google Chat 群 @人；要私訊再開 Chat API 或 LINE。
4. **日曆**：跟拍攝場次同一本，還是另開「源日休假」日曆？（另開的話員工訂閱比較乾淨）。
5. **補休申請**要不要取代 Google 表單（三期），還是表單留著、管理員手開 credit。
6. **Notion 歷史要不要匯入**（7 人、每人一個資料庫；不匯的話用「期初餘額」一筆 credit 帶進來就好——建議期初餘額，快很多）。
7. **特休自動發放**依到職日（勞基法級距）— 要的話請先把 7 人的到職日填齊。
8. ✅ **假日加班補休雙倍**（owner 2026-09-07）：平日加班 1:1、假日（週末／國定假日／颱風假）加班 1:2 → `HOLIDAY_OT_MULTIPLIER = 2`。
9. ✅ **消假硬擋**（owner 2026-09-07）：已核准的單開始前 ≥2 天可自己撤回；<2 天只能「申請消假」進待審由主管決定；颱風假公告當日不可消。
10. ✅ **補休到期**（owner 2026-09-07）：加班發生那個曆年的 12/31 到期，隔年 1 月結算（展延一年或折薪）。

> 1～7 owner 未回，一期先照建議值做（小時制、病事假不進帳、核准通知貼 Chat 群）；4～7 是二三期的事，屆時再問。

---

## 7. 一期規格（API 契約；2026-09-07 開工）

### 7.1 字彙（`core/leave_logic.py`，前端從 `/api/v1/me/leave/summary.vocab` 拿，不寫死）

- `LEDGER_TYPES = ("特休", "補休")`：走時數帳。`RECORD_TYPES = ("病假", "事假", "公假", "婚假", "喪假", "其他")`：只記事實。
- `REQUEST_STATUSES = ("待審", "已核准", "已退回", "已撤回", "消假待審")`。`CREDIT_STATUSES = ("待審", "可用", "展延", "結算", "拒絕")`。
- `PARTS = ("all", "am", "pm", "range")`：整天 8h／上午 4h／下午 4h／時段（迄−起，四捨五入到 0.5h，上限 8h）。
- `HOURS_PER_DAY = 8`、`HOLIDAY_OT_MULTIPLIER = 2`、`NOTICE_DAYS = 7`（不足黃字）、`CANCEL_FREE_DAYS = 2`、`SICK_CAP_DAYS = 30`。

### 7.2 表

- `hr_leave_credits`：id, staff_id, staff_name, kind(特休/補休/其他), hours(Float), granted_on(Date), expires_on(Date, nullable), source(annual_auto/overtime/manual/import), reason, shoot_id(nullable), status, approved_by, approved_at, note, created_by, created_at, updated_at。索引 (staff_id, status)。
- `hr_leave_allocations`：id, request_id, credit_id, hours, created_at。唯一 (request_id, credit_id)。
- `hr_holidays`：date(Date, PK), name, kind(國定假日/補班日/颱風假), created_at。
- `hr_leave_requests` 加欄：hours(Float)、part(String 8, 預設 all)、start_time／end_time(String 5, nullable)、reject_note(Text)、cancel_note(Text)、google_event_id／synced_at／sync_error。開機回填 `hours = days*8 WHERE hours IS NULL`。新表走 create_all；加欄進 `main.py _crm_cols`。

### 7.3 純函式（`core/leave_logic.py`，全部有測試）

- `annual_days_for(hire_date, on) -> int`（規章級距；三期才自動發，一期先給函式）。
- `is_workday(d, holidays) -> bool`：週一～五且不在假日表；補班日的週六算工作日。
- `working_hours(start, end, part, start_time, end_time, holidays) -> float`。
- `allocate(credits, hours) -> list[(credit_id, hours)]`：status=可用、expires_on 未過，先到期先扣；不足 raise `InsufficientHours(short)`。
- `balance(credits, allocations, pending_hours, kind, on) -> {available, reserved, expiring_soon}`。
- `notice_warning(start, today)`、`cancel_mode(start, today, holidays) -> "free"|"apply"|"locked"`。
- `overtime_credit_hours(hours, on, holidays) -> float`（假日 ×2；三期的加班申請用）。
- `leave_event_body(request, staff)`（二期用，一期先寫、有測試）。

### 7.4 員工端（`routers/api_me.py`，守衛 `require_bound_staff(request, "me_leave")`，另加任一 me_* 鑰匙即可讀 summary）

| 端點 | 說明 |
|---|---|
| `GET /api/v1/me/leave/summary` | `{vocab, balances:{特休:{available,reserved,expiring:[{hours,expires_on}]}, 補休:{…}}, sick:{used_days,cap_days}, requests:[最近 20 筆], pending_count}` |
| `POST /api/v1/me/leave/preview` | body `{leave_type,start_date,end_date,part,start_time,end_time}` → `{hours, days, errors:[{code,msg}], warnings:[{code,msg}]}`；errors：overlap／insufficient／bad_range；warnings：notice_short／shoot_conflict（帶案名）／sick_cap |
| `POST /api/v1/me/leave` | 同 body ＋ reason（必填）→ errors 非空回 422；成功建 待審、`notify_tab_async("leave_request")`（既有）|
| `POST /api/v1/me/leave/{id}/cancel` | 待審→已撤回；已核准依 `cancel_mode`：free→已撤回（釋放 allocations、二期刪日曆）／apply→消假待審（cancel_note 必填）／locked→409 |

### 7.5 管理端（`routers/api_hr.py`，`check_admin_or_module(request, "hr_leave")`）

| 端點 | 說明 |
|---|---|
| `GET /hr/leave?status=&staff_id=&year=` | 既有，多回 hours/part/時間/reject_note/cancel_note |
| `GET /hr/leave/{id}/context` | `{balance, same_period:[{staff_name,start,end}], shoot_conflicts:[{date,project_name}], notice_days}` |
| `POST /hr/leave/{id}/approve` | 待審→已核准：走時數帳的先 `allocate` 寫 allocations（不足 422）；通知申請人（`leave_result`）|
| `POST /hr/leave/{id}/reject {note}` | 待審→已退回（note 必填）；通知 |
| `POST /hr/leave/{id}/cancel_decide {approve:bool, note}` | 消假待審→已撤回（釋放）或回已核准；通知 |
| `PUT /hr/leave/{id}` | 只改欄位，**不再接受 status**（改狀態一律走上面三支） |
| `GET /hr/balances?year=` | 全員：特休／補休 available／reserved／到期、病假已用 |
| `GET /hr/credits?staff_id=&year=&status=` | credit 明細（含每筆已被扣多少）|
| `POST /hr/credits` | 手開：staff_id, kind, hours, granted_on, expires_on, reason → 可用 |
| `POST /hr/credits/{id}/approve` ／ `/reject` | 待審 credit（三期加班申請用；一期先給）|
| `DELETE /hr/credits/{id}` | 沒有 allocations 才准 |
| `GET/POST/DELETE /hr/holidays` | 假日表；`POST /hr/holidays/import {csv}` 吃行政院行事曆 CSV（欄：西元日期、星期、是否放假、備註）|

通知模板（`notifier.py`）：`leave_result` →「【請假{result}】{staff_name}：{leave_type} {start}～{end}（{hours} 小時）{note}」貼 Google Chat 群。

### 7.6 前端

- 桌機員工頁 `frontend/my.html`：`ME_ZONE_ON` 加回 `me_leave`；「我的假勤」卡：三個數字（特休剩餘 h／天、補休剩餘、病假已用/30）＋請假表單（假別、起迄、整天/上午/下午/時段、即時 preview 顯示「共 X 小時、警告」、事由）＋近期清單（待審撤回／已核准依 cancel_mode 顯示「撤回」或「申請消假」）。
- 管理 tab `frontend/tabs/hr_leave/`：待核佇列卡（期間／時數／餘額夠不夠／同期誰休／撞場次；核准／退回(理由)／消假決定）、請假紀錄（篩選＋代登）、時數帳（全員一列一人：特休／補休 可用／保留／到期；展開 credit 明細＋手開）、假日表（清單＋貼 CSV 匯入）。
- 手機 `frontend/m/`：新分頁「假勤」（`views/leave.js`）：同三個數字＋請假表單（同 preview）＋清單；走 `/api/v1/me/leave/*`，只 import `./shell.js`／`../ui.js`。

---

## 6. 不動的地方／地雷

- `hr_leave_requests` 既有欄位不刪（days 保留鏡射），舊資料 `hours = days × 8` 一次回填。
- 日曆同步只在**核准後**發生，且失敗只寫 `sync_error` 不擋核准（同場次的規則）。
- 時數帳餘額**不存快照**，一律由 credits − allocations 重算（同預支款、私帳已收的教訓：存快照會被加兩次）。
- 週末／假日算法只住 `core/leave_logic.working_hours`；`hr_logic.is_workday` 那條是給工時用的，不要混用。
