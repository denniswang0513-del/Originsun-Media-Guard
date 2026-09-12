# 士源帳本 —— 私帳手機版（`/m/ledger.html`）規劃

> 狀態：**L1 已實作（2026-09-13，dev 8001 真瀏覽器驗過；未發版）**。後端 `routers/api_ledger_mobile.py`、前端 `frontend/m/ledger.html`＋六支 views；契約釘在 `tests/unit/test_ledger_mobile.py`。**L2 已實作（2026-09-13）**：專案抽屜「推送到母帳」三選一、家用按月切換、資產淨值成長線（純 SVG）。L3：icon 已定稿（無限環，`scripts/make_ledger_icon.py`）；證券／器材唯讀在 L1 資產頁已有。
> 前置：[`docs/CRM_MOBILE_PLAN.md`](CRM_MOBILE_PLAN.md)（手機殼與規矩）、[`docs/LEDGER_ENTITY_PLAN.md`](LEDGER_ENTITY_PLAN.md)（兩本帳）、
> [`docs/LEDGER_UNIFY_PLAN.md`](LEDGER_UNIFY_PLAN.md) §8（母私帳推送）。
> 桌機正本：`/my-ledger.html`（`window._finEntity='mine'` → `tabs/finance/`）。

---

## 0. owner 拍板（2026-09-12）

1. **家用單獨一頁**（不併進「記一筆」）。
2. **主機關機手機也要能用** → 一期就把 finance 端點掛進 NAS office-api。
3. 專案詳情：結案日／營收／案源／備註可改，**工項拆分唯讀**；但要能**記雜支、記委外費用**。
4. 主畫面名稱 **「士源帳本」**。
5. 要看得到**資產表**（資產儀表板）—— **獨立一頁**，不併進總覽。

## 1. 定位

**現場輸入＋快看**，不是把桌機縮成手機寬。你在外面會做的：記一筆錢（案子的收支／家用）、看一個案（收了多少、欠多少、
順手記這案花的雜支或發給誰的委外費）、看誰還沒付、看今年賺多少、看總資產。
坐下來才做的（證券持股維護、快照、財務三表、銀行、科目、對帳、專案對應、工項拆分）留在桌機。

## 2. 分頁（底部 tabbar，六個，落地＝收支）

| 分頁 | 內容 | 寫入 |
|---|---|---|
| **收支**（落地） | 「記一筆」：收入／支出 分段 → 金額 → 分類 picker（私帳分類樹攤平，可打字搜）→ 專案 picker（收入預設要掛專案，沒掛送出前問一次）→ 日期（預設今天）→ 摘要；下方最近 30 筆卡片＋載入更多；點卡片開抽屜改／刪 | `POST/PUT/DELETE /cash-entries?entity=mine` |
| **專案** | 可搜尋清單（案名／客戶／年份），每卡：營收／實收／未收／案源／結案日／「母帳：案名」；抽屜：① 可改欄（結案日〔連著母帳時鎖住〕、營收、案源、備註）② **費用**：委外費用／行政雜支兩格顯眼、其餘（發票代辦費／個人稅款／股東往來）收進「更多」；每格旁「＋加一筆」（輸入金額→加到現值→存）；CRM 撐著的部分（`cost_sources`）另列唯讀灰字「＋ CRM 成本行 N」，手填的才是能改的 ③ 工項拆分**唯讀表** ④ 掛在本案的收支 | `PUT /project-ledger/{id}?entity=mine`（只送有動的鍵，同桌機 exclude_unset） |
| **應收** | 未收＞0 的案按金額排；每卡「收到錢」→ 開「記一筆」預填 收入＋這案＋未收金額 | 走收支的 POST（已收是從收支推的，**沒有**直接寫 `amount_received` 的動作） |
| **家用** | 「記一筆家用」：金額 → 家用分類 picker（分類樹「家用」子樹）→ 日期 → 摘要；本月合計卡＋按分類小計；最近 30 筆 | `POST/PUT/DELETE /cash-entries?entity=mine`（跟收支同一支端點，差在分類樹的子樹） |
| **總覽** | 本月／本年／全部 切換：營收／實收／應收／淨收四卡＋最近 5 筆收支＋未收前 5 案 | 唯讀 |
| **資產** | 總資產一張大卡；各桶（銀行現金／應收帳款／器材淨值／證券現值／手填桶）各一列，點桶展開明細（銀行→各帳戶餘額、證券→持股表：名稱／現值／損益、器材→清單）；上次快照日期與金額、跟上次比的增減 | 唯讀（拍快照、改持股、更新報價留桌機） |

## 3. 後端

### 3.1 BFF 一支：`routers/api_ledger_mobile.py`（前綴 `/api/v1/finance/m`）

守衛：每支 `require_entity(request, "mine", level="full")`（指名制 finance_mine；Lv3 不 bypass）。

- `GET /options`：分類樹攤平 `[{id, label, kind}]`（kind＝income／expense／household，由樹的頂層節點推）、`SELECTABLE_SOURCES`、
  專案 picker（`services/project_picker.list_options(prefer="mine")`，**不帶金額**）、`me.can_write`。
