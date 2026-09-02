"""財務純邏輯 · 底層（`core.finance_logic` 套件的第一塊，不 import 另外兩塊）。

月份運算、器材直線折舊、銀行貸款攤還、發票款項狀態的正本、收支分類與方向、
損益認列迭代器、營業稅位置、期末部位（AR/AP/銀行餘額）、股東往來。

這裡的東西**沒有一支認得三表長什麼樣子** —— 它們是三表的材料。
對外一律從 `core.finance_logic` 匯入，別直接指名這個檔。
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
_AGENCY_FEE_LABEL = "發票代開費"
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


#: 記帳費費率的**出廠預設**。外部會計代繳營業稅，記帳費跟稅款走**同一筆匯出**
#: （雙月一次），所以每期對帳都要把那一筆拆成「營業稅 + 記帳費」兩列。
#:
#: 🔴 正本是**設定**（settings `finance.bookkeeping_fee`），不是這裡 ——
#:    owner 2026-08-24：「寫一個按鈕設定會計費用來解決這件事，之後換會計調整
#:    這個按鈕就好」。費率是會變的商業決定，不該要改程式 + 發版。
#:    這張表只在「還沒設定過」時當種子用。
#:
#: 沿革：owner 一天之內給過三個版本（2026-08-23）。作廢的兩版是
#: ① 一期 4,000／一年 14 個月 ② 單月 2,500／一年 14 個月。第 ① 版害
#: 2024/2025/2026 的 5 月期各被記成 8,000（實際應為 6,000）—— 費率當時只活在
#: 人的記憶裡，所以才會一錯再錯。
DEFAULT_BOOKKEEPING_FEE_RATES = (
    {"effective_from": "0000-00", "monthly": 2000, "months_per_year": 13},
    {"effective_from": "2026-09", "monthly": 2500, "months_per_year": 14},
)

#: 一年多收的那幾個月跟哪一期同收 —— 5 月那期（年度結算）。
BOOKKEEPING_EXTRA_ON_MONTH = 5


def bookkeeping_fee(pay_month: str, rates=None) -> int:
    """那一期的記帳費。`pay_month` ＝**實際付款**的年月（"YYYY-MM"）。

    雙月收一次 → 一期本來就是 2 個月；一年多收的那幾個月併在 5 月那期收。
    所以「一般期」與「5 月期」是同一條規則推出來的，不是兩個各自寫死的數字
    （之前錯的版本正是那個形狀：一般期對、5 月期多 2,000）。

    `rates` ＝ 費率表（由舊到新的 dict 清單）。不給就去讀設定
    `finance.bookkeeping_fee`，沒設定過才退回 DEFAULT_BOOKKEEPING_FEE_RATES。
    🔴 讀設定收在這支裡，不是叫每個呼叫端自己讀 —— 漏讀的那個會靜默用到
       出廠預設，而那正是這整件事要防的錯。

    對得上生產實際資料：2025 整年六期合計 26,000 ＝ 13 × 2,000（唯一乾淨的
    完整年度，改費率後拿它驗）。
    """
    m = (pay_month or "").strip()
    if len(m) < 7 or m[4] != "-" or not m[:4].isdigit() or not m[5:7].isdigit():
        raise ValueError(f"pay_month 要是 'YYYY-MM'，收到 {pay_month!r}")
    if rates is None:
        rates = load_bookkeeping_fee_rates()
    monthly, per_year = 0, 12
    for r in sorted(rates, key=lambda x: str(x.get("effective_from") or "")):
        if m >= str(r.get("effective_from") or ""):
            monthly = int(r.get("monthly") or 0)
            per_year = int(r.get("months_per_year") or 12)
    # 一般期 2 個月；5 月那期再加上一年多收的（per_year − 12）
    months = 2 + (per_year - 12 if int(m[5:7]) == BOOKKEEPING_EXTRA_ON_MONTH else 0)
    return monthly * months


def load_bookkeeping_fee_rates() -> list:
    """設定裡的費率表；沒設定過就回出廠預設（**複本**，呼叫端改不到原件）。"""
    try:
        from config import load_settings
        saved = (load_settings().get("finance") or {}).get("bookkeeping_fee")
    except Exception:
        saved = None
    if not saved:
        return [dict(r) for r in DEFAULT_BOOKKEEPING_FEE_RATES]
    return [dict(r) for r in saved]


def apply_payment_fee(entry, fee) -> None:
    """把匯費認列到一列收支明細上（付款側）。就地改 expense + bank_fee。"""
    entry.expense, bf = recognize_bank_fee(entry.expense, entry.bank_fee, fee)
    entry.bank_fee = bf or None


def apply_receipt_fee(entry, fee) -> None:
    """把匯費認列到一列收支明細上（收款側）。就地改 deposit + bank_fee。

    🔴 為什麼收到「一支吃 entry 的函式」而不是留 (欄名, 純函式) 給呼叫端自己
    搬：兩個欄位**必須一起動**，而「哪兩個欄位」是這條規則的一部分。之前是
    呼叫端各自 read → call → write back，兩處就寫成了兩種樣子（一處用回傳的
    bank_fee、一處丟掉回傳值自己再推一次）。規則有兩個呼叫端就會有第三個，
    寫回的動作留在外面遲早分岔 —— recognize_* 保持純函式（好測不變量），
    「怎麼落到 entry 上」收在這裡。
    """
    entry.deposit, bf = recognize_receipt_fee(entry.deposit, entry.bank_fee, fee)
    entry.bank_fee = bf or None


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


#: 開立狀態 —— **由發票號碼決定**，不是呼叫端各自決定（owner 2026-08-24：
#: 「這裡沒有發票號碼 要標註未開立，等到有發票號碼 才能標註已開立」）。
INVOICE_NOT_ISSUED = "未開立"
INVOICE_ISSUED = "已開立"
INVOICE_VOID = "作廢"

def issue_status_for(invoice_number, current: str = "") -> str:
    """發票的開立狀態：**有號碼才叫已開立**。

    這條規則本來只存在於發票本的前端，而且抄了三份（打字時即時改、存檔時再推
    一次、開視窗時填預設）。專案頁那個入口沒有那三份，於是從專案頁開一張還沒
    拿到號碼的票，會吃到 schema 預設值「已開立」—— 帳上出現一張沒有號碼卻宣稱
    已開立的發票（2026-08-24 實測，全帳 400 張裡就那一張）。所以搬到入口定案，
    跟 payment_status 同一條路（見 create_invoice 的說明）。

    🔴 作廢是**人的決定**，不能用號碼推翻：作廢的發票通常是有號碼的，
       拿號碼重推會把它變回已開立，等於把作廢這件事無聲取消。
    """
    # 只有作廢需要看呼叫端說什麼，其餘一律由號碼決定 —— 所以舊詞「開立中」
    # （前端改名前的用字，生產 0 張）不需要別名表：它不是作廢，就會走號碼那條，
    # 結果自然落在新詞上。
    if (current or "").strip() == INVOICE_VOID:
        return INVOICE_VOID
    return INVOICE_ISSUED if (invoice_number or "").strip() else INVOICE_NOT_ISSUED


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


_PAIRED_EXPENSE_SUFFIX = "支出"


def paired_expense_category(category, cat_map: dict) -> str:
    """收入類別的「支出版」類別（同鍵兩用時，**方向**才是判準）。

    🔴 owner 的私帳把專案的收款與專案的支出放在同一個類別鍵 `公司_專案` 底下 ——
    金額落在「支出」欄就是支出、落在「存入」欄就是收入，他的 Sheet 是這樣讀的。
    對映表只認類別文字，於是 direct_income 的支出列被當成「退款」去沖營收
    （實測 FY2025 有 1,118,775 的專案支出被沖掉，營收因此少了同額，
    成本那側也同額憑空消失 —— 兩張表當然對不起來）。

    規則：`<類別>支出` 這條對映存在就用它（`公司_專案` → `公司_專案支出`），
    沒有就維持原本的沖回行為（利息收入被退款之類，沖回同科目才是對的）。
    """
    cand = (category or "") + _PAIRED_EXPENSE_SUFFIX
    return cand if ("cash", cand) in (cat_map or {}) else ""


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
        elif t == "direct_income":
            # 收入類別的**支出**列 → 掛它的「支出版」科目（見 paired_expense_category）
            pair = paired_expense_category(e.get("category"), cat_map)
            amount = out_amount(e) if pair else 0
            if amount:
                group, label = expense_slot(
                    map_account(cat_map, accounts, "cash", pair))
                yield "cash", e, group, label, amount
        elif t == "unmapped":
            amount = out_amount(e)
            if amount:
                group, label = expense_slot(None)
                yield "cash", e, group, label, amount


def bank_fee_total(cash_entries, mset) -> int:
    """期間匯費合計 — 任何收支（含 transfer/advance）的 bank_fee 都是真實費用，
    損益彙總成「營業費用-管理／銀行手續費」單列；drilldown 出同額 derived 列。

    🔴 這個桶**含發票代開費**（拆項的 fee 疊在 bank_fee 上，為的是守住淨流不變
    ——`recognize_receipt_fee`）。要拆名字用 `bank_fee_breakdown`，它一趟回
    `(真匯費, 代開費)`；**不要自己在顯示端減一次**，那就是第二份規則。
    """
    return sum(int(e.get("bank_fee") or 0) for e in cash_entries
               if month_of(e.get("entry_date")) in mset)


def bank_fee_breakdown(cash_entries, mset):
    """期間匯費桶拆成 `(真匯費, 發票代開費)` —— 兩者相加＝ `bank_fee_total`。

    🔴 **減法只有這一份**。代開費為了守住淨流不變而疊在 `bank_fee` 上
    （`recognize_receipt_fee`），所以「真匯費」是減出來的；那個減法本來散在
    損益表頭與 drilldown 兩個顯示端各寫一次，第三個顯示端只要忘記減，
    代開費就又會被叫成銀行手續費（owner 2026-09-02 看到的那顆 66,960）。

    順帶只掃一次 `cash_entries`（原本兩支各掃一遍，`month_of` 每列算兩次）。
    """
    bank = agency = 0
    for e in cash_entries:
        if month_of(e.get("entry_date")) not in mset:
            continue
        bank += int(e.get("bank_fee") or 0)
        agency += int(e.get("agency_fee") or 0)
    return bank - agency, agency


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

# 信用卡帳戶（owner 2026-08-27「信用卡匯入時也可以區隔哪一個銀行的信用卡」）：
# 一張卡一個帳戶，刷卡列掛在它身上。🔴 **不是現金**：卡片餘額是負債，而負債那
# 條由 card_outstanding 算（期初＋刷卡−還款）—— 卡片帳戶若落進 cash 就是同一
# 筆錢一邊當資產一邊當負債。
CARD_KIND = "card"


def is_card_kind(kind) -> bool:
    return (kind or "") == CARD_KIND


def is_shareholder_kind(kind) -> bool:
    return (kind or "") in SHAREHOLDER_KINDS


def split_bank_lines(bank_accounts, lines) -> dict:
    """把 bank_balances_asof 的結果依帳戶性質分堆。

    現金／股東借款／股東投資款 落在資產負債表的**三個不同區塊**，
    一起丟進現金就是把負債與權益當成資產。信用卡帳戶（CARD_KIND）也單獨
    一堆並且**不進任何 BS 區塊** —— 它只是卡別身分，卡債由 card_outstanding
    算（重複計會讓負債翻倍）。
    """
    kind_of = {b.get("id"): (b.get("acct_kind") or "bank") for b in bank_accounts}
    out = {"cash": [], "shareholder_loan": [], "shareholder_capital": [],
           CARD_KIND: []}
    for ln in lines:
        k = kind_of.get(ln.get("id"), "bank")
        if k in SHAREHOLDER_KINDS or k == CARD_KIND:
            out[k].append(ln)          # 卡片：不進現金（負債由 card_outstanding 出）
        else:
            out["cash"].append(ln)
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


