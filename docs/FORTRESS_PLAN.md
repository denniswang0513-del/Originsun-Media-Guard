# 私帳「堡壘」—— 個人版 Fortress Balance Sheet（桌機分頁＋手機版）規劃

> 狀態：**F1–F4 做完、跑過 /polish（2026-09-16）**：純規則＋後端＋桌機分頁＋手機頁都在 dev 8001 驗過，六張 bug 卡已修（見 `git log --grep="polish: BUG"`）。**未發版**（F5 待 owner 說「推」）。
> 來源：owner 2026-09-16 貼的〈把 JPMorgan「堡壘資產負債表」搬進家庭財務〉：五層資金、Liquidity Runway、家庭版壓力測試。
> 前置：[`docs/MY_LEDGER_MOBILE_PLAN.md`](MY_LEDGER_MOBILE_PLAN.md)（士源帳本手機殼與規矩）、[`docs/LEDGER_ENTITY_PLAN.md`](LEDGER_ENTITY_PLAN.md)（兩本帳）。
> 視覺 demo：桌機 https://claude.ai/code/artifact/d9748841-3ef4-487e-a82e-780ed006260c（範例數字）。

---

## 0. 三句話

1. **只讀現有數字，不重寫任何金額規則**：帳戶餘額（銀行頁）、證券現值（資產頁）、信用卡欠款（卡帳）、房貸下一期（貸款表）、
   「固定支出＋家用」月平均（收支明細）都已經有；堡壘只是把它們套進五層＋一個公式。
2. **新增的只有三份設定＋一張小表**：帳戶分層與三個標記（實體／海外／美元）、各層目標倍數、壓力測試假設 → 設定 JSON；
   **預留清單** → 新表（手機要能在 NAS 上寫，設定 JSON 在 NAS 是唯讀副本，所以不能放設定）。
3. **只有 owner 看得到**：桌機走 `finance_mine` 那條線（`fin-nav-mine-only`＋`require_entity(…, "mine", level="full")`），手機走 `/m/ledger.html` 同一個閘門。

## 1. 定義（純規則，放 `core/fortress_logic.py`，全部可單元測試）

| 名詞 | 定義 |
|---|---|
| 五層 | 1 營運現金／2 預留現金／3 緊急預備／4 機會資金／5 複利資本。每個帳戶（銀行、現金、證券、信用卡）標一層。 |
| 現金 | 第 1–4 層裡**不是證券**的餘額合計（`cash_in_layers`）。🔴 看的是 kind 不是層別：把 ETF 標成第 4 層不會讓它變成股災裡不會跌的現金。信用卡與股東帳戶不列（卡欠款走預留，不然扣兩次）。 |
| 預留 | 預留清單未付項目的合計（含自動帶入：貸款表下一期 `FinanceLoanPayment.status='scheduled'` 最近一筆、信用卡本期欠款）。 |
| 必要支出 | 預設＝近 6 個**完整**月的生活支出月平均，**分母是真的有資料的月份數**（`monthly_need_from_rows`）。口徑 `need_category_ok`：收 `家用`／`個人`／`貸款繳款` 三枝，扣掉收入列、投資支出、借款支出（卡費與房貸走預留，再算一次是重複）、其他支出。owner 可覆寫（設定）。<br>🔴 分類樹只搬了一半，生活費大多掛在 `個人_旅遊` 這種舊的兩層名字上 —— 只收 `家用%` 會漏掉整個個人枝。 |
| 可撐月數 | （第 1–3 層現金 − 預留）÷ 必要支出。另顯示「含第 4 層」版本。 |
| 各層目標 | 1：1 個月必要支出；2：預留合計；3：6 個月；4：3 個月；5：無上限。倍數可改（設定）。 |
| 壓力測試 | 六題，全部由上面的數字算：① 收入斷 6 個月 ② 股票跌 40% ③ 突發 40 萬 ④ 三件同時 ⑤ 台海戰爭 ⑥ 長期照護。每題回「撐得住／撐得住但很緊／會被迫」＋一句結論＋算式各行。跌幅一律套在**證券**（kind＝holding）上，不論它被放在第幾層。 |
| 長照（owner 2026-09-16「把長照考慮進去」） | 🔴 它**不是一次衝擊**，是把每月必要支出抬高好幾年 —— 攻擊的是可撐月數的分母。所以這題動用的是「現金＋第 5 層複利資本」，而不只是現金，並且另外告訴你「只用現金撐得了幾年」。預設假設：每月照護費 4 萬（外籍看護或一般安養機構的常見水準）、7 年（台灣常被引用的平均長照期間）、保險每月給付 0（直接扣在照護費上）。沒有模擬「照顧者離職少一份收入」——那一段看第 1 題。 |
| 資產預期成長（owner 2026-09-16） | 第 5 層複利資本往後 5／10／15／20／30 年。**名目與「換算成今天的購買力」兩個數字一起給**（只看名目會高估未來買得起什麼）。假設：年報酬 6%、通膨 2%、每年再投入 0，都可改。`project_growth`，年底投入的年金公式；報酬 0% 不除以零。 |
| 財富階梯（owner 2026-09-16 拍板：台灣物價回推，不用匯率換） | 門檻＝一百萬起跳、十倍一階（100 萬／1,000 萬／1 億／10 億／100 億）。🔴 **不是匯率換算**：匯率換出來會寬一階（同一個人免思考金額只夠一頓好餐，卻被標成「旅遊自由」）。門檻是從台灣的實際價格反推：咖啡 60–150、一頓好餐 1,000、一趟旅行 3–10 萬、換房 2,000–3,000 萬，除以「免思考比例」（萬分之一）。淨值＝五層合計 − 負債（卡債＋貸款剩餘本金），**不含應收帳款、器材、房地產**（私帳沒有房產科目）。旁邊並排一行台灣分位數（主計總處 2021 年底：中位數 894 萬／前 20% 2,134 萬／前 10% 3,391 萬）—— 那是另一個問題的答案，不取代階梯。門檻、免思考比例、分位數、註記全部可改（`ladder`）；規劃欄位 `plan`＝目標階梯＋目標年份，每年再投入沿用 `growth.annual_add`（不另開一份）。 |
| 台海戰爭假設（預設，可改） | 收入斷 12 個月；台股 −60%；美股 −20%；台幣貶 30%（美元資產台幣價值 ×1.3）；台灣的銀行前 4 週領不到錢 → 「頭一個月拿得到的錢」只算標了**實體**或**海外**的帳戶。 |

