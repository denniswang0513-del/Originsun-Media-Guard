# 報價單／發票／員工工作頁面 —— 不受 8000 生產機開關影響

> owner 2026-09-10：「我希望報價單、發票、員工的工作頁面 不會被 8000 的生產機開關影響」
> 狀態：**規劃，未動工**。拍板事項在 §9。

---

## 0. 一句話

這三個面現在 **100% 靠 master Windows（`C:\OriginsunAgent`，port 8000）**：
頁面是它 serve 的、API 是它跑的。而**它們用到的東西幾乎全都不在 master 上** ——
資料在 NAS 的 Postgres、圖在 NAS 的圖床、程式碼 NAS 已經有一份。
所以這不是「做一套備援」，是**把已經在 NAS 的東西接起來**。

官網（Phase M）已經解過同一題，做法現成：NAS 24/7 容器 + nginx + cloudflared。
這次是把同一招套到內部面上。

---

## 1. 現況：誰在提供這三個面

| 面 | 頁面（誰 serve） | API（誰跑） | master 關機時 |
|----|------------------|-------------|---------------|
| 報價 · 桌機 | `frontend/index.html` SPA + `tabs/crm/crm-quotes.*`（master 靜態 mount） | `routers/crm/quotes.py`（master `_ROUTER_MODULES`） | 全死 |
| 報價 · 手機 | `frontend/m/crm.html` + `m/views/quotes.js`／`quote-chat.js` | 同上 + `api_crm_mobile` | 全死 |
| 報價 · 客戶端 | `/q/{code}` 線上檢視（master `main.py` 轉呼 quotes.py） | 同上 | **客戶手上的連結死掉** |
| 發票 · 桌機 | `tabs/crm/crm-invoices.*` | `routers/crm/finance.py` ＋ `invoice_files.py` | 全死 |
| 發票 · 手機 | `frontend/invoice.html`、`m/views/invoice.js` | 同上 | 全死 |
| 員工工作頁 | `frontend/my.html` + `js/my/*`（七支傳統 script） | `api_me`／`api_timesheets`／`api_hr`／`api_journal`／`api_milestones`／`crm/work_stages` | 全死 |

**已量到的三件好消息**（決定了方案的形狀）：

1. **這些頁全部同源相對路徑取 API**（`fetch('/api/v1/...')`），沒有寫死任何 host。
   → 換一個 origin serve 它們，**前端一行都不用改**。
2. **它們都不用 Socket.IO**（`frontend/m/`、`my.html`、`js/my/`、`invoice.html` 全掃過，零命中）。
   → 不需要把 `main.py` 的 socketio ASGI 殼搬過去。
3. **NAS 容器已經有整份 `routers/` `core/` `db/` `services/`**（`publish_update.NAS_SYNC_CODE`），
   而且 `main_website.py` 已經在 `from routers.crm import ...` —— 那一行會跑 `routers/crm/__init__.py`、
   連帶 import 全部 25 個領域模組。影像紀錄公開頁在 NAS 上是活的，代表**整包 CRM 程式碼在那個
   精簡容器裡 import 得起來**。缺的不是碼，是「有沒有把 router 掛上去」。
   （實作第一步仍要先確認：`docker logs website-api` 沒有 `media_log public router 未掛載` 的 warning。）

---

## 2. NAS 上已經有／還缺什麼

| 項目 | NAS 現況 |
|------|---------|
| 程式碼 `routers/ core/ db/ services/ config.py` | ✅ 每次 `/publish` scp 過去 |
| 同一顆 Postgres（`originsun_postgres`） | ✅ 同一份資料，不是複本 |
| 同一把 `jwt_secret` | ✅ 走 env，master 簽的 token NAS 驗得過 |
| nginx（`Website_Nginx` 8090）+ cloudflared | ✅ 已在跑 |
| 圖床 PasteAssets、`/share/Archive` | ✅ 已掛進容器 |
| `frontend/` | ⚠️ 只有 4 個公開頁 + `tabs/proposals` + `js/shared` + `img`（清單在 `core/public_assets`） |
| `settings.json` | ❌ **不在同步清單**，容器裡拿到的是 `config._DEFAULT_SETTINGS` |
| `users.json` | ❌ 不在容器（帳號正本的另一半） |
| Python 套件 | ⚠️ 只有 fastapi／sqlalchemy／asyncpg／httpx／Pillow／notion。**沒有** jinja2、playwright、google-auth、openpyxl |
| `claude` CLI | ❌ 只有 master 有 |
| 專案磁碟（T:／S:／N: 等 UNC） | ⚠️ 只掛了 Archive 與 PasteAssets 兩個點 |

