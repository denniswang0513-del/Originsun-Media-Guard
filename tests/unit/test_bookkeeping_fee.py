# -*- coding: utf-8 -*-
"""記帳費費率（owner 2026-08-23 定案，2026-08-24 改成可在後台設定）。

外部會計代繳營業稅，記帳費跟稅款走**同一筆匯出**（雙月一次），每期對帳都要把
那一筆拆成「營業稅 + 記帳費」兩列。

🔴 這個費率被講錯過兩次，兩次都真的寫進了生產帳：
   ① 「一期 4,000、一年 14 個月」→ 2024/2025/2026 的 5 月期各被記成 8,000
      （實際應為 6,000），三筆事後才修回來
   ② 「單月 2,500、一年 14 個月」→ 當天就被 owner 自己更正
   根因是費率只活在人的記憶裡。owner 2026-08-24：「寫一個按鈕設定會計費用來
   解決這件事，之後換會計調整這個按鈕就好」—— 所以正本是**設定**，程式裡那張
   表只是出廠預設。

分兩層測：費率算法（傳入費率表 → 純函式）、讀設定的行為（monkeypatch）。
測試**不可以**依賴這台機器 settings.json 的實際內容。
"""
import pytest

from core.finance_logic import (DEFAULT_BOOKKEEPING_FEE_RATES, bookkeeping_fee,
                                load_bookkeeping_fee_rates)

#: 申報期＝單數月（實際付款月）
FILING_MONTHS = (1, 3, 5, 7, 9, 11)
D = list(DEFAULT_BOOKKEEPING_FEE_RATES)


def _fee(month):
    """一律把費率表傳進去 —— 不讀設定，測試才不會被機器狀態影響。"""
    return bookkeeping_fee(month, D)


def _year_total(year: int) -> int:
    return sum(_fee(f"{year}-{m:02d}") for m in FILING_MONTHS)


# ── 對得上生產實際資料 ────────────────────────────────────────

@pytest.mark.parametrize("month, expect", [
    ("2026-07", 4000),    # 生產實際：2026-07-16 會計 4,000
    ("2026-03", 4000),    # 生產實際：2026-03-17 會計 4,000
    ("2026-05", 6000),    # 生產實際（2026-08-23 修正後）：2026-05-14 會計 6,000
    ("2025-05", 6000),    # 同上，2025-05-15
    ("2024-05", 6000),    # 同上，2024-05-15
    ("2025-01", 4000),    # 生產實際：2025-01-16 會計 4,000
])
def test_it_matches_what_is_actually_in_the_ledger(month, expect):
    assert _fee(month) == expect


def test_2025_is_the_clean_full_year_check():
    """🔴 2025 是唯一乾淨的完整年度（六期都在、費率沒變）——
    改費率之後拿它驗。舊的錯版本會算成 28,000。"""
    assert _year_total(2025) == 26000, "一年應為 13 個月 × 2,000"


@pytest.mark.parametrize("month, expect", [
    ("2026-09", 5000),     # 第一筆新費率 —— owner 特別交代不要再用 4,000
    ("2026-11", 5000),
    ("2027-05", 10000),    # 新費率下的 5 月期：多收 2 個月
])
def test_the_new_rate_from_september(month, expect):
    assert _fee(month) == expect


def test_2027_is_a_full_year_on_the_new_rate():
    """2027 整年才完整落在新費率上：14 個月 × 2,500。
    （2026 跨費率切換，本來就不會是整齊的一年）"""
    assert _year_total(2027) == 35000


def test_the_switch_happens_at_september_not_before():
    """🔴 生效點是 2026-09。7 月還是舊費率 —— 差一期就是差 1,000。"""
    assert _fee("2026-07") == 4000
    assert _fee("2026-09") == 5000


# ── 規則本身 ──────────────────────────────────────────────────

def test_only_may_carries_the_extra_months():
    """多收的那幾個月併在 5 月那期 —— 其餘每期都是單純的 2 個月。"""
    for y, monthly in ((2025, 2000), (2027, 2500)):
        for m in FILING_MONTHS:
            got = _fee(f"{y}-{m:02d}")
            assert got == monthly * 2 or m == 5, f"{y}-{m:02d} 不該是 {got}"


def test_the_year_total_is_derived_not_hardcoded():
    """🔴 一年的總額必須是「月費 × 一年幾個月」推出來的，不是各期寫死相加。

    寫死的話，改月費時很容易只改一半（漏掉 5 月那期），而那正是第 ① 版錯的
    形狀：一般期對、5 月期多了 2,000。
    """
    for r in D:
        year = 2025 if r["months_per_year"] == 13 else 2027
        assert _year_total(year) == r["monthly"] * r["months_per_year"]


