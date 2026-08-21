"""core/bank_statement.py — 銀行對帳單純文字 → 結構化交易列（純函式，無 I/O）。

為什麼不做「每家銀行一個欄位對照表」：各行的匯出格式欄序不同、空白欄有的填
`-` 有的直接消失（實測合庫沒有佔位符、一銀有），逐行硬寫對照表是永遠補不完的
清單，而且一旦欄位錯位就**靜默**把支出當存入 —— 財務資料最不能接受的失敗方式。

改用**餘額鏈**：對帳單每列都有「這筆之後的餘額」，所以

    本列金額（帶號） = 本列餘額 − 上列餘額

正負號、金額大小全部由餘額推出來，完全不依賴欄序。再拿列上其它數字回頭核對
（|金額| 必須真的出現在該列），以及銀行自己印的總計交叉驗證 —— 三重把關，
對不上就整份拒收，不猜。

第一列沒有前一列可減：優先用銀行印的總計反推（總計 − 其餘列合計），沒有總計
才退回關鍵字判斷，並在結果標 `inferred=True` 讓 UI 標示要人確認。

輸入可以是 PDF 抽出來的文字、從網銀複製貼上的表格、或 CSV 貼上 —— 一律當
「一列一筆交易的文字」處理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

# 日期：2025/09/10、2025-09-10、114/07/01（民國）、2025年9月10日。
# 🔴 民國年一定要收：台灣網銀匯出常見 3 碼年（114/07/01 = 2025-07-01），
# 只認西元的話整份對帳單一列都解析不出來。年份 < 1000 視為民國。
_DATE = re.compile(r"(\d{2,4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})")
# 金額：1,234.00 / 1234。千分位那支要**至少一組** `,\d{3}`，否則 `\d{1,3}` 會先
# 咬掉 206895 的前三碼、剩下 895.00 被當成餘額（實測合庫餘額整欄被解析成三位數）。
# 前後 lookaround 確保是完整 token，不是長數字串（帳號）的中段。
_AMOUNT = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d,])")

# 摘要關鍵字 → (系統 category, 是否為貸款繳款)。第一列無前列可推時也靠這判方向。
# 值域對齊 finance_category_map 的 source='cash' 對映（沒有的會在預覽標出來）。
KEYWORD_RULES = [
    # (關鍵字, category, 支出/存入方向提示：-1=支出 +1=存入 0=不確定)
    ("放款本息", "貸款繳款", -1),
    ("攤還本息", "貸款繳款", -1),
    ("償還本息", "貸款繳款", -1),
    ("中心轉存", "貸款補貼", +1),
    ("補貼息", "貸款補貼", +1),
    # 一銀把中小企業信保的利息補貼記成「中小７月」「中小十一」這種摘要
    # （合庫的對應物是「中心轉存／文創補貼息」）。都是補貼入帳。
    ("中小", "貸款補貼", +1),
    ("活存息", "銀行利息", +1),
    ("利息", "銀行利息", +1),
    ("跨行轉入", "轉存", +1),
    ("跨行轉帳", "轉存", 0),
    ("網路轉帳", "轉存", 0),
    ("轉存", "轉存", 0),
    ("無摺轉支", "其他", -1),
    ("手續費", "其他", -1),
]

LOAN_CATEGORY = "貸款繳款"

# 繳款日與期別到期日容許差幾天（銀行常延到下一個營業日；合庫實測 11-10 對 11-08）。
MAX_DAY_GAP = 20



@dataclass
class StmtRow:
    """一列銀行交易。amount 帶號：正=存入、負=支出。"""
    line_no: int
    date: str                      # 'YYYY-MM-DD'
    amount: int                    # 帶號
    balance: int
    # 摘要與備註合成一段（銀行的欄位切法各家不同，硬拆只是製造第二個真相）。
    # 🔴 matcher 靠 note 找放款帳號 —— 只留這一個名字，不再有 description/raw 分身。
    note: str = ""
    category: str = ""
    inferred: bool = False         # 方向靠關鍵字猜的（第一列且無總計）


@dataclass
class ParseResult:
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # 看起來像資料但解析不了的原文

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)

    @property
    def total_in(self) -> int:
        return sum(r.amount for r in self.rows if r.amount > 0)

    @property
    def total_out(self) -> int:
        return -sum(r.amount for r in self.rows if r.amount < 0)


def _to_int(tok: str):
    """'1,234.00' → 1234；會計括號 '(1,234)' → -1234（半形與全形都收）。"""
    t = tok.replace(",", "").replace("，", "").strip()
    if t[:1] in ("(", "（") and t[-1:] in (")", "）"):
        t = "-" + t.strip("()（）")
    if t in ("", "-"):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return int(round(v))


def _classify(text: str, rules=None):
    """摘要文字 → (category, 方向提示)。沒中回 ('', 0)。

    rules = [(關鍵字, category, 方向)]，由呼叫端給（正式路徑是 DB 的
    bank_import_rules，使用者可編）。不給就用模組內建的 KEYWORD_RULES ——
    那份現在的角色是**種子與離線預設**，不是唯一真相。
    順序即優先序：先命中的先贏，所以呼叫端要照 sort_order 排好再傳進來。
    """
    for kw, cat, direction in (rules if rules is not None else KEYWORD_RULES):
        if kw and kw in text:
            return cat, direction
    return "", 0


_TOTAL_LABEL = re.compile(r"[^\s]*?(?:總筆數|總金額|金額總計)")


def _find_totals(text: str):
    """抓銀行自己印的總計。回 (out_total, in_total)，抓不到回 (None, None)。

    兩家的總計都是**標籤一行、數值下一行**，而且標籤數不固定（一銀 2 欄：
    支出/存入金額總計；合庫 4 欄：提款總筆數/總金額、存款總筆數/總金額）。
    所以不寫「標籤後面第幾個數字」那種一定會錯位的規則 —— 改成標籤依序抓、
    數值行的數字依序抓，兩邊等長才 zip。對不上就當沒有總計（少一層驗證，
    parse_statement 會發警告），不硬取。
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        labels = _TOTAL_LABEL.findall(ln)
        if len(labels) < 2:
            continue
        # 數值可能在標籤同一行，沒有就看下一行
        for cand in (ln, lines[i + 1] if i + 1 < len(lines) else ""):
            nums = [_to_int(t) for t in _AMOUNT.findall(cand)]
            nums = [n for n in nums if n is not None]
            if len(nums) != len(labels):
                continue
            pair = dict(zip(labels, nums))
            out_t = next((v for k, v in pair.items()
                          if ("支出" in k or "提款" in k) and "筆數" not in k), None)
            in_t = next((v for k, v in pair.items()
                         if ("存入" in k or "存款" in k) and "筆數" not in k), None)
            if out_t is not None or in_t is not None:
                return out_t, in_t
    return None, None


