# 週記與工作紀錄 — 規劃（員工端主軸）

> 2026-09-05。owner：規劃主軸先以**週記、工作紀錄**為主，功能細一點。
> 鐵則沿用：員工端不出現任何個人工時累計（本週／當日／每案／超時）；記工時是輸入不是報表。
> 上一版（專案紀錄整合 staff、工作台版面）在 `docs/STAFF_PROJECT_RECORD_PLAN.md`，本文件是它的前置——先把「每天記什麼、每週寫什麼」做好，專案紀錄是從這兩層聚合出來的結果。

## 0. 一條線：日 → 週 → 案

```
工作紀錄（每天，一列＝一件事）      週記（每週五，四問＋自動區）        專案紀錄（聚合，唯讀）
timesheets 我的一天                  work_journals + 四張 entries         每案時間線／我做過的
  案子、分類、內容、里程碑標記  ──►  「本週做了什麼」自動帶入        ──►  里程碑進時間線
  計畫列／實際列                       四問手寫、可掛案子、可求助             週記片段掛在案子底下
```

同一列資料三處看，不重複輸入。工作紀錄是唯一的輸入源，週記是每週的整理與反思，專案紀錄是結果。

## 1. 現況

**工作紀錄（我的一天）**：`GET/POST/PUT/DELETE /timesheets/mine*`。一列＝日期、案子、工作分類（九類 `WORK_TYPES`）、內容 `task_note`、
員工備註 `remark`、狀態（計畫 `plan`／實際 `draft`）、來源（手填／Sheet）。有「複製昨天」、計畫列按「完成」變實際、
與 Sheet 撞到變衝突待決（三選一）。桌機在工作追蹤分頁與 `/hours.html`；手機沒有入口。

**週記**：一人一週一份（週一正規化），四區：順利的事與想感謝的人、遇到的挑戰、學到了什麼、其他。`PUT /journal/mine` 全量替換，
`editable_window_ok` 控可寫窗口。讀：本週有寫的人、某人歷週、學習庫（跨週搜「學到了什麼」）。桌機 `/journal.html` 以 iframe
嵌在個人工作台；手機沒有。

**兩者的缺口**
- 週記與工作紀錄**不相連**：週五要回想這週做了什麼，全靠記憶手打。
- 工作紀錄只有文字，**沒有里程碑**（交付、送審、上架、結案）——所以專案時間線做不出來。
- 沒有提醒：沒填今天、沒寫週記，沒人知道。
- 週記寫了沒人回：「遇到的挑戰」寫了，主管看不看、回不回，員工不知道。
- 沒有掛案子：週記條目對應哪個案子只能從文字猜。
- 手機兩者都沒有入口（現場最需要記的地方）。

## 2. 功能細項

### A. 工作紀錄（每日）

| # | 功能 | 細節 | 資料／端點 |
|---|------|------|-----------|
| A1 | 今天的列 | 案子（打字找）、工作分類、內容、備註、計畫或實際。**不顯示合計**。 | 既有 `my_day`／`mine/rows`；前端把 `planned_total`／`actual_total` 拿掉不畫 |
| A2 | 從場次帶入 | 今天我在 crew 的拍攝場次 → 一鍵長出一列「拍攝｜案名｜地點」，內容預填場次標題。 | `GET /shoots?from=今天&to=今天` 過濾 crew 含我；前端組列後走既有 `POST /mine/rows` |
| A3 | 從待辦帶入 | 公布欄指派給我的事 → 一鍵成一列（分類「其他」或自選），做完同時把待辦標 done。 | `/me/workspace.todos` ＋ `PUT /bulletin/{id}` |
| A4 | 複製昨天／複製上週同一天 | 既有複製昨天；加「上週同一天」（週期性工作）。 | `list_rows` 多取一天 |
| A5 | 里程碑標記 | 一列可標一個：交付、送審、修改回、上架、結案、會議、備份完成。標了的列進專案時間線與週記自動區。 | `timesheets.tag` 新欄（String 16，nullable）；字彙 `core.hr_logic.MILESTONE_TAGS`，`/options` 給字 |
| A6 | 內容帶連結 | 內容裡貼看片連結、報表連結、NAS 路徑，顯示時自動變可點（路徑可複製）。不加欄位。 | 前端 linkify；路徑走既有「複製」按鈕 |
| A7 | 語音記一句 | 現場對手機講一句 → Whisper 轉文字 → 填進內容。 | 既有 `transcribe` 引擎，新增小端點 `POST /timesheets/mine/dictate`（上傳 10 秒內音檔回文字）；只在手機 |
| A8 | 手機「我的一天」 | `/m/` 新分頁「紀錄」：今天的列、A2／A3／A4 三顆帶入鈕、計畫列按「完成」。 | 同 A1 端點；BFF 不需要 |
| A9 | 沒填提醒 | 平日 18:00 今天沒有實際列 → Chat／LINE 一則「今天還沒記」，帶連結。可關。 | `core/scheduler` 加一支；通知偏好見 D3 |
| A10 | 修正與留痕 | 既有：自己列可改；與 Sheet 撞到變衝突三選一。 | 既有 |
| A11 | 查自己的紀錄 | 按案子、按日期區間、按分類、按里程碑找自己寫過的列；**只列不算**。 | 既有 `mine`（加 `project_id`／`tag`／`q` 篩選） |

