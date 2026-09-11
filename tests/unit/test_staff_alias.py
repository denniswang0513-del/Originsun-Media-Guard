# -*- coding: utf-8 -*-
"""收款人那段字 → 人員（owner 2026-09-11 的代稱對照）。

這一格決定錢匯給誰。所以這裡釘的重點不是「對得到多少」，而是
**對不到的時候要老實回 None，不准猜**。
"""
from core.staff_alias import build_index, parse_aliases, resolve

# 2026-09-11 生產實際要對的五組（owner 逐一確認過）
STAFF = [
    ("s_chen", "陳志廷", "志廷"),
    ("s_luo", "羅文里", "junior"),
    ("s_qiu", "邱冠霖", "蠻牛、jubior"),
    ("s_li", "李宗道", "冰塊"),
    ("s_zheng", "鄭雲詮", "史丹"),
    ("s_wang", "王士源", ""),
]
IDX = build_index(STAFF)


class TestParse:
    def test_separators(self):
        assert parse_aliases("蠻牛、jubior") == ["蠻牛", "jubior"]
        assert parse_aliases("a,b；c\nd/e") == ["a", "b", "c", "d", "e"]

    def test_space_is_not_a_separator(self):
        """「蠻牛 jubior」本身就是一個代稱 —— 拆開的話 jubior 會單獨去比對。"""
        assert parse_aliases("蠻牛 jubior") == ["蠻牛 jubior"]

    def test_blank_and_dupes(self):
        assert parse_aliases("") == [] and parse_aliases(None) == []
        assert parse_aliases("史丹、史丹、") == ["史丹"]


class TestResolveTheRealCases:
    def test_exact_name(self):
        assert resolve("王士源", IDX) == "s_wang"

    def test_exact_alias(self):
        for payee, who in (("志廷", "s_chen"), ("junior", "s_luo"),
                           ("冰塊", "s_li"), ("史丹", "s_zheng")):
            assert resolve(payee, IDX) == who, payee

    def test_purpose_glued_to_the_name(self):
        """🔴 生產實況：同一個人被寫成三個「收款人」，所以他的 638 元要分三次匯。"""
        for payee in ("停車費 史丹", "停車 史丹", "早餐 史丹"):
            assert resolve(payee, IDX) == "s_zheng", payee
        for payee in ("早餐 junior", "早餐咖啡 junior"):
            assert resolve(payee, IDX) == "s_luo", payee

    def test_nickname_with_an_english_handle(self):
        assert resolve("蠻牛 jubior", IDX) == "s_qiu"


class TestItRefusesToGuess:
    def test_no_fuzzy_matching(self):
        """「李文揚」跟「李文陽」也很像 —— 猜錯一次就是匯錯人。"""
        assert resolve("陳志庭", IDX) is None       # 庭／廷
        assert resolve("羅文理", IDX) is None       # 理／里（owner 自己也打錯過）
        assert resolve("王士原", IDX) is None

    def test_unknown_is_none_not_the_closest(self):
        assert resolve("完全沒見過的人", IDX) is None
        assert resolve("", IDX) is None and resolve(None, IDX) is None

    def test_duplicate_alias_disables_it_for_everyone(self):
        """兩個人都填「小明」→ 那個代稱作廢，不是隨便挑一個。
        撞名是資料問題，靜默挑一個就是把它變成匯款問題。"""
        idx = build_index([("a", "甲", "小明"), ("b", "乙", "小明"), ("c", "丙", "阿華")])
        assert resolve("小明", idx) is None
        assert "小明" in idx["dup_alias"]
        assert resolve("阿華", idx) == "c", "沒撞到的照常"

    def test_contains_two_different_people_gives_up(self):
        idx = build_index([("a", "甲", "阿明"), ("b", "乙", "小美")])
        assert resolve("阿明跟小美的便當", idx) is None

    def test_name_beats_alias_when_both_match(self):
        """有人的本名剛好是另一個人的代稱 —— 本名優先（那是正式的）。"""
        idx = build_index([("real", "小華", ""), ("nick", "陳大文", "小華")])
        assert resolve("小華", idx) == "real"


def test_the_module_stays_pure_and_has_no_fuzzy_import():
    from tests.unit._srcscan import code_only, repo_src
    src = code_only(repo_src("core/staff_alias.py"))
    assert "difflib" not in src, "🔴 不准模糊比對：猜錯一次就是匯錯人"
    for forbidden in ("session", "select(", "open(", "requests"):
        assert forbidden not in src, forbidden
