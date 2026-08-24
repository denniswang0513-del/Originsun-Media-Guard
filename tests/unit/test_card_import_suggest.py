# -*- coding: utf-8 -*-
"""api_finance_card.suggest_rows — 卡單匯入三層分類建議（純函式行為測試）。

v2.4.172 的教訓：原始碼掃描擋不住「查詢留著、結果丟掉」—— 這裡直接餵資料
驗行為。"""
from core.card_statement import parse_card_statement, suggest_rows


def _rows(text):
    return parse_card_statement(text).rows


def test_history_suggestion_wins_when_consistent():
    rows = _rows("2026/02/22 連加＊八方雲集雙城店 75\n")
    out = suggest_rows(rows, {"八方雲集雙城店": "個人_生活"}, [], set())
    assert out[0]["category"] == "個人_生活"
    assert out[0]["source"] == "history"


def test_rule_beats_history():
    rows = _rows("2026/02/22 Spotify P234567 149\n")
    out = suggest_rows(rows, {"SpotifyP": "個人_娛樂"},
                       [("Spotify", "個人_學習", 0, 0)], set())
    assert out[0]["category"] == "個人_學習"
    assert out[0]["source"] == "rule"


def test_fee_inherits_previous_spend():
    rows = _rows(
        "2023/06/16 SP MTMOGRAPH 76.83 USD 2,427\n"
        "2023/06/16 國外交易服務費（簽帳 2,427 ) 36\n")
    out = suggest_rows(rows, {"SPMTMOGRAPH": "公司_軟體與耗材"}, [], set())
    assert out[0]["category"] == "公司_軟體與耗材"
    assert out[1]["category"] == "公司_軟體與耗材"
    assert out[1]["source"] == "fee"


def test_fee_without_previous_spend_falls_back():
    rows = _rows("2023/06/16 國外交易服務費（簽帳 175 ) 3\n")
    out = suggest_rows(rows, {}, [], set())
    assert out[0]["category"] == ""


def test_fee_after_unsuggested_spend_stays_blank():
    # 🔴 母交易沒建議 → 手續費不准越級抄更早那筆的分類（dev 冒煙抓到的真 bug）
    rows = _rows(
        "2026/07/03 連加＊八方雲集雙城店 75\n"
        "2026/07/05 沒看過的外幣店 76.83 USD 2,427\n"
        "2026/07/05 國外交易服務費（簽帳 2,427 ) 36\n")
    out = suggest_rows(rows, {"八方雲集雙城店": "個人_生活"}, [], set())
    assert out[0]["category"] == "個人_生活"
    assert out[1]["category"] == ""
    assert out[2]["category"] == ""      # 不是 個人_生活


def test_unknown_merchant_left_blank_not_guessed():
    rows = _rows("2026/02/22 從沒看過的店 999\n")
    out = suggest_rows(rows, {"別家店": "個人_生活"}, [], set())
    assert out[0]["category"] == ""
    assert out[0]["source"] == ""


def test_duplicate_flagged():
    rows = _rows("2026/02/21 立吉富雪坊 270\n2026/02/22 新店家 100\n")
    out = suggest_rows(rows, {}, [], {("2026-02-21", 270)})
    assert out[0]["duplicate"] is True
    assert out[1]["duplicate"] is False


def test_second_identical_charge_is_not_flagged_when_book_has_only_one():
    """🔴 同一天真的可能刷兩筆一樣的錢（兩杯一樣的咖啡）。帳上有一筆就標一筆 ——
    整組標成重複＝預覽全不勾＝那第二筆永遠進不來，跟 set 那個 bug 同一個下場。
    apply 端一開始就是消耗式，preview 這半漏了（/simplify 第 4 輪抓到）。"""
    from collections import Counter
    rows = _rows("2026/02/21 星巴克 270\n2026/02/21 星巴克 270\n")
    out = suggest_rows(rows, {}, [], Counter({("2026-02-21", 270): 1}))
    assert [o["duplicate"] for o in out] == [True, False]


def test_duplicate_accepts_a_plain_set_too():
    """呼叫端傳 set 也要照舊運作（Counter(set) ＝每鍵一筆）。"""
    rows = _rows("2026/02/21 立吉富雪坊 270\n")
    assert suggest_rows(rows, {}, [], {("2026-02-21", 270)})[0]["duplicate"] is True


def test_pin_card_dedupe_counts_and_widens_the_window():
    """兩條都是實測換來的（銀行側 _existing_entry_keys 是正本）：
    🔴 Counter 不是 set —— 同一天真的可能刷兩筆一樣的錢。
    🔴 前後各放寬一天 —— UTC 午夜對 timestamptz 的邊界偏移會讓「卡單最後一天」
       永遠判不出重複，而那正是重送時最常撞到的一天。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "routers/api_finance_card.py").read_text("utf-8")
    fn = src.split("async def _existing_card_keys")[1].split("\n@router")[0]
    assert "Counter()" in fn and "timedelta(days=1)" in fn
    apply_fn = src.split("async def apply_card_statement")[1].split("\n@router")[0]
    assert "seen[key] -= 1" in apply_fn, "帳上已有一筆只能跳一筆"
    assert "seen[key] += 1" in apply_fn, "本批寫入的也要算進去，同批重複才擋得住"
