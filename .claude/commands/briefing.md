Generate a session briefing for the Originsun Media Guard Pro project. This skill collects real-time project state and presents a structured overview so the user can quickly resume work.

## Workflow

### Step 1: Collect real-time info (run ALL in parallel)

1. Read `version.json` to get current version and build date
2. Run `git branch --show-current` to get current branch
3. Run `git log --oneline -10` to get recent commits
4. Run `git status -s` to show uncommitted changes (DO NOT use `-uall`)
5. Run `git diff --stat` to show unstaged change details
6. Run `git diff --cached --stat` to show staged change details

### Step 2: Read Memory files (run ALL in parallel)

Read the following files from the memory directory `C:\Users\originsun\.claude\projects\e--Dev-Originsun-Media-Guard\memory\`:

1. `MEMORY.md` — index of all memories
2. `project_overall_progress.md` — completed phases and next steps
3. `project_phase_j_crm.md` — CRM module details
4. The **most recent** `project_session_*.md` file (sort by date in filename, pick the latest)
5. `feedback_post_implementation.md` — post-implementation rules
6. `feedback_new_tab_rbac.md` — new tab RBAC checklist

### Step 3: Read ROADMAP timeline (targeted read)

Read `ROADMAP.md` with offset/limit to extract:
- The "完整時序" section (the ASCII timeline near the bottom)
- The current main-line Phase sections (2026-07 起主線 = **Phase N Work OS**；其餘待做
  Phase 以 ROADMAP.md 實際內容為準，不要照舊清單背)

### Step 3.5: Read the strategy plan

Read `docs/STRATEGY_2026H2.md` (短中長程戰略計畫，2026-07-08 Fable 交接前定稿)。
briefing 輸出必須包含它的濃縮版（見下方「戰略方向」段），讓每個新 session 都帶著
同一份大方向開工。

### Step 4: Output the briefing

Present the following structured report. Use the EXACT format below, filling in real data from Steps 1-3. Do NOT fabricate any data — if a file is missing or unreadable, say so.

---

## Originsun Media Guard Pro — Session Briefing

### 基本資訊
| 項目 | 值 |
|------|---|
| 版本 | (from version.json) |
| 分支 | (from git branch) |
| 日期 | (today's date) |

### 最近 Commit
(git log --oneline -10, formatted as a list)

### 未提交變更
(from git status + git diff --stat. If clean, show "工作目錄乾淨")

### ROADMAP 概覽

#### 已完成的 Phase
| Phase | 名稱 | 重點內容 |
|-------|------|---------|
| H | 語音生成 | (summarize from ROADMAP) |
| I | 備份可靠性 | (summarize from ROADMAP) |
| B | PostgreSQL | (summarize from ROADMAP) |
| K | OTA 更新 | (summarize from ROADMAP) |
| A | Auth 權限 | (summarize from ROADMAP) |
| 0 | 程式碼重構 | (summarize from ROADMAP) |
| J 核心 | CRM 系統 | (summarize from ROADMAP) |

#### 目前位置
(version from version.json)

#### 待做 Phase（按優先順序）
(以 ROADMAP.md 實際內容產表 — 目前主線為 Phase N Work OS 各子階段；
其餘待做 Phase 照 ROADMAP 現況列出，欄位：Phase / 名稱 / 優先級 / 狀態 / 關鍵待辦)

#### 殘留低優先項目
(from memory: project_overall_progress.md)

### 戰略方向（from docs/STRATEGY_2026H2.md — 每次 briefing 必附）
- **短程（~2026-09）**: (從文件第 1 節濃縮 3-4 點，含未完成的營運地雷，
  🔴 Google Chat webhook 未設定時必須列出)
- **中程（Q4~2027Q1）**: (從第 2 節濃縮 2-3 點，N0 個人帳號化未拍板時必須點名它是骨牌)
- **長程/商業**: (從第 3 節濃縮 1-2 點：內部護城河為預設；切片產品化需 2-3 家付費驗證)
- **自動化下一梯隊**: (從第 4 節列當前最高 ROI 的 1-2 項)

### 上次 Session 摘要
(from the most recent project_session_*.md — show the key accomplishments and any pending items)

### 開發規則速查
1. **改 .py 必驗證** — `python -m py_compile <file>`，前端改完檢查 HTML 平衡 + onclick 引用
2. **大檔案分段讀** — `api_crm.py`(2900+行) / `app.js`(2200+) / `core_engine.py`(1550+) / `crm-projects-cost.js`(1000+)，超過 500 行強制用 offset+limit
3. **編輯前重讀** — 不信任記憶中的程式碼，一律重新讀取目標區段
4. **搜尋結果要懷疑** — 結果太少就縮小範圍重搜，特別注意 .js + .html 雙引用
5. **實作後流程** — 自測 + 跑 /simplify
6. **新模組 RBAC 3 處同步** — core/auth.py ALL_MODULES / tab-config.js / user-mgmt.js MODULE_LABELS（RBAC v2 無角色層）
7. **超過 5 檔拆子 Agent** — 後端/前端/測試分開，避免上下文崩潰
8. **重構前清垃圾** — dead code 先開 commit 清掉，再做正事

### 你的指令
$ARGUMENTS

---

## Important notes

- This briefing must be CONCISE — do not add extra commentary or suggestions beyond what the format requires.
- If $ARGUMENTS contains a task, acknowledge it at the end and ask if the user wants to proceed after reviewing the briefing.
- Do NOT skip any section. If data is unavailable, explicitly state "(無資料)" rather than omitting the section.
- The ROADMAP table must reflect the CURRENT state from the actual ROADMAP.md file, not from memory (memory may be stale).
- Total output should be under 200 lines. Keep each table row to one line.
