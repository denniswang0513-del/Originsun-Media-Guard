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
  子項目        外出用餐 / 出國度假 / 交通 …（sub_item 欄）

🔴 2026-08-27 補：**深度不只三層**。owner 的 Sheet 只有三個固定分類欄，某支要
長第四、五層時他就溢出到旁邊的欄（「款別」「專案標籤」）——
`家用▸變動支出▸醫療保健▸乳癌治療▸台北馬偕` 是五層。正本因此改成一張樹表
（`db.models.CashTaxonomyNode`），而上面那三欄降級為**路徑前三層的鏡射**
（`mirror_from_path`）。三欄的介面不變，所以既有消費端一行都不用改。
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


def mirror_from_path(path) -> tuple:
    """節點路徑 → `(category, item, sub_item)` 三欄鏡射。**規則只有這一份。**

        ['家用','變動支出','醫療保健','乳癌治療','台北馬偕']
            → ('家用_變動支出', '變動支出', '醫療保健')

    🔴 第四層以後**刻意不進這三欄**：那三欄是既有消費端的介面
    （finance_category_map 對映、三表、卡債、core.project_link、銀行匯入規則、
    petty_item_for），塞進去等於讓它們看到不認識的詞。深度活在
    `crm_cash_entries.taxonomy_node_id` 的路徑裡，加層數不必碰任何一個消費端。
    """
    p = [str(x).strip() for x in (path or []) if str(x).strip()]
    book = p[0] if len(p) > 0 else ""
    item = p[1] if len(p) > 1 else ""
    sub = p[2] if len(p) > 2 else ""
    return join_category(book, item), item, sub


def path_from_columns(category, sub_item) -> list:
    """三欄 → 路徑（`mirror_from_path` 的反向）。**規則同一份** —— 寫入端反查
    節點（routers/crm/finance._sync_taxonomy）與種子掃歷史組合
    （db/seed_cash_taxonomy）都吃它；各拼各的，種下的節點跟反查的路徑就會歧義。
    """
    book, item = split_category(category)
    if not book:
        return []
    p = [book]
    if item:
        p.append(item)
    s = (sub_item or "").strip()
    if s:
        p.append(s)
    return p


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


# ── 私帳類別 → 母公司零用金的會計項目 ────────────────────────────────
# 兩本帳的詞彙不同：私帳寫 `公司_器材`（他自己怎麼分帳），母公司的會計項目是
# `設備耗材`（finance_category_map 的鍵，AP 的科目從它來）。把私帳的一列推去
# 零用金請款時要換詞 —— 直接把複合鍵塞進單據的 item，科目就落到另一條分支，
# 應付款的科目跟著錯。
#
# 只列「去掉 `公司_` 前綴後對不上母公司詞彙」的那幾個；其餘（教育訓練／
# 業績獎金／其他…）去前綴後本來就一樣，交給下面的通則。
PETTY_ITEM_OVERRIDES = {
    "公司_專案": "專案雜支",       # 私帳的支出列掛在收入類別下＝該案的成本
    "公司_專案支出": "專案雜支",
    "公司_代墊": "專案雜支",
    "公司_器材": "設備耗材",
    "公司_軟體與耗材": "軟體網路服務",
    "公司_餐敘": "交際應酬",
    "公司_加班": "薪資",
    "公司_薪水": "薪資",
    "公司_年終獎金": "獎金",
    "公司_業績獎金": "獎金",
}


def petty_item_for(category, valid_items=()) -> str:
    """`公司_器材` → `設備耗材`。對不出來就回**空字串**。

    🔴 對不出來時不要硬猜一個項目 —— 猜錯會讓那筆錢默默記到別的科目，
    比留白更難發現。空字串的意思是「請人挑」，呼叫端據此把欄位留給使用者。
    `valid_items` 給了就當白名單（正本＝finance_category_map source='cash'）。
    """
    c = (category or "").strip()
    if not c:
        return ""
    valid = set(valid_items or ())
    hit = PETTY_ITEM_OVERRIDES.get(c)
    if not hit:
        hit = item_of(c) or c        # 去前綴；沒有前綴就是它自己
    if valid and hit not in valid:
        return ""
    return hit
