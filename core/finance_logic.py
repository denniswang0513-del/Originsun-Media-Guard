"""財務管理階段二/三純邏輯（core/finance_logic.py — 比照 core/crm_logic.py 慣例）。

零 DB / FastAPI 依賴 — 收支分類、銀行流水/餘額、對帳差額、月份取值、
權責制三表推導（損益/資產負債/現金流量）都是公司帳務的判定規則，
抽成純函式讓「規則」與「SQL 聚合」分離：endpoint / service 只負責把
DB 撈出來的值餵進來。單元測試在 tests/unit/test_finance_logic.py
（27 個 cash category 全覆蓋）+ tests/unit/test_finance_statements.py
（三表黃金測試 + 恆等式）。

階段三新增（三表推導引擎）：
- period_months / month_range / shift_month：期間字串 → 月清單
- depreciation_for_month / accumulated_depreciation / equipment_net_rows：器材直線折舊
- build_pnl / merge_pnl：損益表（權責認列，locked 月快照可加總）
- build_balance_sheet：資產負債表（推導式，check.diff 誠實外顯）
- build_cashflow / merge_cf / cash_entry_activity：現金流量表（直接法）
- vat_position：營業稅位置（銷項/進項/已繳/淨額）
- ar_open_invoices / ap_open_payments / bank_balances_asof：期末部位
- statement_warnings / statement_interpretation：檢核警示 + 白話解讀

階段四新增（銀行貸款）：
- amortization_schedule：攤還表（annuity/straight/interest_only + 寬限期）
- loan_interest_total：利息費用權責認列（按 due_date，不管繳沒繳）
- loan_outstanding_rows：BS 非流動負債逐筆貸款餘額（單純看繳款事實）
- treatment 'loan'（貸款撥款/繳款收支）：不進損益，CF 走科目 cf_activity=financing

對帳工作台（月底對帳升級 — 逐筆勾銷）：
- statement_line_status：明細列狀態推導（matched/noted/unmatched，不落庫）
- auto_match_statement_lines：金額相等+日期最近的確定性自動配對
- workbench_summary：配對計數/未配對金額加總（差額解釋交給人）
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone

# 關聯 id 優先序（高→低）：預支 → 發票(AR) → 請款單(AP) → 貸款繳款 → category 對映。
# 硬連結（記錄上綁了誰）永遠壓過文字 category — category 是人填的、會漂。
_LINK_PRIORITY = (
    ("advance_payment_id", "advance"),
    ("invoice_id", "ar_settlement"),
    ("payment_request_id", "ap_settlement"),
    ("loan_payment_id", "loan"),
)

# 「不進損益」的 treatment 等價類（結清/內部移動性質，權責已在別處認列）：
# transfer=帳戶互轉、passthrough=代開發票代收代付、loan=貸款撥款/繳款。
# 費用側迭代（iter_expense_items）與現金流活動判定共用此單一來源。
NON_PNL_TREATMENTS = frozenset({"transfer", "passthrough", "loan"})

# 本金是**內部移動**、不算任何一種現金流量活動的那一類。
# 🔴 本來是三個地方各寫一次字面 ("advance", "transfer")：cash_entry_activity、
#    build_cashflow 的迭代、以及現金流鑽取。多一個內部移動桶（跨帳本調撥、
#    零用金週轉）時要找三處字面，漏掉一處＝現金流量表靜靜不平（不是報錯）。
INTERNAL_MOVEMENT_TREATMENTS = frozenset({"advance", "transfer"})


def classify_cash_entry(entry: dict, cat_map: dict) -> str:
    """單筆收支明細 → 會計處理方式（treatment）。

    參數：
      entry    dict，keys: category / invoice_id / advance_payment_id /
               payment_request_id / loan_payment_id（缺 key 視為空）
      cat_map  {(source, category_text): treatment} — 由 finance_category_map
               撈出來的對照（source='cash' 的列才會被查到）

    優先序：advance_payment_id → 'advance'；invoice_id → 'ar_settlement'；
    payment_request_id → 'ap_settlement'；loan_payment_id → 'loan'；
    再查 cat_map；查無 → 'unmapped'。

    cat_map 值相容兩種形狀：treatment 字串（階段二）或
    {"treatment", "account_id"} dict（階段三 — 三表引擎需要科目解析）。
    """
    for key, treatment in _LINK_PRIORITY:
        if entry.get(key):
            return treatment
    category = entry.get("category") or ""
    val = cat_map.get(("cash", category))
    if isinstance(val, dict):
        val = val.get("treatment")
    return val or "unmapped"


def cash_entry_flow(entry: dict) -> int:
    """單筆收支對銀行帳戶的淨流（正=流入、負=流出）。

    = (deposit or 0) − (expense or 0) − (bank_fee or 0) − (claim or 0)
    匯費（bank_fee）與請款（claim）都是實際從帳戶出去的錢，一併計入流出。
    """
    return ((entry.get("deposit") or 0) - (entry.get("expense") or 0)
            - (entry.get("bank_fee") or 0) - (entry.get("claim") or 0))


def bank_running_balance(opening_balance: int, entries: list) -> int:
    """帳戶餘額 = 期初餘額 + Σ 各筆淨流。

    流水公式是線性的，所以把 SQL SUM 出來的聚合值包成一筆 entry 餵進來
    也會得到同樣結果（endpoint 端可用聚合省掉逐筆搬運）。
    """
    return (opening_balance or 0) + sum(cash_entry_flow(e) for e in entries)


def reconciliation_diff(system_balance: int, statement_balance: int) -> dict:
    """對帳差額：diff = 對帳單餘額 − 系統餘額；歸零才算平（balanced）。"""
    diff = (statement_balance or 0) - (system_balance or 0)
    return {"diff": diff, "status": "balanced" if diff == 0 else "diff"}


# 收款方代扣的匯費上限。實收比發票少這個數以內就當「收齊了」——
# 台灣的跨行匯費是 15/30 元，2026-08-20 實測 394 張歷史發票裡有 42 張正好差 30。
FEE_TOLERANCE = 50


def amount_is_settled(paid: int, total: int) -> bool:
    """這張單據（發票／請款單）收付齊了沒？

    🔴 唯一正本 —— 這個判斷同時決定三件事：發票的 payment_status、它出不出現在
    應收帳款、以及分配面板顯示綠燈還是「沒收齊」。分開寫必然漂：2026-08-20 實測，
    「收到任何一毛就標已收款」讓一張只收 111,050 / 面額 144,900 的發票（尚欠
    33,850）整張從應收帳款消失，同一畫面的發票列表卻照實顯示 outstanding。

    容差是為了匯費，不是為了讓沒收齊的發票蒙混過關 —— 差 33,850 不會過，差 30 會。

    🔴 名字刻意是中性的：付款側（一筆匯款掛多張請款單）用的是**同一條**規則。
    2026-08-22 那批本來在 router 裡 inline 寫了第二份 —— 落在沒有單元測試的
    地方，容差改成百分比時 tests/unit 會照樣綠燈，付款側靜靜用舊規則。

    🔴「一毛都沒收到」不算收齊 —— 這個守衛本來散在呼叫端（三處寫
    `got > 0 and amount_is_settled(...)`、collection_fields 那處忘了寫），
    所以一張面額 30 元、實收 0 的發票在應收帳款那側會顯示**已收齊**
    （0 + 50 >= 30）。容差是為了吸收匯費，不是為了讓沒收到錢的單據結案。
    面額 ≤ 0 的單據沒有東西要收，仍算結清。
    """
    paid, total = int(paid or 0), int(total or 0)
    if total <= 0:
        return True
    return paid > 0 and paid + FEE_TOLERANCE >= total


def statement_line_status(line: dict) -> str:
    """對帳單明細列狀態（推導值不落庫）：matched（有配對）> noted（有說明）> unmatched。"""
    if line.get("matched_entry_id"):
        return "matched"
    if (line.get("note") or "").strip():
        return "noted"
    return "unmatched"


def auto_match_statement_lines(lines: list, entries: list, *,
                               tolerance_days: int = 5) -> list:
    """對帳單明細 ↔ 收支明細自動配對 → [(line_id, entry_id), ...]。

    確定性演算法（黃金測試鎖住，改規則要先改測試）：
    - 只配「未配對的 line」×「未被任何 line 佔用的 entry」，一 entry 只配一次
    - 金額判定：line['amount']（有號）== cash_entry_flow(entry)（有號淨流，含匯費）
    - 同金額多候選 → 取 |日期差| 最小者（> tolerance_days 不配）；
      差距同 → entry_date 較早者；再同 → id 字典序（穩定）
    - line 或 entry 缺日期 → 只在該金額候選唯一時才配（多候選無從判遠近，寧可留人工）

    lines 需要 keys: id / amount / line_date / matched_entry_id；
    entries 需要 keys: id / entry_date / deposit / expense / bank_fee / claim。
    日期收 datetime / date / None（呼叫端先 local_day 處理時區；_as_date 收斂型別）。
    """
    used = {ln.get("matched_entry_id") for ln in lines if ln.get("matched_entry_id")}
    avail = {}  # amount → [entry]（保持穩定序）
    for e in entries:
        if e.get("id") in used:
            continue
        avail.setdefault(cash_entry_flow(e), []).append(e)

    pairs = []
    pending = [ln for ln in lines if not ln.get("matched_entry_id")]
    pending.sort(key=lambda ln: (_as_date(ln.get("line_date")) or date.max, str(ln.get("id"))))
    for ln in pending:
        cands = avail.get(ln["amount"], [])
        if not cands:
            continue
        ld = _as_date(ln.get("line_date"))
        dated = [(abs((_as_date(e.get("entry_date")) - ld).days), _as_date(e.get("entry_date")), str(e.get("id")), e)
                 for e in cands if ld and _as_date(e.get("entry_date"))]
        if dated:
            dated.sort(key=lambda t: t[:3])
            if dated[0][0] > tolerance_days:
                continue
            hit = dated[0][3]
        elif len(cands) == 1:
            hit = cands[0]
        else:
            continue  # 缺日期且多候選 → 留人工
        pairs.append((ln["id"], hit["id"]))
        cands.remove(hit)
    return pairs


def workbench_summary(lines: list, entries: list) -> dict:
    """對帳工作台摘要 — 純計數/加總，給前端 summary bar；不做差額推論
    （餘額是累計值、明細是單月，兩者的差額關係交給人看）。

    吃工作台 dict 形狀：lines 需要 matched_entry_id / note / amount；
    entries 需要 matched / amount（有號淨流，配對旗標由呼叫端用全域認領
    集合算好 — 跨月認領也算 matched，這裡不重算）。
    - lines_unmatched/noted 分開列：noted=有說明的未配對（如時間差），
      金額仍計入 lines_unmatched_sum（銀行有、系統本月沒有）
    """
    statuses = [statement_line_status(ln) for ln in lines]
    un_lines = [ln for ln, s in zip(lines, statuses) if s != "matched"]
    un_entries = [e for e in entries if not e.get("matched")]
    return {
        "lines_total": len(lines),
        "lines_matched": statuses.count("matched"),
        "lines_noted": statuses.count("noted"),
        "lines_unmatched": statuses.count("unmatched"),
        # 「銀行有・系統沒有」桶的計數與金額同源出（前端只渲染，不重推桶規則）
        "lines_bank_only": len(un_lines),
        "lines_unmatched_sum": sum(int(ln.get("amount") or 0) for ln in un_lines),
        "entries_total": len(entries),
        "entries_matched": len(entries) - len(un_entries),
        "entries_unmatched": len(un_entries),
        "entries_unmatched_sum": sum(int(e.get("amount") or 0) for e in un_entries),
    }


# 本地時區抓一次就好。無參數的 astimezone() 每次都會重新解析系統時區設定，
# 而 month_of / local_day 是逐列呼叫的原語。
_LOCAL_TZ = datetime.now().astimezone().tzinfo


def month_of(dt) -> str | None:
    """datetime / date / ISO 字串 → 'YYYY-MM'；None 或看不懂 → None。

    月結守衛 + 三表引擎用：只關心「落在哪個月」，把三種來源型別收斂成一種表示。

    ⚠ 時區：使用者填的日期是台灣本地日（_parse_day 產 naive datetime 入
    timestamptz 欄），DB 回讀會轉成 UTC 表示（2026-02-01 00:00+08 →
    2026-01-31 16:00Z）— 直接取 .month 會把月初資料歸到前一個月。
    帶時區的 datetime 先 astimezone() 回系統本地時區再取月，與寫入端
    （naive=本地）及 SQL 端 naive 比較（session tz）的月份判定一致。
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            # 🔴 用快取好的本地 tzinfo，不要用無參數的 astimezone()：後者每次都去
            # 問一次作業系統的時區（實測 1,466ns vs 336ns）。這支是三表引擎的
            # 逐列原語 —— 一次 /finance/statements 會呼叫近三萬次，光這一行就是
            # 整支請求的 42% CPU（150.8ms → 72.9ms）。
            dt = dt.astimezone(_LOCAL_TZ)
        return f"{dt.year:04d}-{dt.month:02d}"
    if isinstance(dt, date):
        return f"{dt.year:04d}-{dt.month:02d}"
    if isinstance(dt, str):
        m = re.match(r"^(\d{4})-(\d{2})", dt.strip())
        if m and 1 <= int(m.group(2)) <= 12:
            return f"{m.group(1)}-{m.group(2)}"
    return None