- `GET /home?period=`：總覽（`/dashboard` 子集＋最近 5 筆＋未收前 5）—— 手機一頁一趟。
- 資產分頁直接打既有 `GET /assets/overview`（buckets／total／bank_lines／holdings）＋ `GET /assets/equipment`＋ `GET /assets/snapshots`（取最近兩筆算增減）；形狀已夠用，不另包 BFF。
- 其餘讀寫**直接打既有端點**：`/cash-entries`、`/project-ledger*`、`/cash-taxonomy`。**不重寫任何金額規則**。

### 3.2 office-api 掛 finance（拍板 2）

`main_office._ROUTER_MODULES` 加 `api_finance`、`api_finance_projects`、`api_finance_assets`（＋新的 `api_ledger_mobile`）。
- 🔴 `main_office.py` 不准長出排程／背景 runner（`test_office_surface` 對模組圖斷言）。`api_finance.py` 的 `/loans/upcoming` 跟
  `core.scheduler._loan_due_check` 共用的是**函式**，不是排程；掛進去後跑 `test_office_surface`，模組圖裡出現 `core.scheduler` 就要拆那段。
- `api_finance_assets` 的報價抓取（TWSE／Yahoo）是**按鈕觸發**不是排程，可掛；但手機版不放「更新報價」鈕（桌機做）。
- 掛進去的端點在 NAS 也吃同一顆生產 DB，`require_entity` 守衛不變。
- `core/office_assets.py`：`frontend/m/` 整個目錄本來就送（`MODULE_DIRS` 有 `"m"`），新頁與 manifest 免另登記；新 icon 放 `frontend/m/`。
- `core/office_settings.py` 看要不要多送什麼設定鍵（資產儀表板讀 `my_ledger.*`？實作時 grep）。

## 4. 前端

- `frontend/m/ledger.html`（🔴 iOS 主畫面模式：要在 app 內開的頁都得住 `/m/` 底下）＋ `ledger.js`（路由，照 `crm.js` 抄）＋
  `views/ledger-{cash,projects,receivable,household,overview,assets}.js`。
- 一律 `import './shell.js'`（殼、登入、`mfetch`、token 續期、`money`、`todayLocal`）；元件用 `ui.js`（`mountPicker`／`segHtml`／`openSheet`／`renderPaged`）。
- 閘門：`(me.modules || []).includes('finance_mine')`。
- 風格深色同 `/m/crm.html`；`manifest-ledger.webmanifest`：`name: 士源帳本`，`start_url: /m/ledger.html`；icon 同一張 logo 換底色（跟「源日 CRM」分得開；要更大尺寸得跟 owner 要原檔）。
- 「＋加一筆」是純前端：讀目前手填值 → 加 → PUT 該鍵；**不送** CRM 撐著的合計（後端 PUT 也會 pop 掉，但前端就不該送）。

## 5. 規矩（繼承，不重列理由）

- 私帳不開發票 → 沒有發票；私帳專案不跟公司請款 → 沒有請款。
- 已收＝收支推導（增量制）→ 只有「記一筆收入掛這案」，沒有「標已收」。
- 收支寫入**必走** `/cash-entries`（`_sync_mine_project_received` 在那裡）。
- 連著母帳的案：結案日鎖住（`locked_fields`），顯示「母帳：案名」，不做跳轉。
- 所有搜尋能打字（`ui.mountPicker`）；按鈕純文字不加 emoji；日期用 `todayLocal()`。
- 費用欄的 CRM 合計不落庫（`apply_crm_costs` 甲案：顯示＝手填＋CRM）。

## 6. 階段

| 階段 | 內容 | 驗收 |
|---|---|---|
| **L1** | 殼＋六分頁＋ BFF 兩支＋ office-api 掛 finance | Playwright iPhone viewport（master 8001 與 NAS 各跑一次）：登入→記一筆收入掛專案→該案未收減少→應收卡消失；專案抽屜「＋加一筆」雜支 500 → 費用欄 +500 且 CRM 合計不變；連著母帳的案結案日灰；家用記一筆→本月合計變；資產分頁數字＝桌機資產儀表板；零 JS 錯誤 |
| **L2** | 專案抽屜「推送到母帳」三選一（沿用 `project_links`）；家用按月切換；資產分頁淨值成長線（快照序列） | 同上＋推送流程 |
| **L3** | 主畫面 icon 定稿（owner 給原檔）；證券／器材唯讀卡 | — |

## 7. 實作分工（規則 F：超過 5 檔拆子代理）

- 後端一支：`api_ledger_mobile.py`＋`main_office` 掛載＋`test_office_surface`／`test_public_surface` 相關測試。
- 前端一支：`m/ledger.html`＋`ledger.js`＋六支 views＋manifest／icon。
- 契約用測試釘（手機版 CRM 的教訓：兩個代理各寫一半，抽屜欄位名對不上後端）：`/options`／`/home` 的回應形狀先寫進 `tests/unit/test_ledger_mobile.py`，前端照那份鍵名寫。
