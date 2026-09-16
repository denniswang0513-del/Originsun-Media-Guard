# 私帳月報 —— 登記餘額後自動產生

> owner 2026-09-17：「我希望你在我更新帳戶後 自動提供一份月報給我」「我希望月報可以給我財務建議與財務分析」。
> 狀態：**dev 8001 做完、單元測試通過（2026-09-17）**，待 owner 看圖說「推」。

## 1. 什麼時候產生、放在哪

- 你在「登記餘額」按儲存的那一刻，後端把**當月**那一份算好存進 `finance_monthly_reports`（同帳本同月覆蓋）。
  登記頁儲存完會出現「X 月的月報已經更新 → 看月報」。產不出來只記 log，登記本身不受影響（`generate_quietly`）。
- 也可以手動：桌機月報頁／手機月報頁的「用現在的數字重新產生本月」（`POST /monthly-reports/generate`，可帶 `month`）。
- 桌機：私帳 nav「月報」（`frontend/tabs/finance/subviews/register.js` 旁邊的 `report.js`），下拉切月份。
- 手機：士源帳本隱藏路由 `#report`（`frontend/m/views/ledger-report.js`；總覽堡壘卡下方那列、或登記存完那列進來）。

## 2. 內容（由上到下）

1. **這個月的錢**：淨值、可動用現金、證券現值、負債；表格：現金／證券／應收／器材／總資產／負債／淨值，本月 vs 上月 vs 變化。
2. **多出來的錢從哪來**：本月收入／支出／家用（收支明細）、存下來的（存款率）、證券漲跌、應收增減、解釋不了的差額。
3. **總資產走勢**：淨值快照（最多 24 點）＋這個月。
4. **各帳戶**：本月登記數、上月、變化、狀態（已登記／還沒登記／還沒補的明細）；證券分券商（未拆明細）。
5. **堡壘**：可撐月數、必要支出、六題幾綠幾黃幾紅、五層水位。
6. **財富階梯與財富自由**：第幾階、離下一階、不工作每月可花、達成率（33 倍）、10 年後資產。
7. **財務體檢**四格：流動性（可撐月數）、韌性（台海戰爭題）、集中度（單一股票佔證券）、收支紀錄（明細／登記齊不齊）。
8. **財務建議**：規則產生（見 §3），照急迫度排（bad → warn → info → ok），每條有標題、說明、做法。
9. **待辦**：補明細、還沒登記的帳戶、未拆證券明細、bad／warn 建議的標題。

## 3. 規則（`core/monthly_report.py`，純函式）

| 規則 | 門檻 | 等級 |
|---|---|---|
| 台海戰爭題紅＋外幣部位沒標「海外」 | 堡壘 war test state == bad | bad |
| 第 2–4 層沒填滿 | 任一層 gap > 0；第 1 層多的夠分 → 說怎麼分 | warn |
| 集中度 | **單一公司的股票**佔證券 ≥ 25%（≥ 35% 升 bad）；指數型基金（`is_broad_fund`：Vanguard／0050／台灣50／全球…）不算一檔股票 | warn／bad |
| 帳沒記齊 | 有帳戶 unfilled ≠ 0、本月沒收支明細、有帳戶沒登記、有未拆明細 | warn |
| 信用卡未繳 | ≥ 半個月必要支出 | warn |
| 本月入不敷出／存款率 | 本月有明細：net < 0 → warn；否則 ok 一條 | warn／ok |
| 應收太多 | 應收 ≥ 2 個月必要支出 | info |
| 財富自由是稅前 | fire.pretax | info |
| 必要支出樣本少 | sample_months < 6 且沒手填 | info |
| 做得好的地方 | 沒貸款／撐 ≥ 12 個月／沒紅燈／已達財富自由 | ok |

🔴 月報**不自己算**餘額、必要支出、階梯、財富自由 —— 全部吃別人算好的（堡壘 payload、登記餘額 payload、資產儀表板的桶、本月收支、
淨值快照、上一份月報）。數字對不上時改來源，不改月報。

## 4. 端點（`routers/api_monthly_report.py`，prefix `/api/v1/finance`；主控與 NAS office-api 都掛，只寫 DB）

- `GET /monthly-reports?entity=` → `{items[]{month, basis_date, generated_at, generated_by}}`（新的在前）。
- `GET /monthly-reports/{month}?entity=` → `{month, basis_date, generated_at, report}`；沒有 → 404。
- `POST /monthly-reports/generate?entity=` body `{month?}` → `{month, report}`。
- `report` 形狀：`{month, basis_date, generated_at, first, totals{cash, securities, receivable, equipment, assets, liabilities, card, loan, net_worth, financial},
  prev{label, totals}|null, delta{}, flow{deposit, expense, household, net, has_entries, securities_change, receivable_change, unexplained, savings_rate},
  trend[]{date, total, now?}, accounts[]{name, balance, prev, delta, anchor_date, unfilled, registered_this_month}, brokers[]{broker, total, prev, delta, plug, count},
  concentration{top_name, top_pct, top3_pct, funds_pct, biggest_name, biggest_pct}, fortress{runway, tone, need, need_sample_months, tests[], counts{}, layers[]},
  ladder_fire{rung, rung_name, to_next, next_name, fire_allowed, fire_spend, fire_ratio33, fire_rate, fire_pretax, y10_nominal, y10_real},
  health[]{key, label, state, grade, text}, advice[]{no, level, key, title, text, how}, todo[], card_outstanding}`。
- 「上月」＝上一份月報的 payload；第一份只拿最近一次淨值快照比總資產（分項不比）。
