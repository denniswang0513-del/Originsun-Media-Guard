"""core/fortress_logic.py — 私帳「堡壘」（個人版 Fortress Balance Sheet）的純規則。

規劃：docs/FORTRESS_PLAN.md。來源：owner 2026-09-16 貼的〈把 JPMorgan 堡壘資產負債表搬進家庭財務〉。
這裡沒有 DB、沒有 settings 檔、沒有 request —— 全部吃 dict／list、回 dict，好測。
資料怎麼從私帳撈出來在 routers/api_fortress.py（帳戶餘額、證券現值、貸款下一期、卡欠款、必要支出月平均）。

五層：1 營運現金／2 預留現金／3 緊急預備／4 機會資金／5 複利資本。
可撐月數＝（第 1–3 層現金 − 預留）÷ 每月必要支出；另給「含第 4 層」的版本。
壓力測試六題：收入斷 6 個月、股票跌 40%、突發 40 萬、三件同時、台海戰爭、長期照護（假設都可調）。
另給「資產預期成長」：第 5 層複利資本往後 5／10／15／20／30 年，名目與今天的購買力各一個數字。
"""
from __future__ import annotations

LAYER_NAMES = {1: "營運現金", 2: "預留現金", 3: "緊急預備", 4: "機會資金", 5: "複利資本"}
LAYER_DESC = {1: "日常花的", 2: "已預留用途：稅、保費、房貸", 3: "收入中斷時活命用",
              4: "等好機會才動", 5: "長期投資，十年不動"}
#: 各層目標＝必要支出的幾倍（第 2 層＝預留清單合計、第 5 層沒有上限，不在這裡）
DEFAULT_TARGET_MONTHS = {1: 1, 3: 6, 4: 3}
#: 目標倍數的上限（10 年）—— 見 normalize_settings 裡的紅字
MAX_TARGET_MONTHS = 120
#: 台海戰爭題的預設假設（owner 可在桌機改）
DEFAULT_WAR = {"months": 12, "tw_drop": 0.6, "us_drop": 0.2, "fx": 1.3, "bank_freeze_weeks": 4}
#: 長照題的預設假設（owner 可在桌機改）。
#: 月費 4 萬＝外籍看護（薪資＋健保＋仲介）或一般安養機構的常見水準；本國照服員或養護型機構會更高。
#: 7 年＝台灣長照需求期間常被引用的平均值；重度失能可能十年以上。保險給付是每月實拿，直接扣掉月費。
#: 🔴 長照跟其他五題不一樣：它不是一次性衝擊，是把**每月必要支出整個抬高好幾年**——
#:    攻擊的是可撐月數的分母，所以它動用的是長期資本（第 5 層）而不只是現金。
DEFAULT_CARE = {"monthly": 40000, "years": 7, "insurance_monthly": 0}
#: 資產預期成長的預設假設：年報酬 6%（台股＋美股 ETF 的長期名目報酬常用值）、通膨 2%、每年再投入 0。
#: 一律同時給名目與「換算成今天的購買力」兩個數字 —— 只看名目會高估未來買得起什麼。
DEFAULT_GROWTH = {"rate": 0.06, "inflation": 0.02, "annual_add": 0}
#: 預期成長要看哪幾年
GROWTH_YEARS = (5, 10, 15, 20, 30)

# ── 財富階梯（owner 2026-09-16 拍板：參考市場先生的台灣版）──────────────────────
#: 階梯的門檻（台幣，5 個門檻＝6 階）。台灣最常見的算法是把書裡的美金級距乘 30（商周／經理人都這樣），
#: 市場先生再把第 5 階「住宅自由」從 3 億拉到 6 億 —— 台灣「維持生存便宜、買房超貴」，台北房子動輒破億。
#: 用這組的理由：跟外面讀到的文章可以直接對照。owner 可以改（連第 5 階要不要拉高都是設定）。
#: 曾經試過「從台灣物價回推」的一百萬起跳版，比較嚴格但沒有人那樣算，對照不上，2026-09-16 換掉。
DEFAULT_LADDER = {
    "thresholds": [300_000, 3_000_000, 30_000_000, 600_000_000, 3_000_000_000],
    "free_rate": 0.0001,          # 不用想就能花的單筆＝淨值的萬分之一（書裡的 0.01% 法則）
    # 主計總處「家庭財富分配統計」2021 年底（2024 年 4 月發布）。這是**另一個問題**的答案
    # （你贏過多少家庭），跟上面的階梯並排顯示、不互相取代。資料會過時，所以做成可改。
    # 🔴 那份統計八成以上來自自住的房子；私帳只有金融資產、沒有房產，拿來比會低估你的位置。
    "median": 8_940_000, "top20": 21_340_000, "top10": 33_910_000,
    "stat_note": "主計總處家庭財富分配統計，2021 年底，含自住房產",
}
#: 各階的名字與「這一階的人不用想就能買什麼」（台灣常見譯法）
LADDER_RUNGS = (
    ("月光族", "每一塊錢都要精打細算"),
    ("大賣場自由", "大賣場裡想買什麼就買什麼"),
    ("餐廳自由", "菜單上想點什麼點什麼，不看價錢"),
    ("旅遊自由", "要去哪、住哪，不用先算"),
    ("住宅自由", "換房、買房不動搖生活"),
    ("完全自由", "錢不再是選項的限制"),
)
#: 規劃欄位的預設：想爬到第幾階（5＝6 億）、幾年內到
DEFAULT_PLAN = {"target_rung": 5, "target_years": 20}
#: 財富自由評估（owner 2026-09-16「此刻如果我不工作沒收入 我每個月可以花多少錢」；1989 年生；提領率依建議）。
#: 提領率預設 3.5%：4% 法則（Bengen／Trinity）是 30 年退休期、50–75% 股票的研究；owner 要撐 50 年以上、
#: 股票比重九成，Early Retirement Now 那派建議 3.25–3.5%。三個提領率都會顯示，這格只決定「判定用哪一個」。
#: self_pay_monthly：不工作後要自付的健保第六類（約 826）＋國民年金（約 1,186），2024–25 費率的約數，可改。
DEFAULT_FIRE = {"withdrawal_rate": 0.035, "birth_year": 1989, "until_age": 90,
                "extra_monthly": 0, "extra_from_age": 65, "self_pay_monthly": 2000}