def local_day(dt):
    """timestamptz 回讀是 UTC 表示 → 轉回系統本地時區再剝時區資訊（naive）。

    與 month_of 同一套時區處理（月初/日界列直接取值會少一天）。DB 撈出的
    帶時區 datetime 序列化成本地日、跨月/跨日比較前都該過這一關。None 原樣回。
    """
    if dt is None:
        return None
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        return dt.astimezone(_LOCAL_TZ).replace(tzinfo=None)   # 理由同 month_of
    return dt


def today_start():
    """今天 00:00（本地 naive）— 到期/逾期比較的日界基準。"""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


# ═════════════════════════════════════════════════════════════════
# 階段三：權責制三表推導引擎（全部純函式，吃 dict/list、零 DB）
# ═════════════════════════════════════════════════════════════════

# 損益表分組（順序 = 前端呈現順序；值域對齊 db/seed_finance.py pnl_group）
COST_GROUPS = ("營業成本-料", "營業成本-工", "營業成本-費")
OPEX_GROUPS = ("營業費用-銷售", "營業費用-管理", "營業費用-研發")

# drilldown kind 單一來源：報表行的 drill 欄位（_finalize_pnl / build_balance_sheet
# / build_cashflow）、/statements/drilldown 的 kind 驗證、單元測試三邊共用同一組值
# （services/finance_statements.py re-export 給 endpoint 與測試）。前端只讀後端
# 給的 drill 欄位，不自拼 kind 字串。
CF_ACTIVITY_DRILLS = {"operating": "cash.operating",
                      "investing": "cash.investing",
                      "financing": "cash.financing"}
VALID_DRILL_KINDS = frozenset(
    {"revenue", "non_operating", "receivable", "payable"}
    | {"cost." + g.split("-", 1)[-1] for g in COST_GROUPS}
    | {"opex." + g.split("-", 1)[-1] for g in OPEX_GROUPS}
    | set(CF_ACTIVITY_DRILLS.values()))

_UNMAPPED_EXPENSE_LABEL = "未歸類支出"
_UNMAPPED_INCOME_LABEL = "未歸類收入"
_BANK_FEE_LABEL = "銀行手續費"
_DEPRECIATION_LABEL = "折舊費用"
_ADVANCE_EXPENSE_LABEL = "預支核銷支出"

_MAX_PERIOD_MONTHS = 120  # 期間上限 10 年，防呆


# ── 月份運算 ─────────────────────────────────────────────────

def _month_index(month: str) -> int:
    y, m = month.split("-")
    return int(y) * 12 + int(m) - 1


def shift_month(month: str, delta: int) -> str:
    """'YYYY-MM' 位移 delta 個月（可負）。"""
    idx = _month_index(month) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_range(start: str, end: str) -> list:
    """起訖月（含）→ 月清單。start > end 或超過上限 → ValueError。"""
    if month_of(start) != start or month_of(end) != end:
        raise ValueError(f"月份格式需 YYYY-MM: {start}..{end}")
    n = _month_index(end) - _month_index(start) + 1
    if n < 1:
        raise ValueError(f"起始月不可晚於結束月: {start}..{end}")
    if n > _MAX_PERIOD_MONTHS:
        raise ValueError(f"期間過長（{n} 個月 > 上限 {_MAX_PERIOD_MONTHS}）")
    return [shift_month(start, i) for i in range(n)]


def period_months(period_str: str) -> list:
    """期間字串 → 月清單（ValueError = 格式錯，endpoint 轉 422）。

    支援四種格式：
      '2026-06'            單月
      '2026-Q2'            季（Q1..Q4，q 大小寫皆可）
      '2026'               整年 12 個月
      '2025-07..2026-06'   起訖月（含），上限 120 個月
    """
    s = (period_str or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})$", s)
    if m:
        if not 1 <= int(m.group(2)) <= 12:
            raise ValueError(f"月份無效: {s}")
        return [s]
    m = re.match(r"^(\d{4})-[Qq]([1-4])$", s)
    if m:
        start = f"{m.group(1)}-{(int(m.group(2)) - 1) * 3 + 1:02d}"
        return [shift_month(start, i) for i in range(3)]
    m = re.match(r"^(\d{4})$", s)
    if m:
        return [f"{s}-{i:02d}" for i in range(1, 13)]
    m = re.match(r"^(\d{4}-\d{2})\.\.(\d{4}-\d{2})$", s)
    if m:
        return month_range(m.group(1), m.group(2))
    raise ValueError(
        f"period 格式無效: {period_str!r}（支援 YYYY-MM / YYYY-Qn / YYYY / YYYY-MM..YYYY-MM）")


# ── 器材折舊（直線法）─────────────────────────────────────────

def depreciation_for_month(equip: dict, month: str) -> int:
    """單一器材在指定月的折舊額（直線法，整數月攤）。

    規則：購入月起算（購入當月即折一整月）；攤滿 depreciation_months 停；
    除役月（retired_date 落點月）起不再折舊（月中除役當月不折）；
    baseline 前購入照算（本函式不看 baseline，期間過濾由 caller 做）。
    整除餘數掛在最後一攤月（Σ 各月折舊 == purchase_cost 恆成立）。
    """
    cost = int(equip.get("purchase_cost") or 0)
    n = int(equip.get("depreciation_months") or 0)
    start = month_of(equip.get("purchase_date"))
    if cost <= 0 or n <= 0 or not start or not month:
        return 0
    retired = month_of(equip.get("retired_date"))
    if retired and month >= retired:
        return 0
    idx = _month_index(month) - _month_index(start)
    if idx < 0 or idx >= n:
        return 0
    base = cost // n
    if idx == n - 1:
        return cost - base * (n - 1)
    return base


def accumulated_depreciation(equip: dict, as_of_month: str) -> int:
    """累計折舊（購入月起到 as_of 月（含）之各月折舊合計）。"""
    start = month_of(equip.get("purchase_date"))
    if not start or not as_of_month or as_of_month < start:
        return 0
    n = int(equip.get("depreciation_months") or 0)
    end_idx = min(_month_index(as_of_month) - _month_index(start), max(n - 1, 0))
    return sum(depreciation_for_month(equip, shift_month(start, i))
               for i in range(end_idx + 1))


def depreciation_rows(equipment, months) -> list:
    """各月器材折舊合計 [{month, amount}]（amount 0 的月不出列）。

    build_pnl 的折舊總額與 drilldown 的 derived 虛擬列同吃這份 —
    單一定義處，明細合計必然對得上報表數字。"""
    out = []
    for m in months:
        dep = sum(depreciation_for_month(eq, m) for eq in equipment)
        if dep:
            out.append({"month": m, "amount": dep})
    return out


def equipment_net_rows(equipment, as_of_month: str) -> dict:
    """器材淨值（BS 資產列 + drilldown 明細）。

    除役者出表（retired_date 月 ≤ as_of，或 status='除役' 且無 retired_date）；
    as_of 之後才購入的不列（尚非資產）；無購入日者以原價列（無法起算折舊）。
    """
    lines, total = [], 0
    for eq in equipment:
        cost = int(eq.get("purchase_cost") or 0)
        if cost <= 0:
            continue
        pm = month_of(eq.get("purchase_date"))
        if pm and as_of_month and pm > as_of_month:
            continue
        rm = month_of(eq.get("retired_date"))
        if (rm and as_of_month and rm <= as_of_month) or \
                ((eq.get("status") or "") == "除役" and not rm):
            continue
        accum = accumulated_depreciation(eq, as_of_month)
        lines.append({"label": eq.get("name") or "?", "cost": cost,
                      "accum": accum, "net": cost - accum})
        total += cost - accum
    return {"lines": lines, "net_total": total}


# ── 銀行貸款（階段四：攤還表 + 利息權責 + 期末餘額）──────────

_LOAN_INTEREST_LABEL = "利息費用"  # 對齊科目 6410 名稱（業外支出）


def _as_date(d):
    """date/datetime/'YYYY-MM-DD…' → date；None/看不懂 → None。"""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", d.strip())
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def _monthly_due_date(anchor: date, offset: int) -> str:
    """anchor 起第 offset 個月的「同日」（月底溢出 → 該月最後一日），'YYYY-MM-DD'。

    錨定日保留原始 day（1/31 → 2/28 → 3/31 → 4/30，不是被 2 月夾成 28 後永遠 28）。
    """
    idx = anchor.year * 12 + anchor.month - 1 + offset
    y, m = idx // 12, idx % 12 + 1
    return f"{y:04d}-{m:02d}-{min(anchor.day, calendar.monthrange(y, m)[1]):02d}"


