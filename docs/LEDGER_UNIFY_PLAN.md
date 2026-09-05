# 兩本帳統一：有連結就以母帳為主

> 狀態：**M1 + M3 已實作**（2026-09-05）。M2/M4/M5 仍是規劃。
> 已上線的：顯示名鏈（§2.1，含 owner 自訂）、佔位金額標記與兩支報表的排除
> （§2.2）、母私帳**專案對應表**與**客戶對應表**的「在母帳建立並連結」按鈕
> （§4）。工時歸戶依 owner 2026-09-05「母帳現階段不處理工時」擱置。
> 前置：[`docs/LEDGER_ENTITY_PLAN.md`](LEDGER_ENTITY_PLAN.md)（兩本帳 v2）
> 相關程式：`core/ledger.py`、`core/ledger_project.py`、`routers/crm/projects.py`
> （`is_mirrored` / `resolve_mine_link`）、`routers/api_finance_projects.py`

---

## 0. owner 拍板（2026-09-05）

1. **名稱**：有連結的案，顯示用母帳的案名。
2. **金額**：有連結的案，母帳如果沒有金額，先填私帳的，後面再改。
3. **工時**：母帳現階段不處理工時 —— `timesheets` 維持全部掛私帳案，
   不做母帳歸戶。（本文件 §5 擱置區）

---

## 1. 現況實查（生產 `mediaguard`，2026-09-05，唯讀）

### 1.1 兩本帳的規模

| | 母帳（`entity='parent'`） | 私帳（`entity='mine'`） |
|---|---|---|
| 專案 | 240 案 | 415 案 |
| 建檔期間 | 2026-04-09 ～ 2026-08-27 | 資料回溯到 2023-07 |
| 有合約金額 | 24 案（741 萬） | 415 案全有（2,425 萬） |
| 有成本行 | 21 案 | — |
| 收支明細 | 1,712 筆（**只有 6 筆掛專案**） | 4,786 筆 |
| 發票 2024–2026 | 403 張（**323 張沒掛專案**，未稅 2,693 萬） | 0 張（私帳不開發票） |
| 客戶 | 146 家 | 84 家（55 家已 `crm_link_id` 連到 CRM 客戶） |

**母帳的「案」大多不存在於專案表** —— 錢記在發票與收支上，專案表是
2026-04 才開始建的薄殼。這是「母帳沒開專案」的成因，也是 §4 補建的理由。

### 1.2 現有連結

**12 組**（12 個母帳案 ↔ 10 個私帳案）。其中 6 組同時存在新舊兩種形狀
（`crm_projects.mine_link_id` 與私帳案的 `source_project_id` 都寫了），
**盤點時務必先對 `(parent_id, mine_id)` 去重**，否則 N:1 會被灌水
（起草時就先數成 17 組、把 6 個 1:1 誤判成 N:1）。

- **8 組是 1:1** → 規則 N（名稱）立即生效
- **2 個私帳案是 N:1**：
  - `總統創新獎頒獎影片` ← `2026 產科會_第七屆總統創新獎` + `2026 第七屆總統創新獎 獎盃製作`
  - `南山人壽頒獎影片` ← `南山人壽王經理` + `南山人壽謝經理`
- 🔴 **12 個母帳案的 `contract_amount` 全部 > 0** → 規則 A（金額）對現有資料
  **一案都不會觸發**。它是為 §4 補建與未來新連結準備的，不要拿現有 12 組驗收它。

### 1.3 工時（本次不動，記錄現況）

`timesheets` 9,860 列：對到私帳案 7,229 列 / 20,393 小時 / 321 個案名；
未對映 2,631 列 / 5,001 小時 / 25 個名字；**對到母帳案 0 列**。
對映表 `timesheet_project_map` 只有 4 筆，全指私帳。
`services/timesheet_lookup.load_project_lookup()` 寫死 `is_mine(entity)`。

---

## 2. 規則契約

### 2.1 規則 N — 名稱以母帳為主

| | |
|---|---|
| 生效條件 | 私帳案**恰好**連到 1 個母帳案 |
| 動作 | **顯示層**取母帳案名 |
| 不做 | **不改寫 `crm_projects.name`** |
| N:1 | 不套用，改標「連結 N 個母帳案」並列出母帳案名 |
| 母帳改名 | 顯示自動跟著（讀取時取，不快取） |

🔴 **為什麼不做破壞性改名**（兩個理由，任一個都足以否決）：

