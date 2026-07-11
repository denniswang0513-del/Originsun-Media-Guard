"""財務管理階段二純邏輯（core/finance_logic.py — 比照 core/crm_logic.py 慣例）。

零 DB / FastAPI 依賴 — 收支分類、銀行流水/餘額、對帳差額、月份取值
都是公司帳務的判定規則，抽成純函式讓「規則」與「SQL 聚合」分離：
endpoint 只負責把 DB 撈出來的值餵進來。單元測試在
tests/unit/test_finance_logic.py（含 27 個 cash category 全覆蓋）。
"""
from __future__ import annotations

import re
from datetime import date, datetime

# 關聯 id 優先序（高→低）：預支 → 發票(AR) → 請款單(AP) → category 對映。
# 硬連結（記錄上綁了誰）永遠壓過文字 category — category 是人填的、會漂。
_LINK_PRIORITY = (
    ("advance_payment_id", "advance"),
    ("invoice_id", "ar_settlement"),
    ("payment_request_id", "ap_settlement"),
)


def classify_cash_entry(entry: dict, cat_map: dict) -> str:
    """單筆收支明細 → 會計處理方式（treatment）。

    參數：
      entry    dict，keys: category / invoice_id / advance_payment_id /
               payment_request_id（缺 key 視為空）
      cat_map  {(source, category_text): treatment} — 由 finance_category_map
               撈出來的對照（source='cash' 的列才會被查到）

    優先序：advance_payment_id → 'advance'；invoice_id → 'ar_settlement'；
    payment_request_id → 'ap_settlement'；再查 cat_map；查無 → 'unmapped'。
    """
    for key, treatment in _LINK_PRIORITY:
        if entry.get(key):
            return treatment
    category = entry.get("category") or ""
    return cat_map.get(("cash", category)) or "unmapped"


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

    月結守衛用：守衛只關心「落在哪個月」，把三種來源型別收斂成一種表示。
    """
    if dt is None:
        return None
    if isinstance(dt, (datetime, date)):
        return f"{dt.year:04d}-{dt.month:02d}"
    if isinstance(dt, str):
        m = re.match(r"^(\d{4})-(\d{2})", dt.strip())
        if m and 1 <= int(m.group(2)) <= 12:
            return f"{m.group(1)}-{m.group(2)}"
    return None
