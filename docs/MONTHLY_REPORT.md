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
2. **多出來的錢從哪來**：本月收入／支出／家用（收支明細，不含轉帳、還卡費、投資買賣）、存下來的（存款率）、證券增減（含買賣、匯率）、應收增減、登記餘額與帳上的差。
3. **總資產走勢**：淨值快照（最多 24 點）＋這個月。
4. **各帳戶**：本月登記數、上月、變化、狀態（已登記／還沒登記／還沒補的明細）；證券分券商（未拆明細）。
5. **堡壘**：可撐月數、必要支出、六題幾綠幾黃幾紅、五層水位。
6. **財富階梯與財富自由**：第幾階、離下一階、不工作每月可花、達成率（33 倍）、10 年後資產。
7. **財務體檢**四格：流動性（可撐月數，跟堡壘同一套顏色 ≥6 綠／3–6 黃）、韌性（台海戰爭題的判語）、集中度（單一股票穿透後佔證券）、
   收支紀錄（明細／登記齊不齊；餘額 0 又沒登記過的閒置帳戶不算、|未補| < 100 當補齊）。
8. **財務建議**：規則產生（見 §3），照急迫度排（bad → warn → info → ok），每條有標題、說明、做法。
9. **待辦**：補明細、還沒登記的帳戶、未拆證券明細、bad／warn 建議的標題。

## 3. 規則（`core/monthly_report.py`，純函式）

| 規則 | 門檻 | 等級 |
|---|---|---|
| 台海戰爭題紅＋外幣部位沒標「海外」 | 堡壘 war test state == bad | bad |
| 第 2–4 層沒填滿 | 任一層 gap > 0；第 1 層多的夠分 → 說怎麼分 | warn |
| 集中度 | **單一公司的股票**佔證券 ≥ 25%（≥ 35% 升 bad），台積電看穿透台灣50 類基金後的比重；指數型基金、外幣現金、保險（`is_broad_fund`）不算一檔股票 | warn／bad |
| 帳沒記齊 | 有帳戶 unfilled ≠ 0、本月沒收支明細、有帳戶沒登記、有未拆明細 | warn |
| 信用卡未繳 | ≥ 1 個月必要支出（`CARD_MONTHS`；半個月是正常帳單週期） | warn |
| 本月入不敷出／存款率 | 本月有明細：net < 0 → warn；否則 ok 一條 | warn／ok |
| 應收太多 | 應收 ≥ 2 個月必要支出（講佔金融資產幾 %、稅前帳面） | info |
| 還沒到財富自由 | fire.state ≠ ok：支出 > 可花 → 差多少；範圍內但模擬會用完 → 「還不穩」 | warn |
| 財富自由是稅前 | fire.state == ok 且 fire.pretax | info |
| 必要支出樣本少 | sample_months < 6 且沒手填 | info |
| 做得好的地方 | 沒貸款／撐 ≥ 12 個月／沒紅燈／已達財富自由（帶「稅前、N 個月樣本」前提） | ok |

🔴 月報**不自己算**餘額、必要支出、階梯、財富自由 —— 全部吃別人算好的（堡壘 payload、登記餘額 payload、資產儀表板的桶、本月收支、
淨值快照、上一份月報）。數字對不上時改來源，不改月報。

## 4. 端點（`routers/api_monthly_report.py`，prefix `/api/v1/finance`；主控與 NAS office-api 都掛，只寫 DB）

- `GET /monthly-reports?entity=` → `{items[]{month, basis_date, generated_at, generated_by}}`（新的在前）。
- `GET /monthly-reports/{month}?entity=` → `{month, basis_date, generated_at, report}`；沒有 → 404。
- `POST /monthly-reports/generate?entity=` body `{month?}` → `{month, report}`；🔴 只能產生本月（帶了別的月 → 422），過去月份以當時登記時產生的那份為準。
- `report` 形狀：`{month, basis_date, generated_at, first,
  totals{cash, securities, receivable, equipment, assets, liabilities, card, loan, net_worth, financial, net_financial, cash_usable},
  prev{label, totals}|null, delta{}, flow{deposit, expense, household, net, has_entries, bank_net, securities_change, receivable_change, register_gap, through, savings_rate},
  trend[]{date, total, now?}, accounts[]{name, balance, prev, delta, anchor_date, unfilled, registered_this_month}, brokers[]{broker, total, prev, delta, plug, count},
  concentration{total, top_name, top_value, top_pct, top3_pct, funds_pct, biggest_name, biggest_pct, lookthrough{value, pct}|null},
  fortress{runway, tone, need, need_sample_months, need_override, tests[], counts{}, layers[]},
  ladder_fire{rung, rung_name, to_next, next_name, fire_allowed, fire_spend, fire_ratio33, fire_rate, fire_state, fire_pretax, y10_nominal, y10_real, growth_rate, inflation},
  health[]{key, label, state, grade, text}, advice[]{no, level, key, title, text, how}, todo[]}`。
  - 標題淨值＝`net_financial`（現金＋證券−負債，跟階梯同定義）；`net_worth` 是含應收、器材的帳面數。`cash_usable`＝堡壘第 1–3 層。
  - 本月收入／支出**不含**轉匯與定存、信用卡兩本與「投資」項目；`bank_net`＝上一份月報基準日之後帳戶真正的淨流（含匯費、請款），
    `register_gap`＝Δ現金 − bank_net＝登記後還沒記進帳的明細（第一份 None）。
  - 集中度只看單一公司的股票；`lookthrough` 是台積電加計台灣50 類基金裡的比例（堡壘設定 `concentration.tsmc_share`，預設 0.55，
    桌機堡壘「帳戶分層」底下可改），體檢與建議都用穿透後的比重。
  - 本月收入／支出跟財務三表同一套分類（`core.finance_logic.cashflow_lines` 的營業活動流入／流出），不再自己用分類名猜。
  - 快照的比較基準與走勢用 `auto` 四桶合計（跟 `totals.assets` 同口徑），舊快照沒有 `auto` 才用 `total`。
- 「上月」＝上一份月報的 payload；第一份只拿最近一次淨值快照比總資產（分項不比）。
