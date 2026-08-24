# -*- coding: utf-8 -*-
"""core/card_statement.py — 信用卡帳單純文字 → 結構化消費列（純函式，無 I/O）。

與 core/bank_statement.py 是同一族（輸入同樣是 PDF 抽字/網銀複製/CSV 的
「一列一筆」文字），但卡單**沒有餘額欄** —— 餘額鏈在這裡不存在，所以：

1. 金額取**面值**：一列的最後一個金額 token 是台幣入帳金額（外幣消費列的
   格式是「消費日 [入帳日] 商家 外幣金額 台幣金額」，台幣在最後）。
2. 方向靠關鍵字：退貨/退款/退刷/溢繳 → 負向；其餘消費列為正（支出）。
3. **繳款列不是消費**（自動扣繳/轉帳繳款/繳款）：那筆錢在銀行對帳單那側
   已經以「信用卡款」記過（transfer 清償），這裡再記一次就是重複支出 ——
   標成 kind='payment'，預覽預設排除。
4. **國外交易服務費跟著前一筆走**（kind='fee'）：它是前一筆外幣消費的附屬
   成本，分類應繼承前一筆（歷史模擬顯示這條規則本身就吃掉 ~7% 的分類量）。
5. 驗證比銀行對帳單弱（沒有餘額鏈可交叉）：找得到帳單自印的總計就核對，
   對不上出 warning 不擋 —— 卡單總計常含循環息/年費等非消費列。
   人本來就要在預覽逐列確認，這裡的職責是把 95% 的列擺對，不是免看。

merchant_key()：商家歸一鍵 —— 自動分類（歷史對映層）用它把「連加＊八方雲集
雙城店」「連加＊八方雲集站前店」之類歸到可比對的鍵。規則與 2026-08-24 的
歷史覆蓋率模擬同源（4,459 筆實測：規則+一致歷史 ≈ 38% 全自動）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 金額 token 與數值化與 bank_statement 共用同一份（同族解析器 —— 那兩條
# 規則各被修過兩次，留兩份等於下次只修得到一份）。
from core.bank_statement import _AMOUNT, _to_int  # noqa: E402

# 日期規則與 bank_statement 共用（民國年、分隔符集合都只留一份）——
# 卡單這邊原本自己寫了一份，分隔符還少了「年/月」，正是那條註解說的
# 「留兩份等於下次只修得到一份」（/simplify 2026-08-25）。
from core.bank_statement import _DATE  # noqa: E402

# 退款方向（金額記負）
_REFUND_KW = ("退貨", "退款", "退刷", "溢繳", "退費", "回饋金")
# 繳款列（排除，不是消費）—— 「繳款截止」是表頭資訊不是交易，另外排除
_PAYMENT_KW = ("自動扣繳", "轉帳繳款", "繳款", "存入", "自行繳納", "臨櫃繳")
_PAYMENT_NOT = ("繳款截止", "繳款期限", "最低應繳", "應繳金額", "應繳總額")
# 手續費附屬列（分類繼承前一筆消費）
_FEE_KW = ("國外交易服務費", "國外交易手續費", "海外交易手續費")
# 帳單自印總計的候選關鍵字（找到就跟解析合計交叉核對）
_TOTAL_KW = ("本期新增", "新增款項", "消費明細總計", "消費總計", "合計", "小計")


@dataclass
class CardRow:
    """一列卡單交易。amount 帶號：正=消費（支出）、負=退款。"""
    line_no: int
    date: str                      # 'YYYY-MM-DD'（消費日；一列兩個日期取第一個）
    amount: int
    note: str = ""                 # 商家/摘要（去掉日期與金額後的殘文）
    kind: str = "spend"            # spend / fee / payment


@dataclass
class CardParseResult:
    rows: list = field(default_factory=list)       # 消費與手續費列（不含繳款）
    payments: list = field(default_factory=list)   # 繳款列（資訊用，預設不匯）
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)

    @property
    def total_spend(self) -> int:
        return sum(r.amount for r in self.rows if r.amount > 0)

    @property
    def total_refund(self) -> int:
        return -sum(r.amount for r in self.rows if r.amount < 0)


def _norm_date(y: int, m: int, d: int) -> str | None:
    if y < 1000:
        y += 1911
    if not (2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def parse_card_statement(text: str) -> CardParseResult:
    """卡單文字 → CardParseResult。一列一筆；無日期或無金額的列跳過。"""
    res = CardParseResult()
    printed_totals: list[int] = []

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        dates = list(_DATE.finditer(line))
        amounts = list(_AMOUNT.finditer(line))
        # 沒日期的列：可能是帳單自印的總計 → 收起來當交叉核對
        if not dates:
            if amounts and any(k in line for k in _TOTAL_KW):
                v = _to_int(amounts[-1].group(0))
                if v:
                    printed_totals.append(v)
            continue
        # 金額 = 日期之後的最後一個金額 token（避免把日期 16 當金額）
        after = [a for a in amounts if a.start() >= dates[-1].end()]
        if not after:
            res.skipped.append(f"L{i}: {line[:60]}")
            continue
        amt = _to_int(after[-1].group(0))
        if amt is None or amt == 0:
            res.skipped.append(f"L{i}: {line[:60]}")
            continue
        date = _norm_date(*(int(g) for g in dates[0].groups()))
        if not date:
            res.skipped.append(f"L{i}: {line[:60]}")
            continue
        # 摘要 = 去掉日期與最後那個金額後的殘文
        cut = line[:after[-1].start()] + line[after[-1].end():]
        for dm in dates:
            cut = cut.replace(dm.group(0), " ", 1)
        note = re.sub(r"\s+", " ", cut).strip(" \t-—|")

        neg = ("-" in line[max(0, after[-1].start() - 1):after[-1].start()]
               or any(k in note for k in _REFUND_KW))
        row = CardRow(line_no=i, date=date, amount=-amt if neg else amt, note=note)

        if any(k in note for k in _PAYMENT_KW) and not any(k in note for k in _PAYMENT_NOT):
            row.kind = "payment"
            res.payments.append(row)
            continue
        if any(k in note for k in _FEE_KW):
            # 附掛前一筆：分類建議由消費端（preview）依此繼承
            row.kind = "fee"
        res.rows.append(row)

    if not res.rows:
        res.errors.append("解析不出任何消費列 —— 請確認貼的是含「日期＋金額」的交易明細")
        return res
    # 總計交叉核對（找得到才驗；卡單總計常含循環息等非消費列 → 只 warning）
    if printed_totals:
        net = res.total_spend - res.total_refund
        if not any(t in (net, res.total_spend) for t in printed_totals):
            res.warnings.append(
                f"帳單自印總計 {printed_totals} 與解析合計（消費 {res.total_spend:,}"
                f"／淨額 {net:,}）對不上 —— 可能含循環息/年費列，請逐列確認")
    return res


# ── 商家歸一鍵（自動分類的歷史對映層用）─────────────────────────────

_PAY_PREFIX = re.compile(r"^(連加[＊*]|街口電支[－\-]|街⼝電⽀[－\-]|悠遊付[－\-]?|全支付[－\-]?)")
# 獨立的數字 token（外幣金額 76.83）與幣別代碼 —— 卡單解析後的殘文會帶著它們，
# 不砍的話「SP MTMOGRAPH 76.83 USD」與歷史裡的「SP MTMOGRAPH 030743」永遠
# 對不上（同一家店、每月鍵都不同 → 歷史對映層整類 miss）。
# 🔴 只砍**前後有空白/邊界**的獨立 token：店名裡嵌著的數字（7-11、85度C）不動。
_LONE_NUM = re.compile(r"(?<!\S)[0-9]+(?:\.[0-9]+)?(?!\S)")
_CURRENCY = re.compile(r"(?<!\S)(USD|EUR|JPY|GBP|HKD|CNY|KRW|TWD|NTD?)(?!\S)", re.I)
_TAIL_NUM = re.compile(r"[0-9０-９]{3,}.*$")
_PAREN = re.compile(r"[（(].*$")


def merchant_key(summary: str) -> str:
    """商家摘要 → 歸一鍵。空字串＝歸一失敗（呼叫端跳過，不硬配）。

    規則：支付平台前綴拿掉留店名 → 砍獨立數字/幣別 token（外幣金額殘文）
    → 砍 3 位以上數字尾巴（交易編號/期數）→ 砍括號附註 → 去空白。
    與 2026-08-24 歷史覆蓋率模擬同一套。
    """
    s = (summary or "").split("\n")[0].split("\t")[0].strip()
    s = _PAY_PREFIX.sub("", s)
    s = _LONE_NUM.sub(" ", s)
    s = _CURRENCY.sub(" ", s)
    s = _TAIL_NUM.sub("", s)
    s = _PAREN.sub("", s)
    return re.sub(r"\s+", "", s)


# ── 分類建議（preview 的三層合成；純函式 —— 測試直接打這裡）────────────

def mark_duplicates(keys: list, seen) -> list:
    """逐列判「帳上是不是已經有這一筆了」。回傳與 keys 等長的 bool 清單。

    🔴 消耗式：帳上有 N 筆就只標 N 列，不是把整組同鍵都標掉。同一天真的可能
    刷兩筆一樣的錢（兩杯一樣的咖啡）—— 整組標成重複＝那第二筆永遠進不來。

    🔴 preview 與 apply **共用這一份**。兩邊各寫一份的下場實際發生過：apply
    那份邊跳邊回補 seen，數字會震盪（三筆同款只寫進兩筆），而且與 preview 對
    同一份輸入給出不同答案（/simplify 第 4 輪抓到）。一條規則只能有一個實作。

    seen 收 Counter（帶筆數）或 set（每鍵當一筆）皆可。
    """
    from collections import Counter
    left = Counter(seen)
    out = []
    for k in keys:
        hit = left[k] > 0
        if hit:
            left[k] -= 1
        out.append(hit)
    return out


def suggest_rows(rows, hist: dict, rules, seen) -> list:
    """解析列 → 帶三層建議與重複旗標的預覽列。

    優先序：手續費繼承前一筆消費 > 規則 > 歷史一致對映。
    - 規則層走 bank_statement._classify —— 與銀行對帳單**同一張規則表、同一套
      命中語意**（含 only_direction 方向條件；卡單 spend 為支出 → signed 取負）。
      自己寫第二份比對迴圈會丟掉方向守衛（/simplify 2026-08-24 抓到）。
    - 手續費只認「緊鄰的前一筆 spend」；🔴 前一筆**沒有建議時手續費也留白**——
      prev_cat 對每個 spend 都覆寫（含空值），否則手續費會越級抄到更早那筆
      的分類（dev 冒煙實抓）。
    """
    from core.bank_statement import _classify
    dups = mark_duplicates([(r.date, abs(r.amount)) for r in rows], seen)
    out, prev_cat = [], ""
    for r in rows:
        cat, source = "", ""
        if r.kind == "fee" and prev_cat:
            cat, source = prev_cat, "fee"          # 手續費跟前一筆
        if not cat:
            cat = _classify(r.note, rules, signed=-r.amount)[0]
            source = "rule" if cat else source
        if not cat:
            cat = hist.get(merchant_key(r.note), "")
            source = "history" if cat else source
        if r.kind == "spend":
            prev_cat = cat
        out.append({
            "line_no": r.line_no, "date": r.date, "amount": r.amount,
            "note": r.note, "kind": r.kind,
            "category": cat, "source": source,
        })
    for row, dup in zip(out, dups):
        row["duplicate"] = dup
    return out
