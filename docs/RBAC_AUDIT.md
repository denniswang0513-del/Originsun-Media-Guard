# 權限全面稽核（2026-09-08）

> owner「全面檢視所有權限」。方法：掃描器把 `routers/**` 每一支端點的守衛盤出來（936 支），四個唯讀代理分區把端點對回前端
> 「哪個分頁／按鈕會打它、那個分頁靠哪把鑰匙開」，找出三種問題：**看得到做不到**（分頁用鑰匙 X 開、端點卻管理員限定或別的鑰匙）、
> **無守衛但不該公開**、**同一分頁讀寫鑰匙不一致**。規劃正本 [`RBAC_PLAN.md`](RBAC_PLAN.md)；本檔是階段 0 的稽核產出。

## 1. 守衛總覽（掃描器，2026-09-08）

| 守衛類別 | 支數 | 說明 |
|---|---:|---|
| 沒有守衛 | 473 | 大半是後期流程（本機代理免登入＝owner 拍板的產品前提）與官網公開頁；官網 admin_* 是透過 `_common.admin_guard` 包裝守 `website_admin`，掃描器看不到，代理逐支確認 |
| 管理員限定 | 141（掃描器）→ **實際約 95** | 掃描器把 `check_admin_or_module(request, *tab_modules(x))` 多行呼叫誤判成管理員：器材庫 8、場景庫 7、產業情報 9、片庫 17 其實是分頁鑰匙級（代理逐支複核）。清單見附錄 A（已修正） |
| 模組鑰匙 | 96 ＋ 官網 13 | `check_admin_or_module` / `_module_guard` |
| 錢流／帳本牆 | 77（`money_dep`）＋ 52（財務 `_guard`）＋ 6 | `require_entity`／`money_view`／`finance_*` |
| 綁定人員／今天與這週 | 5 ＋ 15 ＋ 4 | `require_bound_staff`／`require_zone_staff`／兼職排班 |
| 登入即可 | 16 ＋ 7（Depends） | `check_logged_in` |
| 任一 token | 4 | /auth/me、/auth/refresh 等 |

## 2. 鑰匙 → 分頁（前端閘門正本 `frontend/js/shared/tab-config.js`）

| 鑰匙 | 開哪個分頁 | 額外也能開（TAB_GATES） |
|---|---|---|
| bulletin | 公布欄 | |
| projects | 專案總覽 | |
| preprod_plan | 拍攝企劃 | 場景庫、器材庫、產業情報、提案庫、片庫 |
| preprod_locations／intel／equipment | 場景庫／產業情報／器材庫 | |
| preprod_proposals | 提案庫 | 片庫 |
| references | 片庫 | |
| backup … footage（後期 9 把） | 後期各分頁 | footage 也可由 transcribe 開 |
| crm_clients／crm_projects／crm_quotes | 客戶／專案／報價 | crm_projects 也開提案庫、片庫、審批門戶 |
| crm_invoices | 帳務 | finance_partner 也能開（唯讀） |
| timesheets／hr_leave／hr_benefits／journal | 工作追蹤／請補修／福委會／週誌 | |
| portal | 審批門戶 | |
| website_admin | 官網管理 | |
| me_*（13 把） | 員工工作台 /my.html 各區（無 SPA 分頁） | |

## 3. 發現（代理回報彙整）

### 3.1 員工工作台／人事／前期（代理 B，已逐條到原始碼複核）

**掃描器誤判先更正**：`api_equipment`／`api_locations`／`api_intel`／`api_references` 全是分頁鑰匙級（`check_admin_or_module(request, *tab_modules(key))`）；`api_proposals` 28 支走 `proposal_auth`（提案庫家族鑰匙）；`benefits.py`／`petty.py` 的「無守衛」其實繼承 CRM router 的 `Depends(check_logged_in)` 再加 own-scope；**這一區真正無守衛的端點＝零支**。

**看得到做不到（按分頁）**

