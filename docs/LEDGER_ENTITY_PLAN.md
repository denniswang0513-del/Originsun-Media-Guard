# 兩本帳（公司實體）設計 — LEDGER_ENTITY_PLAN

> 2026-08-19 定案；**同日 v2 修訂**（owner 拍板）：既有帳務資料＝**母公司**的帳；
> 「我的帳」（owner 自己的公司）是全新空帳本、走**獨立頂層 tab**。
> 背景：owner 同時記兩家公司的帳。合夥人（母公司股東）可登入看**母公司報表**
> （唯讀），但**絕不能**看到「我的帳」的任何東西；母公司的帳也只有 owner 能記。

## 0. 一句話架構

錢流資料有 `entity` 欄：**`'parent'`＝母公司（預設；既有資料全歸此）**、
**`'mine'`＝我的帳（owner 私帳）**。財務 tab 固定＝母公司帳；「我的帳」是
獨立 tab（iframe 掛 `/my-ledger.html`，同財務模組、釘死 entity=mine）。
權限兩把新 key：`finance_partner`（母公司報表唯讀，給合夥人）、
`finance_mine`（我的帳，預設只有 owner/Lv3）。

> ⚠ v1（同日稍早）曾用 `'own'`（預設）/`'parent'` 與單一 key `finance_parent`，
> 發現「既有資料屬母公司」後全面改名。`'own'` 這個值**已廢棄不再使用**
> （避免殘留語意）；dev DB 的一次性 own→parent 遷移已完成，遷移句已從 startup 移除（見 §1.1）。

## 1. 資料層

### 1.1 帶 entity 的表（7 張）

`String(16), nullable=False, server_default="parent"`：

| 表 | 備註 |
|---|---|
| `crm_invoices` | 發票（權責認列） |
| `crm_payment_requests` | 請款（母公司域；我的帳 v1 無請款） |
| `crm_cash_entries` | 現金流總帳 |
| `bank_accounts` | 帳戶屬於哪家公司；對帳/對帳單明細/貸款經此推導 |
| `finance_adjustments` | 期初/調整必須分帳 |
| `finance_loans` | 貸款分帳（繳款產生的 cash entry 繼承貸款 entity） |
| `finance_month_close` | (entity, month) 複合 unique，兩本帳各自鎖月 |

Migration（`main.py` startup ALTER 財務區塊）：
```sql
ALTER TABLE <表> ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent';
-- month_close：
ALTER TABLE finance_month_close DROP CONSTRAINT IF EXISTS finance_month_close_month_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_month_close_entity_month ON finance_month_close (entity, month);
```

> v1→v2 的 `SET DEFAULT 'parent'` / `UPDATE … WHERE entity='own'` fixup **已移除**：
> v1 只存在於 dev DB（未 commit、未發版），2026-08-19 實測七表 `own=0`、default 全是
> `'parent'`，prod 這根欄是本次才建、直接就是 `'parent'`。留在 startup 等於每次開機
> 對七張錢流表各做一次全表掃描＋一次 ACCESS EXCLUSIVE DDL，換不到任何東西。

### 1.2 刻意「不」分帳的東西

- **科目表與對映**（`finance_accounts` / `finance_category_map`）：兩本帳共用
  同一套科目（unique constraint 不用動；寫入限母公司 full scope，見 §2.4）。
- **母公司專屬領域**（projects / petty / costs / equipment / staff /
  payment_milestones）：整個 CRM 營運域就是母公司的 → 綁 `'parent'`，不加欄。
  報表引擎在 `entity=='mine'` 時**不餵**這些來源（折舊/專案毛利/預支核銷回空）。
  「我的帳」v1＝發票＋收支＋銀行＋調整＋貸款＋三表報表，無 CRM 連動。

## 2. 權限層

### 2.1 兩把新模組 key

- **`finance_partner`**（label：母公司報表）：合夥人用。母公司帳**報表唯讀**
  （儀表板/三表/drilldown/稅務包）。
- **`finance_mine`**（label：我的帳）：owner 私帳全功能＋獨立 tab 的入口 key。
  Lv3 經 grant_admin_all_modules 自動持有。
- 兩把都 append 在 `ALL_MODULES` **尾端**（⚠ modules[0] 決定 admin 落地頁）。
- RBAC 3 處同步 + `TAB_ACCESS`：`'crm_invoices': ('crm_invoices', 'finance_partner')`
  （合夥人進得了財務 tab）；新 tab key `finance_mine` 自成一格（無 extra）。

### 2.2 scope 判定 — 正本 `core/ledger.py`