---

## 3. 哪些功能天生離不開 master

先講清楚，免得規劃出「看起來全活、真的用才發現半殘」的東西：

| 功能 | 為什麼 | 處置 |
|------|--------|------|
| **AI 報價助理**（`/quotations/{id}/chat`） | 後端 subprocess 叫 `claude` CLI，只有 master 裝了、也只有 master 有那個登入 | 不搬。NAS 面上把 AI 分頁**藏起來**，或顯示「需要主機在線」 |
| **產** 報價單 PDF（`services/html_pdf.py` ＝ Playwright + Chromium） | 容器 image 會多 ~700MB | 不搬 —— 改成「生成報價單」之後，**產**只發生在 master，**送**在 NAS。見 §5 P1 |
| 備份／轉檔／串接／報表／TTS／空拍 | 吃本機 ffmpeg 與記憶卡 | 本來就不在這三個面內 |
| 排程器（`core/scheduler.py`） | `core.topology.is_master_machine()` 擋著 | 新入口**不准** import 它（守衛見 §7.7） |
| 帳號 CRUD（新增／改權限） | `users.json` 是正本的一半，NAS 沒有 → 兩邊會漂 | NAS 面**只開 login／me／refresh**，帳號管理維持 master 限定 |

---

## 4. 建議方案：NAS 開第三個容器 `office-api` ＋ 一個**永遠走 NAS** 的網址

```
                    ┌────────── master Windows（會關機）──────────┐
foundry.originsun-  │  main.py : 8000                             │
studio.com   ──────►│  完整 SPA（30 分頁）＋ AI 報價助理 ＋ PDF     │
                    │  ＋ 備份／轉檔／報表／排程／機隊              │
                    └─────────────────────────────────────────────┘

                    ┌────────── NAS 192.168.1.132（24/7）─────────┐
office.originsun-   │  Website_Nginx 8090（多一個 server 區塊）    │
studio.com   ──────►│      └► office-api : 8002（新容器）          │
                    │           main_office.py                    │
                    │           ＝ 認證 ＋ CRM ＋ 員工工作台        │
www.originsun-      │      └► website-api : 8001（維持原樣）       │
studio.com   ──────►│           對外官網，一個 CRM 端點都不加       │
                    └─────────────────────────────────────────────┘
                              ↕ 同一顆 originsun_postgres
```

`main_office.py` ≈ `main_website.py` 的兄弟：同一套 lifespan／DB 自癒迴圈，
掛的 router 換成「**純資料庫的那半邊**」：

```
api_auth（只留 login / me / refresh / google / register / forgot / reset）
api_me、api_timesheets、api_hr、api_journal、api_milestones
routers.crm（整包 —— 客戶／專案／報價／人力／成本／發票／收支／請款／工作階段…）
api_crm_mobile、api_paste
```

不掛：`api_backup/verify/proxy/concat/report/transcribe/tts/drone_*/agents/ota/system/queue/schedules`、
socketio 殼、scheduler。

前端只搬**輕頁面那一組**（不是整個 SPA）：

```
frontend/m/**（手機殼 + 10 個 view）
frontend/my.html + frontend/js/my/**
frontend/invoice.html、expense.html、hours.html …（手機系列，逐頁決定）
frontend/js/shared/**（已經在同步清單裡）
frontend/img/**（已經在）
```

### 4.1 為什麼是「永遠走 NAS」而不是 failover

技術上可以在 nginx 做 `upstream { master; nas backup; }`，master 掛了自動切。**不建議**：

- master 提供的是完整 SPA，NAS 提供的是子集。靜默切過去 ＝ 使用者拿到一堆 404，
  而且不知道自己被切了。
- 備援系統最常見的死法是**平常沒人跑，真的要用時才發現壞掉**。
- 「有時候能用、有時候不能」的行為最難查。

所以：**`office.` 這條路平常就在用**（手機本來就都是 owner 在用），master 開著也走它。
master 的 `foundry.` 維持完整功能（桌機 SPA、AI 助理、PDF、機隊）。
兩條路都活著、職責清楚、任何一條壞了都看得出來。

### 4.2 為什麼不掛在現有的 `website-api` 上