def _parse_line(raw: str):
    """一行文字 → (date, [候選金額], balance, 文字部分)。不是交易列回 None。

    規則：行內要有日期，且日期之後至少兩個數字（金額 + 餘額）。**最後一個**
    數字當餘額 —— 各行的餘額欄都排在金額後面，票號/備註等尾隨欄位是文字或
    純數字串（帳號/序號），所以只取「帶千分位或小數點」的數字當候選金額，
    避免把 13366031755 這種帳號當成金額。
    """
    m = _DATE.search(raw)
    if not m:
        return None
    year = int(m.group(1))
    if year < 1000:                     # 民國 → 西元
        year += 1911
    month, day = int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None                     # 不是日期（例：金額被誤配）
    date = f"{year:04d}-{month:02d}-{day:02d}"
    tail = raw[m.end():]
    # 交易時間 hh:mm:ss 先拿掉，免得 01:22:03 被當數字碎片
    tail = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", " ", tail)
    # 候選金額只認「有千分位逗號」或「有小數點」的 token —— 純數字長串是帳號
    cands = []
    for tok in _AMOUNT.findall(tail):
        if "," not in tok and "." not in tok:
            continue
        v = _to_int(tok)
        if v is not None:
            cands.append(v)
    if len(cands) < 2:
        return None
    balance = cands[-1]
    amounts = [abs(v) for v in cands[:-1] if v]
    # 文字部分：只拿掉**金額**token（帶千分位或小數點那些）與幣別雜訊。
    # 🔴 純數字串要留著 —— 合庫備註的放款帳號就是 `09-08 315614` 的 315614，
    # 貸款配對全靠它；早期版本把所有數字都濾掉，配對率直接掛零。
    words = [w for w in re.split(r"\s+", tail)
             if w and not (_AMOUNT.fullmatch(w) and ("," in w or "." in w))
             and w not in ("新臺幣", "新台幣", "TWD", "-")]
    return date, amounts, balance, " ".join(words).strip()


