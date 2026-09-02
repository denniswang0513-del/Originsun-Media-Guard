# 工時 Google Sheet 歷史匯入 ＋ 持續同步 —— 執行規劃

> 2026-09-02 定稿。對象：owner 的工時試算表
> `1lITSt66JEQuLJzILMmthpVZ7MZyWv9b4PX9tCNpV_As`。
> 取代藍圖 §3.6 階段 0/1 的抽象描述 —— 那份是照假設寫的，這份是照真表寫的。

## 0. 一句話

**9,845 列全部進得來、98% 自動對到私帳專案、預算 367 案可一併灌進去；**
真正的工作是三件小改動（對映表、匯入路徑、同步腳本修正）＋ owner 四個拍板。

## 1. 資料事實（2026-09-02 實際抓表驗過）

### 1.1 試算表結構

| 分頁 | 角色 | 事實 |
|---|---|---|
| **工作紀錄表** | 正職的**輸入面**（owner 貼的 gid 就是它） | 資料從**列 7** 起，A–H＝日期/人員/專案/內容/製作時間(時)/備註/子專案標籤/狀態；**8,470 列、值、底部追加** |
| **助理工作紀錄表** | 助理的輸入面 | 是第二本試算表 `1vVDw…` 的 IMPORTRANGE（公式），同欄位、列 7 起；**1,374 列、底部追加**；那本檔不公開 |
| 總表（勿動） | 上兩者的 QUERY 聯集，`ORDER BY 日期 DESC` | 9,845 列 ＝ 8,470 ＋ 1,374 ＋ 1；**新列在最上面** |
| 專案狀態(勿動) | 專案清單 ＋ 預算 | 413 案；`預算 ＝ 剩餘工作時數 ＋ 實際工作時數`（驗證：快樂學游泳 −528.3＋1195.4＝667.1，使用率 1.79＝1195.4/667.1 ✓）；≤0 ＝ 沒設 |
| (備份) | 工作紀錄表的 FILTER 複本 | 不用 |
| 劉禮瑜／連婕妤／… | 個人週視圖 | 只有當週幾十列，不是資料來源 |

### 1.2 數量

- **9,845 列、25,270 小時**，2023-08-28 → 2026-09-01；逐年 986 / 3,089 / 3,380 / 2,386
- 14 個人；346 個專案名（337 個是「客戶_案名」形式）
- 品質：19 列時數非數字、4 列日期解析不了、188 列專案空白 —— 收進去但列成報告
- 依狀態：結案 262 案 14,980h ／ 執行中 33 案 7,433h ／ 結案作業 38 案 2,067h
- 2026 年：76 案 6,318h

### 1.3 對映（🔴 目標是**私帳** `entity='mine'`，owner 2026-09-02 指明）

| 規則 | 專案數 | 小時 |
|---|---|---|
| 去掉「客戶_」前綴後**精確同名** | **324** | — |
| 客戶＋案名正規化同名／模糊 ≥0.85 | 3 | 26 |
| 模糊 0.6–0.85（**要人確認**） | 5 | 33 |
| 找不到（全是「源日後期_*」內部作業）| 5 | 326 |
| 內部桶（行政庶務 2,668h、結案作業 710h、提案企劃 277h…） | 9 | ~3,700 |

🔴 **同名撞案**：私帳有 28 個名字各對到 2 案（56 案，全是匯入的、案碼不同，例
「媒體顧問 202608」×2）。Sheet 去前綴後撞到 >1 私帳案的有 52 個專案、1,268h ——
**用「Sheet 前綴＝客戶簡稱（以前綴開頭）」消歧後只剩 1 個**（國家兩廳院_連通案拍攝，
0.5h，兩案客戶相同）。規則見 §4；實際 dry-run 結果見 `docs/timesheet_import_report.md`。

Phase A 實測（2026-09-02，讀生產私帳 413 案）：key 266 案 18,791h ／ key+client 51 案
1,267h ／ ambiguous 1 ／ none 20 案 893h（含源日後期_* 4 個 326h；其餘 16 個報告附相似
案名建議，多是打錯字或前綴不同）／ bucket 7 桶 3,702h。壞列 23。

- 人員：14 人裡 **10 人**對到 `crm_staff`；4 人沒有（林依靜、郭昭君、陳妍蓁、黃柔云，共 319 列）
- 預算：專案狀態 413 案裡 **367 案**有預算；去前綴後 **374 案**對到私帳案名；私帳目前 `budget_hours` **0 案**有值

### 1.4 現有程式的落差

- `docs/appsscript/timesheet_sync.gs`：`SHEET_NAME: '工作紀錄'` **不存在**；欄位差一欄（設 B–G，實際 A–E）；`START_ROW: 2` 應為 7；沒讀第二個分頁。**照現狀裝上去會直接 throw。**
- `ingest`：專案只認**全名精確相等** → 對這張表 0 命中；`source` 寫死 `'sheet'`；**不填 `staff_id`**。
- 生產 `timesheets` **0 筆**、`ingest` 從未被打過、`budget_hours` 0/240、`daily_rate` 0/151。

