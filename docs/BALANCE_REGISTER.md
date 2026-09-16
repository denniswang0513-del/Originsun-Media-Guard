# 登記餘額 —— 先登記帳戶資產，明細後面補

> owner 2026-09-17：「我想要有個方法是先讓我登記我的帳戶資產，明細我後面補。」
> 狀態：**dev 8001 做完、單元測試通過（2026-09-17）**，待 owner 看圖說「推」。

## 1. 它解決什麼

私帳的帳戶餘額本來只有一種算法：**期初餘額 ＋ 全部收支明細**。明細記到 6 月中就停了，
餘額就停在 6 月；owner 今天看銀行 App 知道真正的數字，卻沒有地方可以直接填。
直接改期初會在之後補 7～9 月明細時重複加一次；補一筆「差額調整」會污染支出分析、之後還要記得刪。

所以加一個**基準點（anchor）**：每個帳戶記「哪一天、登記多少」。

## 2. 規則（只有一份：`core/finance_logic/_core.py::derive_balance`）

| 情況 | 餘額 | 還沒補的明細（unfilled） |
|---|---|---|
| 沒登記過 | 期初 ＋ Σ全部流水（老公式，一個數字都不變） | None |
| 登記過 | 登記餘額 ＋ Σ**基準日之後**的流水 | 登記餘額 −（期初 ＋ Σ基準日當天與之前的流水） |
| 對帳／月底餘額的時點不晚於基準日 | 老公式（那時還沒登記） | — |

- 基準日當天與之前的明細只當歷史：之後補多少舊明細，今天的餘額都不動，只有 unfilled 往 0 縮。
- 沒填日期的收支視為基準日之後（跟老公式一樣照算）。
- 例：富邦-收入戶帳上算到 9/17 是 1,520,435，登記 1,482,235 → 餘額 1,482,235、unfilled −38,200。
  補一筆 8 月的支出 38,200 → 餘額還是 1,482,235、unfilled 0（補齊）。記一筆 9/20 的收入 10,000 → 餘額 1,492,235。

六個算餘額的地方全部經 `routers/api_finance.py::_balances_by_account`（SQL 一趟抓「Σ全部」與「Σ基準日之後」兩個聚合，
規則交給 `derive_balance`）：帳戶清單 `current_balance`、對帳月底餘額、資產儀表板的銀行現金與各帳戶列、堡壘的帳戶、
對帳單解析的期初、三表的月底餘額（純函式版 `bank_balances_asof` 自己認 `anchor_date`）。
🔴 誰再自己寫「期初＋流水」就會漏掉基準點（`tests/unit/test_balance_register.py` 掃著）。

## 3. 資料

- `bank_accounts.anchor_balance INTEGER NULL`、`anchor_date TIMESTAMPTZ NULL`（`db/migrations.py` 開機 ADD COLUMN；兩欄一起有或一起空）。
- 證券逐檔（owner「登記的還有證券的股數等」）：`holdings[]{id, shares, last_price, manual_value, cost_total}` 直接寫 `finance_holdings`
  的那幾欄（給現價順便更新 `price_at`）。給了股數或現價、且算得出股數 × 現價 → 清掉 `manual_value`（改用算的）；明著送 manual_value 以它為準。
  PUT 裡**先套持股、再算券商總市值的未拆明細**。
- 證券戶：沒有新欄位。一家券商登記一個總市值，「總市值 − 已拆明細的市值」寫進那家的**「未拆明細」列**
  （`finance_holdings`：symbol 空、name＝未拆明細、manual_value）。之後把真的持股加進去、再登記一次總市值，它就自己歸零。
- 信用卡：不在這裡（既有 `PUT /finance/card-summary` 的 `derive_opening_from` 本來就是「現在實際欠多少」；桌機的登記頁直接呼叫它）。
- 每次登記在對帳表（`BankReconciliation`）留一筆：month＝基準月、statement＝登記數、system＝帳上算到基準日的數字、
  diff＝unfilled、note＝「登記餘額 YYYY-MM-DD」。同帳戶同月覆蓋。

## 4. 端點（`routers/api_balance_register.py`，prefix `/api/v1/finance`；主控與 NAS office-api 都掛）

- `GET /balance-register?entity=` → `{date, accounts[]{id,name,acct_kind,bank_name,balance,booked,anchor_balance,anchor_date,unfilled},
  brokers[]{broker,count,detail,plug,plug_note,total}, card_outstanding, usd_twd, entity}`。只列 bank／cash 帳戶。
- `PUT /balance-register?entity=` body `{date, accounts[]{id, balance|null}, holdings[]{id, shares?, last_price?, manual_value?, cost_total?},
  brokers[]{broker, total}}` → 回 GET 那一整包。GET 的 `brokers[].holdings[]` 帶每檔的 shares／last_price／manual_value／cost_total／value_twd。
  `balance: null` ＝ 取消登記（兩欄清空、回老公式）。基準日不能是未來；信用卡／股東帳戶 422。
- 只寫 DB、不碰 settings.json（NAS 的 settings.json 是唯讀副本）。

## 5. 畫面

- 桌機：私帳 nav「登記餘額」（`fin-nav-mine-only`，`frontend/tabs/finance/subviews/register.js`）。基準日 → 銀行／現金帳戶表
  （帳上算的／上次登記＋取消登記／今天實際餘額輸入格／還沒補的明細）→ 證券戶表（券商列：已拆／未拆／合計／今天總市值；
  底下每檔**只有「股數」一格** —— owner「證券我只需要更改單位數」，現價由更新報價帶、成本在證券投資頁）→ 信用卡一列 → 底部「儲存有填的」。
  只送有填的列。銀行帳戶頁的卡片多一行「登記 $X（日期）・還沒補的明細」。
- 手機：士源帳本隱藏路由 `#register`（`frontend/m/views/ledger-register.js`；從總覽堡壘卡下方那一列進來），
  一列一個帳戶＋輸入格、證券戶同、底部一顆儲存；信用卡只顯示。寫完 `markStale('overview', 'assets')`。