def amortization_schedule(principal, annual_rate, term_months, method,
                          start_date, grace_months=0,
                          first_payment_date=None) -> list:
    """貸款攤還表（純函式）→ [{period_no, due_date, principal_due, interest_due}]。

    參數：
      principal          本金（整數新台幣；導入舊貸時 caller 傳剩餘本金）
      annual_rate        年利率 %（2.85 = 2.85%）；月利率 r = annual_rate/100/12
      term_months        總期數（含寬限期）
      method             'annuity' 等額本息 / 'straight' 等額本金 /
                         'interest_only' 按月付息到期還本
      start_date         起貸日（無 first_payment_date 時首期 = 下月同日）
      grace_months       寬限期 — 前 N 期只付息不還本（三法都適用；
                         interest_only 本來就只付息，寬限期無感）
      first_payment_date 首期繳款日（有值時 due_date 以它起算每月同日）

    規則：
    - due_date 每月同日，月底溢出用該月最後一日（錨定日保留）。
    - annuity：n = 扣寬限後期數，PMT = P·r/(1−(1+r)^−n)；每期
      interest = round(餘額·r)、principal = round(PMT) − interest；
      末期本金吸尾差使 Σprincipal == principal。
    - straight：principal = round(P/n)（末期吸尾差）、interest = round(餘額·r)。
    - interest_only：每期只付息，末期加還全額本金。
    - r = 0 邊界：利息全 0（annuity 退化成等額本金）。
    格式錯 → ValueError（endpoint 轉 422）。
    """
    principal = int(principal or 0)
    term_months = int(term_months or 0)
    grace_months = int(grace_months or 0)
    if method not in ("annuity", "straight", "interest_only"):
        raise ValueError(f"method 無效: {method}（annuity/straight/interest_only）")
    if principal <= 0:
        raise ValueError("principal 需為正整數")
    if term_months <= 0:
        raise ValueError("term_months 需為正整數")
    if grace_months < 0 or (method != "interest_only" and grace_months >= term_months):
        raise ValueError("grace_months 需 ≥ 0 且小於期數")

    anchor = _as_date(first_payment_date)
    first_offset = 0
    if anchor is None:
        anchor = _as_date(start_date)
        if anchor is None:
            raise ValueError("start_date / first_payment_date 需至少一個有效日期（YYYY-MM-DD）")
        first_offset = 1  # 起貸日下月同日

    r = (annual_rate or 0) / 100 / 12
    n = term_months - grace_months  # 扣寬限後的還本期數
    pmt = round(principal * r / (1 - (1 + r) ** (-n))) if (r and method == "annuity") else 0
    base_principal = round(principal / n) if n else 0  # straight / annuity r=0

    rows, balance, repaid = [], principal, 0
    for i in range(1, term_months + 1):
        interest = round(balance * r) if r else 0
        if i == term_months:
            p = principal - repaid          # 末期吸尾差（Σprincipal == principal 恆成立）
        elif method == "interest_only" or i <= grace_months:
            p = 0
        elif method == "annuity" and r:
            p = pmt - interest
        else:
            p = base_principal
        repaid += p
        balance -= p
        rows.append({"period_no": i,
                     "due_date": _monthly_due_date(anchor, first_offset + i - 1),
                     "principal_due": p, "interest_due": interest})
    return rows


def iter_loan_interest(loan_payments, mset):
    """期間內每期利息的認列謂詞（單一來源）→ yield (payment_row, interest)。

    權責：按攤還表 due_date 落在期間內的期別認列利息（不管繳沒繳）；金額 0 不吐。
    loan_interest_total 求和它、drilldown 展開它 —— 兩處不再各抄一份認列規則。
    """
    for p in loan_payments:
        if month_of(p.get("due_date")) not in mset:
            continue
        interest = int(p.get("interest_due") or 0)
        if interest:
            yield p, interest


def loan_interest_total(loan_payments, mset) -> int:
    """期間利息費用 — 進損益「業外支出／利息費用」。繳款收支（treatment='loan'）
    不進損益（否則與權責利息重複）。認列謂詞見 iter_loan_interest。"""
    return sum(interest for _p, interest in iter_loan_interest(loan_payments, mset))


def loan_outstanding_rows(loans, loan_payments, as_of_month: str) -> list:
    """BS 非流動負債：逐筆貸款餘額 [{key, label, amount}]。

    outstanding = 起始本金（opening_balance 或 principal）− Σ「已繳且繳款月
    ≤ as_of」期別的 principal_due — 單純看繳款事實（權責上未繳到期本金仍是
    負債，不因逾期而消失）。起貸月晚於 as_of 的貸款不列（尚未成立）；
    餘額 0（已還清）不出列。
    """
    paid_by_loan: dict = {}
    for p in loan_payments:
        if (p.get("status") or "") != "paid":
            continue
        m = month_of(p.get("paid_at")) or month_of(p.get("due_date"))
        if as_of_month and m and m > as_of_month:
            continue
        lid = p.get("loan_id")
        paid_by_loan[lid] = paid_by_loan.get(lid, 0) + int(p.get("principal_due") or 0)
    out = []
    for ln in loans:
        sm = month_of(ln.get("start_date"))
        if sm and as_of_month and sm > as_of_month:
            continue
        base = int(ln.get("opening_balance") or ln.get("principal") or 0)
        amount = base - paid_by_loan.get(ln.get("id"), 0)
        if amount:
            out.append({"key": f"loan:{ln.get('id')}",
                        "label": ln.get("name") or "銀行貸款", "amount": amount})
    return out


# ── 發票/收支小工具 ──────────────────────────────────────────

def invoice_ex_tax(inv: dict) -> int:
    """發票未稅額：amount_ex_tax 缺值（None/0）→ round(amount_total / 1.05)。"""
    ex = inv.get("amount_ex_tax")
    if ex:
        return int(ex)
    return round(int(inv.get("amount_total") or 0) / 1.05)


def invoice_tax(inv: dict) -> int:
    """發票稅額：tax_amount → 含稅−未稅 → round(total×5/105) 三層 fallback。"""
    t = inv.get("tax_amount")
    if t:
        return int(t)
    total = int(inv.get("amount_total") or 0)
    ex = inv.get("amount_ex_tax")
    if ex:
        return total - int(ex)
    return total - round(total / 1.05)


def passthrough_fee_income(inv: dict) -> int:
    """代開發票真正留在公司的錢（進損益的業外收入）。

        面額(含稅) − 應匯給代開人 − 這張發票的銷項稅額

    🔴 這支存在的理由是一個實際的錯帳（owner 2026-08-21 發現）：
    `commission` 這個欄位名字騙人 —— 它存的是 `passthrough_commission()` 算出來的
    **要匯給代開人的錢**（面額 × (1 − 費率)），不是手續費。build_pnl 卻整筆當成
    「代開手續費收入」記進業外收入，於是 2026-08 的業外收入是 927,222、
    稅前淨利虛增 846,594（真正的 8% 手續費只有 80,628）。

    🔴 為什麼還要再扣銷項稅（owner 2026-08-21 拍板）：代開的對方是個人、
    開不出發票給公司抵進項（那常常正是他要代開的原因），所以整張發票的
    銷項稅由公司自己吞。8% 的手續費裡有一大塊是替人繳的稅 ——
    8 月實收 80,628、扣掉 47,993 的稅只剩 32,635，毛利率從 8% 掉到 3.2%。
    這件事本來看不見，現在報表上會講出來。

    ⚠ 這個假設哪天變了（對方開始給憑證）就要改這裡：那時公司只負擔自己那
    8% 裡的 5%，回到 `(面額 − 應匯) ÷ 1.05`。

    非代開類別回 0 —— 呼叫端不必先判一次。
    """
    if not is_passthrough_category(inv.get("category") or ""):
        return 0
    total = int(inv.get("amount_total") or 0)
    payout = int(inv.get("commission") or 0)
    if not total or not payout:
        return 0
    return total - payout - invoice_tax(inv)


# ── 發票款項狀態：整個系統的正本 ────────────────────────────────────
#
# 一般收款發票：未收款 ──▶ 已收款（收到錢就結束了）
# 代開發票    ：未收款 ──▶ 待撥款 ──▶ 已撥款
#               （過路錢：收到錢只是換公司欠代開人，撥出去才結束）
#
# 🔴 這組字**只在這裡列一次**。呼叫端各寫各的字串，漏一個就會把收到的錢算成
# 沒收到 —— 實測咬過：「已轉撥」不在應收的排除清單裡，應收虛增成 4.7 倍。
# 前端不該自己判「收到了沒」：後端在回應裡直接給旗標（見 group_receivables 的
# collected、_to_invoice_dict 的 settled），前端只讀旗標。
INVOICE_RECEIVED = "已收款"           # 一般發票：錢收到了，結束
INVOICE_PENDING_REMIT = "待撥款"      # 代開：錢收到了，還沒轉給代開人
INVOICE_REMITTED = "已撥款"           # 代開：錢已經轉給代開人
_INVOICE_REMITTED_LEGACY = "已轉撥"   # 2026-08-21 改名前的舊字（資料已改，讀舊備份時還會遇到）

# 「錢已經收進來了」—— 判斷還算不算應收、要不要再標一次收款，一律用整組。
INVOICE_COLLECTED_STATUSES = (INVOICE_RECEIVED, INVOICE_PENDING_REMIT,
                              INVOICE_REMITTED, _INVOICE_REMITTED_LEGACY)
# 代開收到錢的兩種寫法（改名前後）—— 觸發待請款同步、付款收尾時比對用
INVOICE_PASSTHROUGH_COLLECTED = (INVOICE_PENDING_REMIT, INVOICE_RECEIVED)


# 代開＝過路發票（公司幫人開、錢再轉出去）。這個判定決定三件事：走哪條狀態
# 生命週期、要不要自動生一張應付的請款單、commission 算不算業外收入。
# 🔴 本來有三種寫法（`"代開" in category` 的子字串比對兩處、集合比對一處、
# 前端再一份），彼此不一致：一個叫「代開」的類別會走過路狀態、生請款單，
# 卻沒有科目對映也不進業外收入。
INVOICE_PASSTHROUGH_CATEGORIES = ("內部代開", "外部代開")


def is_passthrough_category(category: str) -> bool:
    return (category or "") in INVOICE_PASSTHROUGH_CATEGORIES


# 代開的手續費率（%）。應匯給代開人的錢 = 面額 × (1 − 費率)。
# 🔴 這組數字本來一份在**每個使用者的 localStorage**、一份寫死在後端（0.92）——
# 於是：兩台電腦設不同費率就算出不同的應匯金額；外部代開畫面說 10%、實際產出的
# 應付款卻是 8%；而從手機登記頁、CSV 匯入、對帳單匯入建的發票根本沒跑那段 JS。
# 費率是公司政策，跟申請人名單一樣本來就該存在伺服器（見 get_invoice_applicants）。
PASSTHROUGH_FEE_RATES = {"內部代開": 8.0, "外部代開": 10.0}


def passthrough_commission(amount_total, category: str, rates: dict | None = None) -> int:
    """代開發票要匯回給代開人的金額 = 面額 × (1 − 費率)。

    rates 給後台設定用（settings 的 invoice_fee_rates）；沒給就用預設。
    非代開類別回 0 —— 呼叫端不必先判一次。
    """
    total = int(amount_total or 0)
    if not total or not is_passthrough_category(category):
        return 0
    pct = float((rates or {}).get(category, PASSTHROUGH_FEE_RATES.get(category, 8.0)))
    return round(total * (1 - pct / 100))


def recognize_bank_fee(expense, bank_fee, fee) -> tuple:
    """認列匯費：從 expense **搬**進 bank_fee，回 (新 expense, 新 bank_fee)。

    🔴 不變量是「總流出不變」。bank_fee 是**外加**在 expense 之上的
    （cash_entry_flow = deposit − expense − bank_fee − claim；收支明細列表的
    支出欄顯示 expense + bank_fee），所以只把 fee 寫上去而不從 expense 扣，
    帳上就會多流出一筆 —— 反而製造新的勾稽差額。

    2026-08-22 我就是這樣寫錯的（v2.4.143 上線一小時後才發現，v2.4.144 修）。
    當時的 e2e 只斷言 bank_fee == 10，錯的實作照樣過 —— **要釘不變量，
    不是釘單一欄位**。所以這條規則現在住在這裡，有自己的單元測試。

    用總流出當基準也讓重複呼叫是冪等的。
    """
    total_out = int(expense or 0) + int(bank_fee or 0)
    fee = int(fee or 0)
    if fee < 0:
        raise ValueError("匯費不能是負的")
    if fee > total_out:
        raise ValueError(f"匯費 {fee:,} 比這筆的總流出 {total_out:,} 還大")
    return total_out - fee, fee