| 分頁（鑰匙） | 症狀 | 根因 | 建議 |
|---|---|---|---|
| 人事 › 福委會（`hr_benefits`）🔴 | **整頁打不開**：第一支 `GET /crm/benefits/pools` 就 403 | `benefits.py:198` 要 `money_view`＋`require_entity(full)`；`hr_benefits` 這把鑰匙在後端**零效力** | 讀取改 `money_dep`＋`check_admin_or_module('hr_benefits')`；審核類鈕前端依 `finance_approve` 藏 |
| 人事 › 專案工時（`timesheets`，非 Lv3）🔴 | 預設視圖「專案」與「設定」第一支就 403，整頁載入失敗 | `/timesheets/summary` 是 `check_admin`＋`require_entity(MINE)`（`api_timesheets.py:995`）；建議預算／改預算／指定專案／同步 token 同樣 | summary 對 timesheets 開放並抹私帳欄位，或「專案／設定」視圖限 Lv3；其餘鈕依 `d.editable` 藏 |
| 人事 › 專案工時 → 「人員」連到 /hours.html | 403（沒 me_finance）或 409（沒綁人員） | `/me/team/*` 守 `require_bound_staff(me_finance)`（`api_me.py:498`） | `_me_ident` 加收 timesheets，或拿掉連結 |
| 業務 › 專案管理 › 影像紀錄子頁（`crm_projects`） | 整個子頁 403 | 9 支 `…/media-log*` 全是 `media_log` 限定（`media_log.py:653-671`） | 讀取放行 `crm_projects`；寫入維持 `media_log`＋藏鈕 |
| 財務 › 器材清冊（`crm_invoices`） | 清單看得到，新增／存／刪 403 | `gear.js:153-170` 打 `/api/v1/equipment` 寫入，守 `equipment` 家族 | 寫入端加收 `crm_invoices`／`finance_mine`，或前端依 `hasModule('equipment')` 藏 |
| 前期 › 片庫（`references`） | 每列「解除連結」403 | `DELETE /references/links/{id}` 要**目標**（提案／專案）的鑰匙（`api_references.py:955`） | 放行 references，或依 link_targets 藏 |
| 手機 /m/crm.html | 「工作紀錄」「假勤」tab 固定畫，只有 crm_projects 的人進去 403／409 | 殼閘門只看 admin‖crm_projects（`m/crm.js:24`） | 底部 tab 依 `me.modules` 過濾 |
| 員工頁請假撤回（非 RBAC） | 已核准兩天內撤回必 422 | `my.html:1379` 送 `cancel_note`，schema 只認 `note` | 改欄位名 |

**過鬆**

- 🔴 `GET /api/v1/milestones/week`：登入即可，回**整個在職人員清單＋每案本週工時＋負責人**；剛註冊只有 me_profile 的帳號也拉得到。建議 `check_admin_or_module(timesheets, me_today_zone)`＋子鑰匙任一。
- 🟡 `GET /milestones/project/{id}`（登入即可讀任意案里程碑）、`GET /crm/petty/options`（登入即可讀全部專案含客戶名）。
- 設計內（不標）：`/benefits/me*`、`/petty/me*` 登入＋綁定即可、只動自己；公開 token 端點都逐字比對可撤銷。

**就算有分頁鑰匙也建議留管理員**：`PUT /intel/settings`、`POST /intel/run`（全站 runner、燒 claude）；提案 `plan/share`（鑄匿名可寫外鏈）、`DELETE /proposals/{pid}`（連帶刪五種資料）、`convert`（只有 preprod_plan 就能建 CRM 專案，至少要 crm_projects）；`references/import_csv`、`ai_facets`；`POST /timesheets/manual`（幫任何人代填正本）；`project_map`／`remap`／`budgets`（私帳指名制，別放寬，改藏鈕）；`api_hr` 核准／退回／補休／特休額度（一把 hr_leave＝人事全權，含核自己的假，建議分第二把或留 Lv3）；福委刪池／撥款；零用金 `item-owners`／`bind-label`；`media-log/settings`（改**全系統**收檔根目錄）、`catchup_now`；公布欄 `ask`／`delete`／`reorder`；器材／場景整筆刪除（政策選擇）。

**死端點**：`/journal/reply`、`/journal/help`（前端已拿掉）。

### 3.2 後期流程／系統／OTA／機器／API key／官網（代理 C，AST 掃 Depends＋函式體）

後期流程建任務的端點（backup／verify／transcode／concat／report 建立／transcribe／tts／queue／bookmarks／schedules／drone 掃描）依「本機代理免登入」前提**不列問題**。官網 `routers/website/admin_*.py` 逐支確認：**全部 16 模組、含 `register_crud` 工廠 48 支，都包在 `website_admin` 守衛裡，沒包到的 0 支**。

