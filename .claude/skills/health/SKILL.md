---
name: health
description: 全 repo 健康檢查：靜態閘門（ruff／eslint／pyright／前端漏 import）、依賴與安全通報、倉庫衛生（孤兒檔／該 gitignore 的／大檔）、測試健康（最慢／跳過／覆蓋率低且最近常改的模組補特徵測試）、結構債、文件同步。以「回報」為主，只自動修一份白名單內的安全項目，每階段全套測試並 commit，報告落地到 docs/health/ 可跟上次比較。當使用者說 /health、「健康檢查」、「體檢」、「全 repo 檢查一遍」、「看看專案有什麼債」、「剩下的額度拿來做點有用的事」時使用，即使沒有明說也要用。跟 /polish 的分工：polish 管「這次的 diff」並修到可合併；health 管「整個 repo」並以盤點為主。
---

# /health

目標：對整個 repo 做一次可重複、可比較的體檢，產出一份落地報告，**只修白名單內的安全項目**，其餘全部回報給使用者決定。

## 核心原則

1. **回報為主，修為輔。** 白名單（見「可自動修」）以外的任何東西一律只寫進報告。不升級依賴、不刪檔、不動 `.gitignore`、不改公開介面。
2. **每階段：改 → 跑全套測試 → commit。** 只有白名單修法會產生 commit；純回報階段沒有 commit。
3. **硬上限，不是跑到沒東西改。** 撞上限就停，剩下的交給使用者。
4. **測試失敗不修測試。** 回退該階段並回報。
5. **不碰生產。** 不綁 port 8000、不跑 `/publish`／`publish_update.py`、不跑 `tests/integration`／`tests/e2e`（fixture 會拉真伺服器）、不在 `.venv` 外裝東西。
6. **報告落地。** 寫到 `docs/health/<YYYY-MM-DD>.md`，並跟上一份比關鍵數字（趨勢比單次數字有用）。
7. **修 bug 前先過 `/assess`。** 階段一的白名單修法也要先有卡（短卡即可），過閘門才修。

## 前置檢查（不通過就停止）

1. 必須在 `E:\Dev\Originsun-Media-Guard` 或其 worktree；`database_url` 要是 `*_dev`。不是就停。
2. 測試指令：讀 CLAUDE.md 的 `polish.test`（目前 `.venv\Scripts\python.exe -m pytest tests/unit -q`）。主分支：`polish.base`。
3. 有未 commit 變更 → 先 commit `health: baseline`（跟 /polish 同慣例；`--dry` 模式不 commit，改為把未 commit 清單寫進報告）。
4. 跑一次全套測試建立基準。基準失敗 → 回報並停止，health 不修既有失敗。
5. 工具盤點：`ruff`（venv 已有）、`pyright`、`pytest-cov`、`pip-audit`。**缺的先問使用者一次**「要不要 `pip install` 進 .venv」，拒絕就跳過該子項並在報告註明「未執行：缺 X」。不要默默裝、也不要默默跳。
6. 找上一份報告：`ls docs/health/*.md | sort | tail -1`，沒有就是第一次（報告上「上次」欄留空）。

## 階段一：靜態閘門

依序跑，全部只看「真 bug」類，不管風格：

- `ruff check .` — 規則集就是 `ruff.toml` 的 gate（E9/F63/F7/F82/F401/F841），理論上應該是零。非零 → F401/F841 可 `ruff check --fix --select F401,F841`；其餘回報。
- `npm run lint`（= `eslint frontend`）— 同上，應該是零。非零就回報，eslint 這組規則幾乎沒有安全的 --fix。
- `pyright`（若有）— 只統計 `error` 級，依 rule 分組計數；**只回報 `reportUndefinedVariable`、`reportAttributeAccessIssue`、`reportCallIssue` 的前 20 筆**，其餘一行總數。pyright 在這個 repo 沒設過 gate，噪音大，不要嘗試清零。
- **前端漏 import 掃描（本 repo 特有、最高價值）**：`eslint.config.mjs` 刻意關 `no-undef`，`test_js_parses` 只驗 parse，所以前端少一個 import 是零告警（CLAUDE.md「不要動的地方」有 2026-09-11 `copyText` 案例）。做法：在 scratchpad 寫一份臨時 flat config（`files: frontend/**/*.js`、`globals: browser + window`、`rules: {"no-undef": "error"}`），跑 `npx eslint -c <臨時config> frontend`，輸出當「清單」看：
  - 裸用 `window._*` 慣例的全域 → 忽略
  - 名字在 `frontend/` 下**唯一一個模組**有 `export` → 可自動補 `import`（白名單）
  - 其他（多個來源、或找不到來源）→ 回報，附檔案:行號