顏色門檻同儀表板既有的 `runway` 規則：≥6 綠、3–6 黃、<3 紅（`dashboard.js:181`）。

## 2. 資料與後端

### 2.1 讀（都已存在）
- 銀行／現金／信用卡：`GET /finance/bank-accounts?with_balances=1`（`routers/api_finance.py:588`，餘額＝期初＋收支加總）；卡欠款 `card_outstanding`（`core/finance_logic/_statements.py:521`）。
- 證券：`GET /finance/assets/overview`（`routers/api_finance_assets.py:132`，`value_twd`／`currency`）。
- 貸款下一期：`GET /finance/loans/upcoming`（`routers/api_finance.py:1350`）。
- 必要支出：`crm_cash_entries` 依 `CashTaxonomyNode` 子樹（`core/cash_tree.py`）加總；固定支出枝由 `db/seed_cash_taxonomy.py:46` 種。

### 2.2 新增
- **表 `finance_fortress_earmarks`**：`id, entity, label, amount, due_date, source('manual'|'loan'|'card'), source_ref, paid_at, note, created_at`。
  自動項不落庫（每次算的時候帶入），只有手動項落庫；`paid_at` 非空＝已付、從合計拿掉（不刪，留紀錄）。
- **設定 `finance.fortress.mine`**（`config.load_settings/save_settings`，同 `margin_model[entity]` 的樣式）：
  `{ account_layers: {acct_id: 1..5}, account_flags: {acct_id: {physical, offshore, usd}}, holdings_layer: 5, targets: {1:1, 3:6, 4:3},
     monthly_need_override: null, war: {months:12, tw_drop:.6, us_drop:.2, fx:1.3, bank_freeze_weeks:4} }`。
  🔴 要送到 NAS：`core/office_settings.py` `EXPORT_SUBKEYS` 加 `"finance": ("fortress",)`（NAS 唯讀；分層與假設只在桌機改）。
- **端點**（都 `require_entity(request, "mine", level="full")`）：
  - `GET /api/v1/finance/fortress` → 一趟回全部：`{runway, runway_with_l4, cash, cash_l1_3, earmark_total, monthly_need{auto, override, used},
    layers[5]{have, target, accounts[]}, earmarks[], tests[5]{key, state, title, assume, lines[], verdict}, updated_at}`。
    桌機與手機同一支；撈數字在 `routers/api_fortress.py`（同 api_ledger_mobile 的做法，lazy import 既有 router 的算法），規則在 `core/fortress_logic.py`。
  - `PUT /api/v1/finance/fortress/settings`（桌機）；`POST/PUT/DELETE /api/v1/finance/fortress/earmarks`（桌機＋手機）。
  - 掛進 `main_office._ROUTER_MODULES`（同 `api_ledger_mobile`）：主控關機手機照看、照記預留。
    🔴 但 `/fortress/settings` 在 `_DROP_PREFIXES` 裡 —— NAS 的 settings.json 是唯讀副本，在那邊存會靜默消失。
  - 🔴 守衛是 `_guard()` 不是直接 `require_entity(request, entity)`：entity 空字串會落到 parent，沒有 finance_mine 的管理員會拿到一個「母公司版堡壘」。
  - 🔴 `finance.fortress` 在 `routers/api_system._SECRET_SUBKEYS` 裡 —— `/api/settings/load` 是匿名端點，不抹的話每月必要支出與銀行帳戶 id 會整包外流。

