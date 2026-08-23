# -*- coding: utf-8 -*-
"""從請款單摘要建議專案（owner 2026-08-23：「手動掛，精準為主」）。

408 張未掛專案的請款單要人工掛。摘要幾乎都是案名（「國民法官人員費」「IGER」
「TIE展」），所以機器可以先排候選，人只要看一眼對不對 —— 從「翻 238 個專案
找一個」變成「確認」。

🔴 這支的產出是**建議**，永遠不自動套用。實測「王道活動紀錄」會配到
   「2026 節能減碳觀摩活動紀錄」（0.67，靠「活動紀錄」四個字撞上），那是
   兩個不同的案子。分數高只代表「值得看一眼」。
"""
from core.project_match import (MIN_SCORE, STRONG_SCORE, normalize, similarity,
                                suggest_project)
from tests.unit._srcscan import js_code_only, repo_src

JS = "frontend/tabs/crm/crm-payments.js"


def test_year_prefix_and_separators_are_stripped():
    """請款單那側幾乎都帶年份前綴（29 種 project_label 全部都有），
    專案主檔那側不一定 —— 不去掉的話相似度被稀釋。"""
    assert normalize("2025 王道週年影片") == normalize("王道週年影片")
    assert normalize("2026 泛亞-空拍_台66") == normalize("泛亞空拍台66")


def test_a_year_inside_the_name_is_kept():
    """只去**前綴**。「coding 101」的 101、「台66」的 66 是案名的一部分。"""
    assert "66" in normalize("2026 泛亞空拍台66專案")
    assert "101" in normalize("coding101 影片")


def test_real_matches_from_production():
    """生產真樣本 —— 這幾組是人看過確認對的。"""
    projs = [("a", "2023台灣太赫茲公司年度升遷表揚大會暨公司尾牙 活動紀錄"),
             ("b", "資訊科技素養培育計畫 2024年coding101"),
             ("c", "當代就是創造歷史 第二屆中國信託當代繪畫獎 展覽影片")]
    assert suggest_project("太赫茲", projs)["project_id"] == "a"
    assert suggest_project("coding101 影片+平面", projs)["project_id"] == "b"
    assert suggest_project("中信當代繪畫獎", projs)["project_id"] == "c"


def test_a_generic_phrase_does_not_win_a_long_name():
    """🔴 同分時取**案名較短**的。長案名容易靠一段通用字撞上一堆不相干的摘要
    ——「活動紀錄」「形象影片」這種。"""
    # 🔴 要造出**真的同分**才測得到 tiebreaker。第一版拿「王道活動紀錄」比，
    #    分數是 1.0 vs 0.67 根本沒平手，把 tiebreaker 拆掉照樣過（實測）。
    #    這裡兩邊都完整包含「形象影片」→ 都是 1.0，短的那個才該贏。
    projs = [("long", "某某公司 2025 年度形象影片專案"), ("short", "形象影片")]
    hit = suggest_project("形象影片", projs)
    assert hit["score"] == 1.0, hit
    assert hit["project_id"] == "short", f"同分時挑了長案名：{hit}"
    # 順序反過來也要一樣（不能只是「剛好挑到第一個」）
    assert suggest_project("形象影片", projs[::-1])["project_id"] == "short"


def test_weak_matches_get_no_suggestion_at_all():
    """低於門檻不給建議 —— 給了只是讓人多讀一行沒用的字。"""
    projs = [("a", "泛亞工程空拍專案")]
    assert suggest_project("國民法官人員費", projs) is None


def test_one_character_summaries_are_refused():
    """一個字的摘要配什麼都像。"""
    assert suggest_project("A", [("a", "A計畫形象影片")]) is None


def test_strong_flag_still_needs_a_human():
    """strong 只是 UI 用不同顏色，不是「可以自動掛」。"""
    projs = [("a", "王道週年影片")]
    hit = suggest_project("2025王道週年影片", projs)
    assert hit["strong"] is True
    assert hit["score"] >= STRONG_SCORE
    assert MIN_SCORE < STRONG_SCORE, "門檻順序反了"


def test_similarity_is_symmetric_enough_to_be_useful():
    assert similarity("abc", "xxabcxx") == 1.0
    assert similarity("abc", "xyz") == 0.0


def test_empty_inputs_do_not_explode():
    assert suggest_project("", [("a", "x")]) is None
    assert suggest_project("案子", []) is None
    assert similarity("", "abc") == 0.0


# ── 前端：建議只是建議 ──────────────────────────────────────

def test_the_suggestion_never_writes_itself_in():
    """🔴 建議不可以自己變成 project_id 送出去。owner 要的是確認，不是自動。"""
    js = js_code_only(repo_src(JS))
    i = js.index("_batchApply")
    seg = js[i:i + 1200]
    assert "suggested" not in seg, "送出時碰到了 suggested —— 那就變成自動套用了"


def test_suggestions_are_only_fetched_in_batch_mode():
    """238 專案 × 800 張的子字串比對不該每次列清單都跑。"""
    js = js_code_only(repo_src(JS))
    assert "if (_batch.on)               params.set('suggest', '1');" in js


def test_sorting_falls_back_to_the_suggestion():
    """同建議的列要能靠排序聚在一起 —— 那才是這顆建議的用處（Shift 選一段）。"""
    js = js_code_only(repo_src(JS))
    i = js.index("project:  p =>")
    assert "p.suggested?.project_name" in js[i:i + 200], "排序沒有用到建議"


def test_the_suggestion_is_visually_marked_as_a_guess():
    """畫面上要看得出來那是機器猜的，不是已經掛好的。"""
    js = repo_src(JS)
    assert "建議：" in js
    assert "不會自動掛" in js, "沒有告訴使用者這只是建議"