```python
ENTITIES = ("parent", "mine")
DEFAULT_ENTITY = "parent"

def allowed_entities(payload, level="view") -> set:
    # Lv3 → 兩本全開（view 與 full 皆是）
    # level="view"（看報表）：
    #   parent ⟺ (crm_invoices AND money_view) OR finance_partner
    #   mine   ⟺ finance_mine
    # level="full"（記帳/銀行/原始帳列/月結寫入）：
    #   parent ⟺ crm_invoices AND money_view      ← 合夥人的 key 不在此
    #   mine   ⟺ finance_mine

def require_entity(request, entity: str = "", level: str = "view") -> str:
    # 未登入→401；scope 空→403；entity 空→預設 parent（無 parent 則落 scope 內那本）；
    # 非法值→422；不在 scope→403。回傳解析後 entity。
```

### 2.3 🔴 合夥人帳號的兩種等級（2026-08-19 owner 二次修訂）

owner 拍板「合夥人使用 CRM 是完整的、公司專案資料聯通」→ 合夥人有兩檔：

- **完整合夥人**（常態）：正常帳號，owner 在使用者管理勾要給的模組
  （crm_projects/crm_clients/crm_quotes/crm_invoices/money_view…）——
  母公司的 CRM 與財務對他就是完整系統。
- **報表合夥人**（輕量選項）：只給 `finance_partner` 一把 → 母公司報表唯讀
  （view 層那 8 支 GET），其餘 403。

**不變的硬牆（兩檔都適用）**：**絕不給 Lv3、絕不給 `finance_mine`**。
「我的帳」與（§8）私人專案由 `finance_mine` 在伺服器端鎖死 —— 合夥人拿再多
模組與 money_view 也碰不到 owner 的私人領域。原「合夥人絕不給 money_view」
鐵則**降級為報表合夥人限定**（給了 money_view 就等於升成完整合夥人，
邊界只剩 finance_mine）。

### 2.4 各路由的守衛規則

- `routers/api_finance.py` `_guard(request, entity="", level=...)`：
  - **level="view"**（合夥人可及）：/dashboard、/statements、/statements/drilldown、
    /tax-package、/accounts GET、/category-map GET、/unmapped GET。
  - **level="full"**（其餘全部）：銀行帳戶/對帳/對帳單明細/調整/貸款/
    bulk-assign/category-map PUT/setup-wizard。對帳與明細仍經 bank_account
    推導 entity；跨帳本掛帳 409；setup-wizard 限 parent。
  - 🔴 **逐列端點（PUT/DELETE/單列動作）不收 `entity` query param**：帳本由
    該列（或其 bank_account / loan）自己的 entity 推導，載入後 `_guard` 那次
    才是權威判定。收 param 只會多一個沒有作用、還可能誤 403 的公開參數。
    只有清單與建立端點需要 param（決定「看哪本／建進哪本」）。
- `routers/api_cashflow.py`：milestones/forecast＝`("parent", level="full")`
  （CRM 域）；month-close GET＝view、POST/reopen＝full，皆帶 entity。
- `routers/crm/finance.py`：列表/單筆 `require_entity(..., level="full")`
  疊在 money_dep 之上；建立/更新 payload 帶 entity（None＝建立落 parent／
  更新維持既有值）；CSV 匯入整批 parent。
- 🔴 **更新一律不得換帳本**（owner 2026-08-19 拍板，六張表同一條規矩）：
  payload 的 entity 與該列現值不同 → 422。api_finance（銀行帳戶/調整/貸款）
  本來就是這樣；crm/finance（發票/請款/收支）曾實作成「驗兩邊 scope 後真的
  搬過去」，與本節「更新維持既有值」相反，已統一成 422（正本＝
  `_entity_for_write`）。真要換帳本＝刪掉重建，留得下痕跡；留兩個答案的話，
  §8 私人專案（錢流 entity 跟著專案跑）會踩到。
- petty / costs：不動（月結守衛預設 entity="parent"）。

## 3. 月結鎖帳

`_locked_month_set(session, entity="parent")` /
`_assert_month_open(session, *dates, entity="parent")` /
`_assert_rows_open(session, dated_rows, entity="parent")`。
petty/costs 呼叫點吃預設；invoices/payments/cash-entries 傳該列 entity。

## 4. 報表引擎（`services/finance_statements.py`）

唯一取數咽喉 `_load_inputs(session, entity="parent")`：六個錢流來源 WHERE
entity；equipment 只在 `entity=="parent"` 載；`_advance_state` 在 mine 回空；
`_project_margins`/`_project_client_map` 在 mine 跳過。對外簽名
（compute_live/statements_for_period/month_close_extras/drilldown/
dashboard_summary/tax_package）全部 `entity="parent"` 預設。

## 5. 前端

- **財務 tab＝母公司帳，無切換器**（v1 曾有的實體切換 pill 已移除）。
  nav 依身分：full-parent scope（記帳者/owner）＝全功能；合夥人
  （finance_partner）＝只有 儀表板＋財務三表。
