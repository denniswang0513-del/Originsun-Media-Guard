# -*- coding: utf-8 -*-
"""core/price_book.py — 報價價目的**純規則**（無 I/O、不碰 DB）。

規劃正本 docs/QUOTE_ASSISTANT_PLAN.md P3：「答過的價順手存成價目，下一輪它就自己帶」。

為什麼需要這個：AI 讀得懂客戶要什麼，但**價格是你的** —— 客戶只會說「我要兩支精華」。
2026-09-09 盤點生產庫只有 3 張報價，所以前幾張 AI 每一項都會問；每寄出一張就多一批價，
問的次數會越來越少。

🔴 **只有寄出的報價才進價目**（`routers/crm/quotes` 的 sent transition）：
草稿是還在談的東西，自動存每 1.2 秒一次 —— 拿草稿當價目會（a）寫爆（b）把你談到一半
又放棄的價當成正式價。
"""
from __future__ import annotations

import re

MAX_PROMPT_ROWS = 80        # 進提示的價目筆數上限（照最近用過排序）
_WS = re.compile(r"\s+")

# 全形→半形（數字與括號最常見；同一個品項打成全形不該變成兩筆）
_FULL = "０１２３４５６７８９（）％－"
_HALF = "0123456789()%-"
_TRANS = str.maketrans(_FULL, _HALF)


def norm_text(s: str) -> str:
    """描述正規化：去頭尾空白、空白收成一個、全形數字括號轉半形、英文小寫。"""
    t = _WS.sub(" ", str(s or "").strip()).translate(_TRANS)
    return t.lower()


def norm_key(description: str, unit: str) -> str:
    """(描述, 單位) → 去重用的鍵。同一個品項不同單位算兩筆（「天」跟「式」是兩種報法）。

    🔴 兩段都要截：`crm_price_items.key` 是 varchar(300)，而 `unit` 存進去時是 [:32]。
    不截的話（a）長單位會讓鍵超過 300 → 寫入直接炸；（b）收價時用整串算的鍵，跟之後
    `update_price_item` 用截短後的 unit 重算出來的鍵對不起來，同一筆會分裂成兩筆。
    正常單位遠短於 32 字，所以這個截斷對既有資料是 no-op。
    """
    desc = norm_text(description)[:200]
    return f"{desc}|{norm_text(unit)[:32] or '式'}"


def usable(description: str, unit_price) -> bool:
    """值不值得進價目：有描述、單價 > 0。0 元的是「待定價」，不是價。"""
    try:
        price = int(unit_price or 0)
    except (TypeError, ValueError):
        return False
    return bool(str(description or "").strip()) and price > 0


def collect(items: list) -> dict:
    """一張報價的項目 → {key: {description, unit, unit_price}}（同鍵取最後出現的）。"""
    out = {}
    for it in items or []:
        desc = str(it.get("description") or "").strip()
        unit = str(it.get("unit") or "式").strip() or "式"
        if not usable(desc, it.get("unit_price")):
            continue
        out[norm_key(desc, unit)] = {
            "description": desc[:512], "unit": unit[:32],
            "unit_price": int(it.get("unit_price") or 0),
        }
    return out


def prompt_lines(rows: list, limit: int = MAX_PROMPT_ROWS) -> str:
    """價目 → 提示裡的一段。給空的就回空字串（呼叫端據此整段不放）。"""
    picked = [r for r in (rows or []) if usable(r.get("description"), r.get("unit_price"))][:limit]
    if not picked:
        return ""
    return "\n".join(
        f"- {r['description']}｜{r.get('unit') or '式'}｜{int(r['unit_price'])}" for r in picked)
