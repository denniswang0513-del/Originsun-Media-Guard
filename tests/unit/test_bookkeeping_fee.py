# -*- coding: utf-8 -*-
"""記帳費費率（owner 2026-08-23 定案、2026-08-24 再確認一次）。

外部會計代繳營業稅，記帳費跟稅款走**同一筆匯出**（雙月一次），每期對帳都要把
那一筆拆成「營業稅 + 記帳費」兩列。

🔴 這個費率被講錯過兩次，而且兩次都真的寫進了生產帳：
   ① 「一期 4,000、一年 14 個月」→ 2024/2025/2026 的 5 月期各被記成 8,000
      （實際應為 6,000），三筆事後才修回來
   ② 「單月 2,500、一年 14 個月」→ 當天就被 owner 自己更正
   根因是費率只活在人的記憶與 memory 檔裡。這支測試存在的意義，就是讓它
   從今以後活在程式裡、而且改壞會被擋下來。
"""
import pytest

from core.finance_logic import BOOKKEEPING_FEE_RATES, bookkeeping_fee

#: 申報期＝單數月（實際付款月）
FILING_MONTHS = (1, 3, 5, 7, 9, 11)


def _year_total(year: int) -> int:
    return sum(bookkeeping_fee(f"{year}-{m:02d}") for m in FILING_MONTHS)


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
    assert bookkeeping_fee(month) == expect


def test_2025_is_the_clean_full_year_check():
    """🔴 2025 是唯一乾淨的完整年度（六期都在、費率沒變）——
    改費率之後拿它驗。舊的錯版本會算成 28,000。"""
    assert _year_total(2025) == 26000, "一年應為 13 個月 × 2,000"
    assert _year_total(2025) == 13 * 2000


# ── 新費率（2026-09 起）────────────────────────────────────────

@pytest.mark.parametrize("month, expect", [
    ("2026-09", 5000),     # 第一筆新費率 —— owner 特別交代不要再用 4,000
    ("2026-11", 5000),
    ("2027-01", 5000),
    ("2027-05", 10000),    # 新費率下的 5 月期：多收 2 個月
])
def test_the_new_rate_from_september(month, expect):
    assert bookkeeping_fee(month) == expect


def test_2027_is_a_full_year_on_the_new_rate():
    """2027 整年才完整落在新費率上：14 個月 × 2,500。
    （2026 跨費率切換，本來就不會是整齊的一年）"""
    assert _year_total(2027) == 35000
    assert _year_total(2027) == 14 * 2500


def test_the_switch_happens_at_september_not_before():
    """🔴 生效點是 2026-09。8 月還是舊費率 —— 差一個月就是差 1,000。"""
    assert bookkeeping_fee("2026-07") == 4000
    assert bookkeeping_fee("2026-09") == 5000


# ── 規則本身 ──────────────────────────────────────────────────

def test_only_may_carries_the_extra_months():
    """多收的那幾個月併在 5 月那期 —— 其餘每期都是單純的 2 個月。"""
    for y, monthly in ((2025, 2000), (2027, 2500)):
        for m in FILING_MONTHS:
            got = bookkeeping_fee(f"{y}-{m:02d}")
            assert got == monthly * 2 or m == 5, f"{y}-{m:02d} 不該是 {got}"


def test_the_year_total_is_derived_not_hardcoded():
    """🔴 一年的總額必須是「月費 × 一年幾個月」推出來的，不是各期寫死相加。

    寫死的話，改月費時很容易只改一半（漏掉 5 月那期），而那正是第 ① 版錯的
    形狀：一般期對、5 月期多了 2,000。
    """
    for _eff, monthly, per_year in BOOKKEEPING_FEE_RATES:
        # 用該費率下的完整一年反推
        year = 2025 if per_year == 13 else 2027
        assert _year_total(year) == monthly * per_year


def test_the_filing_reminder_carries_this_periods_fee():
    """🔴 費率寫進程式還不夠 —— 要在**需要它的那一刻**出現在眼前。

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


def test_a_bad_month_raises_instead_of_guessing():
    """🔴 悄悄回一個數字比丟例外危險得多 —— 那會直接變成錯的拆帳金額。"""
    for bad in ("", None, "2026", "26-09", "2026/09", "abcd-ef"):
        with pytest.raises(ValueError):
            bookkeeping_fee(bad)
