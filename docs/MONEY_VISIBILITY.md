# 金額檢視授權（`money_view`）

> owner 2026-08-15 拍板：**預設看不到金額，除非我授權**。
> 起因是 `docs/PROPOSAL_PLANNER.md` §15.3 —— 人員配置搬不進專案頁，卡在
> 「人員與錢是同一張表」。這份文件是那個結的解法，範圍比專案頁大得多。

## 0. 動工前的現況（2026-08-15 實測，不是推論）

**今天「錢」不是沒有權限管理，是只擋到「你得先登入」。**

dev 8001，用一個只有 `journal` 模組的一般員工 token：

```
GET /api/v1/crm/projects      → 200，含 contract_amount / amount_receivable
                                      / amount_received / profit_target_pct
GET /api/v1/crm/invoices      → 200      GET /api/v1/crm/quotations → 200
GET /api/v1/crm/cash-entries  → 200      GET /api/v1/crm/staff      → 200
```

（dev 那幾張表是空的，所以筆數 0 —— **狀態碼 200 代表沒有守衛**；生產同一支是
236 筆真金額。）2026-08-14 補的 `_crm_read_guard` 擋掉了匿名，但公司內任何一個
有帳號的人 curl 一下就看得到全部合約金額。

**鑰匙已經存在一半**：`routers/api_finance.py`（財務管理）與 `api_cashflow.py`
（現金流）早就全部收 `crm_invoices`。漏的是 CRM 套件本身。

## 1. 鑰匙

**新增模組 key `money_view`（標籤「金額檢視」），且沒有任何自動蘊含。**

```python
# core/money.py
def can_see_money(payload) -> bool:
    return payload_grants(payload, 'money_view')     # access_level>=3 一律通過
```

- **為什麼不沿用 `crm_invoices`**：那是**帳務 tab 的鑰匙**。沿用的話，要讓一個 PM
  看到自己案子的合約金額，就得把整個帳務分頁一起給他。
- **為什麼連 `crm_invoices` / `crm_quotes` 都不蘊含**：owner 的話是「預設不要看到
  金額，除非我授權」。蘊含＝有些人不經你的手就拿到了鑰匙。帳務／報價 tab 的
  持有者要看數字，就在使用者管理多勾一格 —— 那一格正是「我授權了」的紀錄。
- 管理員（Lv3）永遠通過：他們本來就是能改所有東西的人，另外擋是自欺。

**上線時不做任何授權**（owner 2026-08-15：「先不給 讓我控制」）。

2026-08-15 生產 12 帳號實查：管理員 `admin` / `denniswang0513` /
`OriginsunFinance` 因 Lv3 自動有；非管理員裡會感覺到差異的只有三個 ——
`Ryansnap`、`soca`（帳務＋報價＋專案）、`nashtsai`（只有專案管理）。他們上線後
會直接看不到金額，**由 owner 在使用者管理逐一勾「金額檢視」開通**。

已查證**沒有任何路徑會自動塞這把鑰匙**（不做也不該做 backfill）：
`main.py` 的 RBAC v2 回填只碰 `username='admin' AND modules IS NULL`；
`api_auth` 的 `ALL_MODULES` 只在「一個使用者都沒有」時給 bootstrap admin；
`grant_admin_all_modules()` 只對 Lv3 生效，而管理員本來就通過。

## 2. 兩層機制、一份名單

| 層 | 對象 | 做法 |
|---|---|---|
| 1｜整支擋 | 端點**本身就是錢**：成本明細／成本摘要／財務摘要、發票、請款、收支、應收應付、報價、費率史、財務管理、現金流 | 403 |
| 2｜欄位抹除 | 端點**夾帶**金額：專案清單／詳情、專案派工、客戶統計 | CRM router 出口一處過濾，沒授權就**把鍵刪掉** |

**為什麼第二層掛 router 出口而不是逐支加**：同樣兩個欄位（`rate`/`cost`）散在
**四支**端點（派工、成本明細、成本摘要、某人的專案清單），逐支加漏一支就等於
沒修 —— 2026-08-14 那個匿名可讀的洞就是這樣長出來的。掛出口，新端點預設就是
安全的。有授權的人整段跳過（零成本），只有沒授權的請求會多一次序列化。

**名單是列舉的常數**（`core/money.py::MONEY_FIELDS`），不是靠字串比對猜：
`days`（檔期）、`tax_rate`（法定 5%，沒有資訊量）長得像錢但不是錢，不能誤殺。

🔴 **刪鍵不是歸零**。前端很多地方寫 `x.contract_amount || 0`，抹成 0 會變成
「這案子合約金額是 0 元」——那是謊報。刪掉鍵，前端畫「—」或整欄不畫。

## 3. 🔴 只抹 `cost` 等於沒抹（這題真正的坑）

`cost = days × rate`，而 `days` 是要留給企劃看的（那是檔期，他真的需要）。
所以只要 `rate` 從任何一個地方漏出來，抹掉的 `cost` 就被還原了。實查三條漏法：