- `preflight.py` — `.venv\Scripts\python.exe preflight.py`，exit 非 0 就回報（代表 `requirements_agent.txt` 的契約破了，OTA 會整包回滾）。

有白名單修法 → 對候選清單執行 `/assess --fix --short`：每項一張短卡（現象／影響範圍／重現＝lint 那一行／判定），
過閘門的才修，**一張卡一個 commit** `health: BUG-N <一句話>`；沒過的進報告「需要人工決定」。
漏 import 這類有行為後果的（不是純 lint）用完整卡，第 4 欄要真的寫一支會失敗的測試。

## 階段二：依賴與安全

**全部只回報**。依賴升級會透過 `requirements_agent.txt` 打到整個生產機隊，不在 health 的權限內。

- `pip list --outdated --format=json` 對照 `requirements_server.txt` 與 `requirements_agent.txt`：列出「有釘版本且落後主版本」的套件。
- `pip-audit`（若有）— 列有 CVE 的套件、對應版本、是否在 `requirements_agent.txt`（在 = 機隊也中）。
- `website/`：`npm audit --omit=dev --json`，只列 high/critical。網路不通就註明跳過。
- requirements 檔漂移：`requirements.txt`／`requirements_server.txt`／`requirements_agent.txt`／`requirements_lock_20260614.txt`／`0225_requirements.txt` 五份，列出「同一套件不同版本」與「只出現在一份裡」的；`0225_requirements.txt` 與 `requirements_lock_*` 這種帶日期的，問使用者還要不要（寫進「需要人工決定」）。

## 階段三：倉庫衛生

**全部只回報**，分三類：候選刪除／候選 gitignore／保留但要註明。不刪、不 mv、不改 `.gitignore`。

- 根目錄非程式檔盤點：`*.bat`／`*.vbs`／`*.ps1`／`*.log`／`*.bak*`／`*.zip`／runtime `*.json`。每個檔給一行：最後修改日、是否被任何 `.py`／`.md`／`.bat` 引用（grep 檔名）、判定類別。
- 被追蹤但符合 ignore 規則的：`git ls-files -i -c --exclude-standard`。非空就是候選移出追蹤（`git rm --cached`），列出。
- 追蹤中的大檔：`git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | sort -k3 -n -r | head -10`。> 5MB 標紅。
- `scripts/` 一次性腳本：`backfill_*`／`import_*`／`seed_*`／`_tmp_*`／`_placeholder_*`，列出最後 commit 日期，超過 60 天沒動的標「候選歸檔到 scripts/archive/」。
- `__pycache__` 或 `.pyc` 有沒有進 git。

## 階段四：測試健康

這是唯一會**主動寫程式碼**的階段，做法沿用 /polish 階段零的「特徵測試」：不判斷對錯，只把現在的行為釘住。

1. `pytest tests/unit -q --durations=15` — 最慢 15 支列出來，> 5 秒的標紅（單元測試不該有）。
2. 統計 `skipped`／`xfail`／`xpass`，逐支列原因（`-rs`）。xpass 代表標記過期，回報。
3. 覆蓋率（若有 pytest-cov）：`pytest tests/unit -q --cov=core --cov=routers --cov=services --cov-report=term-missing:skip-covered --cov-report=json:<scratchpad>/cov.json`。從 JSON 取每檔覆蓋率。
4. 交叉最近變動：`git log --since="30 days ago" --name-only --pretty=format: | sort | uniq -c | sort -rn`。**「30 天內改過 ≥ 3 次」且「覆蓋率 < 40%」** 的檔案 = 補測試候選，依「改動次數 × (1 − 覆蓋率)」排序。
5. 對前 **N 個候選**（預設 3，`--deep` 5）：
   - 只挑公開函式（不以 `_` 開頭）、且是純函式或只依賴可 mock 的 session。
   - 每個模組最多 5 支特徵測試，放 `tests/unit/test_<模組名>_health.py`（跟既有測試檔分開，方便使用者整檔刪）。
   - 掃原始碼的規則測試一律用 `_srcscan.flow_body`，不用 `func_body`（CLAUDE.md 有說明）。
   - 跑全套測試 → 通過 commit `health: tests for <模組>`（一模組一 commit），失敗 → 回退**那個模組**的檔案、記錄、繼續下一個。
