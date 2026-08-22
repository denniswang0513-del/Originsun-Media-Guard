# -*- coding: utf-8 -*-
"""對帳單匯入規則的方向條件（owner 2026-08-22）。

owner：「我之後收支對帳單匯入的話，可以有一些規則自動判斷嗎」

規則引擎本來就有（bank_import_rules，17 條在跑），但少一塊：**方向**。
實測生產全部收支的關鍵字分布：

    薪水 105 收 /  0 支   ← 關鍵字自己就分得出方向
    眷保  57 收 /  0 支
    發票 365 收 /  0 支
    代付   4 收 /  0 支
    代收   3 收 /  0 支
    🔴 薪資  10 收 / 79 支   ← 兩側都有

「薪資」兩側都有，而且是**兩種不同的類別**：股東匯進來是代收薪資、公司發給
員工是代發薪資。沒有方向條件就只能猜，猜錯的那一半會靜靜錯下去 —— 這正是
這幾天在清的那批（289 筆全被歸成「轉存」）的成因。
"""
import pytest

from core.bank_statement import _classify


def test_plain_rule_still_works():
    """沒有方向條件的規則（既有 17 條全是這種）行為完全不變。"""
    rules = [("跨行轉入", "轉存", 0)]
    assert _classify("跨行轉入 合庫", rules) == ("轉存", 0)
    assert _classify("跨行轉入 合庫", rules, signed=-100) == ("轉存", 0)


def test_direction_filter_selects_the_right_category():
    """🔴 同一個關鍵字、兩個方向、兩種類別 —— 這就是要方向條件的理由。"""
    rules = [("薪資", "代發薪資", 0, -1),      # 只在支出
             ("薪資", "代收薪資", 0, +1)]      # 只在存入
    assert _classify("蔡念栩薪資中山分行", rules, signed=-35453)[0] == "代發薪資"
    assert _classify("念栩薪資營業部", rules, signed=+35453)[0] == "代收薪資"


def test_wrong_direction_does_not_match():
    rules = [("薪資", "代發薪資", 0, -1)]
    assert _classify("念栩薪資營業部", rules, signed=+100) == ("", 0)


def test_unknown_direction_skips_directional_rules():
    """🔴 第一列推不出方向那種邊緣情況：不知道方向就不要假裝知道。
    帶方向條件的規則整條跳過，讓它落到通用規則或未分類、由人確認。"""
    rules = [("薪資", "代發薪資", 0, -1), ("薪資", "轉存", 0)]
    assert _classify("蔡念栩薪資中山分行", rules, signed=None)[0] == "轉存"
    rules_only = [("薪資", "代發薪資", 0, -1)]
    assert _classify("蔡念栩薪資中山分行", rules_only, signed=None) == ("", 0)


def test_first_match_wins_within_the_same_direction():
    rules = [("薪資", "先命中的", 0, -1), ("薪資", "後面的", 0, -1)]
    assert _classify("薪資", rules, signed=-1)[0] == "先命中的"


def test_directional_rule_can_be_shadowed_by_an_earlier_generic_one():
    """順序即優先序 —— 方向規則要排在通用規則**前面**才有意義。
    這條把「為什麼我的新規則沒生效」釘住。"""
    rules = [("薪資", "轉存", 0), ("薪資", "代發薪資", 0, -1)]
    assert _classify("薪資", rules, signed=-1)[0] == "轉存", \
        "通用規則排前面就會蓋掉方向規則（這是預期行為，但要有人知道）"


@pytest.mark.parametrize("kw,text,signed,want", [
    ("薪水", "念栩薪水營業部", +35453, "代收薪資"),
    ("眷保", "士源三人眷保營業部", +1329, "代收薪資"),
    ("發票", "士源發票營業部", +672, "發票代開"),
    ("代付", "動工代付營業部", +25200, "代收代付"),
])
def test_the_unambiguous_keywords_need_no_direction(kw, text, signed, want):
    """這五個關鍵字實測是單向的，不用方向條件也不會錯。"""
    assert _classify(text, [(kw, want, 0)], signed=signed)[0] == want


# ── 接線 ──────────────────────────────────────────────────────────

def test_model_and_payload_expose_it():
    from core.schemas import BankImportRulePayload
    from db.models import BankImportRule
    assert "only_direction" in BankImportRule.__table__.c
    assert "only_direction" in BankImportRulePayload.model_fields


def test_it_is_not_confused_with_direction():
    """🔴 direction（推方向的提示）與 only_direction（篩選條件）是兩件事。
    借用同一欄會把原本「第一列推不出方向」那條路弄壞。"""
    from tests.unit._srcscan import repo_src
    src = repo_src("db/models.py")
    i = src.index("class BankImportRule")
    seg = src[i:src.index("\nclass ", i + 10)]
    assert "only_direction" in seg and "direction = Column" in seg
    assert "跟 direction 是兩件事" in seg, "沒有把兩者的差別寫下來"


def test_endpoint_validates_and_returns_it():
    from tests.unit._srcscan import repo_src
    src = repo_src("routers/api_finance_stmt.py")
    assert '"only_direction": int(getattr(r, "only_direction", 0) or 0)' in src
    assert "方向條件只能是 -1／0／+1" in src, "沒有擋掉亂填的值"


def test_rules_are_passed_through_with_direction():
    """規則從 DB 撈出來要帶著第四個欄位，不然引擎收不到。"""
    from tests.unit._srcscan import repo_src
    src = repo_src("routers/api_finance_stmt.py")
    assert 'int(getattr(r, "only_direction", 0) or 0)) for r in rows]' in src


def test_row_classification_passes_the_sign():
    """算完方向才分類 —— 不傳 signed 的話方向條件永遠不會生效。"""
    from tests.unit._srcscan import repo_src
    src = repo_src("core/bank_statement.py")
    assert '_classify(r["words"], rules, signed=r["signed"])' in src