def recognize_receipt_fee(deposit, bank_fee, fee) -> tuple:
    """收款側認列匯費：把 fee **補回** deposit，回 (新 deposit, 新 bank_fee)。

    🔴 不變量是「淨流入不變」（deposit − bank_fee）。銀行實際入帳多少，不會因為
    我們認不認列匯費而改變 —— 客戶匯 149,900、銀行只入 149,870，那 30 元是匯出行
    扣走的：發票要算收齊（149,870 + 30），帳戶只能增加 149,870。

    所以要把 deposit 補成**客戶實際付的金額**，再把 fee 掛上 bank_fee；
    只寫 bank_fee 不補 deposit 的話（cash_entry_flow = deposit − expense
    − bank_fee − claim）帳戶餘額會少 30，而對帳工作台比的就是淨流
    （/statement-lines/{id}/match），那一列從此永遠配不上。

    這是 recognize_bank_fee 的鏡像：付款側守「總流出不變」（從 expense 搬出來），
    收款側守「淨流入不變」（往 deposit 補回去）。兩邊都用淨額當基準 → 冪等。
    """
    net_in = int(deposit or 0) - int(bank_fee or 0)
    fee = int(fee or 0)
    if fee < 0:
        raise ValueError("匯費不能是負的")
    return net_in + fee, fee


# 兩側只差三件事：容差站哪一邊、名詞、實際金額怎麼稱呼。
# 🔴 階梯（empty / ok / fee / over / under）刻意只有一份 —— 之前是兩支各寫一遍，
#    於是同一個 key（diff）在兩側是相反的正負號，前端得靠 `check.received != null
#    ? … : check.paid != null ? …` 猜自己在哪一側。容差政策一改（例如改成百分比）
#    兩份也一定會漂。
#    state 的語意兩側本來就一致：over ＝ 分配比實際**多**、under ＝ 分配比實際**少**。
_ALLOC_SIDES = {
    # fee_sign：差額往哪個方向偏、而且在容差內時，算「匯費」而不是「掛錯」
    #   收款 +1：分配比實收多 ≤ 容差 ＝ 收款方代扣了匯費
    #   付款 -1：分配比實付少 ≤ 容差 ＝ **我們**付了跨行手續費
    "receipt": {
        "noun": "發票", "actual_label": "實收", "fee_sign": +1,
        "empty": "還沒分配任何發票",
        "ok": "分配金額與實收金額相符",
        "fee": "分配比實收多 {n} 元 —— 常見於收款方代扣匯費",
        "over": "分配金額比實收多 {n:,} 元 —— 可能是這幾張發票沒有全部收齊"
                "（分期），或多掛了一張",
        "under": "分配金額比實收少 {n:,} 元 —— 還有沒掛上的發票，"
                 "或這筆收款有一部分不屬於發票",
    },
    "payment": {
        "noun": "請款單", "actual_label": "實付", "fee_sign": -1,
        "empty": "還沒分配任何請款單",
        "ok": "分配金額與實付相符",
        "fee": "實付比分配多 {n} 元 —— 多半是跨行手續費，可認列為匯費",
        "over": "分配比實付多 {n:,} 元 —— 掛太多張了",
        "under": "還有 {n:,} 元沒分配 —— 這筆匯款可能還結清了別張請款單",
    },
}


def alloc_verdict(actual: int, allocated: int, side: str = "receipt") -> dict:
    """分配金額 vs 這筆錢的實際金額 → 給人看的判讀。

    `side="receipt"`（收款掛發票）／`"payment"`（匯款掛請款單）。

    🔴 兩側的容差方向相反，這是實測出來的：
      · 收款：客戶匯 300,000、發票開 300,030 —— 收款方代扣了 30 元匯費
      · 付款：我們匯出 8,010、請款單 8,000（「陳良君英配」）—— 那 10 元是
        跨行手續費。用收款那套判會說「還有單沒掛」，害人去找一張不存在的單。

    但**只有方向與措辭不同**，階梯是同一座。容差用 FEE_TOLERANCE（同
    amount_is_settled）—— 本來這裡寫死兩個 50，調容差時會漏掉其中一邊。

    回 `fee` 時附差額金額，呼叫端可以直接寫進 bank_fee
    （見 recognize_bank_fee —— 那條路把匯費算成管理費用與現金流出）。
    """
    t = _ALLOC_SIDES[side]
    actual, allocated = int(actual or 0), int(allocated or 0)
    gap = allocated - actual          # 正 = 分配比實際多（兩側同義）
    fee = 0
    if not allocated:
        state = "empty"
    elif gap == 0:
        state = "ok"
    elif 0 < gap * t["fee_sign"] <= FEE_TOLERANCE:
        state, fee = "fee", abs(gap)
    else:
        state = "over" if gap > 0 else "under"
    return {"state": state, "actual": actual, "actual_label": t["actual_label"],
            "allocated": allocated, "gap": gap, "fee": fee,
            "message": t[state].format(n=abs(gap))}


def initial_invoice_status(payment_type: str, unpaid: bool = False) -> str:
    """開一張發票時的初始款項狀態。方向決定，不是呼叫端各自決定。

    收款＝我們要跟客戶收的錢；付款＝代開，錢是要轉出去的。
    🔴 這條規則本來散在三個入口（CSV 匯入、手機登記頁、快速新增列），
    2026-08-21 把「已轉撥」改名成「已撥款」時，手機登記頁被漏掉 —— 從手機登記的
    代開發票會拿到一個系統其他地方都不認得的舊狀態，畫面上連顏色都套不到。
    """
    pt = payment_type or ""
    if "作廢" in pt:
        return "作廢"
    if "付" in pt:
        return "未付款" if unpaid else INVOICE_REMITTED
    return "未收款" if unpaid else INVOICE_RECEIVED


def invoice_direction(payment_status: str) -> str:
    """款項狀態 → 方向（收款／付款／作廢）。

    🔴 前端本來自己判：`狀態 === 已撥款 ? '付款' : '收款'` —— 漏掉**待撥款**，
    那也是付款方向的狀態，於是從快速新增列開一張待撥款的代開發票，方向會是收款，
    然後它就會出現在應收帳款裡（代開是過路錢，不該算我們的應收）。
    """
    st = payment_status or ""
    if st == "作廢":
        return "作廢"
    if st in (INVOICE_PENDING_REMIT, INVOICE_REMITTED, _INVOICE_REMITTED_LEGACY,
              "未付款"):
        return "付款"
    return "收款"


def normalize_invoice_status(status: str) -> str:
    """把舊字換成現在的字（改名前存的、或還沒更新的客戶端送上來的）。"""
    return INVOICE_REMITTED if status == _INVOICE_REMITTED_LEGACY else status


def invoice_collected(inv: dict) -> bool:
    """收款發票是否已收現：paid_date 非空 或 狀態落在 INVOICE_COLLECTED_STATUSES。"""
    return (bool(inv.get("paid_date"))
            or (inv.get("payment_status") or "") in INVOICE_COLLECTED_STATUSES)


def out_amount(e: dict) -> int:
    """收支明細的損益流出側（expense + claim；不含 bank_fee — 匯費另列管理費）。"""
    return int(e.get("expense") or 0) + int(e.get("claim") or 0)


def in_amount(e: dict) -> int:
    """收支明細的損益流入側（deposit）。"""
    return int(e.get("deposit") or 0)


_out_amount = out_amount  # 底線別名（相容既有引用）
_in_amount = in_amount


def _pct(part, whole):
    """百分比數字（round 2 位，38.74 代表 38.74%）；分母 0 → None（前端顯示 '—'）。

    前端契約：所有 rate / pct / expense_rate / debt_ratio 都走這支 —
    毛利率/營利率/淨利率/費用率分母 = 營業收入未稅 total；
    debt_ratio 與 BS 每行 pct 分母 = 資產總計（負債/權益側也除以資產總計，
    對齊 owner Excel 呈現）。current_ratio 例外：回倍數（見 build_balance_sheet）。
    """
    if not whole:
        return None
    return round(part * 100 / whole, 2)


def map_info(cat_map: dict, source: str, category) -> dict | None:
    """cat_map 查值 → 統一成 {"treatment", "account_id"} dict（相容字串值）。"""
    val = (cat_map or {}).get((source, category or ""))
    if isinstance(val, dict):
        return val
    if val:
        return {"treatment": val, "account_id": None}
    return None


def map_account(cat_map: dict, accounts: dict, source: str, category) -> dict | None:
    info = map_info(cat_map, source, category)
    if not info:
        return None
    return (accounts or {}).get(info.get("account_id") or "")


_map_info = map_info  # 底線別名（相容既有引用）
_map_account = map_account


def expense_slot(acct: dict | None) -> tuple:
    """費用該落在哪個 (pnl_group, 行標籤)。

    科目 pnl_group 屬 料/工/費/銷售/管理/研發/業外支出/稅 → 照科目；
    其餘（未對映 / 誤映到資產負債科目 / pnl_group=None）→ 誠實掛
    「營業費用-管理 / 未歸類支出」，並由 statement_warnings 計數提醒。
    build_pnl 與 drilldown 共用此判定（單一定義處，避免兩邊漂移）。
    """
    g = (acct or {}).get("pnl_group")
    if g in COST_GROUPS or g in OPEX_GROUPS or g in ("業外支出", "稅"):
        return g, ((acct or {}).get("name") or _UNMAPPED_EXPENSE_LABEL)
    return "營業費用-管理", _UNMAPPED_EXPENSE_LABEL


# ── 損益認列迭代器（build_pnl 與 statements drilldown 共用）──

def iter_revenue_invoices(invoices, mset):
    """yield 認列為營業收入的發票（單一謂詞定義處）。

    規則：issue_status≠作廢、invoice_date 落在 mset、category=專案（缺值視同
    專案）、payment_type=收款（缺值視同收款）。代開發票的手續費屬業外，不在此。
    """
    for inv in invoices:
        if (inv.get("issue_status") or "") == "作廢":
            continue
        if month_of(inv.get("invoice_date")) not in mset:
            continue
        if (inv.get("category") or "專案") != "專案":
            continue
        if (inv.get("payment_type") or "收款") != "收款":
            continue
        yield inv


def iter_expense_items(payments, cash_entries, cat_map, accounts, mset):
    """損益費用側的單一迭代來源，yield (source, row, group, label, amount)。

      source  'payment' | 'cash'
      row     原始 dict（drilldown 取 id / 日期 / 摘要 / 狀態）
      group   expense_slot 判定的 pnl_group（含 業外支出 / 稅 — build_pnl 分流）
      label   行標籤（科目名或「未歸類支出」）
      amount  認列金額 — direct_expense 含 deposit 沖回（out−in）；unmapped 只列
              out 側（in 側走業外未歸類收入）；tax_income 為 out−in

    規則（原 build_pnl 與 drilldown 兩處手抄收斂於此）：
    - 請款單：非預支、request_date 在期間、treatment 非 transfer/passthrough
    - 收支：entry_date 在期間；treatment 屬結清（ar/ap）/預支/轉存/代開/
      營業稅者不進費用側；金額 0 的項不 yield（沖回歸零不出列）
    """
    cat_map = cat_map or {}
    accounts = accounts or {}
    for p in payments:
        if p.get("is_advance"):
            continue
        if month_of(p.get("request_date")) not in mset:
            continue
        info = map_info(cat_map, "payment", p.get("category"))
        if info and info.get("treatment") in NON_PNL_TREATMENTS:
            continue
        amount = int(p.get("amount") or 0)
        if not amount:
            continue
        group, label = expense_slot(
            map_account(cat_map, accounts, "payment", p.get("category")))
        yield "payment", p, group, label, amount
    for e in cash_entries:
        if month_of(e.get("entry_date")) not in mset:
            continue
        t = classify_cash_entry(e, cat_map)
        if t == "tax_income":
            amount = out_amount(e) - in_amount(e)
            if amount:
                yield "cash", e, "稅", "營所稅", amount
        elif t == "direct_expense":
            amount = out_amount(e) - in_amount(e)
            if amount:
                group, label = expense_slot(
                    map_account(cat_map, accounts, "cash", e.get("category")))
                yield "cash", e, group, label, amount
        elif t == "unmapped":
            amount = out_amount(e)
            if amount:
                group, label = expense_slot(None)
                yield "cash", e, group, label, amount