**沒守衛但危險（安全，不是授權不足）**

| 端點 | 現況 | 風險 | 建議 |
|---|---|---|---|
| 🔴 `GET /api/settings/load`（`api_system.py:76-90`） | 匿名可讀；只抹 jwt_secret／database_url／google secret | **`timesheet.ingest_token` 沒在抹除清單**——admin 限定的 token 被匿名端點原樣吐出（經 CF tunnel 公網可讀），持有它可對 `/timesheets/ingest` 塞假工時；`notifications.*webhook*` 也沒抹（可對團隊 Chat 灌訊息） | 加進 `_SECRET_SUBKEYS`；或整支改 `check_lan_or_logged_in` |
| 🔴 `GET /api/v1/settings`（`api_system.py:215-227`） | 匿名、**前端零呼叫**、完全不抹 | 回 line_token／gchat_webhook／alert_webhook／custom_webhook 原值 | 直接刪 |
| 🔴 `POST /api/v1/system/restart`、`/internal/restart`（`api_system.py:528-537`、`api_ota.py:133-144`） | 硬編碼字串 `originsun-internal-restart` 或 loopback | 字串隨公開的 `/download_update` 發到每台機器；知道字串就能讓 master／任一 agent 重啟＋跑 OTA | 改用 `_get_secret()`（`/internal/alert_email` 已是這作法）；呼叫端 `api_ota.py:636`、`api_agents.py:345` 同 commit 改 |
| 🟡 `GET /api/v1/api_keys`、`/all`（`api_api_keys.py:43-48`） | 登入／管理員 | 列表回 **`raw_key`**（明文存），任一 Lv3 拿到所有人的原始 key 可冒充 | 列表不回 raw_key、只留 hash |
| 🟡 `POST /drone_watcher/config`、`run_now`、`cancel_all`；`/schedules*` 增刪改 | 匿名 | 持久化設定＋定時執行，任何人可設任意來源／目的、刪別人排程 | `check_lan_or_logged_in` |
| 🟡 `POST /api/v1/control/update` | 只認 loopback | cloudflared 在 NAS 時擋得住；若搬回 master 本機就全開 | 加 `cf-connecting-ip` 檢查 |
| 🟢 `/publish/status`／`history`／`suggest_notes`、`/deploy_*` GET、`/update_status` | 匿名 | 洩漏 checkout 路徑、版本、commit 標題；suggest_notes 對匿名跑 git 子程序 | 套 `_check_admin`（前端本來就帶 token） |
| 🟢 `/download_update`、`/download_agent`、`/bootstrap.ps1` | 匿名 | 整套原始碼公網可下載 | `check_lan_or_logged_in` |

設計味：`api_system`／`api_ota`／`api_agents`／`api_job_history`／`api_report` 的守衛都是 `try: check_admin except ImportError: pass`（import 失敗＝全開）；官網那邊是失敗回 503。建議統一 fail-closed。

**看得到做不到**

| 分頁／入口 | 症狀 | 根因 | 建議 |
|---|---|---|---|
| 🔴 右上角選單「系統設定」「重新啟動 Agent」 | **對所有人、含未登入都顯示**；非管理員填完整份設定按存 → 403，提示卻是「儲存失敗，請檢查伺服器連線」；重啟按了被告知「正在重新啟動中」白等 15 秒 | `auth-state.js:153-154,170-171` 沒放進 Lv3 區塊；`settings-modal.js:158-162`、`auth-state.js:183-189` 不看回應 | 選單項目移進 Lv3；401/403 顯示「需要管理員」 |
| 🔴 `POST /api/settings/save` 一把 admin 鎖擋住四條非管理員的正常工作 | 員工檔案「編輯職能選項」（`crm-staff.js:548`，crm_staff）、報價頁「公司資訊」（`crm-quotes.js:699`，crm_quotes）、現金流「固定成本」（`crm-cashflow.js:204`）、專案總覽「系統參數」（`projects.js:218`，projects）：畫面看似成功、重整就消失 | 四個前端都寫進同一支 settings/save，且不檢查 `res.ok` | 後端依頂層鍵分流到對應模組（staff_roles→crm_staff、company→crm_quotes／crm_invoices、finance.*→crm_invoices、concurrency→projects）或各開專屬端點；前端看 `res.ok`、403 說「需要管理員」 |
| 🔴 機器卡「編輯名稱／IP」`PUT /agents/{id}` | **連管理員都 401** | `agent-cards.js:451` fetch 沒帶 Authorization；`login-modal.js:66` 的全域補 header 漏了 PUT | 一行修 |
| 🟡 報表歷史每筆 ✕（`DELETE /reports/{id}`） | 非管理員看得到刪除鈕；跨機刪除 token 可能不被對方 agent 認 | `report-history.js:39,57` | ✕ 只在 Lv3 畫；跨機由 master 代理 |
| 🟢 `GET /drone_meta/frame` 管理員限定 | 死端點（編輯器用 filmstrip） | — | 刪或改 `?token=` |
| ✅ 一致 | `DELETE /job_history`（admin-only 鈕）、agents 增刪／OTA 推送（`_isAdmin` 才畫）、drive_map | | |