def test_rates_out_of_order_still_resolve_correctly():
    """🔴 費率表**不能假設是排好序的**。

    settings.json 是可以被手改的檔，未來也可能有別條路徑 append 進去。順序亂掉
    時若照清單順序取「最後一個符合的」，就會挑到舊費率而不是最新生效的那筆 ——
    金額直接錯，而且畫面上完全看不出來。這條就是釘住那個 sorted()
    （破壞驗證時它原本逃掉了：所有測試都剛好傳排好序的清單）。
    """
    jumbled = [
        {"effective_from": "2026-09", "monthly": 2500, "months_per_year": 14},
        {"effective_from": "0000-00", "monthly": 2000, "months_per_year": 13},
    ]
    assert bookkeeping_fee("2026-09", jumbled) == 5000
    assert bookkeeping_fee("2026-07", jumbled) == 4000
    assert bookkeeping_fee("2027-05", jumbled) == 10000


def test_a_bad_month_raises_instead_of_guessing():
    """🔴 悄悄回一個數字比丟例外危險得多 —— 那會直接變成錯的拆帳金額。"""
    for bad in ("", None, "2026", "26-09", "2026/09", "abcd-ef"):
        with pytest.raises(ValueError):
            bookkeeping_fee(bad, D)


# ── 費率的正本是設定，不是程式裡那張表 ────────────────────────

def _patch_settings(monkeypatch, value):
    import config
    monkeypatch.setattr(config, "load_settings",
                        lambda: {"finance": {"bookkeeping_fee": value}}
                        if value is not None else {"finance": {}})


def test_the_saved_rate_wins_over_the_built_in_default(monkeypatch):
    """🔴 換會計就是改設定 —— 改完必須立刻生效，不必改程式也不必發版。"""
    _patch_settings(monkeypatch, [
        {"effective_from": "0000-00", "monthly": 2000, "months_per_year": 13},
        {"effective_from": "2026-09", "monthly": 3000, "months_per_year": 12},
    ])
    assert bookkeeping_fee("2026-09") == 6000, "沒有用設定裡的新費率"
    assert bookkeeping_fee("2026-07") == 4000, "舊期別不該被新費率追溯"


def test_history_is_kept_so_old_periods_still_compute(monkeypatch):
    """🔴 舊費率不能被覆蓋掉。歷史期別要用**當時**的費率算 ——
    只留一筆現行費率的話，回頭對 2025 的帳會整年算錯。"""
    _patch_settings(monkeypatch, [
        {"effective_from": "0000-00", "monthly": 2000, "months_per_year": 13},
        {"effective_from": "2026-09", "monthly": 2500, "months_per_year": 14},
    ])
    assert bookkeeping_fee("2025-05") == 6000
    assert bookkeeping_fee("2027-05") == 10000


def test_it_falls_back_to_the_default_when_never_configured(monkeypatch):
    """還沒設定過（新機器）也要能算 —— 出廠預設就是 owner 定案的那組。"""
    _patch_settings(monkeypatch, None)
    assert bookkeeping_fee("2026-09") == 5000
    assert bookkeeping_fee("2026-07") == 4000


def test_the_loader_hands_back_a_copy(monkeypatch):
    """🔴 回原件的話，呼叫端一改就污染了出廠預設（同一個 process 裡的
    後續呼叫全部跟著錯，而且完全看不出來）。"""
    _patch_settings(monkeypatch, None)
    got = load_bookkeeping_fee_rates()
    got[0]["monthly"] = 999999
    assert DEFAULT_BOOKKEEPING_FEE_RATES[0]["monthly"] == 2000
    assert load_bookkeeping_fee_rates()[0]["monthly"] == 2000


def test_the_filing_reminder_carries_this_periods_fee():
    """🔴 費率存得起來還不夠 —— 要在**需要它的那一刻**出現在眼前。

    單數月 1 號會發「營業稅申報提醒」（`_finance_calendar_check`），而付款就在
    同月中旬。這期的記帳費附在同一則裡，人就不必去翻記憶或問我。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("core/scheduler.py"),
                               "async def _finance_calendar_check("))
    assert "bookkeeping_fee(cur_month)" in body, "申報提醒沒有帶上這期的記帳費"
    tpl = repo_src("notifier.py")
    i = tpl.index('"vat_filing_due"')
    assert "{fee}" in tpl[i:i + 400], "通知模板裡沒有記帳費的位置（帶了也印不出來）"