1. Sheet 工時的 `resolve_project()` 是用 `project_name` 查 `load_project_lookup()`
   的名稱索引。把私帳案名改成母帳案名，之後 Sheet 新進的列就對不到案
   （既有列的 `project_id` 已寫入，不受影響，所以症狀會延遲一週才出現）。
2. 私帳案名是 owner 自己的語彙（「開村影片」對「蟾蜍山｜煥民新村 館所介紹」），
   改掉之後找不回來，而收支明細、對帳、匯入預覽都還在用它認案。

私帳原名在詳情頁以小字保留（「私帳原名：開村影片」）。

**唯一正本**：`core/ledger_project.py` 新增
```python
def linked_display_name(mine_name: str, parent_names: list[str]) -> tuple[str, str]:
    """回 (顯示名, 副標)。1:1 → (母帳名, 私帳原名)；其餘 → (私帳名, "")。"""
```
所有輸出私帳案名的地方都要走它。M1 的第一步就是把這些地方掃出來，
候選（起草時掃到的）：

- 後端：`routers/api_finance_projects.py`、`routers/crm/projects.py`、
  `routers/api_crm_mobile.py`、`routers/crm/cash.py`
- 前端：`frontend/tabs/finance/subviews/projects.js`、`household.js`、`recon.js`、
  `frontend/tabs/crm/crm-cashbook.js`、`crm-projects-detail.js`、`crm-projects-pay.js`

掃描要收成測試（見 §6），否則之後新增一個顯示點就漏一個。

### 2.2 規則 A — 母帳沒金額就先填私帳的

| | |
|---|---|
| 生效條件 | 母帳案 `contract_amount` 為 NULL 或 0，**且** 1:1，**且** 私帳案金額 > 0 |
| 動作 | 寫 `contract_amount = 私帳金額`，同時記 `contract_amount_source='mine'` |
| 解除 | 任何人在母帳手動改過金額 → `contract_amount_source` 清空，之後**永不**再被私帳覆蓋 |
| N:1 | **不自動填**（多個母帳案共用一個私帳金額，填了會重複計）→ 進「待確認金額」清單請人工填 |

🔴 **這個數字是下限占位，不是真值**：私帳金額是「我拿到的那段」，公司跟客戶的
合約額一定 ≥ 它（現有 12 組的落差是 3～17 倍：開村影片 63,000 vs 蟾蜍山 400,000、
2026 臺北城市形象片 96,000 vs 1,600,000）。所以：

🔴 **已驗的副作用 —— 兩個報表要排除佔位案**：

- `services/finance_statements.py:902` 的母公司「專案毛利」儀表板只取
  `contract_amount > 0` 且 `not_mine` 的母帳案。佔位金額進去 → 那案的成本
  是公司的全額、收入卻是我拿到的那段 → **毛利看起來極差甚至負數**，
  而且會把儀表板的 top/bottom 排名整個帶歪。
- `routers/api_cashflow.py:154` 用 `proj.contract_amount` 當預測總額 →
  現金流預測會低估。

兩支都加 `contract_amount_source != 'mine'` 的條件，直到人工確認。
（`core/finance_logic/_statements.py:504` 的權責損益吃的是**私帳**案的
`contract_amount`，不受這條影響。）

**UI**：母帳專案頁金額欄位旁標「暫填自私帳・待確認」；另給一張
「待確認金額」清單（`contract_amount_source='mine'` 的全部母帳案），
點進去可直接改。改完標記自動解除。

**唯一正本**：`core/crm_logic.py` 新增
```python
def placeholder_contract_amount(parent_amount, mine_amount, parent_count) -> int | None:
    """要不要用私帳金額當母帳的佔位。回 None＝不填。"""
```

### 2.3 其他欄位（沿用既有契約，這次不動）

| 母帳為主（私帳顯示唯讀） | 私帳自主 |
|---|---|
| 案名、客戶、拍攝期間 | 我的成本（我再外包給誰） |
| 我的收入＝母帳成本行加總 | 匯費、實收金額、收款日 |
| 工項明細（`ledger_detail.split`） | 分類標籤、備註 |

一律**空值不蓋**：母帳該欄是空的就保留私帳原值。

---

## 3. DB 變更

