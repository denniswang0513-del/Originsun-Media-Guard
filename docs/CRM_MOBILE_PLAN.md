# CRM 手機版規劃（2026-09-03）

> 目標讀者：owner（拍板）＋接手實作的 Claude session。
> 依據：2026-09-03 對 CRM 前端（20,392 行／39 檔）、既有手機頁家族、CRM 後端 300+ 端點的盤點。
> 上位決定：`docs/WORK_OS_BLUEPRINT.md` §H「行動端不是選配——獨立輕頁模式照抄，主 UI 的 RWD 可以再等」、
> `docs/STRATEGY_2026H2.md` 中程第 4 項「行動端強化」、owner 2026-07-17 對 `/my.html` 的拍板
> （獨立頁、官網風格、員工不要碰到那麼多功能）。

## 0. 結論（一段話）

**不把主 UI 做成 RWD，做一個「CRM 手機版」獨立輕頁**：一個頁面、底部五個分頁（專案／客戶／報價／帳款／儀表板），
照抄 `expense.html`／`petty-cash.html` 的殼，但殼抽成共用模組不再各頁抄一份；後端補「手機用的資料形狀」
（分頁、單次打包的 BFF，比照 `routers/api_me.py`）。一期先做「看」，二期補少量「改狀態」，
成本格／收支明細／對帳這三個大型編輯格明確不做。三期約 1.5–2 週有效工時。

## 1. 為什麼不是 RWD

| 事實 | 影響 |
|---|---|
| CRM 前端 20,392 行，全部 media query 只有 1 條（invoices 的 detail 改直向） | 沒有可以「補一補」的基礎 |
| 版面原語全在 `crm-utils.js`：`setupResizeHandle` 只綁 mouse 事件、`.crm-row` 七欄 flex＋min-width 合計 ≈385px、成本格 9 欄 | 375px 螢幕必溢出；拖拉分欄在觸控上是死的 |
| 168 個 `window._*` handler 靠 HTML 字串 `onclick` 呼叫 | 任何 DOM 改寫都得保住這些名字，等於重寫 |
| 13 檔 CSV 匯入、17 檔拖放排序、資料夾路徑靠 `pick_folder`（主機端原生對話框） | 這些互動在手機上本來就不存在 |
| 實務版面下限約 1,100–1,280px | 「縮小一點」不是選項 |

反例只有一個：`tabs/petty/petty-view.js:110` 那條 `@media (max-width:720px)`（grid 塌成兩欄、隱藏表頭）——
它證明**唯讀清單**可以低成本 RWD，這留到三期給 payables／receivables／cashflow 三個純清單分頁用。

## 2. 給誰用、在哪裡用

帳號只有 5 個：admin、owner、財務、助理、Web。手機上真正會用 CRM 的是 **owner 與財務**（外出時看專案狀態、
應收應付、報價有沒有回覆），助理次之。現場人員（工時／雜支／零用金）已經有 `/my.html`、`/expense.html`、
`/petty-cash.html`，不在本計畫範圍。

- 入口：`https://foundry.originsun-studio.com/m/crm.html`（cloudflared → master 8000）。區網 `192.168.1.107:8000` 同樣可用。
- 限制（owner 2026-07-17 已接受同一前提）：master 關機就進不去；NAS 對外容器沒有 CRM 後端與登入，這頁**不能**放上去
  （`core/public_assets.py` 的 `MODULE_DIRS` 不要加它，否則 `test_public_surface` 會紅）。
- 相機／麥克風只在 HTTPS（foundry）有；本計畫一期不用到。

## 3. 分期

### Phase 0 — 地基（1–2 天）

後端：
1. **分頁**：`GET /clients` `/projects` `/quotations` `/invoices` `/payments` `/cash-entries` 加 `limit/offset`
   （照 `routers/crm/petty.py:820` 的寫法：預設不變、上限夾住、回 `returned/offset`）。桌機端不傳 limit → 行為零改變。
   現況：projects 236 列、cash-entries 4,733 列／3.5 MB——gzip 解決了頻寬，沒解決手機的 JSON 解析與 DOM。
2. **手機 BFF** `routers/api_crm_mobile.py`（prefix `/api/v1/crm/m`）：
   - `GET /home`：一次回「我的待辦數字」——進行中專案數、本月應收／應付合計、待回覆報價數、逾期請款數（守 `money_view`，沒有就不回金額）。
   - `GET /projects?phase=&q=&limit=`：卡片用的瘦欄位（id、名稱、客戶簡稱、階段、AM、合約額、收款狀態、最近更新）。
   - `GET /projects/{id}`：詳情打包——基本資料＋收款摘要（沿用 `costs.project_financial_summary`）＋工時 burn
     （沿用 `timesheets/summary`）＋最近 5 筆雜支＋發票清單。一次請求，手機不用打六支。
   - 客戶／報價／帳款各一支瘦清單，應收應付直接沿用 `/payables/summary` `/receivables/summary`（本來就是聚合）。
3. **登入壽命**：JWT 現在 7 天、沒有 refresh（`core/auth.py:88`）。加 `POST /api/v1/auth/refresh`（拿還沒過期的 token 換一顆新
   的、重新讀權限），手機殼在開頁時剩 < 2 天就靜默換。不改預設 7 天，桌機不受影響。

前端：
4. **共用殼** `frontend/m/shell.js`（ES module）：登入三視圖（密碼＋Google）、`authFetch`（401 → 清 token 回登入）、
   權限閘門、toast、`esc`、**本地日期**（`toISOString` 在 08:00 前會寫成昨天——expense.html 檔頭那個坑）。
   現有五個獨立頁各自抄一份殼、已經漂過兩次（發票狀態改名、`tabLoadError` 複本）；新家族只准 import 這一份。
