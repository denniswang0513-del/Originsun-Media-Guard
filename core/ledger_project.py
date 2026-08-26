# -*- coding: utf-8 -*-
"""core/ledger_project.py — 逐案損益的欄位定義與算式（純函式，無 I/O）。

owner 私帳「結案總表」那張表的規則正本。放 core 而不是留在 router，理由與
core/crm_logic.py 檔頭同一條：**錢流判定是帳務的 source of truth，endpoint 只
負責把 DB 加總餵進來**。實際代價當天就付了 —— scripts/backfill_my_ledger_detail
一開始複製了一份 `computed_net()`（router 沒法便宜地被腳本 import），於是
docstring 自稱正本的算式在第一天就有兩份（/simplify 2026-08-25 抓到）。

算式是 2026-08-25 對 402 案反推 Sheet 驗證出來的：
  實收 = 營收(含稅) − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來  （395/402 吻合）
  檢查 = 實收 − Σ工項（應為 0）                                    （397/402 吻合）
不吻合的是 Sheet 自己帳不平的那幾案，照實算、用「檢查」欄顯示出來，不修不猜。

🔴 `invoice_fee` 是獨立欄不由 `tax_fee + buy_invoice` 推導 —— 402 案裡有 1 案
兩者不等（IGER DAY 講座側錄），推導會靜默改掉來源資料。
"""
from __future__ import annotations

import re

# 工項（收入拆分）預設清單 —— 對齊 owner 原 Sheet 的欄序。
# settings `my_ledger.income_items` 可覆寫（見 income_items()）。
DEFAULT_INCOME_ITEMS = ["前期製作", "動態攝影", "剪輯", "調光", "動態效果",
                        "平面攝影", "諮詢", "教學", "錄混音", "其他"]

# ledger_detail 的費用欄（鍵 → 中文標籤）。UI 照這個順序畫；匯入腳本用它的
# 反向對照（中文標籤 → 鍵）把 Sheet 欄位對進來 —— 清單只有這一份。
COST_FIELDS = [
    ("outsource", "委外費用"),
    ("tax_fee", "稅金"),
    ("buy_invoice", "買發票"),
    ("invoice_fee", "發票代辦費"),
    ("personal_tax", "個人稅款"),
    ("misc", "雜支"),
    ("shareholder", "股東往來"),
]

COST_KEYS = [k for k, _label in COST_FIELDS]

# 逐案清單裡要加總成合計列的鍵（一份清單，別在端點裡再列一次）
SUM_KEYS = ("contract", "received", "receivable", "spent", "ap_open", "net")


def income_items(settings: dict | None = None) -> list:
    """工項清單。settings 傳 None 時回預設（純函式，讀設定由呼叫端負責）。"""
    items = ((settings or {}).get("my_ledger") or {}).get("income_items")
    return [str(x) for x in items if str(x).strip()] if items else list(DEFAULT_INCOME_ITEMS)


# 案源（owner 2026-08-25）：源日＝現金收款；代開發票＝營收 × 服務費率的代辦費
# （預設 8%，191 個歷史案實證全部 8.00%；逐案可調）。自接＝中性。
SOURCES = ("自接", "源日", "代開發票")
DEFAULT_FEE_PCT = 8.0

# 案碼協定：匯入/新增都把 `案碼:XXX` 寫進 notes（前綴不同：[私帳匯入]/[私帳新增]），
# 收支回掛與撞碼防線都靠它。
_CODE_NOTE_RE = re.compile(r"案碼:(\S+)")


def code_of(notes) -> str:
    """notes 裡的案碼；無案碼（含匯入時寫的「無」）→ 空字串。

    🔴 這個協定的**唯一解析器**。讀取端曾經各解各的（撞碼防線用 SQL LIKE
    行尾錨點、回填腳本用 regex）——「案碼:2026010 補」這種尾註列 regex 認、
    LIKE 不認，防線就漏了。解析只留這一份，讀案碼的一律呼叫這支。
    """
    m = _CODE_NOTE_RE.search(notes or "")
    code = m.group(1) if m else ""
    return "" if code == "無" else code


def norm_detail(raw) -> dict:
    """ledger_detail → 固定形狀（缺鍵補 0、split 只留非零數字）。

    0 值工項會被清掉 —— 那正是「使用者把格子清空」的意思（＝刪掉這個工項）。
    另保留兩個 meta 鍵：source（案源）與 fee_pct（服務費率；只在非預設時存）。
    """
    d = raw if isinstance(raw, dict) else {}
    out = {k: int(d.get(k) or 0) for k in COST_KEYS}
    split = d.get("split") if isinstance(d.get("split"), dict) else {}
    out["split"] = {str(k): int(v) for k, v in split.items()
                    if isinstance(v, (int, float)) and int(v)}
    src = str(d.get("source") or "").strip()
    if src in SOURCES:
        out["source"] = src
    try:
        pct = float(d.get("fee_pct"))
        if pct > 0 and pct != DEFAULT_FEE_PCT:
            out["fee_pct"] = pct
    except (TypeError, ValueError):
        pass
    return out


def apply_source_fee(contract: int, d: dict) -> dict:
    """案源＝代開發票 → 三個欄位自動算（**唯一的自動費用規則**，寫入端呼叫；
    其他案源不動使用者填的數字）。owner 2026-08-25：「稅金5%+買發票＝代辦
    發票的%數，這邊可以直接自動計算」，Sheet 實證吻合（82,000 →
    稅金 3,905 + 買發票 2,655 = 代辦費 6,560 = 8%）：

        發票代辦費 = round(營收 × 費率)          —— 給代開業者的總服務費
        稅金       = round(營收 / 1.05 × 5%)     —— 其中的營業稅（未稅 × 5%）
        買發票     = 代辦費 − 稅金               —— 其中的開票佣金

    稅金與買發票是代辦費的**組成**，不再另外進實收的減項（compute 只扣
    invoice_fee —— 三個都扣就是同一筆錢扣兩次）。"""
    if d.get("source") == "代開發票":
        c = int(contract or 0)
        pct = float(d.get("fee_pct") or DEFAULT_FEE_PCT)
        d["invoice_fee"] = round(c * pct / 100)
        d["tax_fee"] = round(c / 1.05 * 0.05)
        d["buy_invoice"] = d["invoice_fee"] - d["tax_fee"]
    return d


def receivable_status(contract: int, received: int) -> str:
    """收款狀態判定 —— 收支同步（_sync_mine_project_received）與執行專案的
    新增/更新端點共用同一條（owner 2026-08-26 實測：新增端點沒初始化
    amount_receivable，新案永遠不進應收帳款）。"""
    if contract > 0 and received >= contract:
        return "全額到帳"
    if received > 0:
        return "部分到帳"
    return "未到帳"


def compute(contract: int, d: dict) -> tuple:
    """(實收, 檢查)。算式正本 —— 前端與匯入腳本都只呼叫這支，不各算一份。"""
    net = (contract - d["outsource"] - d["invoice_fee"]
           - d["personal_tax"] - d["misc"] - d["shareholder"])
    return net, net - sum(d["split"].values())