### B. 週記（每週）

| # | 功能 | 細節 | 資料／端點 |
|---|------|------|-----------|
| B1 | 自動區「本週做了什麼」 | 打開週記先看到本週工作紀錄按案子分組：案名、每天做的事、里程碑（交付／送審…）。唯讀、可勾選「帶入順利的事」。**不顯示小時**。 | `GET /journal/mine` 回應加 `worklog: [{project, days:[{date, items:[{task_note, tag}]}]}]`（從 timesheets 撈，純函式 `core.journal_logic.group_worklog`） |
| B2 | 四問照舊 | 順利的事／挑戰／學到／其他，一行一條。 | 既有 |
| B3 | 條目掛案子 | 每條可選一個案子（打字找），不選也行。掛了的條目出現在專案紀錄「週記片段」。 | 四張 `journal_*` 加 `project_id`（nullable） |
| B4 | 求助與討論標記 | 「挑戰」與「其他」每條可標「需要協助」或「想討論」。標了 → 進主管的「等我處理」；主管回覆後員工看到。 | 四張 `journal_*` 加 `flag`（String 16，nullable：help／discuss）；新表 `journal_replies`（id, entry_table, entry_id, username, content, created_at） |
| B5 | 主管回覆 | 主管在「本週大家」逐條回；員工在自己那週看到回覆與時間。回覆不改原文。 | `POST /journal/reply`、`GET /journal/mine` 帶 `replies` |
| B6 | 可寫窗口 | 既有 `editable_window_ok`；細化：逾期想補 → 按「申請補寫」，主管開窗 7 天。 | `work_journals.reopen_until`（Date, nullable）；`POST /journal/reopen` 主管 |
| B7 | 提醒 | 週五 16:00 沒寫 → 本人一則；週一 09:00 主管一則「上週誰沒寫」。可關。 | `core/scheduler`；通知偏好 D3 |
| B8 | 本週大家 | 既有清單改卡片流：每人一卡，四問摘要＋「需要協助」紅點；點開看全文與自動區。 | 既有 `/journal/week`（加 flag 計數） |
| B9 | 搜尋與篩選 | 學習庫既有；擴成全區搜尋（四區＋自動區文字），可按案子、按人、按期間篩。 | `GET /journal/search?q=&project_id=&username=&from=&to=` |
| B10 | 匯出 | 某人某期間週記 → Markdown（貼 Notion 用）。既有 Notion 匯入是反向，兩邊字段對齊。 | `GET /journal/export.md` |
| B11 | 季回顧（選做） | 每季自動整理「這季做過的案子、里程碑、學到的東西」，claude 摘要成一頁，唯讀，可改。 | 走既有 claude runner 模式，預設關 |
| B12 | 手機週記 | `/m/` 「紀錄」分頁第二段：四問 textarea＋自動區摺疊；離線打字不掉（localStorage 草稿）。 | 同 B1 端點 |

### C. 兩者串接（不重複輸入）

| # | 功能 | 細節 |
|---|------|------|
| C1 | 里程碑 → 三處 | A5 標了的列：週記自動區顯示、專案時間線顯示、「我做過的」顯示。一份資料。 |
| C2 | 週記條目 → 案子 | B3 掛了案子的條目：專案頁「週記片段」分頁（唯讀，管理者看得到誰寫的）。 |
| C3 | 案子 → 工作紀錄 | 專案頁一鍵「記一筆」：帶案子開我的一天新列。 |
| C4 | 待辦 ↔ 工作紀錄 | A3 帶入後，列與待辦互連（`timesheets.bulletin_id` nullable）；列標實際＝待辦 done。 |

### D. 通知與設定

| # | 功能 | 細節 |
|---|------|------|
| D1 | 事件通知 | 主管回覆了我的週記、我的求助被回、待辦指派給我 → 本人一則。 |
| D2 | 排程提醒 | A9 沒填今天、B7 沒寫週記、主管週一摘要。 |
| D3 | 通知偏好 | 個人資料卡：走 Chat 或 LINE、要不要 A9／B7、安靜時段。`users.notify_prefs`（JSON）。既有 notifier 只有全域 webhook → 加「個人 webhook／LINE token」欄位。 |

### E. 主管面（最小）