`db/migrations.py` 的 `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 清單加一欄：

```sql
ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS contract_amount_source VARCHAR(8)
```

- `'mine'` ＝ 這個金額是從私帳帶過來的佔位，待確認
- `NULL` ＝ 正常（人工填的、或已確認過）

規則 N 零 schema 變更。

---

## 4. 母帳沒開案的補建

私帳 415 案裡，母帳根本沒開專案的佔絕大多數。**不是每一案都該補**，
判別鍵是**這案的錢有沒有經過公司**：

| | 錢的流向 | 母帳該不該有案 | 做法 |
|---|---|---|---|
| a | 客戶→公司→我（公司發包給我） | 該有 | 補建 + 連結，母帳為主 |
| b | 客戶→公司（公司自己做，只是沒建專案） | 該有 | 補建（母帳自己的漏，與私帳無關） |
| c | 客戶→我（我自己接自己收） | **不該有** | 私帳為主，母帳不要碰 |

自動判別：私帳案 × 母帳發票／收款，比對「客戶（經 `clients.crm_link_id`）
＋ 期間 ＋ 金額」。對得上 → a/b；對不上 → c。

🔴 **c 類絕對不能進母帳**：那些錢公司從來沒收過，補進去會讓母帳的營收、
客戶績效、應收全部虛增。

補建的案要標 `origin='backfill'`（或等價標記），不進今年的管線、成案率、
客戶狀態自動化，否則幾百案灌進來會把 CRM 的統計打亂。

---

## 5. 階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| **M1** | 規則 N ＋ 規則 A ＋ 兩支報表排除佔位案 | ✅ 2026-09-05 |
| **M3** | 對應表：連結／解除／**在母帳建立並連結**（專案＋客戶） | ✅ 2026-09-05 |
| **M2** | 分類報表（唯讀）：415 個私帳案分 a/b/c | 待做 —— 對應表的「未對應」清單已經可以當手動版用 |
| **M4** | 再同步：母帳成本行改了 → 私帳提示差額 ＋ 一鍵套用 | 待做（含「母帳行被刪」「私帳被手改」兩種衝突） |
| **M5** | 合併視圖不重複計算（intercompany 標記） | 待做；目前沒有跨帳本報表 |

**M1/M3 實作落點**

| 東西 | 檔案 |
|---|---|
| 顯示名鏈（唯一正本） | `core/ledger_project.linked_display_name` |
| 連結批次查詢（兩形狀＋去重） | `routers/crm/_shared.mine_parent_names` |
| 案名共同出口 | `routers/crm/_shared.project_names_map`（收支／拆項／應付／雜支跟著走） |
| 清單／詳情／PUT | `routers/api_finance_projects.py`（`name`／`orig_name`／`custom_name`／`parent_names`） |
| 對應表端點 | `routers/crm/projects.py`：`GET /projects-mine-links`、`POST /projects/{id}/parent-create`、`PUT /projects/{id}/parent-link` |
| 客戶補建 | `routers/crm/clients.py`：`POST /clients/{id}/crm-create` |
| 前端 | `frontend/tabs/finance/subviews/projlinks.js`（新頁）、`clients.js`、`projects.js` |
| 欄位 | `crm_projects.display_name`、`crm_projects.contract_amount_source` |

**擱置**：工時歸戶（owner 2026-09-05「母帳現階段不處理工時」）。
`load_project_lookup` 的 `is_mine` 不動。

M1 可獨立上線且影響面小（8 組名稱 + 0 組金額），建議先做。

---

## 6. 驗收

`tests/unit/test_ledger_unify.py`：

- `linked_display_name` 的 1:1 / N:1 / 無連結三態
- `placeholder_contract_amount` 的四個分支（母帳有值不蓋／1:1 才填／
  N:1 不填／私帳 0 不填）
- 咽喉掃描（用 `tests/unit/_srcscan.py`）：所有輸出私帳案名的地方都要
  走 `linked_display_name`，白名單外的直接紅
- 排除條件的來源掃描：`finance_statements.py` 與 `api_cashflow.py` 都要
  出現 `contract_amount_source` 的排除條件

盤點腳本（可重跑，唯讀）：session scratchpad 的 `diag_ledger*.py`，
第 8 支會逐組模擬「兩條規則會不會觸發」。**去重 `(parent_id, mine_id)`
那一行不能拿掉**（見 §1.2）。

---

## 7. 待 owner 決定

1. **N:1 的名稱**：`總統創新獎頒獎影片` 連著兩個母帳案，顯示要用哪一個？
   目前規劃是保留私帳名 + 標「連結 2 個母帳案」。
2. **N:1 的金額**：兩個母帳案共用一個私帳金額，不自動填。要人工填，
   還是按比例拆？
3. **補建的分類鍵**：§4 的 a/b/c 用「母帳有無對應發票／收款」判，
   還是有別的規則（例如某幾家客戶一律算公司的）？
