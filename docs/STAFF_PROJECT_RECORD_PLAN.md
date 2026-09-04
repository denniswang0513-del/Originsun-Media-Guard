# 專案紀錄整合到人力資源（staff）— 規劃

> 2026-09-05 草案。owner 拍板：專案紀錄的正本住在 staff 底下；員工端（個人工作台、手機「我的工作」）
> 讀同一份；**不要本週工時**區塊。示範頁：`/demo/my-work.html`（編號對應下文）。

## 1. 現況與問題

- 人力資源詳情已有「專案紀錄」分頁（`GET /crm/staff/{id}/projects`），但只讀兩個來源：
  **舊派工表 `crm_project_staff`**（派工 UI 已在 2026-09-04 從專案頁拿掉，之後不再有新資料）
  ＋ 官網作品的 credits。真正記錄「誰在哪個案子做了什麼」的資料散在五張表，這頁看不到。
- 簡歷管理的「公司專案」列表也吃同一支舊端點 → 新案子不會出現在簡歷。
- 員工自己看不到自己的專案紀錄（個人工作台 `/my.html` 沒這卡；手機 `/m/` 沒這分頁）。

## 2. 目標

一個人的專案紀錄只有**一份正本、一支聚合服務**，三個入口讀它：

| 入口 | 誰看 | 金額 |
|------|------|------|
| 人力資源詳情「專案紀錄」分頁 | 管理（crm_staff 模組） | 有 money_dep 才給 |
| 個人工作台 `/my.html`「我的專案」卡 | 員工看自己（`User.staff_id` 橋接） | 永不給；帳款只給狀態字 |
| 手機 `/m/` 新分頁「我的工作」 | 員工看自己 | 同上 |

## 3. 資料來源（不新建輸入表，聚合既有）

| 來源 | 表／欄位 | 拿到什麼 |
|------|---------|---------|
| 拍攝 | `crm_shoots.crew`（JSON `[{staff_id,name}]`）、`date/end_date/location_text/status` | 拍攝日、地點、場次狀態 |
| 執行工項 | `crm_project_cost_lines.actual_staff_id`（沒有就 `estimated_staff_id`）、`item_name/phase` | 我的角色（導演／剪輯…）、階段、金額（money 才給） |
| 工時 | `timesheets.staff_id + project_id`、`work_date/hours/task_note` | 每案累計小時、最近工作內容、首末日 |
| 上架掛名 | showcase credits（既有 `credit_service.find_projects_by_staff`） | 作品頁連結、掛名角色 |
| 墊錢 | `crm_project_expenses.staff_id` | 筆數＋是否已核銷（不給金額） |
| 舊派工 | `crm_project_staff` | **唯讀相容**：歷史資料照列，標「派工（舊）」；不再是主來源 |
| 專案本身 | `crm_projects`（status、client、shoot_date、AM）、`entity` | 階段 pill、客戶、下一步；私帳案沿用既有 wall |

角色去重規則：同案同人多筆工項 → 角色合併成集合（「剪輯、攝助」）；時間線事件保留每筆。

## 4. 服務層（唯一正本）

`services/staff_record.py`

```
build_staff_record(session, staff_id, *, money: bool, hide_mine: bool) -> {
  "summary": {"projects": n, "shoots": n, "hours": h, "credits": n},
  "projects": [{
     "project_id", "name", "client_short_name", "status", "entity", "am",
     "roles": ["剪輯","攝助"], "phases": ["現場拍攝","後期製作"],
     "shoots": [{"date","end_date","location","status"}],
     "hours": 22.0, "last_task": "定剪 v2", "first_seen": "2026-08-14", "last_seen": "2026-09-04",
     "credits": [{"role_zh","showcase_url"}],
     "expenses": {"count": 2, "settled": true},
     "pay_word": "已請款",              # core.crm_logic 那組狀態字；沒 money 也給字不給數
     "amounts": {...}                   # 只有 money=True 才有：工項金額、派工舊金額
  }],
  "timeline": [{"date","kind":"shoot|task|deliver|credit|expense","project_id","text"}]
}
project_people(session, project_id) -> [{staff_id, name, roles[]}]    # 查專案「做的人」欄用（反向）
```

