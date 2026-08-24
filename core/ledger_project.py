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


def norm_detail(raw) -> dict:
    """ledger_detail → 固定形狀（缺鍵補 0、split 只留非零數字）。

    0 值工項會被清掉 —— 那正是「使用者把格子清空」的意思（＝刪掉這個工項）。
    """
    d = raw if isinstance(raw, dict) else {}
    out = {k: int(d.get(k) or 0) for k in COST_KEYS}
    split = d.get("split") if isinstance(d.get("split"), dict) else {}
    out["split"] = {str(k): int(v) for k, v in split.items()
                    if isinstance(v, (int, float)) and int(v)}
    return out


def compute(contract: int, d: dict) -> tuple:
    """(實收, 檢查)。算式正本 —— 前端與匯入腳本都只呼叫這支，不各算一份。"""
    net = (contract - d["outsource"] - d["invoice_fee"]
           - d["personal_tax"] - d["misc"] - d["shareholder"])
    return net, net - sum(d["split"].values())