## 2. 設計決定

| # | 決定 | 理由 |
|---|---|---|
| D1 | **對映做成表** `timesheet_project_map(sheet_name → project_id)`，`ingest` 與匯入都先查它、再退回全名精確 | 一次決定、以後每小時同步自動吃到；不做一次性回填（先例：`finance_category_map`） |
| D2 | 匯入**走 `ingest` 同一條路**（同 `row_hash`、同手填優先去重），只加 `source` 參數 | 冪等；重跑安全；不長第二條寫入路徑 |
| D3 | 歷史列 `source='import'`；同步腳本的列 `source='sheet'` | 之後看得出哪批是回溯 |
| D4 | 對不到的列**照樣存**，`project_id=NULL`、`project_name` 留原字 | Burn 表本來就以名稱聚合列出未對映案；沒有任何一列會丟 |
| D5 | 預算從「專案狀態」灌 `crm_projects.budget_hours`（私帳案）；≤0 視為未設 | 這一輪不做 per-row budget 鏡射（該欄不存在） |
| D6 | `staff_id` 以姓名精確對 `crm_staff.name` 回填；對不到留 NULL | 4 人是否建檔 → owner 拍板（§7） |
| D7 | 內部桶（行政庶務／結案作業／提案企劃／業務開發／資訊工程／客戶服務／動畫小劇場／源日後期_*）**不對映到專案**，`project_name` 留桶名 | Burn 表分開看；B2 復盤不吃內部桶 |
| D8 | 同步腳本改讀**兩個輸入分頁**（工作紀錄表＋助理工作紀錄表）、各自記 marker、列 7 起、A–E | 兩者都底部追加，marker 成立；**不讀總表**（DESC 排序，marker 不成立） |
| D9 | 日期 hash 一律用 `yyyy/MM/dd` 字串（同 Apps Script `Utilities.formatDate`） | 匯入與同步算出**同一個 hash**，交接處不重複 |

## 3. 執行步驟

### Phase A —— 程式（約 4 小時，含測試）

1. `db/models/_workos.py`：`TimesheetProjectMap(sheet_name PK, project_id, decided_by, decided_at, note)`；`main.py` startup create_all 自建（新表不需 ALTER）。
2. `core/hr_logic.py` 純函式：
   - `sheet_project_key(name)`：去「客戶_」前綴 ＋ 正規化（空白／全半形括號）—— **只有這一份**
   - `resolve_project(name, map, by_key)`：對映表 → 精確全名 → 去前綴同名（唯一才算）→ None
   - `INTERNAL_BUCKETS` 常數
3. `routers/api_timesheets.py`：
   - `ingest`：`TimesheetIngestRequest` 加 `source: str = "sheet"`（只允許 sheet／import）；對映改呼叫 `resolve_project`；回填 `staff_id`（姓名精確）；回應多帶 `ambiguous_projects`
   - 新 `GET /timesheets/project_map`、`PUT /timesheets/project_map`（admin）：owner 決定用
   - 新 `POST /timesheets/remap`（admin）：依對映表回填既有列的 `project_id`（對映決定完再跑一次）
4. `scripts/import_timesheets.py`：
   - `--xlsx <檔> [--budget] [--apply] [--prod]`；預設 **dry-run 只出報告**
   - 讀「總表（勿動）」（已含兩個來源）；日期轉 `yyyy/MM/dd`；分批 200 列打本機 `ingest`（dev 8001 或 prod 8000，帶 `X-Timesheet-Token`）
   - `--budget`：讀「專案狀態」→ 去前綴對私帳案名 → 唯一者寫 `budget_hours`
   - 報告：對映分層、撞案清單、壞列清單、4 個沒對到的人、預算對到幾案
5. 測試 `tests/unit/test_timesheet_import.py`：`sheet_project_key` 的正規化、`resolve_project` 三段順序與「撞案不猜」、hash 交接一致、`source` 白名單、內部桶不對映、dry-run 不寫。

### Phase B —— dry-run 報告（半小時）

`import_timesheets.py --xlsx timesheet.xlsx --budget`（不帶 `--apply`）→ 一份 Markdown：
撞案 52 個、待確認 5 個、找不到 5 個、壞列 23 列、預算 374 案。**owner 在這份上拍板 §7。**

### Phase C —— 匯入（dev → prod，各 10 分鐘）

1. dev（`mediaguard_dev`）：`--apply` → 驗 `timesheets` 9,8xx 筆、Burn 表、人員月視圖、專案頁工時燈、`/my.html` 卡
2. prod：`--apply --prod` → 同樣四處驗；**再跑一次 `--apply --prod` 確認 0 新增**（冪等證明）
3. 對映表：owner 決定的 52＋5 筆 `PUT project_map` → `POST remap` → 驗撞案的 `project_id` 落定