### 3.3 財務／錢流（代理 D）

**守衛真值表**（`core/ledger.py:107-153`、`core/money.py:221-271`、`routers/crm/_shared.py`）：母帳 view＝(`crm_invoices` AND `money_view`) OR `finance_partner`；母帳 full＝`crm_invoices` AND `money_view`；私帳＝`finance_mine`（Lv3 不隱含）；`money_dep`＝`money_view`；`_check_finance_auth`＝`crm_invoices` 一把（**不看 money_view**）。

🔴 **結構性不對稱**：CRM 帳務三大表（發票／請款／收支）的「讀」要 `crm_invoices`+`money_view`，「寫」只要 `crm_invoices`（`_mine_or_admin_write`、`_import_money_csv`）；api_finance 全域讀寫都要兩把；分類樹寫入也要兩把。同一個財務分頁三種尺。這一條是下面多數問題的根。

**四個指定問題**

| 帳號形狀 | 結果 |
|---|---|
| 有 `crm_invoices`、沒 `money_view` | 財務分頁**入口看得到、進去每一頁都 403**（tab 靠 crm_invoices 顯示，儀表板／三表走 view scope，三條都不成立）；反向卻**寫得動**母帳（POST/PUT/DELETE 發票、請款、收支、三支 import_csv）、能下載發票影像 `GET /invoice-file`——「做得到看不到」 |
| 有 `money_view`、沒 `crm_invoices`（＋crm_projects） | 專案頁「收付款」「發票」分頁讀 403，被 `_q` 吞成「沒有發票」→ 結案檢查誤判；「開發票」「連結發票」「新增預支款」「標已付」按鈕全可見、全 403；`advance-expense.html` 整頁死（首支 `GET /payments/{id}` 403 → 顯示「請登入」）；手機「發票」「付款」列表 403 |
| `finance_partner` 會不會誤寫 | 財務域不會（寫入都 full）；但**任何登入帳號**都能經 `POST /crm/public/projects/{id}/expenses`、`/advance/{id}/expenses`、`/receipts/{eid}` 寫雜支傳收據（見紅名單）；多讀到資產總覽（view scope，前端藏、API 拿得到） |
| 手機寫入 | `MOBILE_WRITE_MODULES=()` 只管「加備註」與 `can_write` 旗標；發票／付款表單靠 `can_write`（Lv3）藏，但端點收 `crm_invoices`（做得到看不到）；雜支端點與表單都 Lv3（一致）；零用金自助＝登入＋綁定，`me_petty` 只在前端判 |

**看得到做不到**

| 分頁／入口 | 症狀 | 根因 |
|---|---|---|
| 🔴 專案頁雜支（crm_projects＋money_view，非 Lv3） | 就地新增列、格子編輯、✕ 刪除、雜支登記 modal、🔗 登記連結全 403 | `costs.py:452-591` expenses 是 `_check_auth`（Lv3）、`expense-links` 也是（:77,100）；**同一畫面旁邊的 cost-lines 已是 `crm_projects`**，兩種尺 |
| 🔴 專案頁收付款／發票（無 crm_invoices） | 讀 403 被吞成「沒有發票」；按鈕全 403 | `crm-projects-pay.js:120-122,296,442`、`crm-projects-finance.js`、`crm-projects-invoices.js` 無 `hasModule('crm_invoices')` 閘 |
| 🔴 財務分頁對 crm_invoices-only | 每頁 403 | `tab-config.js:84-93` 用一把開，後端 view 要兩把 |
| 🟡 advance-expense.html | 整頁「請登入」；收據上傳 Lv3 且不檢查回應→靜默丟 | `payments.py:602` full；`costs.py:558` |
| 🟡 財務設定：申請人／代開費率／發票根目錄／零用金收據根目錄 | 非 Lv3 財務 403 | `invoice_files.py:452,478,538,548`、`costs.py:770,779` 是 `check_admin` |