def bank_fee_total(cash_entries, mset) -> int:
    """期間匯費合計 — 任何收支（含 transfer/advance）的 bank_fee 都是真實費用，
    損益彙總成「營業費用-管理／銀行手續費」單列；drilldown 出同額 derived 列。"""
    return sum(int(e.get("bank_fee") or 0) for e in cash_entries
               if month_of(e.get("entry_date")) in mset)


# ── 營業稅位置 ───────────────────────────────────────────────

def invoice_vat_side(inv) -> str | None:
    """發票的營業稅方向（單一謂詞定義處）— vat_position 摘要與 tax_package 明細共用。

    作廢（issue_status）→ None（不計）；payment_type=='付款' → 'input'（進項）；
    其餘（收款/缺值/任何第三種值）→ 'output'（銷項）。兩處若各自 elif 判定，
    第三種 payment_type 值會在摘要算銷項、明細卻兩邊皆漏 → 數字對不上，故收斂於此。
    """
    if (inv.get("issue_status") or "") == "作廢":
        return None
    return "input" if (inv.get("payment_type") or "收款") == "付款" else "output"


def resolve_client_name(inv, project_client_map) -> str:
    """發票 → 客戶名（歸戶政策單一來源）：project_id→客戶代稱 → 抬頭(company_name)
    → 「未指定」。集中度/未來按客戶的報表共用，避免各處重寫 fallback 鏈而漂移。"""
    pid = inv.get("project_id")
    if pid:
        name = (project_client_map.get(pid) or "").strip()
        if name:
            return name
    return (inv.get("company_name") or "").strip() or "未指定"


def vat_position(invoices, cash_entries, cat_map=None, months=None) -> dict:
    """營業稅位置：銷項（收款發票稅額）− 進項（付款發票稅額）− 已繳（tax_vat 收支）。

    months=None → 不設期間（BS 用：baseline 起累計時 caller 傳 baseline..as_of
    的月清單）。作廢發票（issue_status）不計。已繳額 = tax_vat 收支的
    流出−流入（退稅沖回）。net = output − input − paid（正 = 還欠政府）。
    """
    mset = set(months) if months is not None else None
    output = input_ = 0
    for inv in invoices:
        side = invoice_vat_side(inv)
        if side is None:
            continue
        if mset is not None and month_of(inv.get("invoice_date")) not in mset:
            continue
        t = invoice_tax(inv)
        if side == "input":
            input_ += t
        else:
            output += t
    paid = 0
    for e in cash_entries:
        if mset is not None and month_of(e.get("entry_date")) not in mset:
            continue
        if classify_cash_entry(e, cat_map or {}) == "tax_vat":
            paid += out_amount(e) - in_amount(e)
    return {"output": output, "input": input_, "paid": paid,
            "net": output - input_ - paid}


# ── 期末部位（AR / AP / 銀行餘額）────────────────────────────

def ar_open_invoices(invoices, baseline_month=None, as_of_month=None) -> list:
    """未收應收發票清單（BS 應收帳款 = Σ amount_total；drilldown 共用）。

    範圍：payment_type=收款、category=專案、issue_status≠作廢、未收現
    （invoice_collected 為 False 且 payment_status≠作廢）、invoice_date 落在
    baseline..as_of（任一端 None = 不設界）。代開發票不計 AR（passthrough，
    手續費收入已於損益認列）— v1 決策，見 build_pnl docstring。
    """
    out = []
    for inv in invoices:
        if (inv.get("issue_status") or "") == "作廢":
            continue
        if (inv.get("payment_status") or "") == "作廢":
            continue
        if (inv.get("payment_type") or "收款") != "收款":
            continue
        if (inv.get("category") or "專案") != "專案":
            continue
        if invoice_collected(inv):
            continue
        m = month_of(inv.get("invoice_date"))
        if not m:
            continue
        if baseline_month and m < baseline_month:
            continue
        if as_of_month and m > as_of_month:
            continue
        out.append(inv)
    return out


def ar_overdue_amount(invoices, baseline_month=None, days=60) -> int:
    """應收逾期金額：未收發票中 invoice_date 距今超過 days 天者的含稅合計。

    白話解讀「開立超過 60 天未收，建議催收」的單一規則處（60 天門檻在此）。
    """
    total = 0
    for inv in ar_open_invoices(invoices, baseline_month, None):
        d = inv.get("invoice_date")
        if not isinstance(d, datetime):
            continue
        now = datetime.now(timezone.utc) if d.tzinfo else datetime.now()
        if (now - d).days > days:
            total += int(inv.get("amount_total") or 0)
    return total


def ap_open_payments(payments, cat_map=None, baseline_month=None, as_of_month=None) -> list:
    """未付請款單清單（BS 應付帳款 = Σ amount；drilldown 共用）。

    範圍：非預支（is_advance falsy）、payment_status≠已付款、request_date 落在
    baseline..as_of。category 對映 treatment=transfer 者（零用金/轉存）非對外
    負債不計；passthrough（發票代開）是真的要付出去的錢 → 計入。
    """
    out = []
    for p in payments:
        if p.get("is_advance"):
            continue
        if (p.get("payment_status") or "") == "已付款":
            continue
        info = map_info(cat_map or {}, "payment", p.get("category"))
        if info and info.get("treatment") == "transfer":
            continue
        m = month_of(p.get("request_date"))
        if not m:
            continue
        if baseline_month and m < baseline_month:
            continue
        if as_of_month and m > as_of_month:
            continue
        out.append(p)
    return out


# ── 股東往來（owner 2026-08-21）─────────────────────────────────
#
# owner：「多三個帳戶，是股東向的借款或是股東放在公司裡的錢。需要股東支付的費用
# 可以從這裡扣款，匯給股東的費用這裡則會增加。」方向已與 owner 確認：
#
#     餘額 ＝ **公司欠股東多少**
#     股東墊付／把錢放進公司 → deposit → 餘額增加（公司欠款變多）
#     公司匯還股東           → expense → 餘額減少
#
# 🔴 這種帳戶**不是現金**。混進 bank_lines 的話，資產負債表的現金會憑空多出
# 股東墊付的錢（那些錢從來沒進過公司的銀行帳戶），三表全部失真。
#
# 借款與投資款分兩種（owner「分開沒問題」），因為它們在報表上落在不同區塊：
#     shareholder_loan    股東借款   → **負債**（其他應付款－股東往來）
#     shareholder_capital 股東投資款 → **權益**（股東投入的資本）
# 分不清楚時用借款 —— 未經增資登記的錢本來就還是往來，不是股本。
SHAREHOLDER_KINDS = ("shareholder_loan", "shareholder_capital")


def is_shareholder_kind(kind) -> bool:
    return (kind or "") in SHAREHOLDER_KINDS


def split_bank_lines(bank_accounts, lines) -> dict:
    """把 bank_balances_asof 的結果依帳戶性質拆三堆。

    現金／股東借款／股東投資款 落在資產負債表的**三個不同區塊**，
    一起丟進現金就是把負債與權益當成資產。
    """
    kind_of = {b.get("id"): (b.get("acct_kind") or "bank") for b in bank_accounts}
    out = {"cash": [], "shareholder_loan": [], "shareholder_capital": []}
    for ln in lines:
        k = kind_of.get(ln.get("id"), "bank")
        out[k if k in SHAREHOLDER_KINDS else "cash"].append(ln)
    return out


def bank_balances_asof(bank_accounts, cash_entries, as_of_month: str) -> list:
    """各帳戶推導餘額（期初 + entry_date 月 ≤ as_of 的掛帳流水）。

    未填日期的收支無法定位月份 → 不計（statement_warnings 會計數提醒）。
    帳戶 opening_balance 一律視為期間前既有（opening_date 早於 as_of 的
    校驗不在此做 — 設定精靈統一開在基準月 1 日）。
    """
    flows = {}
    for e in cash_entries:
        aid = e.get("bank_account_id")
        if not aid:
            continue
        m = month_of(e.get("entry_date"))
        if not m or (as_of_month and m > as_of_month):
            continue
        flows[aid] = flows.get(aid, 0) + cash_entry_flow(e)
    return [{"id": b.get("id"), "name": b.get("name") or "?",
             "amount": int(b.get("opening_balance") or 0) + flows.get(b.get("id"), 0)}
            for b in bank_accounts]


# ── 損益表 ───────────────────────────────────────────────────

def _new_pnl_prim() -> dict:
    """損益表中間彙總（可加總的原始桶 — build 與 merge 共用 finalize）。"""
    return {
        "revenue": {},   # key -> {"label", "amount"}
        "by_collection": {"collected": 0, "receivable": 0, "cash": 0},
        "cost": {},      # group -> {label: amount}
        "opex": {},
        "nonop_income": {},   # label -> amount
        "nonop_expense": {},
        "income_tax": 0,
        "vat": {"output": 0, "input": 0, "paid": 0},
    }


def _bump(d: dict, key: str, amount: int) -> None:
    d[key] = d.get(key, 0) + amount


def _bump_line(d: dict, key: str, label: str, amount: int) -> None:
    row = d.setdefault(key, {"label": label, "amount": 0})
    row["amount"] += amount


def _dispatch_slot(prim: dict, group: str, label: str, amount: int) -> None:
    """expense_slot / iter_expense_items 判定好的 (group, label, amount) 入桶。"""
    if not amount:
        return
    if group == "業外支出":
        _bump(prim["nonop_expense"], label, amount)
    elif group == "稅":
        prim["income_tax"] += amount
    elif group in COST_GROUPS:
        _bump(prim["cost"].setdefault(group, {}), label, amount)
    else:
        _bump(prim["opex"].setdefault(group, {}), label, amount)


def _dispatch_income(prim: dict, acct: dict | None, amount: int) -> None:
    if not amount:
        return
    g = (acct or {}).get("pnl_group")
    label = (acct or {}).get("name") or _UNMAPPED_INCOME_LABEL
    if g == "營業收入":
        _bump_line(prim["revenue"], "cash", "現金收入（未開票）", amount)
        prim["by_collection"]["cash"] += amount
    elif g == "業外收入":
        _bump(prim["nonop_income"], label, amount)
    else:
        _bump(prim["nonop_income"], _UNMAPPED_INCOME_LABEL, amount)


