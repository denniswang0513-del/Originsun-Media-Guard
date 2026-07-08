# 前期製作資料資產規劃 — 場景庫 / 提案庫 / 產業情報（2026-07-08）

> 定位：Work OS Phase N 的「N1-pre」——三個**互相獨立、也不依賴 N0/N1 主幹**的
> 資料資產，可先行。共同哲學：把前期製作過程中「用過就丟」的東西（場勘、提案、
> 產業資訊）變成**可搜尋、可重用、會累積的公司資產**。
>
> 實作慣例全部沿用現有 pattern：CRM 新功能 checklist（models + ALTER migration +
> schemas + router + tabs/ 前端 + RBAC 3 處）、uploads 圖片管線（作品圖模式）、
> 手機 RWD 頁（/expense.html 模式）、AI runner 骨架（`_runner_util` + claude gate +
> kill switch + 連續失敗告警 + dev 庫 enabled=false）。每段獨立 canary。

---

## A. 場景資料庫（Location Library）

**解決什麼**：場勘成果現在散在個人手機和記憶裡。同類需求（咖啡廳、工廠、海邊）
每次重新找、重新勘。場景庫讓「勘過一次 = 永久資產」，還累積踩雷紀錄。

### 資料模型（NAS Postgres `mediaguard`）

- `preprod_locations`：id, name, category（咖啡廳/工廠/辦公室/戶外/官署…下拉+可增）,
  region（縣市）, address, contact_name, contact_phone, permit_required(bool),
  permit_note, fee_note, attributes(JSON：電源/收音/自然光/停車/廁所/可用時段),
  tags(JSON), note, status（可用/黑名單/已消失）, cover_url, created_by, timestamps
- `preprod_location_photos`：location_id, url, caption, sort
  （檔案走 `uploads/locations/{id}/`，複用作品圖 WebP 管線）
- `preprod_location_usages`：location_id, project_id(FK crm_projects), used_date,
  rating(1-5), lesson（心得/踩雷，這欄是資產的靈魂）

### UI 與流程

- 前期製作群組新 tab「🗺️ 場景庫」：卡片牆（封面圖）+ 篩選（分類/縣市/tags/需申請）
  + 詳情面板（照片牆、屬性、**使用履歷**＝哪些專案用過+評分+踩雷）
- **手機場勘頁 `/scout.html`**（killer feature）：場勘現場拍照 → 選分類 → 一鍵入庫，
  回公司再補屬性。沿用 /expense.html 的 RWD + 登入模式
- 專案端連動：CRM 專案詳情可掛「本案場景」→ 自動寫 usages

### RBAC

模組 `preprod_locations`，照 3 處同步（core/auth.py ALL_MODULES / tab-config.js /
user-mgmt.js MODULE_LABELS）。

### 後續可選（不進第一版）

照片 EXIF GPS → 自動帶縣市；claude 看圖自動 tag；地圖視圖。

---

## B. 提案資料庫（Proposal Library）

**解決什麼**：提案（treatment、簡報、參考片單）是公司智財，現在散在雲端硬碟。
更重要的是 **win/loss 學習迴圈**：哪類提案對哪類客戶的成案率高，現在全憑感覺。

### 資料模型

- `preprod_proposals`：id, title, client_id(FK clients), project_id(FK, 成案後回填),
  quotation_id(FK, 可連報價), ptype（形象/廣告/紀錄片/政府標案/社群…）,
  status（草稿/已提案/入圍/成案/未成案/擱置）, pitch_date, budget_range,
  deck_url（uploads/proposals/{id}/，PDF/PPT 原檔）, outcome_reason（**成案未成案
  都必填原因** — 組織學習欄）, tags, created_by, timestamps
- `preprod_references`：id, url, title, note, tags, thumb_url — 獨立參考片庫，跨提案重用
- `preprod_proposal_refs`：proposal_id ↔ reference_id

### UI 與流程

- 前期製作群組新 tab「📑 提案庫」：列表 + 篩選（客戶/類型/狀態/年度）+ 詳情
  （deck 下載、參考片單、outcome）