**無守衛／過鬆（錢，紅）**

- 🔴 `POST /crm/public/projects/{id}/expenses`、`/crm/advance/{id}/expenses`、`/crm/public/projects/{id}/receipts/{eid}`（`costs.py:311-336,596`）：**只要登入**就能對任意專案寫雜支、傳收據（含 finance_partner、只有 me_profile 的新註冊）。
- 🔴 `GET /crm/public/projects/{id}/expenses`（`costs.py:435-447`）：任何登入者拿全專案雜支金額（`core/money.py:173-174` 自己標「要 owner 決定」）。
- 🔴 `GET /crm/receipt-file?path=`（`costs.py:740-760`）：任何登入者可讀 uploads/ 與 receipts_root 下所有收據影像。
- 🟠 `GET /crm/invoice-file` 只驗 crm_invoices；`GET /finance/assets/*` view scope 讓合夥人拿母公司資產總額（前端藏）；零用金自助端點 `me_petty` 後端不驗。
- 🟡 `GET /crm/clients/{id}` 不看 entity（私帳客戶匯款資訊）；`GET /crm/users` 列全員 email；`/crm/m/*` 讀取只驗登入不驗 crm_projects；`POST /finance/bank-statement/drafts` 先查 DB 再驗權。

**代理 D 的優先序**：① 關掉三支登入即可寫雜支＋ `receipt-file` 加 `money_dep`；② 母帳寫入統一 `require_entity('parent', full)`（收緊，先盤生產上 crm_invoices 無 money_view 的帳號）；③ 財務分頁入口與 view scope 對齊（或畫「缺金額檢視權限」）；④ 專案頁錢流按鈕加閘、雜支寫入改 `_check_project_write_auth`；⑤ 手機寫入分三把（`can_invoice`＝crm_invoices＋money_view）。

### 3.4 CRM（專案／員工／報價／客戶／完稿結案／提案資產）＋手機＋拍攝＋門戶（代理 A）

**事實**：整個 CRM router 掛 `Depends(check_logged_in)`（`_shared.py:78-98`），所以掃描器的 NONE＝「登入即可」不是匿名；真正匿名只有 token 自驗的 public／token router。CRM 六個分頁裡**除了兩處**（結案收件匣看 website_admin、推送私帳看 finance_mine）**沒有任何按鈕用 hasModule 藏**——後端凡是守得比分頁鑰匙嚴，畫面一定看得到、按下去一定 403。

**看得到做不到（按分頁）**