`main_website.py` 檔內就寫著「**絕不可**改成掛 `routers.crm` 的主 router：那會把 160+ 個 CRM
端點曝在對外服務上」，`tests/unit/test_public_surface.py` 對整個 app 列舉斷言在守它。
那個容器服務的是 `www.originsun-studio.com` —— 匿名流量。內部面塞進去等於把那道守衛廢掉。
分開兩個 app、兩個 server 區塊，守衛才留得住（新 app 另立 `test_office_surface.py`，見 §7.7）。

### 4.3 為什麼不搬整個桌機 SPA

- `index.html` + `app.js` + `tab-config.js` + 30 個分頁 + 全部 `window.*` 交互，
  等於把整套內部系統複製第二份，兩邊會漂。
- 桌機報價分頁的主賣點（AI 報價助理）本來就綁 master 的 `claude` CLI，搬過去也是半殘。
- 手機版與 `my.html` 已經是**獨立入口頁**（不吃 SPA 殼），搬過去是乾淨的切面。

---

## 5. 分期

### P1 —— 客戶手上的連結不能死 ✅ **已實作（2026-09-10，dev）**

最不該壞的是**別人手上的網址**：master 關機時客戶點報價連結 502，而他不知道發生什麼事。

owner 2026-09-10 在這裡把問題改了個形狀，結果比原本的規劃**便宜也正確**：

> 「或者寄出改成 生成報價單」

原本 `/q/{code}` 是**活的** —— 客戶每次點開都重新從 DB 算金額、重新畫版面、重新開
Chromium 產 PDF。所以要讓 NAS 接手就得把 Playwright 塞進容器（+700MB）。改成
「按一顆鈕生成一份定稿文件」之後，那個前提整個消失：

- 生成時產 **PDF ＋ HTML 快照**兩個檔，寫進共用圖床（NAS PasteAssets，24/7、master 用
  UNC 寫得到、對外容器已經掛載）
- 客戶的連結送的是**那兩個檔** —— 不算金額、不畫版面、不開 Chromium
- 於是對外那條路只需要「把檔案送出去」，**現有的 `website-api` 容器就做得到**，
  不必為 P1 先開新容器

順帶修掉一個本來就不該有的行為：報價單是給客戶的**定稿文件**，以前你在系統裡改東西，
客戶手上那份會靜默地跟著變。現在畫面會說「內容已修改，尚未重新生成」，按了才換。

已完成的部分：

| 東西 | 位置 |
|------|------|
| 純規則（快照形狀、過期判定、畫面文案） | `core/quote_snapshot.py` |
| 生成 ＋ 歸檔 ＋ 寫圖床 ＋ 清舊檔 | `routers/crm/quotes.py` `generate_quotation_snapshot` |
| `POST /quotations/{id}/generate` | 同上（`_check_quotes_auth`） |
| 對外兩支（送快照，退路是即時渲染） | `public_quote_html` / `public_quote_pdf`，已從 `token_router` 移到 `public_router` |
| DB 欄位 `crm_quotations.pdf_snapshot` | `db/models/_crm.py` ＋ `main.py` 的 `_crm_cols` |
| 客戶連結網域 `quotes_public_base` | `GET/POST /quotations-root`（跟報價單資料夾同一張卡） |
| 測試 | `tests/unit/test_quote_snapshot.py`（含「檔名要過得了圖床的刪除白名單」那條） |

發票影像分享（`/e/{code}`）是**同一個形狀**但還沒做 —— 它本來就是使用者上傳的靜態檔，
比報價單更單純，之後照這條路補。

### P2 —— 三個面上線（1.5～2 天，主體）

1. `main_office.py` ＋ `docker/Dockerfile.office` ＋ compose 服務 ＋ nginx server 區塊
2. `core/office_assets.py`（仿 `core/public_assets.py`）：頁面清單 ＋ 模組目錄 ＋ import 閉包守衛
3. `publish_update.py` 加第二組同步目標與 `docker restart office-api`
4. settings 搬家（§7.1，**這是最大的一塊，不是收尾**）
5. cloudflared 加 `office.` hostname
6. 端對端驗收（§8）

### P3 —— 補完（依 owner 實際使用狀況再排）

- ~~NAS 裝 Playwright~~ —— 不需要了（P1 的「生成報價單」把「產」與「送」拆開了）
- 發票影像分享 `/e/{code}` 照 P1 同一條路搬上 NAS
- 桌機 CRM 的「唯讀降級殼」（只讀報價／發票清單，不編輯）
- 「主機離線」的統一提示元件（AI 助理、PDF、機隊燈號共用）

---

## 6. 動到的檔案（規劃階段的預期，實作時以現況為準）