| 漏法 | 端點 | 欄位 |
|---|---|---|
| 查日費再乘天數 | `GET /crm/staff` | `daily_rate` / `hourly_rate` |
| 當期費率史 | `GET /crm/staff/{id}/rate-history` | 整支就是薪資史 → 第一層 403 |
| 換個入口拿同一筆 | `GET /crm/staff/{id}/projects` | `days` + `cost` |

所以 `MONEY_FIELDS` **必須含 `daily_rate` / `hourly_rate`**。

## 4. 人員配置怎麼分（§15.3 的解）

「人」有兩張表，只有一張該進專案頁：

| 端點 | 本質 | 去處 |
|---|---|---|
| `GET /projects/{id}/staff`（`crm_project_staff`） | **人的正本**：姓名、職務、專案角色、天數、rate、cost、備註 | 專案頁的人員配置打這支 |
| `GET /projects/{id}/cost-lines` | **錢的正本**：項目 × 單價 × 數量 = 金額，人名只是掛在列上的標籤 | 留在 CRM，第一層 403 |

CRM 詳情面板的「執行人員」畫的是**後者** —— 所以它才有小計、總計、付款狀態。
把那張表閹掉給企劃看是錯的深度：拿掉金額後它只剩「攝影師 A 出現在拍攝階段」，
那是一張殘廢的成本表，不是人員配置。**用對的端點就自然分乾淨了**，不需要為它
發明「不含金額版的成本端點」。

欄位分級：

| 欄位 | 無授權 | 有 `money_view` |
|---|---|---|
| 姓名／職務／專案角色／天數／備註 | ✅ | ✅ |
| `rate`（日費）、`cost`（小計）、總計 | ❌ 整欄不畫 | ✅ |

整欄不畫，不畫 `***` —— 遮罩會讓人一直追問「那是多少」，整欄不存在傳達的是
「這個角色不需要這個」。

**寫入不動**：派工的新增／修改目前收 Lv3（`_check_auth`），所以專案頁的人員配置
是唯讀的。
⚠️ 哪天要放寬派工寫入，`days` 就變成「沒有金額授權的人也能改到金額」的入口
（後端是 `cost = days × rate` 重算）—— 到時候要嘛寫入也收 `money_view`，
要嘛沒授權的人只能改備註。

## 5. 分階段

| 階段 | 內容 |
|---|---|
| **A｜地基** | `core/money.py`（`MONEY_FIELDS` + `can_see_money` + `redact`）、CRM router 出口過濾、第一層 403 補齊、`api_finance`／`api_cashflow` 守衛換成 `money_view`、RBAC 三處同步、列舉式測試、前端 `|| 0` 消費端稽核 |
| **B｜解鎖專案頁 ✅ 2026-08-15** | 人員配置搬進專案頁（見下方 §7） |
| **C｜維持不做** | 成本／帳目 1,700 行搬進專案頁。理由沒變（使用者是財務不是製作），與權限無關 |

## 6. 階段 B 實作（2026-08-15）

元件 `frontend/tabs/proposals/staff-view.js`，**同一份兩個呼叫端**（CRM 專案詳情
的人員配置 + 專案頁的同名分頁），host / fetcher / 刪除動作由呼叫端注入 ——
搬檔而不是 import 過去用的理由同完稿結案那組（那頁的 import 閉包只准
`tabs/proposals/` 與 `js/shared/`，`test_public_surface` 守著）。

🔴 **判斷有沒有金額只看「後端回了鍵沒有」，不問前端旗標**。第一版寫成
`canSeeMoney() && …`，e2e 立刻紅：`canSeeMoney()` 讀的是 SPA 的
`window._accessLevel/_modules`，而獨立頁 proposal-plan.html 根本不設那些全域
—— 管理員在那頁會被判成沒授權。鍵在＝後端認可，這是兩個掛載點唯一都對的判準。
（`js/shared/money.js` 的 `canSeeMoney()` 仍在用，但只用在 SPA 那側：客戶績效
與專案詳情的財務區塊要在**發請求之前**就決定畫不畫。）

專案頁那份是**唯讀**的：不注入 `onRemove` 就不畫刪除鈕（派工寫入仍在 CRM，Lv3）。

順手修掉一處同型的謊話：CRM 新增派工的人員下拉印 `$${s.daily_rate}/天`，沒有
金額權時那個鍵不存在 → 畫面上是 `$undefined/天`。

e2e `tests/e2e/test_project_page.py`：同一筆派工（3 天 × $8,000）分別用管理員與
「拍攝企劃＋專案管理但無 money_view」兩個 token 開同一頁 —— 前者看到日費/小計/
合計，後者只看到人、職務、專案角色、天數、備註。

## 7. 測試

照 `tests/unit/test_crm_read_guard.py` 的**列舉式**寫法（不是挑幾支測）：掃出
app 上所有 CRM GET 路由，用無授權 token 打，斷言回應不含 `MONEY_FIELDS` 任一鍵；
有授權則必須含。將來誰新增端點漏了會紅。
