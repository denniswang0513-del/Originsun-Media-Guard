"""core/staff_alias.py — 「收款人那段字」→ 人員（純函式，無 I/O）。

帳務那邊的收款人是一格**自由文字**。實際長出來的樣子（2026-09-11 生產清點）：

    志廷              本名少一個字
    蠻牛 jubior       綽號 ＋ 英文暱稱
    冰塊              純綽號
    停車費 史丹       **用途 ＋ 人名黏在一起**（同一個人另外還有「停車 史丹」「早餐 史丹」）

對不到人的後果有兩層：撈不到銀行帳號（出納得自己去查），以及同一個人被拆成好幾列、
分好幾次匯（史丹那四筆小錢就是三個不同的「收款人」）。

## 規則

1. **姓名完全相同** → 就是他。
2. **代稱完全相同** → 就是他（代稱是人填的，一個人可以有好幾個）。
3. **收款人那段字「包含」某個代稱**，而且只包含一個人的 → 就是他。
   這條專門對付「用途 ＋ 人名」那種寫法。
4. 但若有**更長的姓名或代稱把剛才命中的那個代稱包在裡面、而且也出現在這段字裡**，
   那個長的才是這段字真正指的人（見下）。
5. 其餘 → 對不到，照原字串走（畫面上要明說「還沒對到人員」，不是留白）。

🔴 **第 4 條是「升級」，不是另一條比對路徑**（2026-09-11 /polish 兩輪才調對）：
   「停車費 史丹利」裡命中的是別人的代稱「史丹」，而字串裡真正寫的是**姓名**
   「史丹利」—— 同一段字的更長讀法，所以改判給史丹利。撈錯的話拿到的是錯的
   銀行帳號與身分證，匯款通知也掛在錯的人名下，畫面上完全看不出異狀。

   第一版寫成「姓名整張表先比一次、都沒有才比代稱」，那只是把同一個問題鏡射
   過去，而且新開了一個更大的洞 ——
     * 反向照樣錯：A 姓名「史丹」、B 代稱「史丹利」，「停車費 史丹利」會落到 A。
     * 姓名會咬到用途詞：有人姓名叫「大方」的話，「大方廣告 印刷費 小明」
       會從正確的小明變成大方（舊版根本沒有這條路，姓名不參與包含比對）。
   所以姓名**不獨立製造比對**，只能在「它包含了那個命中的代稱」時把結果接手。

🔴 **沒有模糊比對。** 這一格決定錢匯給誰，`difflib` 猜錯一次就是匯錯人 ——
   「志廷」跟「陳志廷」看起來很像，但「李文揚」跟「李文陽」也很像。寧可對不到，
   讓畫面說「還沒對到」，也不要猜。要對上就去人員檔把代稱填好（人填、可覆核）。

🔴 **代稱撞名要兩邊都不對。** 兩個人都填了「小明」的話，第 3 條會回 None ——
   不是隨便挑一個。撞名是資料問題，靜默挑一個就是把它變成匯款問題。
"""
from __future__ import annotations

import re

#: 代稱欄的分隔符：頓號、逗號（全半形）、分號、換行、斜線。空白**不算**分隔 ——
#: 「蠻牛 jubior」本身就是一個代稱，拆開會讓「jubior」單獨去比對。
_SEP = re.compile(r"[、,，;；\r\n/|]+")


def parse_aliases(text) -> list[str]:
    """代稱欄的字 → 一串代稱（去空白、去重、保留順序）。"""
    out, seen = [], set()
    for part in _SEP.split(str(text or "")):
        a = part.strip()
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def build_index(staff_rows) -> dict:
    """人員列 → 查表用的索引。`staff_rows` 是 `(key, name, alias)` 的可迭代物件。

    `key` 是呼叫端要拿回去的東西（通常是人員 id 或整列）。回傳的三張表對應上面三條規則。
    """
    by_name: dict = {}
    by_alias: dict = {}
    dup_alias: set = set()
    for key, name, alias in staff_rows:
        n = str(name or "").strip()
        if n:
            by_name.setdefault(n, key)
        for a in parse_aliases(alias):
            if a in by_alias and by_alias[a] != key:
                dup_alias.add(a)      # 兩個人用同一個代稱 → 這個代稱作廢
            else:
                by_alias.setdefault(a, key)
    for a in dup_alias:
        by_alias.pop(a, None)
    return {"by_name": by_name, "by_alias": by_alias, "dup_alias": dup_alias}


def resolve(payee: str, index: dict):
    """收款人那段字 → key（對不到回 None）。規則見檔頭，**不做模糊比對**。"""
    s = str(payee or "").strip()
    if not s or not index:
        return None
    by_name = index.get("by_name") or {}
    by_alias = index.get("by_alias") or {}
    if s in by_name:
        return by_name[s]
    if s in by_alias:
        return by_alias[s]
    # 第 3 條：包含某個代稱。撞到兩個不同的人就放棄（寧可對不到）。
    matched = [a for a in by_alias if a and a in s]
    owners = {by_alias[a] for a in matched}
    if len(owners) != 1:
        return None
    key = owners.pop()
    # 第 4 條：升級。只看「把剛才命中的代稱包在裡面、而且也出現在這段字裡」的
    # 更長姓名／代稱 —— 姓名不獨立製造比對（理由見檔頭）。
    longer = [(t, owner) for t, owner in list(by_name.items()) + list(by_alias.items())
              if t and t in s and any(a in t and len(t) > len(a) for a in matched)]
    if not longer:
        return key
    top = max(len(t) for t, _ in longer)
    winners = {owner for t, owner in longer if len(t) == top}
    return winners.pop() if len(winners) == 1 else None