| 檔案 | 動作 |
|------|------|
| `main_office.py` | 新增（抄 `main_website.py` 的 lifespan／DB 自癒／CORS） |
| `core/office_assets.py` | 新增（頁面 ＋ 模組目錄清單正本，三處對齊） |
| `docker/Dockerfile.office`、`docker/requirements_office.txt` | 新增 |
| `docker/docker-compose.yml` | 加 `office-api` 服務（8002、掛 code/、掛專案磁碟點） |
| `docker/nginx/originsun.conf` | 加 `server_name office.…` 區塊 |
| `publish_update.py` | `NAS_SYNC_PATHS` 加 office 的前端 ＋ 重啟第二個容器 |
| `config.py` / 新 `core/office_settings.py` | settings 讀取改走共用 DB（§7.1） |
| `tests/unit/test_office_surface.py` | 新增（app 層曝露面 ＋ 前端閉包 ＋ nginx location） |
| `docker/INDEX.md`、`CLAUDE.md` | 補「第三個容器」與除錯路徑 |

---

## 7. 地雷（規劃時就要決定，不是實作時再說）

### 7.1 🔴 `settings.json` 不在 NAS —— 最大的一塊

容器裡 `config.load_settings()` 回的是 `_DEFAULT_SETTINGS`。這三個面實際會讀到的鍵：

| 鍵 | 誰讀 | 拿到預設值的後果 |
|----|------|-----------------|
| `invoices_root` | `crm/invoice_files.py:49` | 發票影像上傳／下載直接失敗（訊息還會誤導成「管理員尚未設定」） |
| `invoice_fee_rates` | `crm/finance.py:236,1100`、`invoice_files.py:442` | 代開費算成 0 → **金額靜默錯誤** |
| `invoice_applicants` | `invoice_files.py:419` | 申請人下拉是空的 |
| `quotes_root` | `crm/quotes.py` `_quotes_root()` | 報價單歸檔沒地方放（**只有 master 會用到** —— 生成在 master） |
| `assets_host.dir` / `base_url` | `core/assets_host` | 快照寫不進去／NAS 讀不到 → 客戶連結退成「尚未生成」。**P1 之後 NAS 這側也要有** |
| `company.*`（抬頭／logo／印章） | 報價 PDF | （生成在 master，NAS 不用） |
| `drive_map` 覆寫 | `core/drive_map` | 退回公司預設慣例，多半 OK |
| `timesheets` 同步 token | `api_timesheets:58` | 只有 master 的排程在用，NAS 不碰 |

**先例已經有了**：`core/public_access.py` 就是為了同一個理由把設定搬進共用 Postgres 的
`website_settings.public_access` 列（DB 讀不到 → 退 `settings.json` → 退預設，DB 掛掉不擋人）。
建議照抄那個形狀，把上面這些鍵搬成 `website_settings.office`（或各自一列）。

⚠️ **`invoice_fee_rates` 拿到空值是靜默的金額錯誤**，不是報錯 —— 這一項必須在 P2 開始前
就決定怎麼搬，不能留到收尾。

### 7.2 `users.json` 只在 master

登入讀 DB、JSON 只是 fallback，所以 **login 在 NAS 可行**。但 `_persist_user` 是雙寫，
在 NAS 上會只寫到 DB → 兩邊帳號漂。**處置：NAS 面不掛帳號 CRUD**（`/auth/users*` 不掛）。

### 7.3 檔案落地

發票影像、報價歸檔寫的是 master 視角的 UNC／磁碟代號。`core/drive_map.to_local_path` 的
stage 2 已經會把 `\\192.168.1.132\X` 翻成 `/share/X`，但**對應的 volume 要掛進新容器**
（現在只掛了 `/share/Archive` 與 PasteAssets）。掛哪幾個 ＝ 看 `invoices_root` /
`quotes_root` 實際指到哪，實作時先讀生產 settings。

報價單 P1 刻意繞開了這件事：快照寫的是**共用圖床**（PasteAssets），那個掛載點對外容器
早就有了 —— 「兩台機器都看得見同一份檔」在那邊已經解過一次，不用再解第二次。

### 7.4 Playwright／jinja2

`routers/crm/quotes.py` 對 `services.html_pdf` 是**函式內 import**（`:269, :348, :376`），
所以不裝也 import 得起來，只有真的按 PDF 才炸。要的是**接住那個例外並回友善訊息**，
不是讓它 500。

### 7.5 google-auth