6. 沒有 pytest-cov 時：第 4 步改用「模組有沒有任何 `tests/unit/test_<模組名>*.py`」的二元判定，候選 = 最近常改且完全沒測試檔的模組。

## 階段五：結構債

直接執行 `/polish --debt`，把它的輸出原樣併進報告（超長檔案／超長函式／無測試模組／巢狀過深／循環依賴）。不重複實作、不修。

## 階段六：文件同步

**只回報**，除了最後一條。

- `CLAUDE.md` 的 `## 模組職責` 表 vs 實際 `routers/`／`services/`／`core/` 下的 `.py`：列「程式碼有、表裡沒有」的模組（新模組沒登記）與「表裡有、程式碼沒了」的（拆檔／改名後沒更新；順便對照 `ota_manifest.STALE_PATHS` 有沒有登記）。
- `ROADMAP.md` 未勾選項目 vs 最近 30 天 commit 訊息：關鍵字對得上的列出來，問使用者要不要勾。
- `CHANGELOG.md` 最後一條版本 vs `version.json`：落後就回報。
- `docs/*_PLAN.md` 超過 90 天沒動的：列出，問使用者是「已完成該歸檔」還是「還在等」。
- **唯一寫入**：把這次 health 發現的、確定是地雷的事（例如漏 import 的實際案例、xpass 的過期標記）以一行寫進 `CLAUDE.md` 的 `## 不要動的地方`。沒有新地雷就不動。有動 → commit `health: docs`。

## 收尾

1. 全流程 commit 總數上限 **8**（baseline 不算；階段一的卡片 commit 另計，上限 10），超過即停。
2. 報告寫到 `docs/health/<YYYY-MM-DD>.md`（同日第二次跑加 `-2`），commit `health: report <日期>`。`--dry` 模式報告只印到對話、不落地。
3. 報告格式固定：

```
health 報告 <日期>                         上次：<日期或「無」>

基準測試：通過（N 支，M 秒）              上次：N / M
階段一 靜態閘門：ruff X ／ eslint Y ／ pyright Z errors ／ 前端漏 import W ／ preflight OK ／ 寫卡 K 張：修 A、回報 B、需人工 C
階段二 依賴：落後 X 套 ／ CVE Y（機隊中 Z）／ npm high+ W ／ requirements 漂移 V 項
階段三 衛生：候選刪除 X ／ 候選 gitignore Y ／ 大檔 Z ／ 一次性腳本 W
階段四 測試：最慢 X 秒（<檔>）／ skipped Y ／ xpass Z ／ 覆蓋率 <整體%> ／ 補了 N 個模組 M 支測試
階段五 結構債：>2000 行 X ／ >800 行 Y ／ >200 行函式 Z
階段六 文件：模組表缺 X ／ ROADMAP 可勾 Y ／ CHANGELOG 落後 <是/否> ／ 過期 PLAN Z
commits：<hash 與訊息>
停止原因：正常結束 ／ 撞上限 ／ 測試失敗於階段 N ／ 缺工具跳過 <哪些>

需要人工決定（依影響排序）：
1. ...

未執行：
- ...
```

每個「X」在報告正文都要有對應的明細段落（檔案:行號或套件:版本），數字沒有明細等於沒查。

## 可自動修（白名單，其他一律回報）

- `ruff --fix` 限 `F401`、`F841`
- 前端漏 import：**唯一來源**時補 `import`
- 特徵測試新增（階段四）
- `CLAUDE.md` `## 不要動的地方` 追加一行（階段六）
- 報告檔本身

## 參數

- `/health` — 預設（階段四補 3 個模組）
- `/health --dry` — 只回報，不改不 commit、報告不落地
- `/health --only <1-6,逗號分隔>` — 只跑指定階段（前置檢查與收尾照跑）
- `/health --skip <階段>` — 跳過指定階段
- `/health --deep` — 階段四補 5 個模組、每模組最多 8 支；階段一 pyright 明細放寬到 50 筆
- `/health --no-install` — 缺工具直接跳過，不問

## 不要做的事

- 不要升級任何套件，即使只是 patch 版
- 不要刪檔、mv 檔、改 `.gitignore`，即使報告判定是「候選刪除」
- 不要跑 `tests/integration`／`tests/e2e`
- 不要為了讓數字好看去修測試或加 `noqa`／`eslint-disable`
- 不要把 pyright 當 gate 去清零
- 不要跳過 /assess 直接套白名單修法
- 不要在 `--dry` 模式下做任何寫入（包含 baseline commit）
- 不要因為「還有額度」而多跑一輪；一次 `/health` 就是一次體檢