def build_pnl(months, *, invoices=(), payments=(), cash_entries=(), equipment=(),
              advance_expenses=(), loan_payments=(), cat_map=None,
              accounts=None) -> dict:
    """損益表（權責認列，期間 = 月集合；各來源在函式內按月過濾）。

    認列規則（階段三規格落地）：
    - 營業收入 = 收款發票（payment_type=收款、issue_status≠作廢、category=專案）
      未稅額 by invoice_date 月；by_collection 依收現狀態拆 已收/應收，
      另加 direct_income 現金收款（對映到營業收入科目、未開票）單列 cash。
    - 業外收入 = 代開發票的**淨手續費**（面額 − 應匯 − 銷項稅，
      見 passthrough_fee_income；**不是** commission 欄，那欄是要匯出去的錢）
      + direct_income 收支按對映科目（利息收入等）。
    - 營業成本/費用 = ①請款單（非 is_advance，by request_date，category 走
      source='payment' 對映；transfer/passthrough 不計）②direct_expense 收支
      （by entry_date，source='cash' 對映）③器材月折舊（直線法 → 營業成本-費）
      ④預支核銷支出（見下）。
    - 去重鐵則：invoice_id/payment_request_id/advance_payment_id 硬連結的收支
      是 AR/AP/預支的「現金結清動作」不再計損益（權責認列點在發票/請款）；
      transfer（轉存）/passthrough（代開過水）/loan（貸款撥款/繳款）也不進損益。
    - 利息費用（階段四）：權責按攤還表 due_date 認列進業外支出（不管繳沒繳）
      — loan_payments 由 caller 餵 finance_loan_payments 全表；貸款繳款收支
      （treatment='loan'）只走現金流量表（科目 2400 cf_activity=financing）。
    - 預支核銷支出（CrmProjectExpense 查證結論，2026-07-11）：
      crm_project_expenses 有 advance_id 軟 FK、無支出日期欄（僅 created_at）。
      「有掛 advance_id」的支出明細：其現金對應（發款收支）treatment='advance'
      已被排除在損益外 → 計入營業成本-費（by created_at 月）不會重複。
      「未掛 advance_id」的專案雜支多與請款單/收支明細重疊（同筆錢兩處登記）
      → v1 不計，避免重複計算；caller（service）只餵 advance 掛鉤列。
    - 匯費（bank_fee）：任何收支（含 transfer/advance）的匯費都是真實費用，
      彙總成「營業費用-管理／銀行手續費」單列。
    - 未歸類：direct_expense 查無科目 → 營業費用-管理／未歸類支出；
      未歸類收入 → 業外收入／未歸類收入（statement_warnings 另計數提醒）。
    - 稅區：income_tax = tax_income 收支（近似法 — 以繳納現金入帳月認列，
      非申報所屬年度）；vat_info 為資訊列（不進損益小計）。
    - direct_expense 的 deposit（退款）沖回同科目；direct_income 的支出亦然。

    輸出 shape 見 CLAUDE/前端契約（revenue/cost/gross/opex/operating/
    non_operating/pretax/tax/net/monthly_avg）。rate 均為百分比 1 位小數，
    營收 0 時為 None。
    """
    cat_map = cat_map or {}
    accounts = accounts or {}
    mset = set(months)
    prim = _new_pnl_prim()

    for inv in iter_revenue_invoices(invoices, mset):
        ex = invoice_ex_tax(inv)
        _bump_line(prim["revenue"], "invoiced", "開立發票營收", ex)
        key = "collected" if invoice_collected(inv) else "receivable"
        prim["by_collection"][key] += ex

    for inv in invoices:  # 代開手續費（業外收入）
        if (inv.get("issue_status") or "") == "作廢":
            continue
        if month_of(inv.get("invoice_date")) not in mset:
            continue
        if is_passthrough_category(inv.get("category") or "專案"):
            # 🔴 不是 commission —— 那欄是「要匯出去的錢」。見 passthrough_fee_income。
            c = passthrough_fee_income(inv)
            if c:
                _bump(prim["nonop_income"], "代開手續費（已扣銷項稅）", c)

    # 費用側（請款 + direct_expense/unmapped/tax_income 收支）單一迭代來源
    for _src, _row, group, label, amount in iter_expense_items(
            payments, cash_entries, cat_map, accounts, mset):
        _dispatch_slot(prim, group, label, amount)

    for e in cash_entries:  # 收入側（direct_income + unmapped 的 deposit）
        if month_of(e.get("entry_date")) not in mset:
            continue
        t = classify_cash_entry(e, cat_map)
        if t == "direct_income":
            acct = map_account(cat_map, accounts, "cash", e.get("category"))
            _dispatch_income(prim, acct, in_amount(e))
            _dispatch_income(prim, acct, -out_amount(e))
        elif t == "unmapped":
            _dispatch_income(prim, None, in_amount(e))

    fee = bank_fee_total(cash_entries, mset)
    if fee:
        _bump(prim["opex"].setdefault("營業費用-管理", {}), _BANK_FEE_LABEL, fee)

    dep = sum(r["amount"] for r in depreciation_rows(equipment, months))
    if dep:
        _bump(prim["cost"].setdefault("營業成本-費", {}), _DEPRECIATION_LABEL, dep)

    adv = sum(int(x.get("amount") or 0) for x in advance_expenses
              if month_of(x.get("date")) in mset)
    if adv:
        _bump(prim["cost"].setdefault("營業成本-費", {}), _ADVANCE_EXPENSE_LABEL, adv)

    li = loan_interest_total(loan_payments, mset)
    if li:
        _bump(prim["nonop_expense"], _LOAN_INTEREST_LABEL, li)

    v = vat_position(invoices, cash_entries, cat_map, months)
    prim["vat"] = {"output": v["output"], "input": v["input"], "paid": v["paid"]}
    return _finalize_pnl(prim, max(len(mset), 1))


def _finalize_pnl(prim: dict, n_months: int) -> dict:
    rev_lines = [{"key": k, "label": r["label"], "amount": r["amount"]}
                 for k, r in prim["revenue"].items() if r["amount"]]
    rev_lines.sort(key=lambda x: -x["amount"])
    revenue_total = sum(x["amount"] for x in rev_lines)

    def _groups(bucket, group_names, drill_side):
        out, total = [], 0
        for g in group_names:
            lines = [{"label": lb, "amount": a}
                     for lb, a in (bucket.get(g) or {}).items() if a]
            lines.sort(key=lambda x: -x["amount"])
            gt = sum(x["amount"] for x in lines)
            label = g.split("-", 1)[-1]
            out.append({"group": g, "label": label, "total": gt, "lines": lines,
                        "drill": f"{drill_side}.{label}"})  # ∈ VALID_DRILL_KINDS
            total += gt
        return out, total

    cost_groups, cost_total = _groups(prim["cost"], COST_GROUPS, "cost")
    opex_groups, opex_total = _groups(prim["opex"], OPEX_GROUPS, "opex")
    gross = revenue_total - cost_total
    operating = gross - opex_total
    nonop_inc = sorted(({"label": k, "amount": a}
                        for k, a in prim["nonop_income"].items() if a),
                       key=lambda x: -x["amount"])
    nonop_exp = sorted(({"label": k, "amount": a}
                        for k, a in prim["nonop_expense"].items() if a),
                       key=lambda x: -x["amount"])
    nonop_total = sum(x["amount"] for x in nonop_inc) - sum(x["amount"] for x in nonop_exp)
    pretax = operating + nonop_total
    income_tax = prim["income_tax"]
    net = pretax - income_tax
    v = prim["vat"]
    return {
        "revenue": {"total": revenue_total, "lines": rev_lines,
                    "by_collection": dict(prim["by_collection"]),
                    "drill": "revenue"},
        "cost": {"total": cost_total, "groups": cost_groups},
        "gross": {"amount": gross, "rate": _pct(gross, revenue_total)},
        "opex": {"total": opex_total, "groups": opex_groups},
        "operating": {"amount": operating, "rate": _pct(operating, revenue_total),
                      "expense_rate": _pct(opex_total, revenue_total)},
        "non_operating": {"income": nonop_inc, "expense": nonop_exp,
                          "total": nonop_total},
        "pretax": pretax,
        "tax": {"income_tax": income_tax,
                "vat_info": {"output": v["output"], "input": v["input"],
                             "paid": v["paid"],
                             "net": v["output"] - v["input"] - v["paid"]}},
        "net": {"amount": net, "rate": _pct(net, revenue_total)},
        "monthly_avg": {"revenue": round(revenue_total / n_months),
                        "cost": round(cost_total / n_months),
                        "opex": round(opex_total / n_months)},
    }


def merge_pnl(parts, n_months: int) -> dict:
    """多份已 finalize 的損益表（鎖定月快照 + live 期間）合併為一份。

    行金額線性可加 → 走「拆回原始桶 → 重新 finalize」路，比率/月均以合併後
    總額重算（n_months = 整段期間月數，含快照月）。單一 part 也可過（等於
    以 n_months 重算月均）。
    """
    prim = _new_pnl_prim()
    for p in parts:
        for ln in p["revenue"]["lines"]:
            _bump_line(prim["revenue"], ln.get("key") or ln["label"],
                       ln["label"], ln["amount"])
        bc = p["revenue"].get("by_collection") or {}
        for k in ("collected", "receivable", "cash"):
            prim["by_collection"][k] += int(bc.get(k) or 0)
        for side in ("cost", "opex"):
            for g in p[side]["groups"]:
                for ln in g["lines"]:
                    _bump(prim[side].setdefault(g["group"], {}),
                          ln["label"], ln["amount"])
        for ln in p["non_operating"]["income"]:
            _bump(prim["nonop_income"], ln["label"], ln["amount"])
        for ln in p["non_operating"]["expense"]:
            _bump(prim["nonop_expense"], ln["label"], ln["amount"])
        prim["income_tax"] += int(p["tax"]["income_tax"] or 0)
        vi = p["tax"].get("vat_info") or {}
        for k in ("output", "input", "paid"):
            prim["vat"][k] += int(vi.get(k) or 0)
    return _finalize_pnl(prim, max(n_months, 1))


# ── 資產負債表 ───────────────────────────────────────────────