## 3. 桌機：財務分頁多一個「堡壘」（只在私帳出現）

- `frontend/tabs/finance/finance.html:40-52` 加 `<button class="finance-nav-btn fin-nav-mine-ok fin-nav-mine-only" data-subview="fortress">堡壘</button>`（按鈕純文字，不加 emoji）。
- `frontend/tabs/finance/subviews/fortress.js`：`render(container, {isCurrent})`，自動被 `finance.js:185` 的 `createSubviewLoader` 載入。
- 版面照 demo v3：大數字＋堡壘剖面 → 預留（12 個月時間帶＋表）／必要支出＋各層目標 → 五座塔同框 → 帳戶分層（下拉＋三個勾）。
  帳戶分層、目標倍數、戰爭假設在這頁直接改（PUT settings）；預留在這頁增刪。
- 樣式：桌機正本是深色 CRM 殼（`crm.css`），demo 的淺色只是提案；實作時用 CRM 深色配色，堡壘剖面的綠／藏藍／石灰換成 `--pri`／`--ok`／`--warn` 那組。

## 4. 手機：士源帳本（`/m/ledger.html`）

底部六個分頁已滿（收支／專案／應收／家用／總覽／資產），**不加第七個**。做法：

1. **總覽頂部一張卡**（`views/ledger-overview.js` 的 `#ov-body` 最上面）：「可撐 N 個月」大字＋一行「第 1–3 層 X 萬 − 預留 Y 萬 ÷ 必要 Z 萬」＋五格迷你水位。點卡 → `#fortress`。
2. **`#fortress` 隱藏路由**（同 CRM 的 `HIDDEN_ROUTES` 做法：有頁、沒 tabbar 鈕；`ledger.js` 的 `TABS` 不動，`VIEWS` 多一個 `fortress`）：`views/ledger-fortress.js`。
   由上到下：大數字卡 → 五層（一層一列：名稱、帳戶、水位條、現在／目標）→ 預留清單（一列一筆，「＋記一筆預留」開抽屜：項目、金額、到期日；自動項標「自動」不能刪；付掉可勾「已付」）→ 必要支出（唯讀，註「桌機改」）→ 壓力測試五題（收合列：色點＋題名＋結論；點開看算式各行）。
3. **資料**：直接打 `GET /api/v1/finance/fortress?entity=mine`（桌機同一支；不另開 BFF）。
4. **寫入**：只有預留清單（走 §2.2 的 earmarks 端點，NAS 也掛）。分層、目標、假設**不在手機改**。
5. **規矩**（繼承手機版）：`import './shell.js'`、`mfetch`、`money`、`todayLocal()`；60 秒快取＋`markStale('overview','fortress')`（記了預留兩頁都髒）；按鈕純文字；深色同殼。
6. **不做**：手機不放堡壘剖面圖（窄，改成五列水位條）；不放 12 個月時間帶（預留清單按到期日排就夠）。

## 5. 階段與驗收

| 階段 | 內容 | 驗收 |
|---|---|---|
| F1 | `core/fortress_logic.py`（層合計、可撐月數、目標、五題）＋單元測試（用 demo 的範例數字釘：可撐 9.1、戰爭題「撐得住」；把實體現金移到第 2 層 → 頭一個月 19.5 萬） | pytest |
| F2 | 表＋設定＋三支端點＋office-api 掛載＋`office_settings` 送 `finance.fortress`；`test_office_surface` 模組圖不得長出排程 | pytest＋dev 8001 curl |
| F3 | 桌機分頁 `fortress.js`（照 demo v3，深色配色） | Playwright 桌機截圖給 owner |
| F4 | 手機：總覽頂卡＋`#fortress` 頁＋預留抽屜 | Playwright iPhone viewport（8001 與 NAS 各一次）截圖給 owner |
| F5 | 發版（`/publish` 流程；只有主控＋NAS，機隊不需要這功能但 OTA 一起帶） | 8000 真機 |
| /polish | 六張 bug 卡：分層自動存會清空設定、設定從匿名端點外流＋端點沒鎖私帳、必要支出口徑與分母、壓力測試把證券當現金、匯率沒設無聲算 0＋逾期貸款只留一期、手機顯示過期數字 | 4147 通過 |

owner 待拍板（沒說就照預設）：證券算第 5 層；必要支出用自動平均、可覆寫；戰爭假設照 §1 預設。