| 分頁（鑰匙） | 按鈕 → 端點 | 現守衛 |
|---|---|---|
| 專案管理（`crm_projects`；soca／Ryansnap／nashtsai） | 「啟動專案」（兩處）→ `PATCH /projects/{id}/status`；編輯視窗改「階段」→ `PUT` 內 status 門 | 管理員（`ADVANCE_MODULES=()` 是刻意政策） |
| | kebab「複製」→ duplicate；「案型清單」→ `POST /project-types`；「匯入 CSV」 | 管理員 |
| | 專案表單「新增客戶」→ `POST /clients` | 管理員 |
| | 雜支 modal 新增／編輯／刪除／收據、行政雜支輸入列 → `costs.py:452-591` | 管理員；**預算結算「存檔」一半成功（cost-lines）一半 403（雜支）** |
| | 子表卡 🔗、「雜支登記連結」→ `POST /expense-links` | 管理員 |
| | 「完稿結案」分頁整片：works／showcase 讀、歸檔卡（archive.py 連 GET 都管理員）、新增作品、編輯連結 | website_admin／管理員 |
| | 提案企劃 ⚙ 根目錄 | 管理員（全站設定，可留） |
| 員工檔案（`crm_staff`） | 新增／複製／編輯（含 inline）／刪除／匯入／履歷每一格／照片／作品集**讀**寫／發履歷編輯連結；連 `GET /staff/{id}/portfolio`、rate-history 都要管理員 | 全 `_check_auth`——這把鑰匙目前等於唯讀分頁，但按鈕全在 |
| 報價管理（`crm_quotes`） | 新增／編輯／刪除／分享／範本三支／報價彈窗建新專案（要 crm_projects）／建新客戶／已簽核→啟動專案 | 管理員；另外**沒 money_view 的 crm_quotes 帳號連清單都 403**（讀端點全 money_dep，crm-quotes.js 沒 moneyGate） |
| 客戶管理（`crm_clients`） | 新增／複製／編輯（inline）／刪除／匯入 | 全管理員 |
| 提案庫（只有 `preprod_plan` 的帳號） | 資產資料夾／deck／上傳全 403 | `assets_auth` 只放 (crm_projects, preprod_proposals)，tab 閘與 `proposal_auth` 卻含 preprod_plan |
| project.html 人員配置（preprod_*） | 「＋ 新增派工」無條件畫 | 端點 crm_projects |
| 手機報價分頁（crm_projects） | 「建立／成案／拒絕」沒掛 `.w` | 管理員 |

**過鬆**：`POST /advance/{id}/expenses`、`/public/projects/{id}/expenses`、`/public/cost-groups/{id}/expenses|receipts`、`/public/projects/{id}/receipts/{eid}` 登入即可寫（本尊是管理員限定，同一張表）；讀取側 `/public/cost-groups|projects/{id}/info|expenses` 不走 money_dep。命名誤導：`GET /public/site/works|team` 掛主 router＝需登入且無人呼叫。

**應留管理員**：刪除（客戶／員工／報價／雜支／作品集）、推進階段（政策）、三支 CSV 匯入、全站根目錄／設定、歸檔 folders／scan（動 NAS）、官網發佈（已一致）。錢流寫入若放行建議用 `crm_invoices` 而非 crm_projects（owner 2026-08-15 註解「其他 CRM 寫入的使用者是財務」）。


## 4. 修法提案（三批，owner 點頭哪批就做哪批）

### 第一批：安全洞，不改任何人的權限 —— ✅ 已修（2026-09-08，v2.4.397）

做法備註：內部重啟金鑰維持原字串（機隊各自的 jwt_secret 不共用，換成 `_get_secret()` 會讓 master 推不動 agent），改成**經 cloudflared 進來的一律 403**（`core.auth.via_cloudflare`），公網那條路關掉、區網機隊互打不受影響。`settings/load` 仍匿名可讀（前端四處無 token 讀它），但工時同步 token 與 webhook 只回給管理員 token。雜支舊路（`?project=`／`?group=`／預支款頁）收成登入＋`crm_projects`，外部一律走 `?t=` 連結。

| # | 修什麼 | 檔案 |
|---|---|---|
| 1 | `/api/settings/load` 抹除清單加 `timesheet.ingest_token`、`notifications.*webhook*`；刪掉無人呼叫、不抹機密的 `GET /api/v1/settings` | `routers/api_system.py:76,215` |
| 2 | 硬編碼 `originsun-internal-restart` 改 `_get_secret()`（兩處守衛＋兩處呼叫端同 commit） | `api_system.py:532`、`api_ota.py:139,636`、`api_agents.py:345` |
| 3 | 三支「登入即可寫雜支／傳收據」對齊本尊守衛；`/public/*/expenses|info` 讀取加 `money_dep`；`GET /receipt-file` 加 `money_dep` | `routers/crm/costs.py:310-447,595,740` |
| 4 | `GET /milestones/week`、`/milestones/project/{id}` 從登入即可改成 timesheets 或今天與這週鑰匙 | `routers/api_milestones.py:33,63` |
| 5 | API key 列表不回 `raw_key`（只留 hash） | `routers/api_api_keys.py:43` |
| 6 | 機器卡 `PUT /agents/{id}` 補 Authorization（連管理員都 401） | `frontend/js/auth/login-modal.js:66` 或 `agent-cards.js:451` |
| 7 | 員工頁請假撤回 `cancel_note`→`note`（必 422） | `frontend/my.html:1379` |
| 8 | `drone_watcher/config|run_now|cancel_all`、`schedules` 寫入加 `check_lan_or_logged_in`；publish/deploy 狀態類 GET 套 `_check_admin` | `api_drone_watcher.py`、`api_schedules.py`、`api_ota.py` |
| 9 | 五個 `try: check_admin except ImportError: pass` 改 fail-closed | `api_system`／`api_ota`／`api_agents`／`api_job_history`／`api_report` |

