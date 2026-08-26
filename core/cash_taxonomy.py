# -*- coding: utf-8 -*-
"""收支的三層分類（owner 2026-08-27：「我這本帳同時覆蓋了公司帳、個人帳、
家用帳，我希望收支明細可以分為 類別、項目、子項目 這三項來分類」）。

現況：`category` 這一欄本來就是**前兩層黏在一起**的複合鍵（`公司_專案`、
`家用_變動支出`、`個人_生活`…），`item` 欄存的正是後半段（3,990/4,704 筆有值），
`sub_item` 是第三層。

🔴 為什麼不把 category 拆成兩欄存：那一欄是**會計對映的鍵**
（finance_category_map.category_text → 科目＋treatment），三表、卡債、家用往來、
可掛專案判定（core.project_link）、銀行匯入規則全部吃它。拆欄＝那些對映一次
全毀。所以：**儲存維持複合鍵，畫面與篩選拆三層**，兩邊由本檔的 split/join
互轉 —— 規則只有這一份。

  類別（book）  公司 / 個人 / 家用 / 轉匯與定存 / 信用卡
  項目（item）  專案 / 生活 / 變動支出 / 薪水 / 代墊 …（category 的後半段）
  子項目        外出用餐 / 出國度假 / 交通 …（sub_item 欄，自由詞）
"""
from __future__ import annotations

SEP = "_"

# 三本帳 + 兩個跨帳流動（轉帳/卡費本來就不屬於任何一本，刻意獨立成類別）
KNOWN_BOOKS = ("公司", "個人", "家用", "轉匯與定存", "信用卡")


def split_category(category) -> tuple:
    """`公司_專案` → ('公司', '專案')；`信用卡` → ('信用卡', '')；空 → ('', '')。

    只切**第一個**底線 —— `轉匯與定存_公司信用卡` 的項目是「公司信用卡」，
    切成三段會把它切爛。
    """
    s = (category or "").strip()
    if not s:
        return "", ""
    if SEP not in s:
        return s, ""
    book, item = s.split(SEP, 1)
    return book, item


def join_category(book, item) -> str:
    """('公司', '專案') → `公司_專案`；項目空 → 只有類別（如 `信用卡`）。"""
    b = (book or "").strip()
    i = (item or "").strip()
    if not b:
        return i
    return b + SEP + i if i else b


def book_of(category) -> str:
    return split_category(category)[0]


def item_of(category) -> str:
    return split_category(category)[1]


def taxonomy(categories, sub_items=()) -> dict:
    """把現有的 category 清單整理成三層樹（給前端的下拉／篩選用）。

    回 {'books': [...], 'items_by_book': {book: [item...]}, 'sub_items': [...]}
    —— 值域一律來自**實際使用中的類別**（寫死的清單跟不上使用者新增的類別，
    這個教訓在收支篩選器上付過一次）。
    """
    items_by_book: dict = {}
    for c in categories or ():
        b, i = split_category(c)
        if not b:
            continue
        items_by_book.setdefault(b, [])
        if i and i not in items_by_book[b]:
            items_by_book[b].append(i)
    for b in items_by_book:
        items_by_book[b].sort()
    books = sorted(items_by_book, key=lambda b: (KNOWN_BOOKS.index(b)
                                                 if b in KNOWN_BOOKS else 99, b))
    return {"books": books, "items_by_book": items_by_book,
            "sub_items": sorted({s for s in (sub_items or ()) if s})}