- 純聚合、無寫入；`hide_mine=True` 時私帳案整個不出現（同 `core.ledger.hide_mine_projects`）。
- 單元測試釘：五個來源都要進 `projects`；同案多筆工項角色合併；沒 money 時 `amounts` 鍵不存在；
  舊派工列標 `legacy=True`。

## 5. API

| 端點 | 用途 | 守衛 |
|------|------|------|
| `GET /api/v1/crm/staff/{id}/record` | 人力資源詳情、簡歷「公司專案」 | crm_staff；`money` 依 money_dep |
| `GET /api/v1/crm/staff/{id}/projects` | 舊端點：改成 `record` 的薄殼（回同形狀），下一版退場 | 同上 |
| `GET /api/v1/me/record` | `/my.html`「我的專案」；token → `User.staff_id` | 登入；沒對映回 409「請管理員把帳號連到人員」 |
| `GET /api/v1/crm/m/me/record` | 手機「我的工作」BFF（瘦身：無 amounts、時間線最近 30 筆） | 同 `/crm/m/*` 現有守衛 |
| `GET /api/v1/crm/m/team/today` | 「大家今天在做什麼」：今天 `crm_shoots.crew` ＋ `hr_leave_requests` ＋ `timesheets(status=plan, 今天)` → 每人一行；沒資料＝「今天還沒紀錄」，不猜 | 登入 |
| `GET /api/v1/crm/m/projects?q=` | 「查專案」沿用既有搜尋，回應多一欄 `people`（`project_people`） | 既有 |

## 6. 前端

**一期｜人力資源詳情「專案紀錄」分頁重做**（`frontend/tabs/crm/crm-staff.js` `_loadStaffProjects`）
- 頂端 chips：案數、拍攝場次、累計小時、上架作品（money 再加「工項金額合計」）。
- 每案一張卡：案名｜客戶｜階段 pill；我的角色（工項合併）；拍攝日列；累計小時＋最近工作內容；
  上架掛名連到作品頁；墊錢筆數；帳款狀態字。舊派工列標「派工（舊）」灰字。
- 篩選：年份、階段（進行中／結案）、角色。
- 簡歷管理「公司專案」改吃 `record.projects`（角色＝合併後的 roles）。

**二期｜員工端**
- `/my.html` 加「我的專案」卡（同資料、無金額）＋「我做過的」時間線（示範頁 4、5）。
- 手機 `/m/` 新分頁「我的工作」放第一顆（發票退第二）：今天（2）、等我處理（3）、大家今天（9）、
  我的案子（4）、我做過的（5）、查專案（10）、快速記錄（7）。**不做本週工時**。
- 「等我處理」來源：`TimesheetConflict`（工時被退回／衝突待決）、`PortalComment`（客戶看片留言未回）、
  `EquipmentCheckout` 未歸還。

**三期（待拍板）**
- 「正在做」計時器（1）：`timesheets` 新增 `status=running` 列，結束時填 hours。最有用也最花工。
- 「大家今天」也上桌機（人力資源分頁頂端一條）。

## 7. 權限

- 人力資源分頁：`crm_staff` 模組；金額看 `money_dep`。
- `/my` 與手機：登入＋`User.staff_id`；金額永不給；私帳案不出現（沒 finance_mine 的人本來就看不到）。
- 新分頁權限同步五處（memory：福委會那次踩過，CLAUDE.md 只寫三處）。

## 8. 分期與驗收

| 期 | 內容 | 驗收 |
|----|------|------|
| 一 | service ＋ `/staff/{id}/record` ＋ 人力資源分頁重做 ＋ 簡歷改源 | 挑三個人（有拍攝、有工時、有上架）DOM 探針對照 DB 筆數 |
| 二 | `/me/record`、`/m/me/record`、`/my.html` 卡、手機「我的工作」前半、查專案 people 欄 | Playwright iPhone viewport；沒對映帳號要看到 409 提示 |
| 三 | 大家今天、等我處理、計時器 | 依拍板 |

## 9. 待拍板

1. 計時器做不做（三期）。
2. 員工端帳款狀態字給不給（示範是給狀態不給金額）。
3. 舊派工資料：只讀相容即可，還是要一次性搬進工項（`crm_project_cost_lines.actual_staff_id`）後把表退場。

