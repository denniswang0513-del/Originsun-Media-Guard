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

Read the following files from the memory directory `C:\Users\XXX\.claude\projects\d--Antigravity-OriginsunTranscode\memory\`:

1. `MEMORY.md` — index of all memories
2. `project_overall_progress.md` — completed phases and next steps
3. `project_phase_j_crm.md` — CRM module details
4. The **most recent** `project_session_*.md` file (sort by date in filename, pick the latest)
5. `feedback_post_implementation.md` — post-implementation rules
6. `feedback_new_tab_rbac.md` — new tab RBAC checklist

### Step 3: Read ROADMAP timeline (targeted read)

Read `ROADMAP.md` with offset/limit to extract:
- The "完整時序" section (the ASCII timeline near the bottom, approximately lines 543-596)
- The "待做 Phase" sections: J-3, J-4, L, G, F-lite, C, D, E, F-full

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
| Phase | 名稱 | 優先級 | 狀態 | 關鍵待辦 |
|-------|------|--------|------|---------|
| J-3 | 備份整合 | 🔴 高 | (status) | (key items from ROADMAP) |
| J-4 | CRM 進階 | 🟡 中 | (status) | (key items from ROADMAP) |
| L | 行動端 RWD | 🟡 中 | (status) | (key items from ROADMAP) |
| G | 多機多專案 | 🔴 高 | (status) | (key items from ROADMAP) |
| F-lite | 基礎監控 | 🟠 高 | (status) | (key items from ROADMAP) |
| C | Webhook | 🟡 中 | (status) | (key items from ROADMAP) |
| D | MCP Server | 🟡 中 | (status) | (key items from ROADMAP) |
| E | 生產部署 | 🟢 低 | (status) | (key items from ROADMAP) |
| F-full | 完整監控 | 🟢 低 | (status) | (key items from ROADMAP) |

#### 殘留低優先項目
(from memory: project_overall_progress.md)

### 上次 Session 摘要
(from the most recent project_session_*.md — show the key accomplishments and any pending items)

### 開發規則速查
1. **改 .py 必驗證** — `python -m py_compile <file>`，前端改完檢查 HTML 平衡 + onclick 引用
2. **大檔案分段讀** — `api_crm.py`(2900+行) / `app.js`(2200+) / `core_engine.py`(1550+) / `crm-projects-cost.js`(1000+)，超過 500 行強制用 offset+limit
3. **編輯前重讀** — 不信任記憶中的程式碼，一律重新讀取目標區段
4. **搜尋結果要懷疑** — 結果太少就縮小範圍重搜，特別注意 .js + .html 雙引用
5. **實作後流程** — 自測 + 跑 /simplify
6. **新 Tab 4 處 RBAC** — user-mgmt.js / auth-state.js / db/session.py / index.html
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