FIRE_RATES = (0.03, 0.035, 0.04)
#: 沒有出生年時模擬幾年
FIRE_DEFAULT_HORIZON = 50
#: 房產（owner 2026-09-16「可以新增房產的選項 但我現在沒有」）：手填估值，只算進**財富階梯的淨值**，
#: 不進五層、不進可撐月數、不進長照題 —— 房子不是能拿來付帳的錢。有房貸的話貸款那邊本來就會扣。
DEFAULT_PROPERTY = {"value": 0, "note": ""}
#: 第 3 題「突發支出」的金額
SHOCK_AMOUNT = 400_000
#: 第 2 題股票跌幅
MARKET_DROP = 0.4
#: 可撐月數的顏色門檻（同儀表板 runway：≥6 綠、3–6 黃、<3 紅）
RUNWAY_GREEN = 6
RUNWAY_AMBER = 3
#: 必要支出自動算：最多往回看幾個完整月（真的有資料的月份才進分母，見 monthly_need_from_rows）
NEED_SAMPLE_MONTHS = 6
#: 算進「每月生活支出」的分類（收支明細 `category`＝分類樹「頂層_第二層」的鏡射，但**舊列只有兩層的舊名字**）。
#: 🔴 2026-09 實查私帳：分類樹只搬了一半，生活費大多掛在 `個人_旅遊`／`個人_生活`／`家用` 這種舊名字上。
#: 只收 `家用%`＋`個人_固定支出%` 的話會漏掉整個「個人」枝（實查 6 個月漏 33 萬），分母太小 → 可撐月數被高估。
NEED_BOOKS = ("家用", "個人", "貸款繳款")
#: 那幾枝裡再扣掉的項目：收入列、買股票、還款（卡債與貸款走「預留」，這裡再算一次就是重複）、帳務對齊
NEED_SKIP_ITEMS = ("主動收入", "被動收入", "投資支出", "借款支出", "其他支出")


