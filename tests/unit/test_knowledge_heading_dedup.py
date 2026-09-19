# -*- coding: utf-8 -*-
"""章節標題去重（owner 2026-09-19：「做去重」）。

編譯時我們在每章開頭寫一行 `# 第 N 章 章名（p.X–Y）`，模型整理出來的正文
**自己也常常再寫一次**（頁碼寫法不同）。四個畫面（書頁、公開頁、PDF、章節 md）
都會看到那兩行，所以修在讀的那一支。

這支測試的重點是**寧可少去重一次，也不要吃掉真的內容** —— 所以「不該動的」
那幾條比「該去重的」還多。
"""
import pytest

from core import knowledge_logic as kl
from tests.unit._srcscan import func_body, repo_src


# ── 該去重的 ───────────────────────────────────────────────
def test_the_same_title_twice_leaves_one():
    md = "# 第 3 章 凡例（p.9–9）\n\n# 第 3 章 凡例（p.9）\n\n## 核心概念\n內容\n"
    got = kl.drop_repeated_heading(md)
    assert got.count("第 3 章 凡例") == 1
    assert got.startswith("# 第 3 章 凡例（p.9–9）"), "留下來的要是我們寫的那一行（帶完整頁碼）"
    assert "## 核心概念" in got and "內容" in got


def test_it_does_not_leave_a_hole_where_the_line_was():
    md = "# 第 3 章 凡例（p.9–9）\n\n# 第 3 章 凡例（p.9）\n\n## 核心概念\n"
    assert "\n\n\n" not in kl.drop_repeated_heading(md)


@pytest.mark.parametrize("second", [
    "# 第 3 章 凡例（p.9）",        # 頁碼不同
    "# 第 3 章 凡例(p. 9-9)",      # 半形括號、空格
    "## 第 3 章 凡例",             # 層級不同、沒頁碼
    "#  第 3 章  凡例 （p.9）",     # 空白多寡不同
])
def test_the_page_numbers_and_spacing_do_not_stop_it(second):
    md = f"# 第 3 章 凡例（p.9–9）\n\n{second}\n\n內容\n"
    assert kl.drop_repeated_heading(md).count("凡例") == 1


# ── 靠章名判斷（模型換了寫法）────────────────────────────
# 實測 5 本書 102 章：光靠「去掉頁碼後一模一樣」只修掉一半，模型很常換寫法。
def test_a_different_punctuation_style_is_still_the_same_title():
    """`第二章 : 從灌湯包…` vs `第二章：從灌湯包…` —— 冒號一個半形一個全形。"""
    md = ("# 第 4 章 第二章 : 從灌湯包到小籠包 - 為什麼鼎泰豐的皮不是最薄？（p.16–24）\n\n"
          "# 第二章：從灌湯包到小籠包 - 為什麼鼎泰豐的皮不是最薄？（p.16–24）\n\n## 核心概念\n")
    got = kl.drop_repeated_heading(md, "第二章 : 從灌湯包到小籠包 - 為什麼鼎泰豐的皮不是最薄？")
    assert got.count("從灌湯包") == 1
    assert "## 核心概念" in got


def test_the_model_writing_a_shorter_title_still_counts():
    """模型把長章名縮短了（實測 百年飯桌 ch19）。"""
    long_t = "第十七章 : 天皇吃牛肉了！日本百年洋食史 - 蛋包飯、咖哩飯、可樂餅與漢堡排"
    md = f"# 第 19 章 {long_t}（p.142–154）\n\n# 第十七章：天皇吃牛肉了！日本百年洋食史（p.142–154）\n\n內容\n"
    assert kl.drop_repeated_heading(md, long_t).count("天皇吃牛肉了") == 1


def test_the_model_writing_a_longer_title_still_counts():
    """模型多寫了幾個字、頁碼還抄錯（實測 鯨豚 ch08）。"""
    md = "# 第 8 章 第五章 結論（p.169–173）\n\n# 第五章 結論（p.160–164）筆記\n\n內容\n"
    assert kl.drop_repeated_heading(md, "第五章 結論").count("結論") == 1


def test_a_short_chapter_name_is_not_a_problem_in_that_direction():
    """章名短（「作者簡介」）但第二行整個包住它 —— 一定是重複。"""
    md = "# 第 1 章 作者簡介（p.1–3）\n\n# 《百年飯桌》第 1 章：作者簡介（p.1–3）\n\n內容\n"
    assert kl.drop_repeated_heading(md, "作者簡介").count("作者簡介") == 1


def test_a_real_section_that_happens_to_share_a_few_words_survives():
    """🔴 反過來就要保守：章名「第一章 前言與背景」，而底下**真的有**一節叫「前言」。"""
    md = "# 第一章 前言與背景\n\n# 前言\n這一節是內容\n"
    got = kl.drop_repeated_heading(md, "第一章 前言與背景")
    assert "# 前言" in got and got == md


def test_a_second_level_heading_is_never_touched():
    """`## 核心概念` 不管跟章名多像都留著 —— 那是文章的結構。"""
    md = "# 第 1 章 核心概念導讀\n\n## 核心概念\n內容\n"
    assert kl.drop_repeated_heading(md, "核心概念導讀") == md


def test_the_chapter_title_comes_from_the_toc_not_a_guess():
    body = func_body(repo_src("services/knowledge_service.py"), "def read_chapter(")
    assert 'c.get("title")' in body


# ── 不該動的 ───────────────────────────────────────────────
@pytest.mark.parametrize("md", [
    "# 第 1 章 甲\n\n## 核心概念\n內容\n",            # 第二個是別的標題
    "# 第 1 章 甲\n\n# 第 2 章 乙\n內容\n",            # 兩個不同章
    "# 第 1 章 甲\n內容\n# 第 1 章 甲\n",              # 同名但中間隔著內容（不是開頭連著）
    "內容在最前面\n\n# 第 1 章 甲\n",                  # 開頭不是標題
    "# 第 1 章 甲\n",                                  # 只有一行
    "",
    "   \n\n  \n",
])
def test_everything_else_is_left_exactly_as_it_was(md):
    assert kl.drop_repeated_heading(md) == md


def test_a_non_string_does_not_blow_up():
    assert kl.drop_repeated_heading(None) == ""


# ── 修在一個地方 ───────────────────────────────────────────
def test_one_place_does_it_for_all_four_screens():
    """書頁、公開頁、PDF 都是走 `read_chapter` 拿 md 的 —— 各畫面各修一次會長歪。"""
    body = func_body(repo_src("services/knowledge_service.py"), "def read_chapter(")
    assert "kl.drop_repeated_heading(" in body


def test_the_stored_file_is_never_rewritten():
    """那是他的書，不要為了排版去動原始資料（同 ensure_assets 不碰 full_text 的理由）。"""
    body = func_body(repo_src("services/knowledge_service.py"), "def read_chapter(")
    for bad in ("_write_text", "open(", "w\""):
        assert bad not in body, "read_chapter 不可以寫檔：" + bad