| # | 功能 | 細節 |
|---|------|------|
| E1 | 誰沒填 | 工作追蹤 dashboard 既有 `missing_fillers`；加「本週沒寫週記的人」。 |
| E2 | 求助清單 | 所有 `flag=help` 未回覆的條目，一頁回完。 |
| E3 | 開窗 | B6 申請補寫的核准。 |

## 3. 資料模型變更（全部可空、`ALTER … IF NOT EXISTS`）

| 表 | 欄位 | 用途 |
|----|------|------|
| `timesheets` | `tag` String(16) | 里程碑（A5） |
| `timesheets` | `bulletin_id` String(32) | 從待辦帶入的連結（C4） |
| `journal_wins/challenges/learnings/others` | `project_id` String(32)、`flag` String(16) | 掛案子（B3）、求助標記（B4） |
| `work_journals` | `reopen_until` Date | 補寫窗口（B6） |
| `journal_replies`（新表） | id, entry_table, entry_id, journal_id, username, content, created_at | 主管回覆（B5） |
| `users` | `notify_prefs` JSON | 通知偏好（D3） |

不加任何「小時合計」欄位；`hours` 照舊只在總表與主管報表用。

## 4. API 增修

| 端點 | 動作 |
|------|------|
| `GET /timesheets/mine` | 加 `project_id`／`tag`／`q`／`from`／`to` 篩選（A11）；回應不帶合計（前端也不畫） |
| `POST /timesheets/mine/rows` | 列可帶 `tag`、`bulletin_id` |
| `POST /timesheets/mine/dictate` | 音檔 → 文字（A7，手機） |
| `GET /timesheets/options` | 加 `milestone_tags` |
| `GET /journal/mine` | 加 `worklog`（B1）、`replies`（B5）、`reopen_until` |
| `PUT /journal/mine` | 條目可帶 `project_id`、`flag` |
| `POST /journal/reply`、`GET /journal/help` | B5、E2 |
| `POST /journal/reopen/request`、`POST /journal/reopen` | B6、E3 |
| `GET /journal/search`、`GET /journal/export.md` | B9、B10 |
| `PUT /me/notify_prefs` | D3 |

純規則進 `core/journal_logic.py`（分組、可寫窗口、求助判定）與 `core/hr_logic.py`（里程碑字彙、normalize），各加單元測試。

## 5. 介面

**桌機 `/my.html`「我要記錄」區**（取代連結按鈕，直接內嵌）
- 「我的一天」卡：今天的列＋三顆帶入鈕（場次／待辦／昨天）＋里程碑下拉；沒有任何數字合計。
- 「本週週記」卡：自動區（本週做了什麼，按案子）在上，四問在下；條目旁「掛案子」「需要協助」；主管回覆以灰底顯示在條目下。
- 週五 16:00 後沒寫 → 卡片頂端一條提醒。

**手機 `/m/` 新分頁「紀錄」**（放第一顆）
- 上半：我的一天（同上，含語音記一句）。
- 下半：本週週記（摺疊自動區＋四問）。
- 離線草稿：localStorage，回線自動送。

**專案頁**：「週記片段」分頁（C2）、「記一筆」按鈕（C3）。
**工作追蹤（主管）**：本週大家卡片流（B8）、求助清單（E2）、誰沒寫（E1）。

## 6. 分期

| 期 | 內容 | 驗收 |
|----|------|------|
| 一｜工作紀錄細化 | A2 場次帶入、A3 待辦帶入、A5 里程碑、A6 連結、A8 手機我的一天、A11 篩選；前端拿掉合計 | Playwright：今天有場次 → 一鍵長列；標里程碑後 `GET mine?tag=` 找得到；畫面 grep 不到「小時」合計 |
| 二｜週記串接 | B1 自動區、B3 掛案子、B4/B5 求助與回覆、B6 補寫、B8 卡片流、B12 手機週記 | 週五打開週記看得到本週按案子分組；標「需要協助」→ 主管清單出現 → 回覆後員工看到 |
| 三｜提醒與設定 | A9、B7、D1–D3、E1 | 排程在 dev 8001 跑一次假時間；通知走 owner 指定的測試群 |
| 四｜聚合 | 專案時間線、我做過的、週記片段（接 STAFF_PROJECT_RECORD_PLAN §4） | 依該文件 |
| 選做 | A7 語音、B9 搜尋、B10 匯出、B11 季回顧 | — |

## 7. 待拍板

1. 里程碑字彙：交付、送審、修改回、上架、結案、會議、備份完成——夠不夠、要不要「拍攝完成」。
2. 週記逾期補寫：要主管開窗，還是一律開放補寫但標「補寫」。
3. 主管回覆要不要即時通知本人（D1），還是只在頁面上看到。
4. 週記「本週大家」全員可讀照舊，還是「需要協助」那類只給主管看。
