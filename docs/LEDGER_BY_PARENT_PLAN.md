# 私帳案收入分案記帳（`ledger_detail.by_parent`）

> 2026-09-13 owner「為何不加起來？」。起因：/health 的 code review 抓到 `apply_billing_mode` 對「一個私帳案承接多個
> 母帳案」會把 X 的收入整個換成 A 的合約額（B 鏡射進來的錢不見）；先用 409 擋（BUG-5），owner 要的是「加起來」。
> 同日做完、已上 `feature/website-m`（8 個 commit，`3af43be4`…）。這份是設計正本。

## 問題

私帳案 X 只存**一個**收入總數（`contract_amount`）、**一份**工項（`ledger_detail.split`）、**一個**上次同步合計（`mirror_total`）。
X 承接 A、B 兩個母帳案時（2026-09-01 起允許），沒有任何地方記「A 給了多少、B 給了多少」：

- 推送「取代」只能 409（會洗掉別案的錢）、「加上去」只是 `contract += 這次合計`，不記是誰加的。
- 收款方式 A → 後期代開：收入該變成 A 的合約額，但不知道 X 現在的數字裡 A 佔多少，換不了。
- 「私帳落後了沒」對 N:1 直接放棄判斷（合計不屬於任何一案）。
- owner 自己在私帳填的錢（非工項的合約額、手改的工項）跟母帳鏡射來的混在一起，系統一動就可能動到他的。

## 資料

不動 DDL。`crm_projects.ledger_detail`（JSON）多兩個鍵，`norm_detail` 保留：

```json
"by_parent": {
  "<母帳案 id>": {"amount": 100000, "split": {"導演": 60000, "剪接": 40000}, "synced_total": 100000, "at": "2026-09-01", "source": "代開發票"},
  "<母帳案 id>": {"amount":  80000, "split": {"剪接": 80000},               "synced_total":  80000, "at": "2026-09-05", "source": "源日"}
},
"by_parent_pending": true        // 只有回填分不出份額的舊 N:1 才有；全部認領完自動拿掉
```

- `amount`：這個母帳案貢獻給 X 收入的錢（代開＝母帳合約額；一般＝掛給我的成本行合計）。
- `split`：這個母帳案貢獻的工項。
- `synced_total`：上次同步時母帳掛給我的成本行合計 → `mirror_stale(detail, now, pid)` 逐案判落後。
- **不變式**：`contract_amount = Σ amount + owner 自己的`；`split[k] = Σ split[k] + owner 自己的`。
  「owner 自己的」不存，用差額推 —— 所以系統只動差額，永遠碰不到他手填的。

## 純函式（`core/ledger_project.py`，全部有測試）

| 函式 | 做什麼 |
|---|---|
| `parent_shares(detail)` | `{pid: {amount, split, synced_total, at}}`，沒記錄＝`{}` |
| `set_parent_share(detail, contract, pid, amount, split, *, synced_total=None, at="", source=None, claim=False)` | 把 pid 的份額換成 (amount, split)；X 的金額與工項只吃 **新 − 舊**；工項扣到 0 為止；`synced_total`／`at` 沒給就沿用。`claim=True`＝只記份額不動錢（錢已在 X 上） |
| `drop_parent_share(detail, contract, pid)` | 解除連結：那案的金額＋工項扣掉；沒記錄＝原樣 |
| `mirror_stale(detail, current_total, pid="")` | 逐案判；沒分案記錄退回 `mirror_total` |

## 寫入點（`routers/crm/project_links.py`）—— 規則測試釘死：分身的 `contract_amount` 只准接 `new_contract`

| 寫入點 | 行為 |
|---|---|
| `_new_mirror_row`（建分身） | 整筆認成該案份額（claim） |
| 推送「取代」 | 只換**這一個母帳案**的份額（N:1 不再 409） |
| 推送「加上去」 | 這案份額再加一筆（同名工項相加） |
| `apply_billing_mode` → 後期代開 | 只把 A 的 `amount` 換成 A 的合約額，工項沿用；B 不動 |
| `apply_billing_mode` 換回 | 不動金額（跟以前一樣，只拿掉代開三欄） |
| `_write_link(mine=None)` 解除 | `drop_parent_share`（**決策 ①**：連結加的、解除就拿掉；舊連結沒記錄不動錢） |

