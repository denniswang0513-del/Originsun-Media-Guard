# -*- coding: utf-8 -*-
"""金融機構代號表（owner 2026-09-11：「可以代碼自動帶銀行，或銀行自動帶代碼」）。

出納在網銀填的是兩格（三碼代號、帳號），而人員檔只有一格 bank_name，
人各自打成四種寫法。這支表是唯一的翻譯規則——**不准再用切字串的方式拆**。
"""
from core.bank_codes import BANKS, by_code, normalize_name, options, resolve


class TestResolveRealWorldSpellings:
    """生產資料 2026-09-11 實際出現的寫法，一個都不能漏。"""

    def test_every_spelling_of_the_same_bank_lands_on_one_code(self):
        for text in ("中國信託(822)", "中國信託（822）", "中國信託 822", "中信（822)", "中信", "中國信託"):
            assert resolve(text)[0] == "822", text

    def test_the_eleven_banks_actually_in_production(self):
        """這 10 個代號是從生產庫清點出來的；這條紅了代表表被改瘦了。

        （下面是 13 種**寫法**對到 10 個相異代號 —— 別把列數當成家數。）
        """
        for text, code in (("合作金庫", "006"), ("第一銀行", "007"), ("華南商銀", "008"),
                           ("華南銀行", "008"), ("上海銀行", "011"), ("台北富邦", "012"),
                           ("富邦", "012"), ("國泰世華", "013"), ("郵局", "700"),
                           ("玉山", "808"), ("玉山銀行", "808"), ("台新", "812"),
                           ("連線商業銀行", "824")):
            assert resolve(text)[0] == code, text

    def test_name_and_code_that_disagree_are_not_guessed(self):
        """名字說一家、三碼說另一家 → 不猜（owner 2026-09-11「都修好」拍板）。

        「玉山銀行 013 分行」的 013 是分行號、「局號 021 郵局」的 021 是局號 —— 都不是
        銀行代號。舊規則「三碼優先」會回國泰世華／花旗，而那個值是出納直接複製進
        網銀第一格的；留空他會自己去查，給一個看起來很肯定的錯代號才會匯錯。
        生產 25 筆銀行字串目前沒有任何一筆矛盾，這條改的是未來的輸入。
        """
        for text in ("玉山銀行(822)", "玉山銀行 013 分行", "局號 021 郵局", "台新銀行 008-123-456789"):
            assert resolve(text) == (None, text), text

    def test_code_alone_or_agreeing_code_still_resolves(self):
        """只有三碼、或三碼跟名字一致 → 照用（生產資料的形狀全在這裡）。"""
        assert resolve("822")[0] == "822"
        assert resolve("中信（822)")[0] == "822"
        assert resolve("玉山銀行 808")[0] == "808"
        assert resolve("郵局700")[0] == "700"

    def test_full_width_and_half_width_mixed(self):
        """`中信（822)` —— 左全形右半形，生產資料裡真的有這一筆。"""
        assert resolve("中信（822)") == ("822", "中國信託商業銀行")


class TestUnknownPassesThrough:
    """表裡沒有的東西**原樣放行** —— 不能因為沒收錄就讓畫面變空白。"""

    def test_unknown_name_keeps_what_the_person_typed(self):
        assert resolve("某某農會信用部") == (None, "某某農會信用部")

    def test_unknown_code_keeps_the_string(self):
        code, name = resolve("999 沒這家")
        assert code is None and "沒這家" in name

    def test_blank(self):
        assert resolve("") == (None, "")
        assert resolve(None) == (None, "")

    def test_by_code_unknown_is_empty_not_none(self):
        assert by_code("999") == "" and by_code("") == ""


class TestNormalizeName:
    def test_strips_code_brackets_and_spaces(self):
        assert normalize_name("中國信託（822）") == "中國信託"
        assert normalize_name("台北富邦 012") == "台北富邦"

    def test_account_digits_are_not_mistaken_for_a_code(self):
        """帳號是 11–16 碼，不該被 `\\d{3}` 咬出一段來當代號。"""
        assert resolve("342168993550")[0] is None


class TestTableHygiene:
    def test_every_alias_points_at_a_real_code(self):
        from core.bank_codes import ALIASES
        bad = {a: c for a, c in ALIASES.items() if c not in BANKS}
        assert not bad, f"別名指向表裡沒有的代號：{bad}"

    def test_codes_are_three_digits(self):
        assert all(len(c) == 3 and c.isdigit() for c in BANKS)

    def test_options_are_sorted_and_shaped_for_a_dropdown(self):
        opts = options()
        assert [o["code"] for o in opts] == sorted(BANKS)
        assert all(set(o) == {"code", "name"} and o["name"] for o in opts)

    def test_it_stays_pure(self):
        """無 I/O：這支要能在任何一台 agent 上直接 import（含 DB 斷線時）。"""
        from tests.unit._srcscan import code_only, repo_src
        src = code_only(repo_src("core/bank_codes.py"))
        for forbidden in ("import requests", "open(", "session", "settings"):
            assert forbidden not in src, forbidden
