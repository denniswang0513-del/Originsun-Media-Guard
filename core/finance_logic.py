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
            dt = dt.astimezone()
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
        return dt.astimezone().replace(tzinfo=None)
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


def invoice_collected(inv: dict) -> bool:
    """收款發票是否已收現：paid_date 非空 或 payment_status='已收款'。"""
    return bool(inv.get("paid_date")) or (inv.get("payment_status") or "") == "已收款"


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

def vat_position(invoices, cash_entries, cat_map=None, months=None) -> dict:
    """營業稅位置：銷項（收款發票稅額）− 進項（付款發票稅額）− 已繳（tax_vat 收支）。

    months=None → 不設期間（BS 用：baseline 起累計時 caller 傳 baseline..as_of
    的月清單）。作廢發票（issue_status）不計。已繳額 = tax_vat 收支的
    流出−流入（退稅沖回）。net = output − input − paid（正 = 還欠政府）。
    """
    mset = set(months) if months is not None else None
    output = input_ = 0
    for inv in invoices:
        if (inv.get("issue_status") or "") == "作廢":
            continue
        if mset is not None and month_of(inv.get("invoice_date")) not in mset:
            continue
        t = invoice_tax(inv)
        if (inv.get("payment_type") or "收款") == "付款":
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
    - 業外收入 = 代開發票 commission（內部/外部代開，by invoice_date）
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
        if (inv.get("category") or "專案") in ("內部代開", "外部代開"):
            c = int(inv.get("commission") or 0)
            if c:
                _bump(prim["nonop_income"], "代開手續費收入", c)

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
                        cumulative_net=0, note_counts=None) -> dict:
    """資產負債表（as_of = 期末月月底；推導式，非複式簿記）。

    - 資產：各銀行帳戶推導餘額分列 + 應收帳款 + 員工往來-預支（未結清預支
      餘額，caller 以 compute_advance_status 即時算 — 為即時值非期末歷史值）
      + 器材淨值（除役者出表）。
    - 負債：流動 = 應付帳款 + 應付營業稅（caller 傳 baseline 起累計 net）；
      非流動 = 銀行貸款逐筆分列（loan_rows 由 loan_outstanding_rows 算，
      階段四）。流動比率分母只算流動負債；負債比率吃負債總計。
    - 權益：期初調整（opening）+ 業主往來（owner_in − owner_out，amount 取
      正值填寫）+ 累積損益（baseline..as_of 累計淨利，caller 算）+ 其他調整
      （correction/accountant/writeoff/other 合計）。調整列按 adj_date ≤ as_of
      過濾（不設 baseline 下限 — 期初列本來就開在基準月）。
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

    liab_current = [
        {"key": "payable", "label": "應付帳款",
         "amount": int(payable_total or 0), "drill": "payable"},
        {"key": "vat_payable", "label": "應付營業稅", "amount": int(vat_payable or 0)},
    ]
    # 非流動負債：銀行貸款逐筆分列（loan_outstanding_rows 已保證 key/label/amount；
    # BS 行為推導值無明細 drill）
    liab_noncurrent = [{"key": x["key"], "label": x["label"], "amount": x["amount"]}
                       for x in loan_rows]
    liab_current_total = sum(x["amount"] for x in liab_current)
    liab_total = liab_current_total + sum(x["amount"] for x in liab_noncurrent)
    for x in liab_current + liab_noncurrent:
        x["pct"] = _pct(x["amount"], assets_total)

    opening = owner = other = 0
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
        else:
            other += amt
    equity_lines = [
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
    if t in ("advance", "transfer"):
        return None
    if t in ("direct_expense", "direct_income", "loan"):
        acct = map_account(cat_map or {}, accounts or {}, "cash", entry.get("category"))
        act = (acct or {}).get("cf_activity")
        if act in ("investing", "financing"):
            return act
    return "operating"


def build_cashflow(months, *, opening, closing, cash_entries=(),
                   cat_map=None, accounts=None) -> dict:
    """現金流量表（直接法）。opening/closing = {"total", "by_account":[{name,amount}]}
    由 caller 以 bank_balances_asof 算（期間前一月月底 / 期末月月底）。

    規則：
    - 只計「有掛銀行帳戶」的收支（未掛帳戶者不影響任何帳戶餘額，計入會
      破壞恆等式 — 排除 + note 提醒；損益表則照計）。
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
    cat_map = cat_map or {}
    accounts = accounts or {}
    mset = set(months)
    acts = {"operating": 0, "investing": 0, "financing": 0}
    advance_net = transfer_net = 0
    unassigned = 0
    for e in cash_entries:
        if month_of(e.get("entry_date")) not in mset:
            continue
        if not e.get("bank_account_id"):
            unassigned += 1
            continue
        t = classify_cash_entry(e, cat_map)
        if t in ("advance", "transfer"):
            principal = in_amount(e) - out_amount(e)
            if t == "advance":
                advance_net += principal
            else:
                transfer_net += principal
            fee = int(e.get("bank_fee") or 0)
            if fee:
                acts["operating"] -= fee
            continue
        acts[cash_entry_activity(e, cat_map, accounts)] += cash_entry_flow(e)
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
