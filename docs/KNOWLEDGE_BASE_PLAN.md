# 私帳「知識庫」（書架＋討論＋結論）—— 規劃正本

> 2026-09-18 owner 拍板：「做一個上傳介面（整合在私帳）讓我上傳 PDF 作為我的知識庫；
> 理想的建立方式是你讀過、我們一起討論出一些結論」；討論管道選「私帳裡內建對話」。
> 參考 [book-to-skill](https://github.com/virgiliojr94/book-to-skill) 的產物結構（心智模型／每章／glossary／patterns／cheatsheet），
> 但**產物住在 `D:\Originsun-Advisor\books\`、由顧問 agent（`~/.claude/agents/wealth-advisor.md`）調用**，不裝成全域 skill。

## 1. 顧問怎麼用（這是設計的終點，先寫）

一本書進來變成三層，顧問讀的順序反過來：

| 層 | 檔 | 誰寫 | 顧問怎麼用 |
|---|---|---|---|
| 結論 | `結論.md` | owner＋Claude 在對話裡「存成結論」／「整理結論」；可手改 | **每次必讀、優先引用** —— 這是 owner 認同過的原則 |
| 筆記 | `筆記.md` | owner 自己寫 | 每次讀，排在結論之後、書之前 |
| 骨架 | `SKILL.md`＋`cheatsheet.md`＋`glossary.md`＋`patterns.md` | 編譯自動產 | 每次讀（合計 ≤ 8K tokens），拿規則對照 owner 的數字 |
| 章節 | `chapters/chNN-<slug>.md` | 編譯自動產 | **問題碰到才開** |

顧問回答順序：數字 → owner 的結論 → 書的規則；打架時兩邊都講、說偏向哪邊。

## 2. 儲存（檔案，不進 DB、不進 git）

- 根目錄：settings `finance.knowledge_root`，預設 `D:\Originsun-Advisor\books`（進 `api_system._SECRET_SUBKEYS["finance"]`，匿名 `/api/settings/load` 不回）。
- **只掛 master**（`main.py`），不掛 `main_office.py`：D:\ 只有 master 看得到，而且編譯／討論都要 claude CLI（master 才有）。
  手機在 NAS 那條路打到 404 → 畫面寫「書架需要主控主機在線」。
- 一本書一個資料夾 `books/<id>/`，`id`＝16 hex（URL 用；標題在 meta）。**路徑一律 `id` 先過 `^[0-9a-f]{16}$` 再拼**。

```
books/<id>/
  source.pdf          原檔（原檔名記在 meta.source_name）
  full_text.txt       pymupdf 抽出的全文，每頁前加 `[[p.N]]`（編譯用；討論不讀它）
  meta.json           {id, title, author, source_name, pages, chars, uploaded_at,
                       status: uploaded|compiling|compiled|failed, error, compiled_at, chapters: n, model}
  SKILL.md            骨架（≤4K tokens）：核心心智模型／決策規則（「當 X 就 Y，因為 Z」）／章節索引表／主題索引／範圍與限制
  chapters/chNN-<slug>.md   每章：核心概念／框架（保留作者原用語，譯本附原文）／心智模型／反模式／實例／重點／關聯章節
  glossary.md  patterns.md  cheatsheet.md
  chat.json           討論紀錄 [{role: user|ai, text, at}]
  結論.md             「## <日期>」分段追加；可整檔 PUT 覆寫
  筆記.md             owner 的筆記；可整檔 PUT 覆寫
```

`books/README.md` 改寫成描述這個結構（舊的「把 PDF 丟這裡」那份作廢）。

## 3. 後端（`routers/api_knowledge.py`，prefix `/api/v1/finance/knowledge`）

守衛：每支 `_guard(request)`＝`require_entity(request, "mine", level="full")`（照 `api_fortress._guard`，entity 寫死 mine）。

| 方法 | 路徑 | 做什麼 |
|---|---|---|
| GET | `` | 書架：`[{id,title,author,status,pages,chapters,uploaded_at,compiled_at,has_conclusion,has_notes,stage}]` |
| POST | `` | multipart 上傳（`file`、可選 `title`）：看檔頭 `%PDF`（不看副檔名）、上限 300MB、pymupdf 抽文字＋頁數 → 建資料夾＋meta；回 meta |
| GET | `/{id}` | meta＋stage＋`skill`（SKILL.md）＋`cheatsheet`＋`chapters:[{n,title,file}]`＋`conclusion`＋`notes`（不含 chat） |
| PUT | `/{id}` | `{title?, author?}` |
| DELETE | `/{id}` | 整個資料夾 |
| POST | `/{id}/compile` | 背景編譯（503 沒 claude CLI；409 已在跑；`{model?}` 走 `quote_chat.pick_model`，預設 settings `ai.models.knowledge` 或 `opus`） |
| GET | `/{id}/chapters/{n}` | 該章 md |
| POST | `/{id}/chat` | `{text, model?}` → 追加 user 那則、`fire` 背景叫 claude；503 沒 CLI |
| GET | `/{id}/chat` | `{chat, partial, stage}`（前端 1 秒輪詢；同報價助理） |
| POST | `/{id}/conclusions` | `{text}` → 追加到 `結論.md`（`## YYYY-MM-DD HH:MM` 一段） |
| POST | `/{id}/conclude` | 背景叫 claude 把整段討論收成 3–7 條結論 → 追加 `結論.md`；回 `{status:"asking"}`，前端輪詢 GET `/{id}` 看 conclusion 變了 |
| PUT | `/{id}/conclusion` | 整檔覆寫 `結論.md` |
| PUT | `/{id}/notes` | 整檔覆寫 `筆記.md` |

### 3.1 純規則 `core/knowledge_logic.py`（無 I/O，有單元測試）

- `is_valid_id`、`new_id`、`slugify_chapter`（章名 → 檔名 slug，中文保留、去掉路徑字元）。
- `page_index(full_text)` → `[(頁碼, 該頁前 80 字)]`（給結構 pass）。
- `split_chapters(full_text, boundaries)`／`chunk_text(text, max_chars)`（章太長切段）。
- `structure_prompt(index_rows, first_chars)`、`chapter_prompt(book_meta, ch, text, part_i, part_n)`、
  `support_prompt(book_meta, chapter_mds)`、`chat_prompt(...)`、`conclude_prompt(...)`。
  全部**繁體中文**、保留作者原用語（譯本附原文）、不推薦金融商品。
- `parse_structure(text)` → 章清單（容錯：抓第一個 JSON 陣列）。
- `split_files(text)`：`===== FILE: <name> =====` 分段 → `{name: content}`，**name 只准白名單**（`SKILL.md`／`glossary.md`／`patterns.md`／`cheatsheet.md`）。
- `append_section(existing, text, when)`（結論追加格式）。
- `chat_message(role, text, at)`、`recent_chat(chat, n)`。

### 3.2 服務 `services/knowledge_service.py`（I/O）

- `root()`、`book_dir(id)`、`read_meta`／`write_meta`、`save_upload(bytes, name, title)`（pymupdf 抽文字：`import fitz`；每頁 `[[p.N]]`）。
- `list_books()`、`book_detail(id)`、`delete_book(id)`。
- `compile_book(id, model)`：三個 pass，狀態寫進模組層 `_stage[id]`＋meta.status：
  1. 結構：`page_index` 全書 ＋ 前 8,000 字 → claude → 章清單（頁界）。
  2. 每章：章文（>60K 字切段各產再合併）→ `chapters/chNN-<slug>.md`。進度 `第 3/12 章`。
  3. 骨架：所有章 md → 一次產 SKILL.md／glossary／patterns／cheatsheet（`split_files`）。
  自己的閘 `_KNOWLEDGE_GATE = asyncio.Semaphore(1)`；每次 claude 呼叫逾時 20 分鐘；失敗寫 `meta.status="failed"`＋`error`，已產的章保留（重編從缺的補）。
- `run_chat(id, model)`：提示＝角色框架＋SKILL.md＋cheatsheet＋章節索引（相對路徑）＋`筆記.md`＋`結論.md`＋顧問快照
  （`D:\Originsun-Advisor\latest.json` 有就帶 `report.basis_date`／淨值／可動用現金／可撐月數四個數，沒有就略）＋最近 30 則對話＋這則。
  `cwd`＝這本書的資料夾、`--allowedTools Read`（讓它按需 Read `chapters/...`）、`--permission-mode plan`。
  串流 partial 同 `routers/crm/quotes._call_claude_stream`（**抄一份到 `services/knowledge_claude.py`**，不改 quotes 那支：它綁著 `_chat_partial[quotation_id]`）。
  回覆是純 markdown 文字（不是 JSON）。
- `run_conclude(id, model)`：整段 chat＋既有結論 → 3–7 條、每條一句可執行的原則、標明出自哪章 → `append_section`。

### 3.3 不要動的地方（先寫進來）

- 任何拼路徑前 `is_valid_id`；`split_files` 的檔名白名單；章檔名由我們產、不信 claude 回的路徑。
- 編譯與討論**都不動 DB**；owner 的財務數字只讀 `latest.json` 那四個欄位，不把整份塞進提示。
- `_KNOWLEDGE_GATE` 跟 `_QUOTE_CHAT_GATE`、seo 的閘各自獨立（編譯一本 10–20 分鐘，共用會把報價助理卡死）。
- 版面文字不用 emoji（`feedback_ui_no_emoji`；分頁 nav 那顆 `📚 知識庫` 跟 `🏰 堡壘` 同級，可以）。

## 4. 前端

### 4.1 桌機（私帳＝財務分頁 mine 視角）

- `frontend/tabs/finance/finance.html`：nav 加 `<button class="finance-nav-btn fin-nav-mine-ok fin-nav-mine-only" data-subview="knowledge">📚 知識庫</button>`（放堡壘旁）。
- `frontend/tabs/finance/subviews/knowledge.js`（`export default function(container, { isCurrent })`，同其他 subview）：
  - 書架：卡片（書名／作者／狀態 pill：未編譯・編譯中(第 3/12 章)・已編譯・失敗／頁數／有結論）＋上傳區（拖放 PDF 或選檔；`accept` 在 JS 設 `.pdf,application/pdf`）。
  - 書頁：標題列（改名、刪除、「讀這本書」＝compile，編譯中顯示 stage）；分頁 **討論／結論／筆記／骨架／章節**。
  - 討論：泡泡列＋輸入框＋送出；送出後 1 秒輪詢 GET chat，`partial` 填「處理中」泡泡（可用 `js/shared/quote-wait.js` 的點點）；
    每則 AI 泡泡右下「存成結論」（選取文字優先，否則整則）；工具列「整理結論」；「跟顧問討論這本書怎麼用在我身上」的提示字放輸入框 placeholder。
  - 結論／筆記：`<textarea>` 整檔編輯＋儲存（PUT）；顯示用 md 渲染。
  - 骨架：SKILL.md＋cheatsheet 渲染；章節：清單 → 點開該章。
  - md 渲染：新 `frontend/js/shared/md-lite.js`（零 import 葉節點；標題／粗斜體／清單／表格／程式碼／連結，**先 esc 再套**），桌機手機共用。
  - 打 API 用 `fin-utils.js` 的 `finFetch`（FormData 上傳要看它怎麼處理 body；不行就 `authFetch`＋`bearerHeader`）。
  - 404／503 → 「書架需要主控主機在線」。

### 4.2 手機（`frontend/m/ledger.html`）

- `m/ledger.js`：`HIDDEN_ROUTES` 加 `knowledge: '知識庫'`，`HIDDEN_PARENT` 指 overview，`VIEWS` 加 `knowledgeView`。
- 總覽頁（`m/views/ledger-overview.js`）加一張「知識庫」入口卡（照堡壘卡的 `data-go` 做法）。
- `m/views/ledger-knowledge.js`：書架（上傳走 `<input type=file>`＋`mfetch` FormData）／書頁（討論、結論、筆記；骨架與章節唯讀）。
  `mfetch` 對 FormData 已支援（shell.js:110）。

## 5. 顧問端（不在 repo；owner 拍板整個功能時一併改）

- `~/.claude/agents/wealth-advisor.md` 第 4 條：改成「Glob `books/*/meta.json`；每本先讀 `結論.md`、`筆記.md`，再讀 `SKILL.md`＋`cheatsheet.md`；
  `chapters/` 只在問題碰到時開；引用時寫書名＋章」。`~/.claude/skills/advisor/SKILL.md` 第 2 步的「Glob **/* 全讀」同步改。
- `D:\Originsun-Advisor\books\README.md` 改寫。

## 6. 測試（`tests/unit/test_knowledge_base.py`）

- 純規則：id 驗證（拒 `..`、`/`、大寫、長度不對）；`split_files` 白名單外的檔名丟掉；`chunk_text` 邊界；`append_section` 格式；`parse_structure` 容錯；提示裡不出現 `internal_cost`／`jwt_secret` 之類字串（沒東西可漏，但 chat_prompt 只帶四個數）。
- 路由：守衛＝`require_entity(..., "mine", level="full")`（`_srcscan` 掃每支端點都經 `_guard`）；上傳非 PDF 檔頭 → 422；`/{id}` 非法 id → 404；`main_office._ROUTER_MODULES` **不含** `api_knowledge`；`main._ROUTER_MODULES` 含。
- 前端：`test_js_parses` 自動涵蓋新檔；`md-lite` 對 `<script>` 一定 esc（node 跑純函式，輸出只印布林）。

## 7. 不做的事

- 不做「安裝成全域 skill」、不 publish、不 fold-in（第二版再說）。
- 不做 EPUB／DOCX（只收 PDF；副檔名不看、看檔頭）。
- 不推版：今天工作日，建在 dev 8001，owner 看過再說（`feedback_deploy_timing`）。

## 8. 讀者／學習者視角（owner 2026-09-18：「用一個讀者學習者的角度來規劃」）

前面是「顧問的資料庫」；這節把主體換成**讀的人**。一本書在他手上有四個階段，知識庫每個階段給一樣東西：

| 階段 | 他在做什麼 | 給什麼 | 版本 |
|---|---|---|---|
| 讀之前 | 決定要不要讀、帶著什麼問題 | `meta.intent`（為什麼讀）＋`meta.questions[]`（帶著的問題，讀完回頭對答案）；cheatsheet＝10 分鐘版先看 | v1.5 |
| 讀的時候 | 劃線、有想法、卡住 | 骨架伴讀；討論框可選「只針對第 N 章」（後端把該章文塞進提示）；劃線本（貼原文＋想法＋章） | 章節討論 v1.5；劃線本 v2 |
| 讀完 | 消化、變成自己的 | 結論用**自己的話**寫（AI 提、他改——「存成結論」先開一格可改再存）；`行動.md`（這本書對我的三個行動，可勾完成）；`meta.open_questions[]`（還沒解的） | v1.5 |
| 之後 | 忘、要用時想不起來 | `quiz.md`（編譯多產：每章 2–3 題＋答案＋出自哪章）＋「考我」（記得／不記得；不記得的下次先出，狀態存 `quiz_state.json`）；私帳總覽「今天的一條」卡（隨機抽結論／行動）；跨書主題索引 | 考我、今天的一條 v1.5；跨書索引 v2 |

書架多 `meta.reading: want|reading|done`（想讀／在讀／讀完）。

顧問（`/advisor`）是第 4 階段的其中一個消費者：讀 `結論.md`、`行動.md`（對照做了沒）、`筆記.md`，再讀骨架。

### v1.5 端點增量（v1 兩個代理回報後才動）
- `PUT /{id}`：多收 `intent`、`questions[]`、`open_questions[]`、`reading`。
- `POST /{id}/chat`：多收 `chapter?: int` → 提示前面加那章全文。
- `GET/PUT /{id}/actions`：`行動.md`（每行 `- [ ] …`／`- [x] …`；前端勾選＝改那一行）。
- `GET /{id}/quiz`、`POST /{id}/quiz/{n}` `{remembered: bool}`：出題順序＝不記得的先、其次最久沒考的。
- `GET /api/v1/finance/knowledge/today`：隨機一條（結論或行動），總覽卡用。

### 8.1 劃線本（owner 2026-09-18：「我手機截圖，你幫我截取文字」）→ v1.5

- 手機書頁「貼劃線」：拍照／截圖 → 走全站貼圖層（`routers/api_paste.py`，同報價助理的截圖路）→ 後端叫 claude（`--allowedTools Read` 讀那張 WebP／高解析度那份）回 `{text, chapter_guess}`
  → 畫面先顯示認出的字讓他改、加一行「我的想法」、選第幾章（預設 claude 猜的）→ 存 `劃線.md`（`## <日期> · 第 N 章` ＋ 引文 ＋ 想法）。
- 端點：`POST /{id}/highlights/ocr` `{image: "paste:<32hex>.webp"}`（背景、輪詢 `GET /{id}/highlights/ocr`）、`GET/PUT /{id}/highlights`（整檔）、`POST /{id}/highlights` `{text, thought, chapter}`（追加）。
- 認完字圖就刪（同報價助理「做完自動刪圖」：書頁截圖沒有保留的理由）。
- 討論提示多帶 `劃線.md`（他劃的比骨架更代表他在意什麼）；顧問也讀它，排在筆記之後。

## 9. 定期收錄這本書的網路資料（owner 2026-09-18：「也可以定期收錄整理這本書相關的網路資料」；同日追加：「定期依照我書的內容主題幫我做研究助理，持續把一些網路上的研究——不限制中文，如果是英文或其他語言也可以，附上出處、翻譯與摘要給我」）→ v1.5 後段

### 9.1 它是第四層，不是骨架的一部分
骨架（SKILL／章節）是**作者說了什麼**；收錄的是**別人怎麼用、作者後來又說了什麼、跟他處境相關的新資料**。
兩者不混：收錄不 fold-in 進 SKILL.md（會稀釋作者框架），住自己的層：

```
books/<id>/
  延伸.md          目前的「延伸索引」：每則一行 —— 日期｜中譯標題（連結）｜原文標題與語言｜出處｜一句摘要｜關聯章｜對他的意義｜他的評分（有用／沒用／未評）
                   （非中文的那幾則，中譯標題在前、原文附在後：顧問與討論提示讀的是中文，要回原文時對得回去）
  收錄/YYYY-MM-DD.md  每次跑的原始整理（找到什麼、為什麼收、為什麼丟）；只追加
```
讀的順序（討論提示與顧問）：結論 → 劃線 → 筆記 → 骨架 → **延伸（只帶「有用」與「未評」的最近 20 則）** → 章節按需。

### 9.2 兩條進料路
1. **手動「收錄這篇」**（最有價值、先做）：書頁貼一個網址 → 後端 claude `WebFetch` 那頁 → 回 `{title, summary, chapter_guess, why_it_matters}` → 他改一改 → 進 `延伸.md`。手機桌機都有。
2. **定期自動**：每本書 `meta.watch: true` 的（預設 **false**）＋全域 `finance.knowledge_watch.enabled`（預設 **false**，同 `intel.enabled`／`social.enabled` 的規矩：owner 手動開，防 claude 額度）。
   一週一次（settings `finance.knowledge_watch.weekday` 預設週日、`hour` 預設 21）：對每本 watch 的書叫一次 claude（`--allowedTools WebSearch,WebFetch`，cwd temp，plan mode）：
   「這本書（書名／作者／骨架的核心框架 5 條／他的結論）最近 30 天有哪些值得看的：**同行評審論文、工作報告、官方統計與研究機構報告優先**，其次是作者新文章／訪談／podcast 文字稿、對這本書框架的實際應用或批評、跟他情境（台灣、影像製作公司負責人、私帳）相關的討論。
   **語言不限**（繁中、英文、日文、其他都可以；同一個主題用不同語言各搜一輪，好東西常常只有英文有）。回 JSON 陣列，每則：
   `{url, title_original, title_zh, lang, source, published, summary_zh, why_it_matters, chapter_guess}`
   —— `title_original` 保留原文標題（要對得回原文），`title_zh` 是中譯，`summary_zh` 是**繁體中文**三到五句的摘要，
   `lang` 標原文語言。英文以外的語言一律照翻，不要只給原文。
   **已收錄過的網址**（給清單）不要再回；找不到就回空陣列，不要湊。」
   → 去重（URL 正規化＋標題近似）→ 追加 `收錄/<日期>.md`＋`延伸.md`（評分＝未評）。
   一次最多 8 則；一本一週一次；全部走 `_KNOWLEDGE_GATE`（跟編譯排隊，不搶報價助理）。

### 9.3 排程與機器
- 掛 `core/scheduler.py` 的 `_run_daily(task_key="knowledge_watch", hour_key=..., body)`（**只 master**、DB 不用但守衛沿用）；body 內判「今天是設定的那一天且這本 `meta.watch_last` 不是本週」才跑。
- `main_office.py` 不掛任何東西（規則：office 不長排程）；手機端看得到延伸、評分、貼網址收錄，都是打 master 的端點。
- 沒 claude CLI／不是 master → 不跑不記，下週再看（同 seo_runner 的 gate）。

### 9.4 學習者那一面
- 書頁多一個分頁「延伸」：每則有 連結／摘要／關聯章／「有用」「沒用」兩顆（評分回寫 `延伸.md`；「沒用」的下次提示裡列成負面樣本，讓收錄越來越準）。
- 私帳總覽「今天的一條」偶爾抽一則未評的延伸（「這本書最近有人這樣用：…」），看完順手評。
- 週日跑完若有新收錄，書架卡片標「新 3」，**不推播**（owner 一貫：自己的事不用通知）。

### 9.5 端點
- `POST /{id}/extend` `{url}` → 背景 fetch＋整理；`GET /{id}/extend` → `{items:[...], running}`；`PUT /{id}/extend/{n}` `{rating: useful|useless|""|, chapter?, note?}`。
- `POST /{id}/watch` `{on: bool}`；`POST /{id}/watch/run`（現在跑一次，不管星期幾）。
- 設定：`finance.knowledge_watch.{enabled, weekday, hour}`（桌機知識庫分頁上方一行開關；不進 settings modal）。

### 9.6 不做的事／風險
- 網頁內容是**不可信輸入**（提示注入）：收錄那支只准 WebSearch／WebFetch，不給 Read／Bash，cwd 在 temp；回來的東西只當資料寫檔，不執行、不進任何設定。
- 不抓 YouTube 逐字稿、不抓付費牆後的內容；podcast 只收有文字稿的頁面。
- 不做 RSS 訂閱管理（那是 `intel_runner` 的事；書的延伸靠搜尋就夠，來源會變）。
- 額度：一本一週一次、一次 ≤ 8 則；watch 預設關、總開關預設關。
- **翻譯只翻標題與摘要，不轉貼原文全文**（同 intel_runner 的版權規矩：只存標題＋摘要＋原文連結）。
  要讀全文一律點出處連結出去，系統不做全文鏡像。
- ✅ **2026-09-18 已實測**：`claude --print --permission-mode plan --tools WebSearch --allowedTools WebSearch` 在非互動、
  stdin 餵提示的情況下可以真的搜到網路並回結果（exit 0，回了真實網址與語言）。所以 §9.2 不需要另外接搜尋 API；
  WebFetch 尚未同樣實測，做的時候順手驗一次。

## 10. 「書慢慢長成我的知識庫」（owner 2026-09-18）→ v2 主軸

一本書是**原料**，知識庫是**他的**：跨書、會隨新書修訂、用他的話寫。書架之上再長一層：

```
D:\Originsun-Advisor\
  books/<id>/…            一本書一個資料夾（§2；原料：骨架／章節／結論／劃線／延伸）
  知識庫/                 跨書、屬於他的
    原則.md               我的原則：每條一句（他的話）＋來源（書＋章）＋**修訂歷史**（哪本書、哪次討論把它改了；被反駁但他堅持的也記）
    主題/<主題>.md        主題卡（緊急預備金、集中度、時間與自由、借錢…）：我的立場一句 → 各書怎麼說（含互相打架的）→ 我的結論 → 相關行動 → 相關延伸
    問題.md               還沒解的問題（各書帶進來的；哪本書後來回答了就標上）
    行動.md               跨書行動清單（做了／沒做；顧問對照做了沒）
    地圖.md               主題 ↔ 書 ↔ 章 ↔ 原則 的索引（自動維護，給 AI 找路用）
```

### 10.1 怎麼「長」
- **觸發**：一本書編譯完、或存了一條結論、或整理結論之後 → 「併入知識庫」（一顆鈕；也可設自動）：claude 讀新材料＋現有 `原則.md`／相關主題卡 → 回三種東西：
  1. 新原則（草稿，**他按過才算**）；2. 既有原則的修訂建議（「這本書反駁了你從 A 書得出的第 3 條，原因是…」）；3. 主題卡更新（把這本書的說法加進「各書怎麼說」）。
- **衝突是主菜**：新書跟他既有原則打架時，不自動改，進討論框問他：「留原則、改原則、還是兩者都對但適用情境不同？」——他的回答寫進修訂歷史。知識庫長大的方式就是這些被逼著想清楚的時刻。
- **用他的話**：原則一律先讓他改字再存（同「存成結論」那格）；AI 提的字是草稿。
- 主題不預設清單，從結論裡長出來（第一本書 3–5 張，之後合併／拆分由他決定，AI 只建議）。

### 10.2 誰讀什麼
- 顧問（`/advisor`）：`原則.md` → 跟問題相關的主題卡（`地圖.md` 找）→ `行動.md`（對照做了沒）→ 各書 `結論.md` 當佐證 → 骨架／章節按需。書只是來源，回答引用「你的原則第 N 條（出自 X 書第 M 章）」。
- 書頁討論：提示多帶跟這本書主題相關的主題卡（讓 AI 在讀新書時知道他已經相信什麼）。
- 學習者首頁（知識庫分頁的第一眼，書架退到第二區）：我的原則（幾條、最近修訂）／主題卡格／未解問題／行動清單／「今天的一條」／全文搜尋（原則、主題、結論、劃線、延伸）。

### 10.3 端點（`/api/v1/finance/knowledge/kb`）
- `GET /kb` 總覽；`GET/PUT /kb/principles`、`/kb/topics/{name}`、`/kb/questions`、`/kb/actions`（整檔）；`POST /kb/fold` `{book_id}`（背景併入 → 回草稿：新原則／修訂建議／主題更新）；`POST /kb/principles` `{text, source}`（他按過的才進）；`GET /kb/search?q=`。
- 全部檔案在 `知識庫/`，不進 DB、不進 git；`地圖.md` 由後端維護（每次寫入重算）。

### 10.4 順序
v1（書架＋討論＋結論）→ v1.5（§8：讀之前／章節討論／行動／考我／劃線／延伸收錄）→ **v2＝這節**。
第一本書編完、有了幾條結論之後再動 v2 最好：主題要從真的結論長，不是先畫格子。

## 11. 改成獨立網頁應用（owner 2026-09-18：「不要綁在私帳裡，之後會拿知識庫做更多衍生使用，未必跟財務有關」）

v1 建好當天拆出來（還沒 commit，最便宜）。**同一台伺服器上的獨立頁**（同 `/leave.html`／`/calendar.html` 的做法），不是另一個專案。

| 項目 | v1（綁私帳） | 改成 |
|---|---|---|
| 頁 | 財務分頁 subview ＋ 手機 `m/ledger` 隱藏路由 | **`frontend/knowledge.html`** 一頁（殼抄 `calendar.html`：登入卡／noperm／app-view，白底）＋ **`frontend/js/knowledge/`**（ES module；桌機手機同一份、響應式） |
| 鑰匙 | `finance_mine` | 新模組鍵 **`knowledge`**（ALL_MODULES 尾端；後端 `MODULE_LABELS`／前端 `user-mgmt.MODULE_LABELS`／`tab-config` 群組 同步；守衛 `check_admin_or_module(request, "knowledge")`） |
| API | `/api/v1/finance/knowledge` | **`/api/v1/knowledge`**（檔名不變 `routers/api_knowledge.py`） |
| 存放 | `finance.knowledge_root`＝`D:\Originsun-Advisor\books` | **`knowledge.root`**＝`D:\Originsun-Knowledge\books`（`_SECRET_SUBKEYS["knowledge"]=("root",)`；`finance` 那條拿掉） |
| 標籤 | 無 | `meta.tags: []`（自由字串；PUT 可改；書架可篩）。**顧問只讀含 `財務` 標籤的書**；討論提示只在含 `財務` 時帶四個數＋財務顧問角色，其他標籤用一般「讀過這本書的討論夥伴」角色 |
| 提示 | 「給財務顧問讀」「當客戶問…」 | 中性：「給之後每次討論／顧問讀的對照卡」，決策規則用「當你…就…因為…」 |
| 私帳 | 分頁本體 | 只留一顆「知識庫 ↗」（桌機 nav、手機總覽卡）開 `/knowledge.html` |
| 顧問端 | `D:\Originsun-Advisor\books` | 改讀 `D:\Originsun-Knowledge\books`，只挑 `meta.tags` 含 `財務`；`D:\Originsun-Advisor\books\README.md` 留一行指路 |
| office-api | 不掛 | 仍不掛（D:\ 與 claude 都在 master） |

其餘（§2 資料夾結構、§3 端點形狀、§8–§10）不變。`reset_compiled` 連 `_support_raw.txt` 一起清。