def build_balance_sheet(as_of_month: str, *, bank_lines=(), receivable_total=0,
                        advance_balance=0, equipment=(), adjustments=(),
                        payable_total=0, vat_payable=0, loan_rows=(),
                        cumulative_net=0, note_counts=None,
                        shareholder_loan_lines=(),
                        shareholder_capital_lines=()) -> dict:
    """資產負債表（as_of = 期末月月底；推導式，非複式簿記）。

    - 資產：各銀行帳戶推導餘額分列 + 應收帳款 + 員工往來-預支（未結清預支
      餘額，caller 以 compute_advance_status 即時算 — 為即時值非期末歷史值）
      + 器材淨值（除役者出表）。
    - 負債：流動 = 應付帳款 + 應付營業稅（caller 傳 baseline 起累計 net，
      再加 adj_type='vat' 的調整列 —— 見下）；
      非流動 = 銀行貸款逐筆分列（loan_rows 由 loan_outstanding_rows 算，
      階段四）。流動比率分母只算流動負債；負債比率吃負債總計。
    - 權益：期初調整（opening）+ 業主往來（owner_in − owner_out，amount 取
      正值填寫）+ 累積損益（baseline..as_of 累計淨利，caller 算）+ 其他調整
      （correction/accountant/writeoff/other 合計）。調整列按 adj_date ≤ as_of
      過濾（不設 baseline 下限 — 期初列本來就開在基準月）。
    - 🔴 adj_type='vat' 是唯一**不落權益**的調整型別：它加在應付營業稅上。
      為什麼要有它：vat_payable 是從發票推出來的（銷項−進項−已繳），發票沒記
      全的年份會算出「繳的比該繳的多」→ 負數負債。那不是政府欠你，是帳的缺口。
      補發票不可行時（owner 2026-08-23：2024–2025 帳沒記好但稅都有繳好），
      用一筆具名、有日期、有說明的調整沖平那個年代 —— 比讓報表掛著一個
      解釋不了的負數誠實，也比偷偷把負數夾成 0 誠實（那會連真的溢繳都看不見）。
    - 檢核誠實外顯：diff = 資產 −（負債+權益），≠0 時附可能原因清單
      （note_counts 來自 statement_warnings）。推導式三表在器材購置已費用化、
      預支即時值等情況天生會有 diff — 掩蓋比外顯危險。
    - 比率門檻文案照 owner Excel：負債比 <65% 資金運用效能不良、65-75 良好、
      >80 需要增資（75-80 補「偏高」過渡帶）。
    """
    current = [{"key": f"cash:{b.get('id') or i}", "label": b.get("name") or "現金",
                "amount": int(b.get("amount") or 0)}
               for i, b in enumerate(bank_lines)]
    # drill 只掛有列級明細可下鑽的行（應收/應付）— 其餘 BS 行為推導值無明細
    current.append({"key": "receivable", "label": "應收帳款",
                    "amount": int(receivable_total or 0), "drill": "receivable"})
    current.append({"key": "advance", "label": "員工往來-預支",
                    "amount": int(advance_balance or 0)})
    eq_rows = equipment_net_rows(equipment, as_of_month)
    noncurrent = [{"key": "equipment", "label": "器材淨值",
                   "amount": eq_rows["net_total"]}]
    assets_total = sum(x["amount"] for x in current) + sum(x["amount"] for x in noncurrent)
    for x in current + noncurrent:
        x["pct"] = _pct(x["amount"], assets_total)

    # 調整列先算 —— 其中 vat 型別要進負債側的應付營業稅，不是權益。
    # （按 adj_date ≤ as_of 過濾；期初列本來就開在基準月，不設下限。）
    opening = owner = other = vat_adj = 0
    for a in adjustments:
        m = month_of(a.get("adj_date"))
        if not m or (as_of_month and m > as_of_month):
            continue
        amt = int(a.get("amount") or 0)
        t = a.get("adj_type") or ""
        if t == "opening":
            opening += amt
        elif t == "owner_in":
            owner += amt
        elif t == "owner_out":
            owner -= amt
        elif t == "vat":
            vat_adj += amt
        else:
            other += amt

    liab_current = [
        {"key": "payable", "label": "應付帳款",
         "amount": int(payable_total or 0), "drill": "payable"},
        {"key": "vat_payable", "label": "應付營業稅",
         "amount": int(vat_payable or 0) + vat_adj},
    ]
    # 股東借款逐筆分列（owner 2026-08-21）。餘額＝公司欠該股東多少 —— 是負債，
    # 🔴 不可以混進上面的 bank_lines（那會讓現金憑空多出股東墊付的錢）。
    liab_current += [{"key": f"sh_loan:{x.get('id')}",
                      "label": f"股東往來－{x.get('name') or '?'}",
                      "amount": int(x.get("amount") or 0)}
                     for x in shareholder_loan_lines]
    # 非流動負債：銀行貸款逐筆分列（loan_outstanding_rows 已保證 key/label/amount；
    # BS 行為推導值無明細 drill）
    liab_noncurrent = [{"key": x["key"], "label": x["label"], "amount": x["amount"]}
                       for x in loan_rows]
    liab_current_total = sum(x["amount"] for x in liab_current)
    liab_total = liab_current_total + sum(x["amount"] for x in liab_noncurrent)
    for x in liab_current + liab_noncurrent:
        x["pct"] = _pct(x["amount"], assets_total)

    equity_lines = [
        # 股東投資款＝股東投入的資本，落在權益不是負債（與借款的差別就在這裡）
        *[{"key": f"sh_cap:{x.get('id')}",
           "label": f"股東投資款－{x.get('name') or '?'}",
           "amount": int(x.get("amount") or 0)}
          for x in shareholder_capital_lines],
        {"key": "opening", "label": "期初調整", "amount": opening},
        {"key": "owner", "label": "業主往來", "amount": owner},
        {"key": "retained", "label": "累積損益", "amount": int(cumulative_net or 0)},
        {"key": "adjustments", "label": "其他調整", "amount": other},
    ]
    equity_total = sum(x["amount"] for x in equity_lines)
    for x in equity_lines:  # 權益側 pct 分母同樣 = 資產總計（owner Excel 呈現）
        x["pct"] = _pct(x["amount"], assets_total)

    diff = assets_total - liab_total - equity_total
    notes = []
    if diff != 0:
        nc = note_counts or {}
        if nc.get("unmapped"):
            notes.append(f"{nc['unmapped']} 筆收支/請款尚未歸類科目")
        if nc.get("unassigned"):
            notes.append(f"{nc['unassigned']} 筆收支未掛銀行帳戶")
        if nc.get("undated"):
            notes.append(f"{nc['undated']} 筆收支未填日期（無法定位月份）")
        notes.append("器材購置若已走收支明細費用化、或早於基準月，其淨值會造成差額")
        notes.append("員工預支餘額為即時推導值，非期末歷史值")

    current_assets = sum(x["amount"] for x in current)
    current_liab = liab_current_total  # 流動比率分母只算流動負債（貸款屬非流動）
    current_ratio = round(current_assets / current_liab, 2) if current_liab else None
    debt_ratio = _pct(liab_total, assets_total)
    labels = {}
    if current_ratio is None:
        labels["current_ratio"] = "無流動負債"
    elif current_ratio >= 2:
        labels["current_ratio"] = "流動比率 ≥ 2：短期償債能力充足"
    elif current_ratio >= 1:
        labels["current_ratio"] = "流動比率 1–2：尚可"
    else:
        labels["current_ratio"] = "流動比率 < 1：流動資產不足以覆蓋流動負債"
    if debt_ratio is not None:
        if debt_ratio < 65:
            labels["debt_ratio"] = "負債比率 <65%：資金運用效能不良（可更積極運用資金）"
        elif debt_ratio <= 75:
            labels["debt_ratio"] = "負債比率 65–75%：良好"
        elif debt_ratio <= 80:
            labels["debt_ratio"] = "負債比率 75–80%：偏高，留意償債壓力"
        else:
            labels["debt_ratio"] = "負債比率 >80%：需要增資"

    return {
        "as_of": as_of_month,
        "assets": {"current": current, "noncurrent": noncurrent, "total": assets_total},
        "liabilities": {"current": liab_current, "noncurrent": liab_noncurrent,
                        "total": liab_total},
        "equity": {"lines": equity_lines, "total": equity_total},
        "check": {"diff": diff, "notes": notes},
        "ratios": {"current_ratio": current_ratio, "debt_ratio": debt_ratio,
                   "labels": labels},
    }


# ── 現金流量表（直接法）──────────────────────────────────────

def cash_entry_activity(entry: dict, cat_map: dict, accounts: dict):
    """單筆收支 → 現金流量活動。None = 本金不列入（transfer/advance 內部移動）。

    direct_* / loan 走對映科目的 cf_activity（'none'/查無 → operating）—
    貸款撥款/繳款（treatment='loan'）對映科目 2400 cf_activity=financing，
    自然落籌資活動且不進損益；硬連結結清（ar/ap）、稅、passthrough、
    unmapped → operating（最不錯的預設，unmapped 另由 statement_warnings 計數）。
    """
    t = classify_cash_entry(entry, cat_map or {})
    if t in INTERNAL_MOVEMENT_TREATMENTS:
        return None
    if t in ("direct_expense", "direct_income", "loan"):
        acct = map_account(cat_map or {}, accounts or {}, "cash", entry.get("category"))
        act = (acct or {}).get("cf_activity")
        if act in ("investing", "financing"):
            return act
    return "operating"


def cash_account_ids(bank_accounts):
    """哪些銀行帳戶算「現金」。None 進 → None 出（呼叫端沒給就別擋）。

    🔴 跟 split_bank_lines 是**同一條規則**：期初／期末只取 ["cash"]，迭代那側
    必須一致，否則股東往來上每記一筆就多一筆勾稽差額（v2.4.142 修過一次）。
    具名之後兩邊指向同一個述詞，加一個非現金桶（票據存款、履約保證專戶）時
    不會只改到一半。認不得的性質落到現金（同 split_bank_lines 的預設）。

    唯一的差別是**帳戶已被刪掉**的情況：那個 id 不在集合裡 → 迭代這側當非現金
    跳過。這是對的，因為餘額那側（bank_balances_asof）也是從 bank_accounts 生的，
    刪掉的帳戶連期初期末都不會出現 —— 兩側一起消失才平。
    """
    if bank_accounts is None:
        return None
    return {b.get("id") for b in bank_accounts
            if not is_shareholder_kind(b.get("acct_kind"))}


def cashflow_lines(cash_entries, months, *, cat_map=None, accounts=None,
                   bank_accounts=None):
    """現金流量表「哪幾列算數、各算多少」的**唯一正本** → (rows, stats)。

    rows：[{"entry", "activity", "amount", "treatment", "is_fee"}]
    stats：{"advance_net", "transfer_net", "unassigned", "noncash"}（給註記用）

    🔴 為什麼要有這一支：表上的數字（build_cashflow）與點進去的明細
    （statements_drilldown）本來各跑一次同一座階梯，於是每補一條規則就得記得
    改兩個地方。實際發生過兩次 ——
      · 非現金帳戶（股東往來）那條只加在表上 → 兩邊差 777,000（v2.4.146 修）
      · **轉存/預支的跨行手續費**：本金不算活動、但手續費是真的流出去的錢，
        表上算了、鑽取整筆跳過 → 2026 年兩邊差 150（十筆各 15 元）。
    這支存在之後，「哪幾列算數」只有一個答案，鑽取合計恆等於表上那格。
    """
    cat_map = cat_map or {}
    accounts = accounts or {}
    mset = set(months)
    cash_ids = cash_account_ids(bank_accounts)
    rows = []
    stats = {"advance_net": 0, "transfer_net": 0, "unassigned": 0, "noncash": 0}
    for e in cash_entries:
        if month_of(e.get("entry_date")) not in mset:
            continue
        if not e.get("bank_account_id"):
            stats["unassigned"] += 1        # 不影響任何帳戶餘額
            continue
        if cash_ids is not None and e["bank_account_id"] not in cash_ids:
            stats["noncash"] += 1           # 股東往來等：不在現金總額裡
            continue
        t = classify_cash_entry(e, cat_map)
        if t in INTERNAL_MOVEMENT_TREATMENTS:
            principal = in_amount(e) - out_amount(e)
            stats["advance_net" if t == "advance" else "transfer_net"] += principal
            fee = int(e.get("bank_fee") or 0)
            if fee:
                # 本金是內部移動，手續費不是 —— 那筆錢真的離開公司了
                rows.append({"entry": e, "activity": "operating", "amount": -fee,
                             "treatment": t, "is_fee": True})
            continue
        rows.append({"entry": e, "activity": cash_entry_activity(e, cat_map, accounts),
                     "amount": cash_entry_flow(e), "treatment": t, "is_fee": False})
    return rows, stats