def _num(v, as_int: bool = False):
    """設定值 → 數字；不是數字、NaN、Infinity 一律回 None（讓呼叫端沿用預設）。
    🔴 `int(float("Infinity"))` 丟的是 **OverflowError**，不是 ValueError ——
    漏接的話一個 PUT 就讓整支端點 500（2026-09-16 /polish 第二輪抓到，第一輪只修了值域）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):      # NaN／±Infinity
        return None
    try:
        return int(f) if as_int else f
    except (OverflowError, ValueError):
        return None


def need_category_ok(category) -> bool:
    """這一列的分類算不算「每月生活支出」。分類切帳本／項目用 core.cash_taxonomy 那份正本
    （`公司_專案` → ('公司','專案')；只切第一個底線）。"""
    from core.cash_taxonomy import split_category
    book, item = split_category(category)
    return book in NEED_BOOKS and not item.startswith(NEED_SKIP_ITEMS)


def monthly_need_from_rows(rows) -> tuple:
    """[(category, 'YYYY-MM', expense)…] → (月平均, 真的有資料的月份數)。
    🔴 分母是**有資料的月份數**不是固定 6：帳本才記 3 個月就除以 6，生活支出會被腰斬、可撐月數翻倍
    —— 而且錯在危險的方向。一個月都沒有 → (0, 0)，畫面會說「算不出來」而不是「撐很久」。"""
    by_month: dict = {}
    for category, month, expense in rows:
        if not need_category_ok(category):
            continue
        by_month[month] = by_month.get(month, 0) + int(expense or 0)
    if not by_month:
        return 0.0, 0
    return sum(by_month.values()) / len(by_month), len(by_month)


STATE_OK, STATE_WARN, STATE_BAD = "ok", "warn", "bad"
#: 六題的 key／題名／小標。na 路（沒有必要支出、算不出來）與正常路共用這一份，
#: 不然改了 MARKET_DROP／SHOCK_AMOUNT，只有其中一條路的題名會跟著變。
TEST_TITLES = (
    ("income", "收入中斷 6 個月", "現金夠嗎？要賣股票嗎？"),
    ("market", f"股票下跌 {int(MARKET_DROP * 100)}%", "生活會受影響嗎？會被迫停損嗎？"),
    ("shock", f"突然多 {SHOCK_AMOUNT // 10000} 萬支出", "錢從哪裡來？會打亂投資計畫嗎？"),
    ("combined", "三件同時發生", "失業、市場下跌、突發支出一起來，還有選擇權嗎？"),
    ("war", "台海戰爭", "銀行停擺、台幣貶值、收入斷一年，撐得過去嗎？"),
    ("care", "長期照護", "家裡有人需要長照，這筆錢從哪裡出？撐得了幾年？"),
)
_TITLE = {k: (t, q) for k, t, q in TEST_TITLES}


def _state(ok: bool, warn: bool) -> str:
    return STATE_OK if ok else (STATE_WARN if warn else STATE_BAD)


def _card(key: str, state: str, lines: list, verdict: str, assume: str = "") -> dict:
    title, question = _TITLE[key]
    return {"key": key, "title": title, "question": question, "state": state,
            "assume": assume, "lines": lines, "verdict": verdict}


# ── 設定 ──────────────────────────────────────────────────────────
def normalize_settings(raw) -> dict:
    """settings.finance.fortress[entity] → 補齊預設的完整設定（複本）。
    account_layers：{帳戶id: 1..5}；account_flags：{帳戶id: {physical, offshore, usd}}；
    holdings_layer：證券整批的層別（預設 5）；targets：{層: 倍數}；monthly_need_override：None＝用自動；
    war／care：壓力測試假設；growth：資產預期成長的假設。各段都夾在合理範圍內（見各自的 bounds）。"""
    raw = raw if isinstance(raw, dict) else {}
    layers = {}
    for k, v in (raw.get("account_layers") or {}).items():
        n = _num(v, as_int=True)
        if n is not None and 1 <= n <= 5:
            layers[str(k)] = n
    flags = {}
    for k, v in (raw.get("account_flags") or {}).items():
        if isinstance(v, dict):
            flags[str(k)] = {f: bool(v.get(f)) for f in ("physical", "offshore", "usd")}
    targets = dict(DEFAULT_TARGET_MONTHS)
    for k, v in (raw.get("targets") or {}).items():
        n, m = _num(k, as_int=True), _num(v)
        if n is None or m is None:
            continue
        # 🔴 上限不能省：`float('inf') >= 0` 是真的，倍數 Infinity 會被寫進設定檔，
        #    之後每次開頁都在 _m(inf) 炸 OverflowError → 500，而且要手改 settings.json 才救得回來。
        if n in DEFAULT_TARGET_MONTHS and m >= 0:
            targets[n] = min(m, MAX_TARGET_MONTHS)      # 夾住（同 war 的做法），不是丟掉使用者填的
    # 假設要有上下限：months=0 會讓「撐得過」變成必然、fx=0 會讓美元資產整批歸零 —— 兩個都是無聲的假答案
    war = dict(DEFAULT_WAR)
    bounds = {"months": (1, 120), "bank_freeze_weeks": (0, 520), "tw_drop": (0.0, 1.0), "us_drop": (0.0, 1.0), "fx": (0.1, 10.0)}
    for k, v in (raw.get("war") or {}).items():
        if k not in DEFAULT_WAR:
            continue
        n = _num(v, as_int=k in ("months", "bank_freeze_weeks"))
        if n is None:
            continue
        lo, hi = bounds[k]
        war[k] = max(lo, min(hi, n))
    care = dict(DEFAULT_CARE)
    care_bounds = {"monthly": (0, 1_000_000), "years": (1, 40), "insurance_monthly": (0, 1_000_000)}
    for k, v in (raw.get("care") or {}).items():
        if k not in DEFAULT_CARE:
            continue
        n = _num(v, as_int=True)
        if n is None:
            continue
        lo, hi = care_bounds[k]
        care[k] = max(lo, min(hi, n))
    growth = dict(DEFAULT_GROWTH)
    growth_bounds = {"rate": (-0.5, 0.5), "inflation": (0.0, 0.5), "annual_add": (0, 100_000_000)}
    for k, v in (raw.get("growth") or {}).items():
        if k not in DEFAULT_GROWTH:
            continue
        n = _num(v, as_int=k == "annual_add")
        if n is None:
            continue
        lo, hi = growth_bounds[k]
        growth[k] = max(lo, min(hi, n))
    ladder = dict(DEFAULT_LADDER)
    raw_l = raw.get("ladder") or {}
    th = raw_l.get("thresholds")
    if isinstance(th, (list, tuple)) and len(th) == len(DEFAULT_LADDER["thresholds"]):
        nums = [_num(x, as_int=True) for x in th]
        cleaned = sorted(max(1, min(10 ** 13, n)) for n in nums) if all(n is not None for n in nums) else None
        if cleaned and len(set(cleaned)) == len(cleaned):      # 要嚴格遞增，不然「在第幾階」會算不出來
            ladder["thresholds"] = cleaned
    for k, lo, hi in (("free_rate", 1e-6, 0.01), ("median", 0, 10 ** 13), ("top20", 0, 10 ** 13), ("top10", 0, 10 ** 13)):
        if k in raw_l:
            n = _num(raw_l[k], as_int=k != "free_rate")
            if n is not None:
                ladder[k] = max(lo, min(hi, n))
    if isinstance(raw_l.get("stat_note"), str):
        ladder["stat_note"] = raw_l["stat_note"][:80]
    plan = dict(DEFAULT_PLAN)
    for k, lo, hi in (("target_rung", 2, len(LADDER_RUNGS)), ("target_years", 1, 60)):
        if k in (raw.get("plan") or {}):
            n = _num((raw["plan"])[k], as_int=True)
            if n is not None:
                plan[k] = max(lo, min(hi, n))
    fire = dict(DEFAULT_FIRE)
    fire_bounds = {"withdrawal_rate": (0.01, 0.10), "birth_year": (0, 2100), "until_age": (40, 120),
                   "extra_monthly": (0, 10_000_000), "extra_from_age": (0, 120), "self_pay_monthly": (0, 1_000_000)}
    for k, v in (raw.get("fire") or {}).items():
        if k not in DEFAULT_FIRE:
            continue
        n = _num(v, as_int=k != "withdrawal_rate")
        if n is None:
            continue
        lo, hi = fire_bounds[k]
        fire[k] = max(lo, min(hi, n))
    prop = dict(DEFAULT_PROPERTY)
    raw_p = raw.get("property") or {}
    n = _num(raw_p.get("value") or 0, as_int=True)
    if n is not None:
        prop["value"] = max(0, min(10 ** 12, n))
    if isinstance(raw_p.get("note"), str):
        prop["note"] = raw_p["note"].strip()[:80]
    override = raw.get("monthly_need_override")
    override = _num(override, as_int=True) if override not in (None, "", 0, "0") else None
    if override is not None and override <= 0:
        override = None      # 負的／零＝沒填，回到自動平均
    hl = _num(raw.get("holdings_layer", 5), as_int=True)
    if hl is None:
        hl = 5
    return {"account_layers": layers, "account_flags": flags, "holdings_layer": hl if 1 <= hl <= 5 else 5,
            "targets": targets, "monthly_need_override": override, "war": war, "care": care, "growth": growth,
            "ladder": ladder, "plan": plan, "property": prop, "fire": fire}


def merge_settings(current: dict, patch: dict) -> dict:
    """PUT 設定：淺層合併（account_layers／account_flags／targets／war 各自整份取代；其餘逐鍵）。"""
    out = normalize_settings(current)
    patch = patch if isinstance(patch, dict) else {}
    for k in ("account_layers", "account_flags", "targets", "war", "care", "growth", "ladder", "plan", "property", "fire"):
        if k in patch and isinstance(patch[k], dict):
            out[k] = patch[k]
    for k in ("holdings_layer", "monthly_need_override"):
        if k in patch:
            out[k] = patch[k]
    return normalize_settings(out)


# ── 帳戶 → 層 ────────────────────────────────────────────────────
def assign_layers(accounts: list, settings: dict) -> list:
    """給每個帳戶掛上 layer 與 flags（複本）。
    帳戶 dict：{id, name, kind('bank'|'cash'|'holding'), balance(int 台幣), currency('TWD'|'USD'), market('tw'|'us'|'')}。
    沒設定過的：證券＝holdings_layer、其餘＝第 1 層。usd 旗標：設定勾了、或 currency 不是 TWD。"""
    out = []
    for a in accounts:
        a = dict(a)
        aid = str(a.get("id") or "")
        default = settings["holdings_layer"] if a.get("kind") == "holding" else 1
        a["layer"] = settings["account_layers"].get(aid, default)
        f = dict(settings["account_flags"].get(aid) or {"physical": False, "offshore": False, "usd": False})
        if (a.get("currency") or "TWD").upper() != "TWD":
            f["usd"] = True
        a["flags"] = f
        out.append(a)
    return out


def layer_sums(accounts: list) -> dict:
    """每一層的餘額合計（含證券；「現金」要用 cash_in_layers）。"""
    have = {n: 0 for n in LAYER_NAMES}
    for a in accounts:
        have[a["layer"]] += int(a.get("balance") or 0)
    return have


def is_holding(a) -> bool:
    """這一筆是不是證券（股票／ETF／外幣持股）。"""
    return (a or {}).get("kind") == "holding"


def cash_in_layers(accounts: list, upto: int) -> int:
    """第 1..upto 層的**現金**（證券不算）。
    🔴 看的是「這筆是不是證券」不是第幾層：把一檔 ETF 標成第 4 層機會資金（下拉就能做），
    它不會因此變成股災裡不會跌的現金。"""
    return sum(int(a.get("balance") or 0) for a in accounts if a["layer"] <= upto and not is_holding(a))


# ── 數字 ──────────────────────────────────────────────────────────
def runway_months(cash: float, earmark_total: float, monthly_need: float):
    """可撐月數；必要支出 ≤0 → None（算不出來，不是無限）。"""
    if not monthly_need or monthly_need <= 0:
        return None
    return round((cash - earmark_total) / monthly_need, 1)


def tone(months) -> str:
    """可撐月數 → 顏色（同儀表板：≥6 綠、3–6 黃、<3 紅；算不出來 na）。"""
    if months is None:
        return "na"
    return "g" if months >= RUNWAY_GREEN else ("a" if months >= RUNWAY_AMBER else "r")


def layer_targets(monthly_need: float, earmark_total: float, targets: dict) -> dict:
    """各層目標金額；第 5 層 None。"""
    return {1: monthly_need * targets[1], 2: earmark_total, 3: monthly_need * targets[3],
            4: monthly_need * targets[4], 5: None}


def _m(x) -> int:
    return int(round(x))


# ── 壓力測試 ──────────────────────────────────────────────────────
def stress_tests(have: dict, accounts: list, monthly_need: float, earmark_total: float, war: dict, care: dict = None) -> list:
    """六題。每題 {key, title, question, state, assume, lines:[[label, value, unit]], verdict}；
    unit：'twd'（金額）、'months'、'years'。必要支出 ≤0 → 每題 state='na'、只回一句話。"""
    m = float(monthly_need or 0)
    ear = float(earmark_total or 0)
    cash3 = cash_in_layers(accounts, 3)
    cash4 = cash_in_layers(accounts, 4)
    holdings_total = sum(int(a.get("balance") or 0) for a in accounts if is_holding(a))
    if m <= 0:
        return [_card(k, "na", [], "先設定每月必要支出才算得出來。") for k, _t, _q in TEST_TITLES]
    out = []
    # 1. 收入斷 6 個月
    need = m * 6 + ear
    left = cash3 - need
    if left >= 0:
        st, vd = STATE_OK, "撐得住。不用動股票，也不用動機會資金。"
    elif left + have[4] >= 0:
        st, vd = STATE_WARN, f"第 1 到 3 層差 {_wan(-left)}，要動到機會資金，股票不用賣。"
    else:
        st, vd = STATE_BAD, f"差 {_wan(-left)}，機會資金也不夠，會被迫賣股票。"
    out.append(_card("income", st,
                     [["6 個月必要支出＋預留", _m(need), "twd"], ["第 1 到 3 層現金", _m(cash3), "twd"], ["撐完還剩", _m(left), "twd"]], vd))
    # 2. 股票跌 40%
    rw = runway_months(cash3, ear, m)
    st = _state(rw >= RUNWAY_GREEN, rw >= RUNWAY_AMBER)
    out.append(_card("market", st,
                     [["證券現值", _m(holdings_total), "twd"], ["跌完剩", _m(holdings_total * (1 - MARKET_DROP)), "twd"],
                      ["可撐月數（不動股票）", rw, "months"]],
                     "生活資金在第 1 到 3 層，跌了不用賣，等得起。" if st == STATE_OK
                     else "生活資金不到 6 個月，跌的時候可能被迫在低點賣。先補第 3 層。"))
    # 3. 突發 40 萬
    from4 = min(SHOCK_AMOUNT, max(0, cash4 - cash3))
    from3 = SHOCK_AMOUNT - from4
    rw3 = runway_months(cash3 - from3, ear, m)
    st = _state(rw3 >= RUNWAY_GREEN, rw3 >= RUNWAY_AMBER)
    out.append(_card("shock", st,
                     [["先從第 4 層機會資金扣", _m(from4), "twd"], ["再從第 3 層緊急預備扣", _m(from3), "twd"], ["扣完可撐月數", rw3, "months"]],
                     "撐得住，投資計畫不用動。" if st == STATE_OK
                     else (f"撐得住，但緊急預備降到 {rw3:g} 個月，之後幾個月先回補第 3 層。" if st == STATE_WARN
                           else "會被迫動到投資。先補第 3、4 層。")))
    # 4. 三件同時
    need4 = m * 6 + SHOCK_AMOUNT + ear
    left4 = cash4 - need4
    st = _state(left4 >= m * 3, left4 >= 0)
    out.append(_card("combined", st,
                     [["6 個月支出＋40 萬＋預留", _m(need4), "twd"], ["第 1 到 4 層現金", _m(cash4), "twd"], ["撐完還剩", _m(left4), "twd"]],
                     f"撐得住，不用在低點賣股票，但撐完只剩 {round(left4 / m, 1):g} 個月緩衝，之後要重建第 3、4 層。" if left4 >= 0
                     else f"第 1 到 4 層差 {_wan(-left4)}，會被迫在低點賣股票。"))
    # 5. 台海戰爭
    fx = float(war["fx"])
    months = int(war["months"])
    # 現金只算「不是證券」的（證券照樣跌，不論被放在第幾層）
    cash_l = [a for a in accounts if a["layer"] <= 4 and not is_holding(a)]
    war_cash = sum(int(a.get("balance") or 0) * (fx if a["flags"].get("usd") else 1) for a in cash_l)
    first = sum(int(a.get("balance") or 0) * (fx if a["flags"].get("usd") else 1)
                for a in cash_l if a["flags"].get("physical") or a["flags"].get("offshore"))
    tw = sum(int(a.get("balance") or 0) for a in accounts if is_holding(a) and not a["flags"].get("usd")) * (1 - float(war["tw_drop"]))
    us = sum(int(a.get("balance") or 0) for a in accounts if is_holding(a) and a["flags"].get("usd")) * (1 - float(war["us_drop"])) * fx
    need5 = m * months + ear
    left5 = war_cash - need5
    first_ok = first >= m
    span = f"{months} 個月"
    if not first_ok:
        st = STATE_BAD
        vd = f"頭一個月拿得到的錢只有 {_wan(first)}，不夠 1 個月支出。先把實體現金或海外帳戶補到 {_wan(m)} 以上。"
    elif left5 >= m * 3:
        st, vd = STATE_OK, f"撐得過 {span}，不用賣任何股票。美元資產在台幣貶值時反而變厚。"
    elif left5 >= 0:
        st, vd = STATE_WARN, f"撐得過 {span}，但只剩 {round(left5 / m, 1):g} 個月緩衝。美股是最後一道，跌完換台幣還有 {_wan(us)}。"
    elif left5 + us >= 0:
        st, vd = STATE_WARN, f"現金差 {_wan(-left5)}，要賣美股補。台股跌六成先不要動。"
    else:
        st, vd = STATE_BAD, f"現金加美股都不夠，差 {_wan(-(left5 + us))}。海外和美元的比重要拉高。"
    freeze = int(war["bank_freeze_weeks"])
    out.append(_card("war", st,
                     [[f"前 {freeze} 週拿得到的錢（實體＋海外）", _m(first), "twd"],
                      [f"{months} 個月支出＋預留", _m(need5), "twd"],
                      ["第 1 到 4 層現金（美元已升值）", _m(war_cash), "twd"], ["撐完還剩", _m(left5), "twd"],
                      ["台股跌完剩", _m(tw), "twd"], ["美股跌完換台幣", _m(us), "twd"]], vd,
                     assume=(f"假設：收入斷 {months} 個月、台股跌 {int(round(float(war['tw_drop']) * 100))}%、"
                             f"美股跌 {int(round(float(war['us_drop']) * 100))}%、台幣貶 {int(round((fx - 1) * 100))}%、"
                             f"銀行前 {freeze} 週領不到錢")))
    # 6. 長期照護：不是一次衝擊，是把每月支出抬高好幾年 → 本來就該動用長期資本，所以現金與投資一起算
    out.append(_care_test(cash4, holdings_total, m, ear, care or DEFAULT_CARE))
    return out


def _care_test(cash4: float, holdings_total: float, m: float, ear: float, care: dict) -> dict:
    care_net = max(0.0, float(care["monthly"]) - float(care["insurance_monthly"]))
    years = int(care["years"])
    per_month = m + care_net                       # 長照期間每月總支出＝原本的生活支出＋照護費
    per_year = per_month * 12
    need = per_year * years + ear
    have = cash4 + holdings_total
    covered = (have - ear) / per_year if per_year > 0 else None
    cash_only = (cash4 - ear) / per_year if per_year > 0 else None
    st = _state(covered is not None and covered >= years + 1, covered is not None and covered >= years)
    if st == STATE_OK:
        vd = (f"撐得過 {years} 年，還多 {covered - years:.1f} 年緩衝。現金先頂著，長期的部分由第 5 層複利資本支應 —— "
              "這正是它存在的理由，不用因為長照就停掉投資。")
    elif st == STATE_WARN:
        vd = (f"剛好撐得過 {years} 年（{covered:.1f} 年），沒什麼緩衝。長照常常比預估久，"
              f"把每月照護費往上調一成再看一次，或把第 4 層機會資金補厚。")
    else:
        short = need - have
        vd = (f"撐不到 {years} 年，只撐得了 {covered:.1f} 年，差 {_wan(short)}。"
              "這一題差的通常不是存款而是保險：長照險或失能扶助險的月給付直接扣在照護費上，比存同樣的錢有效率。")
    return _card("care", st,
                 [["每月照護費（已扣保險給付）", _m(care_net), "twd"],
                  ["長照期間每月總支出", _m(per_month), "twd"],
                  [f"{years} 年總共要（含預留）", _m(need), "twd"],
                  ["現金＋投資合計", _m(have), "twd"],
                  ["撐得了幾年", round(covered, 1) if covered is not None else None, "years"],
                  ["只用現金撐得了幾年", round(cash_only, 1) if cash_only is not None else None, "years"]], vd,
                 assume=(f"假設：每月照護費 {_wan(float(care['monthly']))}"
                         + (f"、保險每月給付 {_wan(float(care['insurance_monthly']))}" if float(care["insurance_monthly"]) else "")
                         + f"、需要 {years} 年。這題把第 5 層複利資本也算進來（長照是長期支出，本來就該由它支應）。"
                           "沒有算「照顧者離職少一份收入」—— 那一段看第 1 題。"))


def ladder_position(net_worth: float, ladder: dict) -> dict:
    """淨值 → 在第幾階、不用想就能花多少、距離下一階還差多少。
    門檻是 5 個數字＝6 階；第 6 階沒有上限。"""
    th = list(ladder["thresholds"])
    n = float(net_worth or 0)
    rung = 1
    for i, t in enumerate(th):
        if n >= t:
            rung = i + 2
    top = th[rung - 2] if rung >= 2 else 0          # 這一階的下緣
    nxt = th[rung - 1] if rung - 1 < len(th) else None   # 下一階的門檻（第 6 階沒有）
    name, desc = LADDER_RUNGS[min(rung, len(LADDER_RUNGS)) - 1]
    span = (nxt - top) if nxt else 0
    return {
        "rung": rung, "name": name, "desc": desc,
        "net_worth": _m(n), "free_amount": _m(n * float(ladder["free_rate"])),
        "floor": _m(top), "next": _m(nxt) if nxt else None,
        "to_next": _m(nxt - n) if nxt else None,
        "pct_in_rung": max(0, min(100, int(round((n - top) / span * 100)))) if span > 0 else 100,
        "rungs": [{"no": i + 1, "name": LADDER_RUNGS[i][0], "desc": LADDER_RUNGS[i][1],
                   "floor": _m(th[i - 1]) if i >= 1 else 0, "ceiling": _m(th[i]) if i < len(th) else None,
                   "free_from": _m((th[i - 1] if i >= 1 else 0) * float(ladder["free_rate"])),
                   "free_to": _m(th[i] * float(ladder["free_rate"])) if i < len(th) else None,
                   "you": i + 1 == rung} for i in range(len(LADDER_RUNGS))],
    }


def percentile_note(net_worth: float, ladder: dict) -> str:
    """「你在台灣家庭的哪個位置」—— 跟階梯並排的另一個答案，不取代它。"""
    n = float(net_worth or 0)
    note = ladder.get("stat_note") or ""
    for key, label in (("top10", "前 10%"), ("top20", "前 20%")):
        t = float(ladder.get(key) or 0)
        if t and n >= t:
            return f"超過台灣家庭{label} 的門檻（{_wan(t)}，{note}）"
    med = float(ladder.get("median") or 0)
    if med and n >= med:
        return f"高於台灣家庭淨值中位數（{_wan(med)}，{note}）"
    return f"低於台灣家庭淨值中位數（{_wan(med)}，{note}）" if med else ""


def years_to_reach(base: float, target: float, rate: float, annual_add: float, cap: int = 100):
    """照目前的報酬與每年再投入，幾年後到得了 target。到不了（cap 年內）回 None。"""
    v, r, add = float(base), float(rate), float(annual_add)
    if v >= target:
        return 0
    for y in range(1, cap + 1):
        v = v * (1 + r) + add
        if v >= target:
            return y
    return None


def plan_result(base: float, target: float, years: int, growth: dict) -> dict:
    """三個規劃欄位接起來：照現在的速度幾年到、要在指定年限內到的話每年要再投入多少、
    或（不再投入的話）年報酬要多少。做不到就說做不到。"""
    r = float(growth["rate"])
    add = float(growth["annual_add"])
    base, target, years = float(base), float(target), max(1, int(years))
    at_current = years_to_reach(base, target, r, add)
    grown = base * (1 + r) ** years
    # 年金公式反算：要補的缺口 ÷ 年金終值因子
    need_add = None if grown >= target else (target - grown) * r / ((1 + r) ** years - 1) if r else (target - grown) / years
    need_rate = None
    if base > 0 and target > base:
        lo, hi = 0.0, 1.0
        for _ in range(60):                       # 二分：不再投入的話年報酬要多少
            mid = (lo + hi) / 2
            if base * (1 + mid) ** years < target:
                lo = mid
            else:
                hi = mid
        need_rate = round(hi, 4)
    return {"target": _m(target), "years": years, "years_at_current": at_current,
            "need_annual_add": _m(need_add) if need_add else 0,
            "need_rate": need_rate, "on_track": at_current is not None and at_current <= years}


def effort_focus(base: float, growth: dict) -> dict:
    """這一階該把力氣放哪：資產自己長的錢 vs 你今年存進去的錢，哪個大。
    越過那條線之後，報酬率與資產配置比多接一個案子更能決定結果。"""
    gain = float(base) * float(growth["rate"])
    add = float(growth["annual_add"])
    return {"passive": _m(gain), "added": _m(add), "passive_wins": gain > add}


def fire_simulate(assets: float, monthly: float, rate: float, inflation: float, years: int,
                  extra_monthly: float = 0, extra_from_year: int = 10 ** 6) -> tuple:
    """逐年模擬：資產先長、再提領（今年的生活費隨通膨調、扣掉那一年開始有的其他收入）。
    回 (第幾年用完 或 None, 終點還剩多少)。"""
    a = float(assets)
    for y in range(1, int(years) + 1):
        a *= 1 + float(rate)
        need = float(monthly) * 12 * (1 + float(inflation)) ** (y - 1)
        if y >= extra_from_year:
            need -= float(extra_monthly) * 12 * (1 + float(inflation)) ** (y - 1)
        a -= max(0.0, need)
        if a < 0:
            return y, 0
    return None, _m(a)


def fire_block(financial: float, cash3: float, monthly_used: float, growth: dict, fire: dict, this_year: int,
               earmark_total: float = 0) -> dict:
    """財富自由：此刻不工作、沒收入，每月可以花多少；照現在的花法錢什麼時候用完。
    資產＝金融資產（五層合計）；支出＝必要支出＋不工作後要自付的固定支出（健保、國保）。
    提領率的研究都是美國市場資料、30 年退休期、50–75% 股票；要撐 50 年以上又股票比重高的人用 3.25–3.5%。"""
    # 🔴 na 看的是 monthly_used（帳本真的有沒有生活支出資料），不是 spend ——
    #    spend 還加了「不工作後自付的健保、國保」（預設 2,000），永遠 > 0，
    #    於是新帳本會拿那 2,000 當生活費宣告「已達財富自由」。可撐月數那邊同樣情況回 None，兩邊要一致。
    spend = float(monthly_used or 0) + float(fire["self_pay_monthly"])
    by_rate = {f"{r:g}": _m(float(financial) * r / 12) for r in FIRE_RATES}
    chosen = float(fire["withdrawal_rate"])
    allowed = _m(float(financial) * chosen / 12)
    age = (this_year - int(fire["birth_year"])) if fire["birth_year"] and this_year else None
    horizon = max(1, int(fire["until_age"]) - age) if age is not None else FIRE_DEFAULT_HORIZON
    # 通膨指數是 y-1 → 第 y 年跨的是 age+y-1 歲；年金從 E 歲開始＝第 (E-age+1) 年
    extra_from = max(1, int(fire["extra_from_age"]) - age + 1) if age is not None else 10 ** 6
    base = {"spend": _m(spend), "self_pay": _m(fire["self_pay_monthly"]), "allowed": allowed, "by_rate": by_rate,
            "withdrawal_rate": chosen, "age": age, "until_age": int(fire["until_age"]), "horizon_years": horizon}
    if float(monthly_used or 0) <= 0 or financial <= 0 or spend <= 0:
        return {**base, "state": "na", "verdict": "先設定每月必要支出才算得出來。", "lines": []}
    current_rate = spend * 12 / float(financial)
    fi25, fi33 = spend * 12 * 25, spend * 12 * 100 / 3
    r, infl = float(growth["rate"]), float(growth["inflation"])
    runs_out, left = fire_simulate(financial, spend, r, infl, horizon, fire["extra_monthly"], extra_from)
    runs_out0, _ = fire_simulate(financial, spend, 0.0, infl, horizon, fire["extra_monthly"], extra_from)
    # 🔴 扣掉預留，跟同一頁上面的可撐月數同口徑 —— 不扣的話兩個數字會互相矛盾
    cash_years = round(max(0.0, float(cash3) - float(earmark_total or 0)) / spend / 12, 1)
    end_txt = f"{int(fire['until_age'])} 歲" if age is not None else f"第 {horizon} 年"
    when = lambda y: f"第 {y} 年" + (f"（{age + y - 1} 歲）" if age is not None and y else "")  # noqa: E731
    within = current_rate <= chosen
    if within and runs_out is None:
        st = STATE_OK
        vd = (f"已達財富自由。照現在的花法（{_wan(spend)}／月）只用到資產的 {current_rate:.2%}，低於 {chosen:.1%} 的安全提領率；"
              f"模擬到 {end_txt}用不完。要注意的不是錢夠不夠，是股票比重與現金層：現金只撐 {cash_years:g} 年不賣股票。")
    elif runs_out is None:
        st = STATE_WARN
        vd = (f"模擬到底用不完，但現在的花法用到資產的 {current_rate:.2%}，高於你設的 {chosen:.1%}。"
              f"這代表答案很依賴報酬率假設（{r:.0%}）；報酬差一點就撐不到。")
    elif within:
        st = STATE_WARN
        vd = (f"提領率 {current_rate:.2%} 在安全範圍內，但照 {r:.0%} 報酬模擬，{when(runs_out)}會用完 —— "
              f"通膨與年數把它吃掉了。把「退休後其他收入」填進來再看。")
    else:
        st = STATE_BAD
        vd = (f"還沒到。現在的花法用到資產的 {current_rate:.2%}，{when(runs_out)}會用完。"
              f"要嘛每月支出降到 {_wan(allowed)} 以下，要嘛資產再多 {_wan(max(0.0, fi25 - financial))} 到 25 倍門檻。")
    return {
        **base, "current_rate": round(current_rate, 4),
        "fi25": _m(fi25), "fi33": _m(fi33), "ratio25": round(float(financial) / fi25, 2), "ratio33": round(float(financial) / fi33, 2),
        "runs_out_year": runs_out, "runs_out_age": (age + runs_out - 1) if (age is not None and runs_out) else None,
        "left_at_end": left, "runs_out_year_zero": runs_out0, "cash_years": cash_years, "state": st, "verdict": vd,
        "lines": [["每月支出（含不工作後自付的健保、國保）", _m(spend), "twd"],
                  ["你現在的花法對應提領率", round(current_rate * 100, 2), "pct"],
                  ["25 倍法則的門檻", _m(fi25), "twd"], ["33 倍法則的門檻", _m(fi33), "twd"],
                  ["只靠現金不賣股票可以撐", cash_years, "years"],
                  [f"報酬 {r:.0%}、通膨 {infl:.0%}，錢用完在", runs_out, "year_or_never"],
                  ["報酬 0% 的話用完在", runs_out0, "year_or_never"]],
    }


def _ladder_block(have: dict, settings: dict, liabilities: dict) -> dict:
    """財富階梯那一段：你在第幾階、贏過台灣多少家庭、規劃欄位的答案、這一階該把力氣放哪。
    淨值＝五層合計 ＋ 房產（手填估值）− 負債（卡債＋貸款剩餘本金）。不含應收帳款與器材
    （階梯問的是「不用想就能花多少」，那些不是能隨手花的錢）。房產只進這裡，不進五層與可撐月數。"""
    ladder, plan, growth = settings["ladder"], settings["plan"], settings["growth"]
    debt = sum(int(v or 0) for v in liabilities.values())
    prop = int(settings["property"]["value"] or 0)
    net = sum(have.values()) + prop - debt          # 房產只在這裡算（見 DEFAULT_PROPERTY）
    pos = ladder_position(net, ladder)
    target = ladder["thresholds"][plan["target_rung"] - 2]
    return {
        **pos,
        "liabilities": {"total": _m(debt), **{k: _m(v) for k, v in liabilities.items()}},
        "property": {"value": _m(prop), "note": settings["property"]["note"]},
        "financial": _m(sum(have.values())),
        "percentile": percentile_note(net, ladder),
        "plan": {**plan, **plan_result(have[5], target, plan["target_years"], growth)},
        "focus": effort_focus(have[5], growth),
        "settings": ladder,
    }


def project_growth(base: float, growth: dict, years=GROWTH_YEARS) -> list:
    """第 5 層複利資本往後推。回 [{year, nominal, real, added}]。
    名目＝複利＋每年再投入（年底投入）；real＝換算成今天的購買力（除以通膨）。
    🔴 兩個都要給：只看名目會高估未來買得起什麼 —— 30 年後的 1000 萬不是今天的 1000 萬。"""
    r = float(growth["rate"])
    infl = float(growth["inflation"])
    add = float(growth["annual_add"])
    out = []
    for y in years:
        grown = float(base) * (1 + r) ** y
        contrib = add * (((1 + r) ** y - 1) / r if r else y)
        nominal = grown + contrib
        out.append({"year": y, "nominal": _m(nominal), "real": _m(nominal / ((1 + infl) ** y)),
                    "added": _m(add * y)})
    return out


def pick_loan_dues(rows, today: str) -> list:
    """貸款期別 [(loan_id, 'YYYY-MM-DD', amount)…] → 要進預留的那幾期 [(loan_id, due, amount, overdue)…]。
    🔴 逾期的**全部**留著（欠三期就是三期），再加每筆貸款未來最近的一期。
    只留「最近一期」的話，欠了三期的人看到的預留只有一期 —— 少算的方向是危險的。"""
    out, seen_future = [], set()
    for loan_id, due, amount in sorted(rows, key=lambda r: (str(r[1]), str(r[0]))):
        overdue = str(due) <= str(today)
        if overdue:
            out.append((loan_id, due, int(amount or 0), True))
        elif loan_id not in seen_future:
            seen_future.add(loan_id)
            out.append((loan_id, due, int(amount or 0), False))
    return sorted(out, key=lambda r: (str(r[1]), str(r[0])))


def _wan(x: float) -> str:
    """金額 → 「12.5 萬」／一億以上「1.2 億」（結論句用；數字欄位另外回原始整數）。"""
    a = abs(float(x))
    if a >= 1e8:
        return f"{round(float(x) / 1e8, 2):,g} 億"
    return f"{round(float(x) / 10000, 1):,g} 萬"


# ── 組整份 ────────────────────────────────────────────────────────
def build(accounts: list, earmarks: list, monthly_need_auto: float, settings: dict, today: str = "",
          need_months: int = 0, warnings: list = None, liabilities: dict = None) -> dict:
    """整頁要的東西一趟算完。
    accounts：見 assign_layers；earmarks：[{id, label, amount, due_date, source, source_ref, paid, note}]（paid 的不算進合計）；
    monthly_need_auto：近幾個月平均（router 算）；settings：normalize_settings 過的。"""
    settings = normalize_settings(settings)
    accts = assign_layers(accounts, settings)
    have = layer_sums(accts)
    ear_total = sum(int(e.get("amount") or 0) for e in earmarks if not e.get("paid"))
    used = settings["monthly_need_override"] if settings["monthly_need_override"] else int(round(monthly_need_auto or 0))
    # 可撐月數的分子是**現金**：放在第 1–4 層的證券不算（同 stress_tests，見 cash_in_layers）
    cash3 = cash_in_layers(accts, 3)
    cash4 = cash_in_layers(accts, 4)
    rw = runway_months(cash3, ear_total, used)
    rw4 = runway_months(cash4, ear_total, used)
    targets = layer_targets(used, ear_total, settings["targets"])
    layers = []
    for n in (5, 4, 3, 2, 1):
        tgt = targets[n]
        gap = None if tgt is None else _m(tgt - have[n])
        pct = 100 if tgt is None or tgt <= 0 else max(0, min(100, int(round(have[n] / tgt * 100))))
        if n == 2:
            rule = "預留清單合計"
        elif n == 5:
            rule = "沒有上限"
        else:
            rule = f"{settings['targets'][n]:g} 個月必要支出"
        layers.append({"no": n, "name": LAYER_NAMES[n], "desc": LAYER_DESC[n], "have": _m(have[n]),
                       "target": None if tgt is None else _m(tgt), "target_rule": rule, "gap": gap, "pct": pct,
                       "accounts": [{"id": a.get("id"), "name": a.get("name"), "kind": a.get("kind"), "balance": int(a.get("balance") or 0)}
                                    for a in accts if a["layer"] == n]})
    return {
        "today": today,
        "warnings": list(warnings or []),
        "monthly_need": {"auto": int(round(monthly_need_auto or 0)), "override": settings["monthly_need_override"],
                         "used": used, "sample_months": int(need_months or 0)},
        "cash": {"l1_3": _m(cash3), "l1_4": _m(cash4)},
        "earmark_total": _m(ear_total),
        "runway": {"months": rw, "with_l4": rw4, "tone": tone(rw)},
        "layers": layers,
        "earmarks": sorted(earmarks, key=lambda e: (bool(e.get("paid")), str(e.get("due_date") or ""))),
        "tests": stress_tests(have, accts, used, ear_total, settings["war"], settings["care"]),
        "projection": {"base": _m(have[5]), **settings["growth"],
                       "rows": project_growth(have[5], settings["growth"])},
        "ladder": _ladder_block(have, settings, liabilities or {}),
        "fire": fire_block(sum(have.values()), cash3, used, settings["growth"], settings["fire"],
                           int(str(today)[:4]) if str(today)[:4].isdigit() else 0, ear_total),
        "accounts": [{"id": a.get("id"), "name": a.get("name"), "kind": a.get("kind"), "balance": int(a.get("balance") or 0),
                      "currency": a.get("currency") or "TWD", "layer": a["layer"], "flags": a["flags"]} for a in accts],
        "settings": settings,
    }
