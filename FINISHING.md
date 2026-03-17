# 收尾流程指令模板 (FINISHING.md)

> 每次完成一個開發階段後，把以下指令貼給 Claude Code 執行。
> 只需要填寫「這次完成的工作」區塊，其餘流程固定不變。

---

## 使用方式

複製以下整段指令，填入工作描述後貼給 Claude Code：

```
請讀取 FINISHING.md 並執行收尾流程。

這次完成的工作：
- 新增的檔案：
- 修改的檔案：
- 刪除的檔案：
- 功能說明：

---

收尾步驟如下，請依序執行：

### Step 1 — 判斷版本號
根據這次改動幅度，自動決定新版本號：
- 只修正 bug、調整樣式、修改文件 → 修訂號 +1（例如 v1.5.0 → v1.5.1）
- 新增功能、新增 API 端點、新增頁籤 → 次版本號 +1（例如 v1.5.0 → v1.6.0）
- 有破壞性變更、架構重構 → 主版本號 +1（例如 v1.5.0 → v2.0.0）
判斷後告訴我新版本號是什麼，以及判斷理由。

### Step 2 — 更新版本號
將新版本號寫入以下兩個地方：
1. `version.json` 的 `version` 欄位與 `build_date`（今天日期，格式 YYYY-MM-DD）
2. `CLAUDE.md` 頂部的 `> **版本**: vX.X.X（YYYY-MM-DD）`

### Step 3 — 更新 CLAUDE.md
1. 讀取現有的 CLAUDE.md
2. 在第 3 節「完整目錄結構」補上新增的檔案，格式與現有條目一致
3. 若有新的 API 端點，更新第 7 節對應的路由表格
4. 若架構有變動，更新第 4 節的說明
5. 頂部版本號已在 Step 2 更新，不需重複

### Step 4 — 更新 CHANGELOG.md
1. 若 CHANGELOG.md 不存在，先建立它（格式見下方）
2. 在檔案最頂部插入新的一筆紀錄，格式如下：

```markdown
## [版本號] - YYYY-MM-DD

### 新增
- 條列新增的檔案與功能

### 修改
- 條列修改的現有檔案與原因

### 刪除
- 條列刪除的檔案（若無則省略此區塊）

### 技術備註
- 條列重要的技術決策或注意事項（若無則省略此區塊）
```

### Step 5 — 更新 ROADMAP.md
1. 讀取 ROADMAP.md
2. 找到這次完成的項目，將 `- [ ]` 改為 `- [x]`
3. 若整個 Phase 已全部完成，在 Phase 標題旁標注 ✅

### Step 6 — Git commit + push
執行以下 git 指令：
1. `git add -A`
2. 根據改動內容產生 commit message，格式如下：
   ```
   <類型>(<範圍>): <中文簡述> / <English summary>

   - 條列主要變更（中文）
   - List key changes (English)

   版本：vX.X.X
   ```
   類型使用：feat / fix / refactor / docs / chore
   範例：`feat(auth): 新增 JWT 身份驗證層 / Add JWT authentication layer`
3. `git push`
若 push 失敗，回報錯誤訊息，不要強制 push。

### Step 7 — 發布到 NAS（publish_update.py）
執行：
```
d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe publish_update.py --version vX.X.X --no-interactive
```
若 publish_update.py 不支援 `--no-interactive` 參數，改用：
```
d:\Antigravity\OriginsunTranscode\.venv\Scripts\python.exe publish_update.py
```
並在互動提示出現時自動填入版本號。
若執行失敗，回報錯誤，不要重試超過一次。

### Step 8 — 確認回報
完成後，請列出：
- 新版本號與判斷理由
- CHANGELOG 新增的內容摘要
- ROADMAP 打勾的項目
- Git commit message 全文
- NAS 發布結果（成功 / 失敗 + 原因）
供我確認是否正確。
```

---

## CHANGELOG.md 初始模板

若 CHANGELOG.md 尚未建立，請用以下格式初始化：

```markdown
# Changelog

所有重要的版本變更都記錄在此文件。
格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/)。

---

<!-- 新的紀錄從這裡往上插入 -->
```

---

## 版本號規則

| 改動類型 | 規則 | 範例 |
|---------|------|------|
| Bug fix、文件、樣式調整 | 修訂號 +1 | v1.5.0 → v1.5.1 |
| 新功能、新 API、新頁籤 | 次版本號 +1 | v1.5.0 → v1.6.0 |
| 破壞性變更、架構重構 | 主版本號 +1 | v1.5.0 → v2.0.0 |

## Git Commit Message 規則

| 類型 | 使用時機 |
|-----|---------|
| `feat` | 新功能 |
| `fix` | 修正 bug |
| `refactor` | 重構（不影響功能） |
| `docs` | 只改文件 |
| `chore` | 雜項（設定、依賴更新） |

## 注意事項

- `settings.json` 不進 git，不需要在 CHANGELOG 紀錄其內容變動
- `FINISHING.md` 本身不需要在 CHANGELOG 紀錄
- NAS 發布失敗不影響 git push，兩者獨立
- 若 git 有未追蹤的敏感檔案（credentials/、*.key），確認 .gitignore 有排除後再 commit