def build_cashflow(months, *, opening, closing, cash_entries=(),
                   cat_map=None, accounts=None, bank_accounts=None) -> dict:
    """現金流量表（直接法）。opening/closing = {"total", "by_account":[{name,amount}]}
    由 caller 以 bank_balances_asof 算（期間前一月月底 / 期末月月底）。

    規則：
    - 只計「有掛**現金類**帳戶」的收支。兩種都要排除，理由相同 ——
      它們不影響 opening/closing 的現金總額，計入就破壞恆等式：
        · 未掛帳戶（不影響任何帳戶餘額）→ 排除 + note 提醒；損益表則照計。
        · 🔴 掛在**非現金帳戶**上的（股東往來 shareholder_loan/capital）——
          期初/期末只取 split_bank_lines(...)["cash"]，這些帳戶根本不在裡面。
          🔴 帳戶清單直接吃 `bank_accounts`，**「什麼算現金」的判定就只有這一份**
          （跟 split_bank_lines 同一個述詞）。本來是呼叫端自己再做一次
          `not is_shareholder_kind(...)` 的 comprehension —— 那等於同一條規則
          兩份，之後多一個非現金桶（票據存款、履約保證專戶）時它會從期初期末
          消失、卻不會從迭代裡消失，差額靜靜長出來。
          不給 `bank_accounts` 時退回舊行為（只擋未掛帳戶），給舊呼叫端與單元測試用。
    - transfer/advance 本金不列入活動（規格：內部移動）；其 bank_fee 是真實
      流出 → 計入 operating。轉存若兩邊成對登記，本金跨帳戶互抵不影響總額；
      未成對差額與預支往來淨流都寫進 check.notes 解釋 diff 來源。
    - 每筆流量 = cash_entry_flow（deposit − expense − bank_fee − claim），
      與餘額推導同一公式 → 乾淨帳（無預支/未成對轉存）時恆等式自然成立。
    - 器材購入不另計：規格 v1 決策 — 現金流全部由收支明細 classify 派生，
      科目 cf_activity=investing 者（如對映到 1500 的 category）自然落
      investing；直接用 equipment 表另計會與收支重複。
    - 自檢：check.diff = closing.total − opening.total − net，≠0 誠實外顯。
    """
    acts = {"operating": 0, "investing": 0, "financing": 0}
    rows, stats = cashflow_lines(cash_entries, months, cat_map=cat_map,
                                 accounts=accounts, bank_accounts=bank_accounts)
    for r in rows:
        acts[r["activity"]] += r["amount"]
    advance_net = stats["advance_net"]
    transfer_net = stats["transfer_net"]
    unassigned = stats["unassigned"]
    noncash = stats["noncash"]
    net = acts["operating"] + acts["investing"] + acts["financing"]
    # closing − (opening + net)：正 = 期末實際比活動推算多
    diff = int(closing.get("total") or 0) - (int(opening.get("total") or 0) + net)
    notes = []
    if advance_net:
        notes.append(f"員工預支往來淨流 {advance_net:+,} 元未列入活動分類（內部移動）")
    if transfer_net:
        notes.append(f"帳戶間轉存未完全成對，差額 {transfer_net:+,} 元")
    if unassigned:
        notes.append(f"{unassigned} 筆未掛帳戶收支未列入")
    if noncash:
        notes.append(f"{noncash} 筆掛在非現金帳戶（股東往來）的收支未列入現金流")
    return {"opening": opening, "closing": closing,
            "operating": acts["operating"], "investing": acts["investing"],
            "financing": acts["financing"], "net": net,
            "drills": dict(CF_ACTIVITY_DRILLS),
            "check": {"diff": diff, "notes": notes}}


def merge_cf(parts, opening, closing) -> dict:
    """多份現金流量表（鎖定月快照 + live 期間）合併：三活動線性相加；
    opening/closing 由 caller 以整段期間重算傳入（餘額推導不受鎖月影響）；
    check.diff 以合併後數字重算；notes 去重串接。"""
    acts = {"operating": 0, "investing": 0, "financing": 0}
    notes = []
    for p in parts:
        for k in acts:
            acts[k] += int(p.get(k) or 0)
        for line in ((p.get("check") or {}).get("notes") or []):
            if line not in notes:
                notes.append(line)
    net = acts["operating"] + acts["investing"] + acts["financing"]
    diff = int(closing.get("total") or 0) - int(opening.get("total") or 0) - net
    return {"opening": opening, "closing": closing, **acts, "net": net,
            "drills": dict(CF_ACTIVITY_DRILLS),
            "check": {"diff": diff, "notes": notes}}


# ── 檢核警示 + 白話解讀 ──────────────────────────────────────

def statement_warnings(cash_entries, payments, cat_map, months=None) -> dict:
    """報表品質警示：未歸類（cash+payment category 查無對映）、未掛帳戶、
    未填日期（undated 不受期間過濾 — 沒日期本來就進不了任何期間）。"""
    mset = set(months) if months is not None else None
    unmapped = unassigned = undated = 0
    for e in cash_entries:
        m = month_of(e.get("entry_date"))
        if m is None:
            undated += 1
            continue
        if mset is not None and m not in mset:
            continue
        if classify_cash_entry(e, cat_map or {}) == "unmapped":
            unmapped += 1
        if not e.get("bank_account_id"):
            unassigned += 1
    for p in payments:
        if p.get("is_advance"):
            continue
        m = month_of(p.get("request_date"))
        if mset is not None and (m is None or m not in mset):
            continue
        if not map_info(cat_map or {}, "payment", p.get("category")):
            unmapped += 1
    messages = []
    if unmapped:
        messages.append(f"{unmapped} 筆收支/請款尚未歸類科目（暫列未歸類）")
    if unassigned:
        messages.append(f"{unassigned} 筆收支未掛銀行帳戶（不列入現金流量表）")
    if undated:
        messages.append(f"{undated} 筆收支未填日期（無法定位月份，不列入報表）")
    return {"unmapped": unmapped, "unassigned": unassigned, "undated": undated,
            "messages": messages}


def statement_interpretation(pnl, bs, cf, *, ar_over_60=0) -> list:
    """三表白話解讀（規則句，資料不足的句子不出，上限 8 條）。"""
    out = []
    rev = pnl["revenue"]["total"]
    if rev > 0:
        gross = pnl["gross"]["amount"]
        out.append(f"本期每收 100 元營收，付完直接製作成本剩約 {round(gross * 100 / rev)} 元"
                   f"（毛利率 {pnl['gross']['rate']}%）")
        net = pnl["net"]["amount"]
        if net >= 0:
            out.append(f"扣完全部開銷與稅後，每 100 元營收約留下 {round(net * 100 / rev)} 元"
                       f"（淨利率 {pnl['net']['rate']}%）")
        else:
            out.append(f"本期淨虧損 {abs(net):,} 元 — 支出大於收入")
        recv = pnl["revenue"]["by_collection"].get("receivable") or 0
        if recv > 0:
            out.append(f"本期營收中還有 {recv:,} 元未收款（占 {_pct(recv, rev)}%），"
                       "現金還沒真的進來")
    cash_total = sum(x["amount"] for x in (bs or {}).get("assets", {}).get("current", [])
                     if str(x.get("key", "")).startswith("cash"))
    monthly_spend = pnl["monthly_avg"]["cost"] + pnl["monthly_avg"]["opex"]
    if cash_total > 0 and monthly_spend > 0:
        out.append(f"帳上現金 {cash_total:,} 元，約可支撐 "
                   f"{round(cash_total / monthly_spend, 1)} 個月的平均開銷")
    if ar_over_60 and ar_over_60 > 0:
        out.append(f"應收帳款中有 {ar_over_60:,} 元開立超過 60 天未收，建議優先催收")
    op = (cf or {}).get("operating") or 0
    if op:
        out.append(f"本期營運現金流 {op:+,} 元"
                   f"（{'日常營運有淨現金流入' if op > 0 else '日常營運正在消耗現金'}）")
    if bs:
        dl = bs["ratios"]["labels"].get("debt_ratio")
        if dl:
            out.append(dl)
        if bs["check"]["diff"]:
            out.append(f"資產負債表檢核差額 {bs['check']['diff']:,} 元，"
                       "帳務尚有未對齊項目，數字解讀請保留餘裕")
    return out[:8]


# ═════════════════════════════════════════════════════════════════
# 階段五：儀表板 / 稅務包純函式（零 DB，配黃金測試）
# ═════════════════════════════════════════════════════════════════

# AR 帳齡固定五桶（key, 中文 label；順序 = 前端呈現順序）
_AGING_BUCKETS = (
    ("current", "未逾期"),
    ("d1_30", "1-30 天"),
    ("d31_60", "31-60 天"),
    ("d61_90", "61-90 天"),
    ("over90", "90 天以上"),
)


def _month_end_date(month: str) -> date:
    """'YYYY-MM' → 該月月底 date（帳齡基準日）。"""
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def aging_buckets(open_invoices, as_of_month: str) -> dict:
    """AR 帳齡分桶（未收款發票以 invoice_date 到 as_of 月底的天數分桶）。

    open_invoices = 未收款發票 dict 列（含 invoice_date（datetime/str）、
    amount_total（含稅）；金額用 amount_total）。天數 =（as_of 月底 − 開票日）：
    ≤0 天 current（當期未逾期）、1-30 d1_30、31-60 d31_60、61-90 d61_90、
    >90 over90。日期一律過 local_day（timestamptz 回讀 UTC → 本地）再取日；
    無法解析日期的列不計（也不進 total）。
    回 {"buckets":[{key,label,amount,count}...(固定五桶順序)], "total": int}。
    """
    ref = _month_end_date(as_of_month)
    agg = {k: {"amount": 0, "count": 0} for k, _ in _AGING_BUCKETS}
    for inv in open_invoices:
        d = _as_date(local_day(inv.get("invoice_date")))
        if d is None:
            continue
        days = (ref - d).days
        if days <= 0:
            key = "current"
        elif days <= 30:
            key = "d1_30"
        elif days <= 60:
            key = "d31_60"
        elif days <= 90:
            key = "d61_90"
        else:
            key = "over90"
        agg[key]["amount"] += int(inv.get("amount_total") or 0)
        agg[key]["count"] += 1
    buckets = [{"key": k, "label": lb, "amount": agg[k]["amount"],
                "count": agg[k]["count"]} for k, lb in _AGING_BUCKETS]
    return {"buckets": buckets, "total": sum(b["amount"] for b in buckets)}


def client_concentration(revenue_by_client, *, top=3) -> dict:
    """客戶集中度（revenue_by_client = {client_name: amount}，services 端已加總）。

    clients 依金額 desc；pct = amount/total*100（百分比數字，1 位小數）。
    top_n_pct = 前 top 家金額佔比；warn = top_n_pct > 50。
    total=0 → 各 pct 0.0、top_n_pct=0.0、warn False。
    回 {"clients":[{name,amount,pct}...], "top_n":top, "top_n_pct":float, "warn":bool}。
    """
    total = sum(int(v or 0) for v in revenue_by_client.values())
    clients = sorted(
        ({"name": name, "amount": int(amt or 0),
          "pct": round(int(amt or 0) * 100 / total, 1) if total else 0.0}
         for name, amt in revenue_by_client.items()),
        key=lambda x: -x["amount"])
    top_amount = sum(c["amount"] for c in clients[:top])
    top_n_pct = round(top_amount * 100 / total, 1) if total else 0.0
    return {"clients": clients, "top_n": top, "top_n_pct": top_n_pct,
            "warn": top_n_pct > 50}


def runway_months(cash_total, avg_monthly_net) -> float | None:
    """現金跑道（月）：帳上現金還能支撐幾個月的平均淨消耗。

    avg_monthly_net ≥ 0（不燒錢）→ None（前端顯示「充裕/無限」）；
    否則 round(cash_total / (-avg_monthly_net), 1)；cash_total ≤ 0 → 0.0。
    """
    if (avg_monthly_net or 0) >= 0:
        return None
    if (cash_total or 0) <= 0:
        return 0.0
    return round(cash_total / (-avg_monthly_net), 1)