### 第二批：「看得到做得到」——照分頁鑰匙放行（約 1.5 天；這批直接消掉同事的 403）

✅ 已做（2026-09-08）。實作備註：雜支寫入歸 crm_projects（owner）、收據清單另要 money_view；零用金收據根目錄 GET 給 finance_approve；hr 核准類留管理員；benefits 讀取＝money_view＋三把任一；timesheets summary 抹私帳欄位（suggested_hours、candidates）；settings/save 依頂層鍵分流（非管理員只准寫那組鍵）；手機 can_invoice／can_expense 兩旗標；前端藏鈕用「明確 false 才藏」讓 CF 快取的舊元件照舊。四支新測試檔 test_batch2_*.py 釘住。

| 分頁鑰匙 | 放行的端點 | 仍留管理員（前端藏鈕） |
|---|---|---|
| `crm_staff` | 新增／編輯／履歷／照片／作品集讀寫／編輯連結；`GET portfolio`、`rate-history`（配 money_view） | 刪除、匯入 CSV |
| `crm_clients`（建客戶也給 `crm_projects`、`crm_quotes`） | `POST /clients`、`PUT /clients/{id}` | 刪除、匯入 CSV |
| `crm_quotes` | 新增／編輯／分享／範本三支 | 刪除；「已簽核→啟動專案」走政策（見下） |
| `crm_projects` | 複製專案、案型清單、`expense-links`、歸檔 GET／PATCH／rows、works／showcase 讀取、影像紀錄子頁讀取、提案資產給 `preprod_plan` 家族 | 推進階段（`ADVANCE_MODULES` 政策）、匯入 CSV、根目錄設定、歸檔 folders／scan、官網發佈 |
| 雜支寫入 | `costs.py` expenses POST／PATCH／PUT／收據：**owner 選** `crm_projects`（跟同畫面 cost-lines 一致）或 `crm_invoices`（錢歸財務） | 刪除雜支 |
| `hr_benefits` | 池／撥款／entries 讀取（配 money_view） | 審核類仍 `finance_approve`＋前端依它藏 |
| `timesheets` | `/timesheets/summary` 讀取（抹私帳欄位）、`/me/team/*` 加收 timesheets | 私帳對映／預算（指名制不放寬）、Sheet 拉取、代填正本 |
| `references` | `DELETE /references/links/{id}` | import_csv、ai_facets |
| `settings/save` | 依頂層鍵分流：`staff_roles`→crm_staff、`company`→crm_quotes／crm_invoices、`finance.*`→crm_invoices、`concurrency`→projects | 其餘仍管理員；右上角「系統設定」「重啟」只在 Lv3 畫、403 顯示「需要管理員」 |
| 手機 | `/crm/m/options` 多回 `can_invoice`（crm_invoices＋money_view）、`can_expense`（同雜支政策）；發票／付款／雜支表單各看各的；狀態轉換鈕掛 `.w` | 加備註仍 Lv3 |
| 前端閘 | 專案頁發票／請款／預支款按鈕依 `crm_invoices`＋`money_view` 藏；`_q` 吞 403 改顯示「沒有帳務權限」；報表歷史 ✕ 只在 Lv3 畫；手機底部 tab 依 modules 過濾 | |

### 第三批：一把尺——要先盤帳號再動（約 1 天）

✅ 已做（2026-09-08）。盤過生產：沒有任何帳號是「有帳務沒金額檢視」或「有報價沒金額檢視」，收緊不鎖人。母帳寫入（`_mine_or_admin_write` 母帳分支、三支匯入、發票影像）改 `require_entity('parent', full)`；`money_view` 成為帳務／報價的父鑰匙（範本 normalize 自動配、權限畫面 PERM_PARENT 縮排）；財務分頁對缺金額檢視的人直接說缺哪把；`tests/unit/test_batch3_one_ruler.py` 的 `ADMIN_ONLY_CRM` 白名單釘住「routers/crm 還是管理員限定的只能是這 27 支」。零用金自助端點加 me_petty、`/crm/clients/{id}` 私帳 404 兩項**沒做**（放寬守衛不能收回原本的鑰匙，先留）。

