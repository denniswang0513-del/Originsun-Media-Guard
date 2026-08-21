# 福委會 — 規格正本

> owner 2026-08-21 的原話：
>
> 「我有幾個福利池，一個是**快樂**、一個是**進修**，這兩塊員工都可以登記，
> 他們登記後**我審核通過，就進公司請款**。我**每年會撥一筆錢**進這個池。」
> 「這個池子的名稱是**福委會**。」
>
> 這份文件就是照那三句話寫的。跟現況不符就改這份，程式跟著這份走。

## 0. 一句話

```
每年撥款 ──▶ 池（快樂 / 進修）
              ▲
員工登記 ──▶ 待審 ──owner 核准──▶ 已核准（進公司請款）──▶ 已付款
              └──退回──▶ 退回（本人可改可刪，改完自動重新送審）
```

**餘額 ＝ Σ撥款 − Σ(已核准 ＋ 已付款)**

## 1. 三張表

| 表 | 是什麼 | 關鍵欄 |
|---|---|---|
| `hr_benefit_pools` | 池（快樂／進修）。**跨年度滾動** | name / status(open,closed) / entity |
| `hr_benefit_fundings` | 每年公司放進池裡的那筆錢 | year / amount / fund_date |
| `hr_benefit_entries` | 員工登記的一筆花費 | title(自由文字) / amount / staff_id / status |

**為什麼撥款自己一張表**，不跟登記混成一本帶號流水帳（Notion 原本是那樣）：
撥款沒有請款人、不需要審核、也不該進公司請款 —— 跟員工登記是兩種東西。
混在一起的話，員工登記時把金額填成正數就變成一筆「撥款」，池憑空多錢。

**項目是自由文字**（電影名／餐廳／課程名），不做枚舉。分類這件事由**池**承擔，
再加一層項目分類只是逼人每次多選一格。

## 2. 三個設計取捨

🔴 **待審不扣餘額**。待審就扣的話，退件之後餘額要回沖 —— 那是很容易對不起來
的帳。畫面另外顯示「審核中 $X」就夠了。

🔴 **超支的餘額是負的、轉紅**，不夾成 0。真的花超了，畫面就該講出來。

🔴 **登記即待審**，沒有草稿階段。owner 的流程就是「登記後我審核」，中間再插一個
草稿只是逼人多按一次送出。退回後本人改完會**自動回到待審**，不用再找送出鈕。

## 3. 兩個入口

| 誰 | 在哪 | 權限 key |
|---|---|---|
| 員工 | `/my.html` 的「福委會」卡（選池／填項目金額／送出／看自己的） | `me_benefits` |
| owner／HR | 人事管理 › 福委會（池、撥款、待審佇列、代登、送會計） | `hr_benefits` |

🔴 **自己登記的那幾筆不受 `money_view` 管**（同零用金 PETTY_CASH_PLAN §4）。
靠 **scope** 而不是抹鍵：`/benefits/me` 一族一律 `WHERE staff_id = 我`，
而 `staff_id` 只從 token 解（`resolve_current_staff`），永不收 client 傳的值。
給那幾支掛上 `money_dep` ＝ 沒有金額權的員工連自己登記了什麼都看不到。

**審核／匯款**另外要 `finance_approve`（審的是別人的錢，同零用金）。

## 4. 進公司請款＝沿用既有金流，一步都不自己造

核准 → 產一張 `CrmPaymentRequest`（`category=員工福利`、`payee_name=員工`、
`payment_status=應付款`）→ 自動出現在**應付帳款**。
匯款 → 那張應付款結清成一列 `CrmCashEntry`。

抄過來的四條規則（零用金的教訓，不可在這條路上重犯）：
- 核准**冪等**：已經有 `payment_request_id` 就不再產（重按不會變兩張）。
- 退回要**撤掉未付款的應付款** —— 不撤就是**幽靈負債**：登記回到本人手上、
  帳上還掛著那筆錢，月結與現金流預測都會多算。
- **已付款的不准撤** → 409，請走沖銷（那筆錢真的出去了，不是把歷史抹掉）。
- 匯款**不重複記帳**：已經有對應收支列的不再落帳。

## 5. 送會計

`GET /crm/benefits/accounting-package`（＋`.csv`）：每個池的**撥款／已用／餘額**
＋ 撥款逐筆 ＋ 支出逐筆。只收「已核准／已付款」—— 待審與退回還不是帳，
送過去只會讓人對不起來。

## 6. 刻意不做

- 職工福利委員會的法定那一套（提撥率、福委會選舉、法定申報）。
- 勞健保／勞退雇主負擔（那是薪資模組）。
- 「這筆要不要併入個人所得」的稅務分區 —— owner 的流程裡沒有這件事，
  2026-08-21 我自己加過一次，是多做的，已移除。真的需要時再談。

## 7. 相關

- `routers/crm/benefits.py`（I/O）／`core/hr_logic.py`（純規則＋單元測試）
- 測試：`tests/unit/test_benefit_pool.py`、`tests/e2e/api_benefits.py`、
  `tests/e2e/api_benefits_me.py`（own-scope 用兩個真帳號驗隔離）、
  `tests/e2e/ui_benefits.py`、`tests/e2e/ui_benefits_my.py`
- 金流那一側：`docs/PETTY_CASH_PLAN.md`（同構，共用同一條應付帳款管線）

## 8. 🔴 新 tab 的權限同步是**五處**

CLAUDE.md 只寫三處，實際做下來是五處，少任何一處都不會報錯只會靜默壞掉：

1. `core/auth.py` `ALL_MODULES`（source of truth；新 key 一律 **append 在尾端**
   —— 插前面會改掉所有管理員的登入落地頁）
2. `frontend/js/shared/tab-config.js` 的 `TAB_MAP` / `TAB_LOADERS` / `TAB_GROUPS`
3. `frontend/js/shared/tab-config.js` 的 **`PERMISSION_GROUPS`**
   （`tests/unit/test_rbac_module_sync.py` 會擋）
4. `frontend/js/admin/user-mgmt.js` 的 `MODULE_LABELS`
5. **`frontend/index.html` 的 `<section id="tab_xxx">` 殼** —— 少了它 tab 根本
   開不起來，而 **API 全綠時完全看不出來**（是瀏覽器 e2e 抓到的）

`me_*` 的卡片還要第六處：`routers/api_me.py` 的 `ME_MODULE_KEYS`
（`/me/workspace` 的 `allowed` 靠它，卡片是宣告式閘門）。