## 10. 個人工作台 `/my.html` 版面重整（員工角度，2026-09-05 owner 要的）

**現在**：八張卡丟在 auto-fit 格子裡，沒有順序邏輯——我的專案（吃舊派工，多數人看到「目前沒有派工紀錄」）、我的待辦（公布欄）、
我的請假、個人資料、工時與薪酬、零用金、福委會、工作日誌（iframe）。員工進來要自己找「我今天該做什麼」。

**重整原則**：由上到下＝「今天 → 我的工作 → 我要記錄 → 我的權益 → 我的紀錄 → 團隊」。每區一列標題，卡片仍可收合（沿用 FOLDED）。
功能一律從 CRM 既有模組拉，不新造表單。

| 區 | 卡 | 從哪拉 | 現況 → 去留 |
|----|----|--------|------------|
| 今天 | **今天一條**：拍攝場次（我在 crew）、到期交付、等我處理計數 | `crm_shoots.crew`、`TimesheetConflict`、`PortalComment`、`EquipmentCheckout`、公布欄指派、請假核准結果 | 新，置頂、不可收合 |
| 我的工作 | **我的案子**：案名｜客戶｜階段、我的角色、下一步、拍攝日；點案名開只讀專案頁（企劃／素材報表／看片／作品頁） | §4 `build_staff_record` | 取代現在的「我的專案」 |
| 我的工作 | **我的待辦**（公布欄 own-scope） | 既有 | 保留，去 emoji |
| 我要記錄 | **記工時**：我的一天（當天列）＋「一週整批填」格子 | 工作追蹤既有元件 | 從「工時與薪酬」拆出來；**不做本週工時圖** |
| 我要記錄 | **記雜支／零用金** | 既有零用金卡 | 保留，改名 |
| 我要記錄 | **請開發票** | 手機 `/m/#invoice` 那套表單（同一份字彙） | 新（桌機也能請同事開票） |
| 我要記錄 | **登記拍攝／領器材** | 行事曆拍攝場次＋器材登記 | 新（連到行事曆分頁，帶 project 預設） |
| 我的權益 | **請假** | 既有 | 保留 |
| 我的權益 | **請款與勞報進度**：未付請款筆數／合計、最近三筆狀態 | `crm_payment_requests`（payee＝我） | 從「工時與薪酬」拆出來，只留錢的部分 |
| 我的權益 | **福委會** | 既有 | 保留 |
| 我的紀錄 | **我做過的**：時間線（拍攝、交付、上架掛名、結案） | §4 timeline | 新 |
| 我的紀錄 | **簡歷與作品集**：自助編輯（既有 staff-edit token）、作品上架掛名清單 | `/staff-edit.html`、showcase credits | 併進現在的「個人資料」卡 |
| 我的紀錄 | **工作日誌**（週誌） | 既有 iframe | 保留，移到最後一區 |
| 團隊 | **大家今天在做什麼** | §5 `/crm/m/team/today` | 新 |
| 團隊 | **查專案**：打字找案名／客戶／年份／誰做的 | 既有 `/crm/m/projects?q=` ＋ people 欄 | 新 |

**版面**：桌機兩欄——左欄「今天、我的工作、我要記錄」（做事），右欄「我的權益、我的紀錄、團隊」（看事）；
1000px 以下一欄依區順序。手機 `/m/` 「我的工作」分頁＝同一份資料的前三區＋團隊。

**順手清掉**：卡片裡的 emoji（🎉 📌 ⏳ ✓，違反「UI 無 emoji」鐵則）、「目前沒有派工紀錄」這句（派工已退場）。

**權限**：新卡沿用 `me_*` 鑰匙——今天／我的案子／我做過的 → `me_projects`；記工時 → 既有工時鑰匙；請開發票 → `me_petty` 同一把
（owner 之前拍板「請款要單獨控制」的那把）；團隊兩卡 → 任何有 `User.staff_id` 的登入者。

**分期**：先做「今天 ＋ 我的案子 ＋ 我做過的」（吃 §4 服務，一起上）；再拆「工時與薪酬」成兩卡、拉請開發票與登記拍攝；最後團隊兩卡。