5. **PWA 最小集**：`manifest.json`（名稱、圖示、`display: standalone`、`theme-color`）＋ `apple-mobile-web-app-*` meta，
   讓 owner 加到主畫面像 app。**不做 service worker**（離線快取牽涉登入態與資料新鮮度，三期再議）。
6. 風格：白底＋旭日紅 `#c9372c`＋髮絲框，跟 `/my.html` 同一家族；從 `/my.html` 加一張「CRM」卡當入口（只對有 CRM 模組的人出現）。

### Phase 1 — 看（3–4 天）

`frontend/m/crm.html` 一頁五分頁（底部 tab bar，切換不重載）：

| 分頁 | 內容 | 資料來源 |
|---|---|---|
| 儀表板 | 四個數字（進行中／本月應收／本月應付／待回覆報價）＋近 6 個月現金流小圖 | `/crm/m/home`、`/cashflow` |
| 專案 | 階段 chips（提案→製作→結案）＋搜尋＋卡片；點進詳情（收款、工時 burn、雜支、發票、負責人） | `/crm/m/projects`、`/crm/m/projects/{id}` |
| 客戶 | 卡片（簡稱、狀態、進行中專案數、年度營收）；點進看專案紀錄 | `/clients`＋`/clients/{id}/stats` |
| 報價 | 清單（專案、版本、狀態、金額、報價日）＋唯讀明細（項目、小計、稅、付款條件） | `/quotations`、`/quotations/{id}` |
| 帳款 | 應收／應付兩個切換，按月分組，逾期標紅 | `/receivables/summary`、`/payables/summary` |

規則：每個清單先載 30 筆、往下捲再抓；金額用 `tabular-nums`；沒有 `money_view` 的帳號看不到金額（後端 `MoneyRedactRoute` 已經會抹）。

### Phase 2 — 改（2–3 天）

只做「一個按鈕就完成」的寫入，不做表單編輯格：
- 專案：改階段／狀態、改負責人、加一則備註。
- 報價：改狀態（草稿→已寄送→已簽核／已拒絕）；簽核時順手「啟動專案」（沿用 `_quoteActivateProject` 背後的端點）。
- 請款：標記已付／取消已付（單筆，不做批次）。
- 客戶：改狀態、改聯絡備註。
- 新增專案「簡表」：名稱、客戶、案型、階段——**不含資料夾路徑**（那要主機端 `pick_folder`）。

權限：CRM 寫入端點現在幾乎都是 `_module_guard()` 零 key ＝ 只有 Lv3 管理員。owner／財務都是 Lv3，助理是 Lv1——
助理要能在手機上改，得先決定要開哪幾支寫入給 `crm_projects` 模組（見 §6 待拍板）。

### Phase 3 — 選配（各 0.5–1 天）

- 主 UI 三個唯讀分頁（payables／receivables／cashflow）套 `petty-view.js` 樣板的 RWD，順手把 `setupResizeHandle` 在 < 900px 停用。
- 通知：報價被簽核、請款逾期 → 走既有 `notifier.py`（Google Chat／LINE），手機端不做推播。
- Service worker：只快取殼與靜態檔，資料永遠打 API。

## 4. 明確不做

成本格（`crm-projects-cost.js` 1,457 行、9 欄可編輯）、收支明細（`crm-cashbook.js` 2,440 行、7 篩選器＋拆帳）、
對帳工作台、CSV 匯入、拖放排序、報價項目編輯、任何要主機端資料夾路徑的欄位、影像／作品上傳。
這些在桌機做，手機看結果。

## 5. 技術決定與理由

| 決定 | 理由 |
|---|---|
| 獨立頁 `frontend/m/`，不是 SPA 分頁 | 深色殼包不住官網風格（owner 已否決過一次）；SPA 的 168 個 window handler 不必背 |
| 殼抽成 `m/shell.js`，不再每頁抄 | 現有五頁各抄一份，發票狀態改名時手機送出的資料曾落在沒人認得的狀態 |
| 後端加 BFF 而不是讓手機打六支 | 遠端每支加幾十到上百 ms；打包一次回也讓「沒權限就不回金額」在一處決定 |
| `limit/offset` 加在既有端點，預設不變 | 桌機零改動、測試零改動；只有手機傳 limit |
| refresh 端點而不是拉長 JWT | 拉長會連桌機一起放寬；refresh 只在手機殼裡用 |
| 不做 service worker | 登入態＋財務數字的新鮮度比離線重要；PWA manifest 就夠「像 app」 |

## 6. 待 owner 拍板（三題）

1. **使用者**：一期只給你＋財務（都是 Lv3，寫入權限不用動），還是助理也要能改？後者要開放特定寫入端點給 `crm_projects` 模組。
2. **一期就要能改，還是先純看**：純看可以早兩、三天上線讓你先用；改狀態併進一期也行，多 1 天。
3. **風格**：跟 `/my.html` 同一套（白底旭日紅）並從 `/my.html` 進入——我的建議；或跟後台一樣深色。

## 7. 估時

| 期 | 工時 | 產出 |
|---|---|---|
| 0 地基 | 1–2 天 | 分頁參數、BFF 四支、refresh、殼模組、manifest |
| 1 看 | 3–4 天 | 五分頁可用、owner 手機驗收 |
| 2 改 | 2–3 天 | 狀態類寫入 |
| 3 選配 | 各 0.5–1 天 | 視需要 |

驗收一律用真手機（或 Playwright 的 iPhone viewport）打 foundry，不用桌機縮視窗——`docs/PROPOSAL_PLANNER.md` 記過
HTTPS 才有的能力在區網 http 上量不到。
