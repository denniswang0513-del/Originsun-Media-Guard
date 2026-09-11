# -*- coding: utf-8 -*-
"""發版說明的「中文被 codepage 吃掉」防呆（`publish_update.notes_look_mangled`）。

為什麼值得一支測試：壞掉的後果是**靜默且不可逆**的 —— `?????` 會寫進
version.json，跟著 OTA 包送到機隊每一台、NAS、以及前端的更新提示，而發版流程
一路綠燈。2026-09-11 的 v2.5.2 就是這樣上線的。

判定規則本身是啟發式的，所以這裡兩邊都釘：真的被吃掉的要擋下來（含帶版號或
`fix:` 英文前綴的那幾種 —— 第一版的判定正是在那裡漏光的），正常的 notes 一個
都不准誤殺。
"""
import pytest

from publish_update import notes_look_mangled


@pytest.mark.parametrize("notes", [
    "?????",                    # 純中文 5 字
    "???",                      # 純中文 3 字（舊判定的 count>=5 漏掉這個）
    "????",
    "v2.5.3 ?????",             # 帶版號前綴（舊判定：去掉 ? 還剩 6 字 → 漏）
    "fix: ????",                # 帶英文動詞前綴 → 漏
    "?? A ? B ???",             # 中英交錯 → 漏
    "2026-09-11 ??????",
])
def test_mangled_notes_are_caught(notes):
    assert notes_look_mangled(notes), f"沒擋下來：{notes!r}"


@pytest.mark.parametrize("notes", [
    "匯款通知：NAS 短網址路由、全選、複製連結",      # 中文完好
    "v2.5.3 報價單改版",                          # 中英混合但中文完好
    "fix backup path resolution",                 # 純英文
    "Bump deps",
    "",
    "備份頁綁定專案改用專案工時那份清單（兩邊都不篩狀態）",
])
def test_healthy_notes_are_not_touched(notes):
    assert not notes_look_mangled(notes), f"誤殺：{notes!r}"


def test_chinese_that_still_has_a_question_mark_is_fine():
    """中文還在就不是被吃掉 —— 那個問號是作者自己打的。"""
    assert not notes_look_mangled("這樣算修好了嗎？先發一版看看")
    assert not notes_look_mangled("修好了嗎? 先發一版")