`api_auth` 已經對缺套件優雅降級（`try: from core.google_auth import ... except ImportError`），
但那會讓 NAS 面**不能用 Google 登入**。→ `requirements_office.txt` 直接裝
`google-auth` ＋ `requests`（純 Python，不重）。

### 7.6 🔴 Cloudflare 的 `.js` 4 小時快取

新 hostname 一樣吃 `reference_cloudflare_js_cache` 那條規則。手機頁與 `my.html` 是
**傳統 script（非 module）**、載入順序敏感 —— 發版時同樣會出現「新 html ＋ 舊 js」。
新面上線第一版就要把相容契約寫進註解，不要事後補。

### 7.7 曝露面守衛

`test_public_surface.py` 守的是 `website-api`。新 app 要有**自己的** `test_office_surface.py`：

- 對整個 app 列舉路由（擋「順手多掛一個 router」）
- 前端 import 閉包都落在 `office_assets.MODULE_DIRS`（擋「順手 import 一個 `../crm/…`」）
- nginx 每個項目都有對應 location
- **斷言 office app 沒有 import `core.scheduler` 或 socketio**（擋「NAS 也開始跑排程」）

### 7.8 兩個 app 同時寫一顆 DB

已經是現況（master ＋ website-api），沒有新風險。但**排程只准 master 跑**
（`core.topology.is_master_machine()` 已擋，新入口別繞過它）。

### 7.9 `fire()` 背景工作

寄出報價會 `fire(purge_quote_chat_images)` ＋ `record_quote_prices` —— 兩支都是純 DB ＋
圖床刪檔，在 NAS 跑得動（圖床已掛載）。✅ 不用改。

### 7.10 版本握手

`website-api` 的 `/healthz` 回自己的版號給發版流程比對「碼同步了但容器沒重啟」。
新容器要有同一顆，否則就是靜默跑舊碼。

---

## 8. 驗收（做完要真的拔插頭驗）

1. `docker logs office-api` 沒有 `未掛載` 的 warning
2. `curl https://office.…/healthz` 版號 == master 版號
3. **把 master 的 8000 停掉**，然後：
   - `office.…/my.html` 登入 → 填一列工時 → 送出 → master 開機後在桌機看得到同一列
   - `office.…/m/crm.html` → 報價清單 → 開一張 → 改金額 → 存 → 桌機對得上
   - 發票登記 → 上傳一張影像 → 檔案真的落在 `invoices_root`（不是容器內的孤兒路徑）
   - 客戶的 `/q/{code}` 開得起來
   - AI 報價助理與 PDF **清楚地說「主機離線」**，不是白畫面或 500
4. master 開回來：桌機 SPA 一切如常，`test_public_surface` 仍綠（官網曝露面沒被動到）

---

## 9. 待 owner 拍板

1. **網址**：`office.originsun-studio.com`？還是別的命名？
   （LAN 內要不要另開一個 `192.168.1.132:8091` 的直達 port，當網路／CF 故障時的退路？）
2. ~~**PDF**：master 關機時要不要能下載報價單 PDF？~~
   ✅ 已解（2026-09-10）：owner 提「寄出改成生成報價單」→ 產與送拆開，NAS 不用裝 Playwright。
   剩下的是**「寄出」與「生成」怎麼擺**：目前實作是 owner 選的 B（兩顆鈕，先生成再寄出），
   但寄出時若還沒生成／已過期仍會自動補生成一次 —— 漏按不該就沒有檔。
3. **範圍**：只搬「手機版 ＋ `my.html` ＋ 客戶連結」（本規劃），還是連桌機 CRM 也要一份唯讀降級殼（P3）？
4. **`settings.json` 搬家**：照 `public_access` 那樣搬進共用 DB（乾淨、一次到位），
   還是先用「`/publish` 把 settings.json 也 scp 過去」的簡便法？
   （簡便法的問題：master 上改設定不會立刻生效在 NAS，要等下次發版 —— 而
   `invoice_fee_rates` 錯了是靜默的金額錯誤）
5. **時機**：這批會動 `publish_update.py` 與 nginx，屬於「上班時間不要一版一版部署」
   （`feedback_deploy_timing`）的範圍 —— 挑一個下班後的窗口？

---

## 10. 這件事的邊界

不在本規劃內：備份／轉檔／報表／機隊管理／空拍／TTS（都吃本機硬體與記憶卡，
本來就該綁 master）、官網（已經 24/7）、公布欄「問 Claude」（同樣吃 `claude` CLI）。