- **一鍵成案**：status→成案 時自動建 CRM 專案（帶客戶+報價連結，複用既有專案建立），
  proposal.project_id 回填 — 提案到專案零重輸入
- **轉換率卡**：提案→成案率 by 類型/客戶/年度（呼應 STRATEGY 三指標之一
  「詢問→成案轉換」，資料從此有出處）
- 未成案強制填 outcome_reason 才能存

### RBAC

模組 `preprod_proposals`。

---

## C. 產業情報 runner（第四個 AI runner）

**解決什麼**：標案公告、補助窗口、產業動態現在靠人肉刷網站。做成每日自動蒐集
+ claude 摘要分類，**高分標案/補助直接轉提案草稿** — 這是三塊裡商業回報最直接的
（等於自動 lead generation）。

### 來源（白名單制，起手式）

- `intel_sources` 表：name, type(rss/html), url, keywords(JSON), enabled
- 建議首批：政府電子採購網（關鍵字：影片/影像/宣傳/紀錄片/多媒體）、
  文化部/文策院/各地影視委員會補助公告、產業媒體 RSS（動腦、Campaign 等）、
  器材/後期技術 blog

### Pipeline（複用 social_runner 骨架 + `_runner_util`）

1. cron `30 8 * * *`（08:30，錯開社群 09:00）→ `_run_lock` 防重入
2. Python 抓取（feedparser/requests，timeout + 尊重 robots.txt）→ URL hash 去重
3. claude CLI 摘要 + 分類（標案機會/補助/產業動態/技術/競品）+ 相關性評分 0-100
   + 抽截止日（標案/補助 deadline 是關鍵欄位）
4. 寫 `intel_items`：source_id, url, title, summary, category, score, deadline,
   status（new/starred/archived/converted）
5. digest：高分項推 Google Chat（**依賴 webhook 設定 — 又一個前置**）

### UI

- 前期製作群組新 tab「📡 產業情報」：按分類/分數排序、**截止日倒數紅字**、
  星號收藏、「轉提案」按鈕（建 preprod_proposals 草稿，type=政府標案 → 完整閉環：
  **情報 → 提案 → 專案 → 結案 → 官網 → 社群**，全鏈路都在系統裡）

### 守則（與其他 runner 一致）

白名單外不抓、每源每日上限、只存摘要+原文連結（不轉貼全文，版權）、
enabled kill switch、連續失敗告警、dev 庫 enabled=false 防額度、claude 用量入 log。

---

## 建議順序與依賴

| 段 | 內容 | 依賴 | 粗估 |
|----|------|------|------|
| P-a | 場景庫 core（DB+CRUD+照片+篩選） | 無 | 1-2 session |
| P-a2 | 手機場勘頁 /scout.html | P-a | 0.5 session |
| P-b | 提案庫 core（DB+CRUD+一鍵成案+轉換率卡） | 無 | 1-2 session |
| P-b2 | 參考片庫 | P-b | 0.5 session |
| P-c | 情報 runner（來源表+抓取+摘要+工作台） | 無 | 2 session |
| P-c2 | Chat digest 推播 | P-c + **webhook 設定** | 0.5 session |
| P-c3 | 情報轉提案鍵 | P-b + P-c | 0.5 session |

建議 **a → b → c**（a 最單純先熱身；c 的「轉提案」需要 b 先存在）。
全部不依賴 N0/N1，可插在 N-now 收尾後任何空檔，每段照慣例 canary + /simplify。

## 商業視角

- 場景庫：重複場勘成本↓、新人接案速度↑（踩雷紀錄=經驗傳承）
- 提案庫：win rate 可量測 → 提案資源投對地方；deck 資產化可重組復用
- 情報 runner：**標案/補助自動進 pipeline = 新案源**，一年只要多成一個標案就回本
- 三者串起來後，前期製作從「靠記憶的手工業」變成「有資產底座的流水線」——
  這正是 STRATEGY 選項 B（切片產品化）未來最有賣相的切片之一