def parse_statement(text: str, opening_balance: int = None,
                    rules=None) -> ParseResult:
    """對帳單文字 → ParseResult。見模組檔頭的三重把關。

    opening_balance：知道對帳單期初餘額就傳，第一列的方向會由它決定（最準）。
    rules：分類規則 [(關鍵字, category, 方向)]，不給就用內建 KEYWORD_RULES。
    """
    res = ParseResult()
    parsed = []
    for i, raw in enumerate(text.splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        got = _parse_line(raw)
        if got:
            parsed.append((i, raw) + got)
        elif _DATE.search(raw) and re.search(r"\d[\d,]*\.\d{2}", raw):
            res.skipped.append(f"第 {i} 行：{raw[:80]}")

    if not parsed:
        res.errors.append("找不到任何交易列 —— 這份檔案可能是掃描影像（沒有文字層），"
                          "或格式不是逐列的交易明細。可改用「從網銀複製貼上」。")
        return res

    out_total, in_total = _find_totals(text)

    # ── 餘額鏈：第 2 列起金額＝本列餘額 − 上列餘額 ──
    rows = []
    for idx, (line_no, raw, date, amounts, balance, words) in enumerate(parsed):
        signed = None
        if idx > 0:
            signed = balance - rows[idx - 1]["balance"]
        elif opening_balance is not None:
            signed = balance - opening_balance
        rows.append(dict(line_no=line_no, raw=raw, date=date, amounts=amounts,
                         balance=balance, words=words, signed=signed))

    # 第一列：優先用銀行總計反推，其次關鍵字
    first = rows[0]
    inferred_first = False
    if first["signed"] is None:
        rest = sum(r["signed"] for r in rows[1:] if r["signed"] is not None)
        solved = None
        if out_total is not None and in_total is not None:
            solved = (in_total - out_total) - rest
            # 反推出來的第一列金額必須真的出現在該列 —— 對不上代表這份不是完整
            # 的對帳單（只貼了一段，總計卻是整份的），此時總計不能拿來推方向。
            if abs(solved) not in first["amounts"]:
                solved = None
        if solved is not None:
            first["signed"] = solved
        else:
            cat, direction = _classify(first["words"], rules)
            amt = first["amounts"][0] if first["amounts"] else 0
            first["signed"] = amt * (direction if direction else -1)
            inferred_first = True
            # 🔴 direction == 0 是「這個關鍵字本身講不出方向」（轉存/跨行轉帳/
            # 網路轉帳三條規則都是 0，因為它們兩個方向都常見），不是「方向是負」。
            # 落到 -1 只是預設值，不是判斷 —— 一筆客戶匯進來的 500,000 轉存會被
            # 寫成 -500,000，帳差 100 萬，而回頭核對只驗 abs() 所以驗不出來。
            # 這種列一定要人看過，所以警告要講清楚是「猜的」還是「推的」。
            if direction:
                res.warnings.append(
                    f"第 1 列（{first['date']} {first['words'][:20]}）沒有前一列餘額也沒有"
                    f"總計可核對，方向是照摘要文字推的 —— 請確認是"
                    f"{'存入' if first['signed'] > 0 else '支出'}。")
            else:
                res.warnings.append(
                    f"第 1 列（{first['date']} {first['words'][:20]}）沒有前一列餘額、"
                    f"沒有總計，摘要「{cat or '未分類'}」本身也看不出方向 —— "
                    f"這裡**先當支出**填，請務必自己確認；若是進帳請改成存入。")

    # ── 回頭核對：|金額| 必須真的出現在該列的數字裡 ──
    for r in rows:
        amt = abs(r["signed"] or 0)
        if amt and r["amounts"] and amt not in r["amounts"]:
            res.errors.append(
                f"第 {r['line_no']} 行餘額推出的金額 {amt:,} 與列上數字 "
                f"{r['amounts']} 對不上：{r['raw'][:70]}")

    for r in rows:
        cat, _ = _classify(r["words"], rules)
        res.rows.append(StmtRow(
            line_no=r["line_no"], date=r["date"], amount=r["signed"] or 0,
            balance=r["balance"], note=r["words"], category=cat,
            inferred=(inferred_first and r is first)))

    # ── 交叉驗證：與銀行印的總計比對 ──
    _partial = ("（最常見原因：只貼了對帳單的一部分，但總計是整份的 —— "
                "請把該期間全部交易一起貼上，或改用上傳整份檔案）")
    if out_total is not None and res.total_out != out_total:
        res.errors.append(f"支出合計 {res.total_out:,} 與對帳單印的總計 "
                          f"{out_total:,} 不符{_partial}")
    if in_total is not None and res.total_in != in_total:
        res.errors.append(f"存入合計 {res.total_in:,} 與對帳單印的總計 "
                          f"{in_total:,} 不符{_partial}")
    if out_total is None and in_total is None:
        res.warnings.append("這份對帳單沒有印總計 —— 少一層交叉驗證，匯入前請自行核對筆數。")
    if res.skipped:
        # 原文直接寫進警告 —— 舊版寫「（見明細）」，但那份明細前端從來沒有畫出來，
        # 等於叫人去看不存在的東西。跳過哪幾行只有原文講得清楚，就放在這裡。
        head = "；".join(res.skipped[:3])
        more = f"…（另有 {len(res.skipped) - 3} 行）" if len(res.skipped) > 3 else ""
        res.warnings.append(
            f"有 {len(res.skipped)} 行看起來像交易但解析不出來，已跳過：{head}{more}")
    return res


def _dparse(d):
    from datetime import date
    try:
        return date.fromisoformat(str(d)[:10])
    except (ValueError, TypeError):
        return None


def _by_account(loan, _period, remaining):
    """備註帶得出這筆貸款的放款帳號（取後 6 碼比對）→ 這些列就是它的。"""
    tail = (loan.get("account_no") or "").strip()[-6:]
    return [r for r in remaining if tail and tail in r.note] if tail else []


# 一筆貸款最多被銀行拆成幾筆扣款。實測真實對帳單最多 2 筆（一銀 150 萬分兩次
# 撥款 → 29,418＋3,269），3 是餘裕。有上限才不會在異常輸入（幾十筆同日繳款）
# 時把子集列舉變成 2^n 卡死 event loop。
MAX_SPLIT = 3


def _by_amount(loan, period, remaining):
    """湊得出「剛好等於該期應繳」的子集 → 那個子集就是這期的扣款。"""
    due = (period or {}).get("total")
    if not due:
        return []
    return next((list(c)
                 for n in range(1, min(len(remaining), MAX_SPLIT) + 1)
                 for c in combinations(remaining, n)
                 if -sum(r.amount for r in c) == due), [])


def match_loan_payments(rows, loans):
    """把「貸款繳款」的列配到貸款期別。

    銀行常把一筆貸款拆成多筆扣款（一銀 150 萬分兩次撥款 → 每月扣 29,418＋3,269），
    所以**同一天的貸款繳款列先合併成一組**，再去湊某筆貸款某一期的金額。

    期別用**到期日最接近**選（差距上限 MAX_DAY_GAP 天），不是「從第一期依序消耗」。
    依序消耗在三種真實情況會出錯：只匯某個月的對帳單（會配到第 1 期）、重匯已經
    匯過的月份（會往後推一期並蓋上錯的繳款日）、對帳單期間早於貸款建檔（歷史繳款
    被硬塞進未來期別）。到期日比對三種都自然處理 —— 差太多就配不到，交給人。

    loans：[{id, name, account_no, periods:[{period_no,total,due_date,paid}]}]
    confidence：'account'=備註帳號認出｜'exact'=金額吻合｜'already_paid'=該期已經
    記過繳款（重匯偵測）｜''=配不到，UI 讓人手選。**配不到就是配不到，不硬猜。**
    """
    periods = {}
    for loan in loans:
        periods[loan["id"]] = [dict(p) for p in (loan.get("periods") or []) if p]
    claimed = set()

    def nearest(loan_id, date_str):
        """該貸款離這個繳款日最近、且還沒被這批認領走的期別。"""
        d = _dparse(date_str)
        best = bestgap = None
        for p in periods.get(loan_id, []):
            pd = _dparse(p.get("due_date"))
            if not (d and pd) or (loan_id, p.get("period_no")) in claimed:
                continue
            gap = abs((pd - d).days)
            if gap <= MAX_DAY_GAP and (bestgap is None or gap < bestgap):
                best, bestgap = p, gap
        return best

    def emit(date, hits, loan, p, how):
        claimed.add((loan["id"], p["period_no"]))
        return dict(date=date, lines=[r.line_no for r in hits],
                    amount=-sum(r.amount for r in hits),
                    loan_id=loan["id"], loan_name=loan.get("name", ""),
                    period_no=p["period_no"],
                    confidence="already_paid" if p.get("paid") else how)

    by_date = {}
    for r in rows:
        if r.category != LOAN_CATEGORY or r.amount >= 0:
            continue
        by_date.setdefault(r.date, []).append(r)

    out = []
    for date in sorted(by_date):
        remaining = list(by_date[date])
        # 兩種策略依序試，每種都掃過所有貸款。帳號優先 —— 銀行月付金額固定、
        # 系統用剩餘本金重算會差幾十元，所以備註裡的放款帳號比金額可靠。
        # 「認領走 remaining」只寫一次：早期版本兩趟各寫一遍，加第三種策略時
        # 只要漏掉那行就會重複認領同一列（claimed 正是為了防這件事）。
        for how, pick in (("account", _by_account), ("exact", _by_amount)):
            for loan in loans:
                if not remaining:
                    break
                p = nearest(loan["id"], date)
                if not p:
                    continue
                hits = pick(loan, p, remaining)
                if not hits:
                    continue
                out.append(emit(date, hits, loan, p, how))
                remaining = [r for r in remaining if r not in hits]
        for r in remaining:
            out.append(dict(date=date, lines=[r.line_no], amount=-r.amount,
                            loan_id=None, loan_name="", period_no=None,
                            confidence=""))
    return out