| # | 修什麼 | 為什麼要先盤 |
|---|---|---|
| 1 | 母帳寫入統一 `require_entity('parent', full)`（發票／請款／收支寫入、三支 import、`GET /invoice-file`） | 這是**收緊**：生產若有「crm_invoices 沒 money_view」的帳號會失去寫入。合夥範本兩把都有，在職範本都沒有，所以套完範本後應該沒人受影響，但要先查 |
| 2 | 財務分頁入口對齊 view scope：`crm_invoices` 沒 `money_view` 就顯示「缺金額檢視權限」而不是每頁 403；使用者管理勾 crm_invoices 時提示配 money_view | 同上 |
| 3 | `crm_quotes` 讀取要 money_view：要嘛範本裡 crm_quotes 一定配 money_view，要嘛報價分頁加 moneyGate | 政策 |
| 4 | 零用金自助端點加 `me_petty`、`/crm/m/*` 讀取加 `crm_projects`、`GET /crm/clients/{id}` 私帳列回 404 | 放寬守衛不能收回原本的鑰匙——先列舊守衛放行的帳號形狀 |
| 5 | 源碼掃描測試釘住：`routers/crm/*` 每支端點不是分頁鑰匙守衛就得在 `ADMIN_ONLY_ACTIONS` 白名單裡 | 做完一、二批才釘得住 |

### 要 owner 決定的三個政策

1. **推進專案階段**（啟動專案、改階段）：維持管理員限定（前端把「啟動專案」依 `can_advance` 藏），還是開給 `crm_projects`？
2. **雜支寫入**歸誰：`crm_projects`（做專案的人自己登）還是 `crm_invoices`（財務）？
3. **`hr_leave` 一把＝人事全權**（含核自己的假）：要不要分出「核准」第二把，或核准留管理員？

## 附錄 A：管理員限定端點（141 支，掃描器輸出）

| 檔案 | 端點 |
|---|---|
| api_auth（6） | 使用者 CRUD、身份範本 —— 本來就該管理員 |
| api_ota（7） | restart／publish／rollback／deploy／push_fleet —— 本來就該管理員 |
| api_crm_mobile（2） | 手機加備註、手機改報價狀態（`MOBILE_WRITE_MODULES` 空＝一期只給管理員） |
| api_portal（5） | 看片連結 CRUD、評論解決 —— 分頁鑰匙是 `portal` |
| api_references（1） | 只有 `POST /archive/settings` 真的是管理員；其餘 17 支是 `references` 家族鑰匙（掃描器誤判） |
| api_shoots（6） | 場次單筆讀改、狀態、日曆設定 |
| api_timesheets（11 真管理員＋6 私帳管理員） | ingest_token／pull／digest／rows 寫入／conflicts／recent＝`check_admin`（前端多數已用 `d.editable` 藏）；summary／projects／project_map／remap／budgets／project_budget＝`check_admin`＋`require_entity(MINE)`（連沒指名私帳的管理員也 403） |
| api_drone_meta（1）、api_job_history（1）、api_report（1） | frame／清歷史／刪報表 |
| crm/archive（7） | 完稿結案整區 —— 分頁鑰匙是 `crm_projects` |
| crm/clients（3） | 建客戶、改客戶、匯入 —— 分頁鑰匙是 `crm_clients` |
| crm/costs（11） | 雜支 CRUD、單據、收據根目錄 |
| crm/invoice_files（5） | 遷移、費率、申請人、根目錄 |
| crm/projects（4） | 案型、複製專案、狀態推進、匯入 |
| crm/proposal_assets（1） | 提案資產設定 |
| crm/quotes（9） | 建報價、改／刪報價、分享、範本 CRUD、根目錄 —— 分頁鑰匙是 `crm_quotes` |
| crm/showcase（6） | 封面、圖庫、製作過程、自動 credits |
| crm/staff（11） | 員工 CRUD、匯入、履歷、照片、作品集、編輯 token —— 分頁鑰匙是 `crm_staff` |