### Phase D —— 同步腳本修正並安裝（owner 15 分鐘）—— **腳本已改好（2026-09-02），剩 owner 安裝**

1. `timesheet_sync.gs` 改：`SHEETS: [{name:'工作紀錄表', startRow:7}, {name:'助理工作紀錄表', startRow:7}]`、`COL: {DATE:1, STAFF:2, PROJECT:3, TASK:4, HOURS:5}`、拿掉 `BUDGET`、marker 改 per-sheet key
2. **安裝前把兩個 marker 設成匯入當下的最後一列**（`executeSetMarker`）—— 之後只送新列；就算重疊，D9 保證 hash 相同、後端去重
3. owner 照檔頭 5 步裝；第一次手動跑 `syncNewRows` 看 `inserted: 0`（因為都匯過了）

### Phase E —— 收尾（1 小時）—— **「指定專案」UI 已做（2026-09-02）**

- ✅ 人事管理 › 專案工時 tab 的「未對映專案」表：每列多了「原因」（撞案附候選、找不到附相似建議）與「指定專案」鈕 → 寫 `project_map` → `remap` → 重整。owner 的 17 個決定在這裡做，不用碰 API。
- 更新 ROADMAP N2 階段 0/1 的勾勾與藍圖 §3.6 的「現況」段

## 4. 消歧規則（撞案 52 個、1,268h）

按序，第一個唯一命中就停：
1. 對映表有 → 用它
2. Sheet 前綴＝私帳案的客戶 `short_name` 且案名同 → 唯一才算（解掉「政大資訊系 vs 政大AI學程」這類）
3. 仍 >1（多半是「媒體顧問 2026xx」系列、同名不同案碼）→ **不猜**，列進報告給 owner 指定；在指定前 `project_id=NULL`、名稱保留

🔴 **絕不 fuzzy 自動合併**（同 `import_my_projects.py` 的鐵則）。0.6–0.85 那 5 個只給建議。

## 5. 冪等與回滾

- 冪等：`row_hash`（日期字串｜人員｜專案｜內容｜時數）；匯入兩次第二次 0 新增
- 回滾：`DELETE FROM timesheets WHERE source='import'`（一句話）；`budget_hours` 用報告裡的「灌前值＝NULL」全部歸零；對映表獨立，不需回滾
- 手填優先：prod 目前 0 筆手填，交接無衝突；之後同步照既有規則

## 6. 驗收

| 檢查 | 期望 |
|---|---|
| `SELECT count(*) FROM timesheets WHERE source='import'` | 9,8xx（＝總列 − 壞列） |
| 第二次 `--apply` | `inserted: 0` |
| Burn 表 | 執行中 33 案有預算、消耗率；行政庶務等桶以名稱列出 |
| 專案頁（私帳案）工時燈 | 對到的案亮 |
| `/my.html`（綁了 staff_id 的 7 個帳號） | 本月／累計工時有數字 |
| 同步腳本第一次跑 | `inserted: 0`；隔天新列 `inserted: N` |

## 7. 要 owner 拍板

owner 2026-09-02 已拍板：**撞案逐一指定（owner）／4 人建 crm_staff／「源日後期」建一個
私帳案收／預算灌**。剩下要指定的只有：撞案 1 個＋找不到 16 個（報告附建議）。

## 8. 風險

- 第二本試算表不公開：一次性匯入靠總表已解；持續同步靠 IMPORTRANGE 分頁 `getValues()` 拿得到值 —— 但 IMPORTRANGE 若失連，該分頁整段空白，腳本會**當作沒有新列**而非報錯 → 腳本加「該分頁列數比 marker 少就 throw」
- Burn 表把私帳案名亮給有 `timesheets` 模組的人：目前只有 3 個管理員能看 `summary`（`check_admin`）；`by_staff` 只回 Sheet 原字 —— 先維持，之後私帳案名要不要露給員工是 N0 的題
- `timesheets.project_name` 對「媒體顧問 202608」這種同名撞案，人員月視圖仍以 Sheet 原字（含客戶前綴）顯示 → 對人不會混淆

## 9. 時程

| | 時間 | 誰 |
|---|---|---|
| Phase A 程式＋測試 | 4 h | Claude |
| Phase B dry-run 報告 | 0.5 h | Claude |
| owner 拍板 §7 | 1–2 h | owner |
| Phase C 匯入 dev→prod ＋ remap | 0.5 h | Claude |
| Phase D 改腳本 ＋ owner 安裝 | 0.5 h ＋ 15 min | Claude ＋ owner |
| Phase E 收尾 | 1 h | Claude |

先做 A＋B，把報告交給你拍板；C 之後才碰生產。
