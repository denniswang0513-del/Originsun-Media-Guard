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
    # 🔴 apply 那半刻意不在這裡驗實作長相 —— 第 4 輪就是照著當時的程式碼寫斷言
    # （把那個會震盪的計數器當成正確答案釘住），測試綠著、bug 活著。斷言要從
    # 「該有的行為」推出來，不是從眼前的實作抄下來。行為驗證見下面 _apply_sim。


# ── 重複判定：preview 與 apply 的同一份實作（/simplify 第 4 輪 HIGH）──
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

from core.card_statement import mark_duplicates  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _apply_sim(stmt_keys, books, selected=None):
    """照 apply 端的形狀跑一次：回 (寫入數, 跳過數)。

    apply 收的是**整份**卡單 —— 這支模擬刻意也吃整份，因為那正是修法的本體。
    """
    dups = mark_duplicates(stmt_keys, Counter(books))
    sel = [True] * len(stmt_keys) if selected is None else selected
    made = sum(1 for s, d in zip(sel, dups) if s and not d)
    skipped = sum(1 for s, d in zip(sel, dups) if s and d)
    return made, skipped


def test_three_identical_charges_all_land_when_books_are_empty():
    """🔴 apply 端曾自己寫一份「跳過就 −1、寫入就 +1」的計數器 —— 它會震盪：
    三筆同款只寫進兩筆、四筆也只寫進兩筆，而且沒有任何跡象。"""
    k = [("2026-01-16", 120)] * 3
    assert _apply_sim(k, []) == (3, 0)
    assert _apply_sim([("2026-01-16", 120)] * 4, []) == (4, 0)


def test_partial_month_does_not_eat_the_rows_the_user_ticked():
    """帳上已有 1 筆、卡單有 3 筆 → 該進 2 筆。

    這是修法要救的那個情境：preview 取消勾第 1 列，若前端只把勾選的 2 列送出，
    apply 會對著被裁過的清單再扣一次帳上那筆 → 只進 1 筆，使用者刻意勾的那列
    無聲消失。整份送進來就不會。"""
    stmt = [("2026-01-16", 120)] * 3
    books = [("2026-01-16", 120)]
    dups = mark_duplicates(stmt, Counter(books))
    assert dups == [True, False, False]          # preview 的判定
    assert _apply_sim(stmt, books, selected=[not d for d in dups]) == (2, 0)


def test_resubmitting_the_same_statement_writes_nothing():
    """重按一次匯入（或同一份卡單再傳一次）＝整份都在帳上了 → 一筆都不進。"""
    stmt = [("2026-01-16", 120), ("2026-01-17", 55)]
    assert _apply_sim(stmt, stmt) == (0, 2)


def test_preview_and_apply_agree_on_the_same_input():
    """同一份輸入不准給出兩個答案 —— 兩邊都走 mark_duplicates 就恆等。"""
    stmt = [("2026-02-01", 90), ("2026-02-01", 90), ("2026-02-03", 12),
            ("2026-02-01", 90), ("2026-02-03", 12)]
    books = [("2026-02-01", 90), ("2026-02-03", 12)]
    preview = mark_duplicates(stmt, Counter(books))
    assert preview == [True, False, True, False, False]
    assert _apply_sim(stmt, books, selected=[not d for d in preview]) == (3, 0)


def test_mark_duplicates_accepts_a_plain_set():
    assert mark_duplicates([("d", 1), ("d", 1)], {("d", 1)}) == [True, False]


def test_apply_uses_the_shared_helper_and_the_full_statement():
    """🔴 釘住修法的兩個支柱：apply 不准自己寫第二份判定，前端不准只送勾選的列。"""
    src = (ROOT / "routers/api_finance_card.py").read_text(encoding="utf-8")
    assert "mark_duplicates(" in src, "apply 沒走共用的重複判定"
    assert "seen[key] -= 1" not in src and "seen[key] += 1" not in src, \
        "apply 又長出自己那份會震盪的計數器"
    js = (ROOT / "frontend/tabs/finance/subviews/recon.js").read_text(encoding="utf-8")
    assert "d.rows.map(" in js and "selected:" in js, "前端沒有送整份卡單"