- **「我的帳」＝外部連結 `/my-ledger.html`，刻意無 SPA tab**（owner 2026-08-19
  二次拍板，取代原「獨立頂層 tab」方案）：側欄零入口 —— 旁人不知道這頁存在；
  頁面**永遠先出帳密登入表單**（不吃 localStorage 既有登入態，重新驗證是本頁
  存在理由；也刻意不放 Google 一鍵登入），登入＋閘門（Lv3 或 finance_mine）
  通過才載財務模組並**釘死 entity=mine**（`window._finEntity='mine'` 於 import
  前設定；比照 website-admin.html 獨立登入頁模式）。「登出／鎖定」＝回登入表單，
  不清 auth_token（清了會殺同瀏覽器主系統登入態）。nav 只留：發票/收支明細/
  儀表板/三表/銀行帳戶/科目與設定（零用金/請款/應付/應收/現金流預測是母公司
  CRM 域，隱藏）。真防線在伺服器端（require_entity 的 mine scope），本頁的
  重新登入是螢幕旁防窺的第二道門。
- `fin-utils.js`：`finEntity()`＝目前帳本（pin 來源 `window._finEntity`，預設 parent）；
  `finFetch` 自動附 entity param；`finHasFullParentScope()` 給 nav 判身分。
  無 entity→顯示名稱對照表（沒有切換器就沒有要渲染的地方）。
  cashbook/invoices 直接 import 這兩支，不各自複寫 pin。
- cashbook/invoices 表單的 entity＝跟著頁面 pin 走，**無使用者可見的帳本選單**
  （兩本帳在不同 tab，選單多餘）。
- 🔴 PUT 洗欄位陷阱不變：payload `entity` 預設 None（None＝維持既有值）。
- 合夥人落地頁：`app.js` 的 `TAB_MAP[modules[0]] || _firstAuthorizedSection()`
  fallback 已處理。

## 6. v1 刻意不做（防 scope 漂移）

- 合併視圖與母子公司往來對沖（stage 2）。
- 我的帳的請款/零用金/專案/器材/現金流預測（我的帳＝純記帳＋報表）。
- 我的帳月結 UI（API 已 entity 化）。
- 科目表分帳（共用；只有母公司 full scope 能改 —— 未來若私帳要自己的科目再說）。
- 實體顯示名稱後台可編（寫死 母公司/我的帳）。

## 8. 私人專案（第三階段 — 已定案未實作）

> 2026-08-19 owner 拍板：自己公司外接的案子**也記在這套 CRM**，必須對合夥人
> （以及所有非 finance_mine 者）隱藏。帳本維度延伸到專案層。

- **資料（兩個獨立開關，owner 2026-08-19 三次修訂）**：
  - `crm_projects.entity`（'parent' 預設 / 'mine'）＝**記在哪本帳**（錢流歸屬）。
  - `crm_projects.mine_shared`（0 預設 / 1）＝**給不給合夥人看**——只對 mine
    專案有意義，owner 逐案自己設（專案詳情一顆「開放檢視」開關，只有
    finance_mine 者看得到這顆鈕）。
- **可見性規則（正本進 core/ledger）**：專案可見 ⟺ entity=='parent'
  OR Lv3/finance_mine OR (entity=='mine' AND mine_shared)。
  開放檢視＝專案資料聯通（合夥人看得到案子與其專案層資訊）；**帳本歸屬不變**
  ——錢仍記在 mine 帳本、只出現在 /my-ledger.html 的報表，不混進母公司三表。
- **錢流連動（一致性）**：mine 專案掛的發票/請款/雜支自動落 mine 帳本
  （entity 跟專案走）→ 私人案的財務自然只在 /my-ledger.html 出現。
- **影響面盤點（實作時逐一上鎖＋測試）**：專案列表/詳情/管線看板、報價
  （by-project＋quotations/stats 聚合）、成本/雜支 project-scoped 端點、
  客戶績效聚合、應付/應收 join、提案庫（提案=專案合體）、portal、timesheets
  專案選單、footage/media-log by-project、bulletin 引用、工作流 deep-link。
  中央 helper（列表過濾 + 單案守衛）取代逐端點散裝判斷；收尾跑洩漏審計
  （用無 finance_mine 的 token 掃全部 GET 端點找 mine 專案殘影）。
- **v1 邊界**：mine 專案不派工給員工（owner 自己管）；出現在任何共用聚合
  （統計/儀表板）一律排除。

## 7. 測試

`tests/unit/test_ledger_entity.py`：scope 矩陣（view/full 兩層 × 四種身分）、
require_entity 行為、合夥人寫入 403、月結複合 unique、`_load_inputs` entity
WHERE 掃描釘、api_finance level 分層掃描釘、RBAC 3 處同步（既有
test_rbac_module_sync 蓋）、schema entity 預設 None 釘、`'own'` 值不再出現於
runtime 程式碼（只允許 migration fixup 句與本文件）。