退路 `_ensure_share`：沒有任何分案記錄的舊 1:1 分身，先把現況整筆認成該案（claim），再照差額走 —— 結果跟以前一樣。
待認領（`by_parent_pending`）的舊 N:1：改收款方式 → 409 要求先逐案「推送 → 取代」（每次取代都是 claim，全部認領完旗標自清）。

## 回填（`db/startup_migrations._m22_ledger_by_parent_backfill`，冪等）

- 1:1：整筆認成那一案（`synced_total`＝舊 `mirror_total`、`at`＝舊 `mirror_at`）。
- N:1（**決策 ②**）：owner＝唯一一個有 `finance_mine` 且綁了人員檔的帳號；各案份額＝掛給 owner 的成本行合計
  （母帳是後期代開的用母帳合約額），全部 > 0 且加總 ≤ X 的合約額才認；否則 `by_parent_pending`。
- 已有記錄的列不碰。

## 讀取端

- 「後期連結」那一行（`link_note_for` → `link_note`）：逐案判落後；N:1 句子多「份額 A 100,000、B 80,000」；待認領有字。
- 對應表 `/projects-mine-links`：母帳列 `share`、私帳列 `shares`／`shares_pending`；前端 `projlinks.js` 在「連 N 案」旁畫份額與待認領標記。

## 代辦費分案（同日第二輪，`65644ef8`…）

每案份額多 `source`（代開發票／源日／執行業務所得）。**代辦費只算走代開那幾案的份額**：

- `fee_bases(contract, d) → (代開基數, 執行業務所得基數)`：每案份額照自己的 `source` 算進哪個基數；沒標的跟 X 的 `source` 走；
  owner 自己填的那部分（合約額 − Σ份額）也跟 X 走；沒分案記錄＝整案（舊算法）。
- `apply_source_fee` 吃基數：代辦費／稅金／買發票只算代開基數，個人稅款只算執行業務所得基數，兩種混著各算各的；
  有分案記錄但沒任何一案走代開 → 三欄歸零（手改的不動）。
- 寫入點：建分身／推送／`_ensure_share`／回填都寫**明確**的 `source`（＝當時 X 的案源），之後 A 翻成代開 B 不跟著走。
  收款方式 → 後期代開：只標 A；換回：只把 A 標回源日，別案還走代開就留 X 的案源。
- 讀取端：詳情 API 回 `parent_shares`（含 source）／`fee_bases`／`source_mixed`；**前端試算改用 `_feeBases()`**（同一條規則），
  不能再用整案算 —— 否則後端 8,000、前端送 14,400 會被當成人改過而凍住（規則測試釘著）。
  「後期連結」那行：各案案源不同 → 「案源 混合」、份額後面帶案源；私帳詳情案源格下方標「混合：…」。
- 手改代辦費仍是 X 一個總數（`manual`），不逼 owner 分案填。

## 已知限制

- 工項扣到 0 就停（見上）。
- 案源格（`fpl-source`）改的是 X 的案源＝owner 自己那部分；各母帳案的案源由母帳的收款方式決定，私帳這邊不能逐案改。
- 工項扣到 0 就停：owner 把某工項手改得比份額還低、之後那案撤掉，X 那項會是 0 而不是負數（他的手改被吞掉一部分）。這是刻意的 —— 負數工項沒有意義。

## 部署注意

`norm_detail` 在 NAS 的 office-api（8002）也跑：NAS 拿到舊碼時，手機端任何一次 PUT 會把 `by_parent` **洗掉**（舊 `norm_detail`
不認識這個鍵）。`/publish` 本來就一起 scp `core/` 到 NAS 並 `docker restart website-api`；office-api 那個容器也要確認重啟。
