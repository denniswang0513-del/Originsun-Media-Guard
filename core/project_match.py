# -*- coding: utf-8 -*-
"""從請款單摘要猜「這是哪個案子」——**建議**用的，不是自動套用。

owner 2026-08-23：「專案我可以手動掛，精準為主」。所以這裡的產出永遠是建議：
機器把 408 張未掛專案的請款單排出候選，人看一眼確認。差別在於人要做的從
「翻 238 個專案找一個」變成「看一眼對不對」。

為什麼摘要有得猜：實測 371 張「專案外包」的摘要幾乎都是**案名**而不是工作內容
（「國民法官人員費」「亞洲藝術雙年展」「IGER」「TIE展」）。

🔴 為什麼不能自動套用：實測「王道活動紀錄」會配到「2026 節能減碳觀摩活動紀錄」
   （相似度 0.67，靠「活動紀錄」四個字撞上），而那是兩個不同的案子。分數高
   不等於對 —— 它只代表「值得看一眼」。

比對方式刻意簡單：正規化（去年份前綴、去空白與分隔符）後取最長共同子字串，
除以較短者長度。中文沒有詞界，token 化在這種短案名上比子字串更差
（「中信當代繪畫獎」vs「當代就是創造歷史 第二屆中國信託當代繪畫獎 展覽影片」
token 化後幾乎沒有共同詞，子字串抓得到「當代繪畫獎」）。
"""
from __future__ import annotations

import re
import unicodedata

#: 低於這個分數不給建議 —— 給了只會讓人多讀一行沒用的字
MIN_SCORE = 0.35
#: 到這個分數才標成「高信心」（UI 用不同顏色，但**仍然要人確認**）
STRONG_SCORE = 0.6

_YEAR_PREFIX = re.compile(r"^(19|20)\d{2}\s*")
_NOISE = re.compile(r"[\s_\-－—·、,，.。()（）\[\]【】]")


def normalize(name: str) -> str:
    """案名正規化 —— 年份前綴與分隔符不帶資訊，留著只會稀釋相似度。

    「2025 王道週年影片」與「王道週年影片」是同一個案子；請款單那側幾乎都帶
    年份前綴（29 種 project_label 全部都有），專案主檔那側則不一定。

    🔴 先 NFKC 折全形。人打的案名混著全形英數是常態（「ＩＧＥＲ」「２０２５」），
    而全形與半形在字串比對裡完全不同 —— 連年份前綴那條 regex 都不會匹配全形數字。
    沒折的話那些案子的相似度是 0，而「0 分」跟「沒有夠像的候選」在畫面上長得
    一模一樣：使用者只會看到沒有建議，永遠不知道是這個原因。
    """
    folded = unicodedata.normalize("NFKC", (name or "").strip())
    return _NOISE.sub("", _YEAR_PREFIX.sub("", folded))


def similarity(a: str, b: str) -> float:
    """最長共同子字串 / 較短者長度。兩邊都已正規化過。"""
    if not a or not b:
        return 0.0
    best = 0
    for i in range(len(a)):
        # 已經找到長度 best 的共同子字串 → 從 best+1 起試才有意義
        for j in range(i + best + 1, len(a) + 1):
            if a[i:j] in b:
                best = j - i
            else:
                break
    return best / min(len(a), len(b))


def prepare(projects) -> list:
    """[(id, name)] → [(id, name, 正規化後的名字)]，**一份請求算一次**。

    🔴 沒有這支的話，`suggest_project` 會在候選迴圈裡呼叫 `normalize(name)` ——
    400 張未掛請款單 × 238 個專案 ＝ 95,200 次（每次兩個 regex），而實際只需要
    238 次。這是那 2.4 秒裡最沒必要的一段。
    """
    return [(pid, name, normalize(name)) for pid, name in projects]


def suggest_project(summary: str, projects) -> dict | None:
    """{project_id, project_name, score, strong} 或 None（沒有值得看的候選）。

    `projects` 吃 `prepare()` 的輸出；也收舊的 [(id, name)]（會就地正規化，
    單元測試與一次性腳本方便）。同分時取**案名較短**的 —— 長案名容易靠一段
    通用字（「活動紀錄」「形象影片」）撞上一堆不相干的摘要。
    """
    s = normalize(summary)
    if len(s) < 2:            # 一個字的摘要配什麼都像
        return None
    best = None
    for row in projects:
        pid, name, npn = row if len(row) == 3 else (row[0], row[1], normalize(row[1]))
        sc = similarity(s, npn)
        if sc < MIN_SCORE:
            continue
        if best is None or (sc, -len(name or "")) > (best[0], -len(best[2] or "")):
            best = (sc, pid, name)
    if best is None:
        return None
    return {"project_id": best[1], "project_name": best[2],
            "score": round(best[0], 3), "strong": best[0] >= STRONG_SCORE}
