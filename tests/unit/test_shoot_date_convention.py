# -*- coding: utf-8 -*-
"""日期欄的寫入慣例：`_parse_shoot_date` 一律產出**該日的 UTC 午夜**。

🔴 為什麼這條要有測試（2026-08-19 匯 394 筆歷史發票時整批踩到）：

台北是 UTC+8，所以 UTC 午夜換算台北仍是同一天 —— 兩個讀取端才會一致：
  - `_fmt_day`（astimezone 台北後取日期）→ 同一天
  - 前端（取 ISO 字串前 10 碼，即 UTC 那天）→ 同一天
若改成寫台北午夜，存進去是**前一天 16:00Z**，前端就少一天，而 `_fmt_day` 不會 ——
兩個讀取端對同一筆資料給出不同答案，最難查的那種。

第二條：也要吃**完整 ISO datetime**。任何「GET 回來、改一個欄位、PUT 回去」的
呼叫端都會把 API 自己吐出的 `'...T16:00:00+00:00'` 送回來；原本只認純日期，
遇到這種字串直接回 None → 日期被清成 NULL（發票列表的種類切換鈕實際踩到）。
"""
from datetime import datetime, timezone

import pytest

from routers.crm._shared import _fmt_day, _parse_shoot_date


def test_plain_date_becomes_utc_midnight():
    got = _parse_shoot_date("2026-08-19")
    assert got == datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("day", ["2024-01-02", "2026-08-13", "2026-12-31", "2025-01-01"])
def test_round_trip_is_stable_for_both_readers(day):
    """存進去再讀出來，_fmt_day 與『ISO 前 10 碼』必須都等於原本那天。"""
    dt = _parse_shoot_date(day)
    assert _fmt_day(dt) == day, "_fmt_day（台北）讀出來變了"
    assert dt.isoformat()[:10] == day, "前端取 ISO 前 10 碼讀出來變了"


def test_full_iso_is_accepted_not_dropped():
    """API 吐出的完整 ISO 送回來不能變 None —— 那會把日期清空。"""
    assert _parse_shoot_date("2026-08-19T00:00:00+00:00") is not None


def test_legacy_taipei_midnight_normalises_to_the_real_day():
    """舊的台北午夜資料（前一天 16:00Z）再存一次要歸一到正確那天的 UTC 午夜。

    '2024-01-01T16:00:00+00:00' 其實是台北的 2024-01-02。"""
    got = _parse_shoot_date("2024-01-01T16:00:00+00:00")
    assert got == datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert _fmt_day(got) == "2024-01-02"
    assert got.isoformat()[:10] == "2024-01-02"


def test_already_normalised_value_is_idempotent():
    once = _parse_shoot_date("2026-08-19")
    twice = _parse_shoot_date(once.isoformat())
    assert once == twice, "再存一次不該把日期挪動"


@pytest.mark.parametrize("raw", ["2025/05/26", "這筆8月中開", "abc", "", None])
def test_unparseable_returns_none(raw):
    """壞值回 None 由呼叫端決定怎麼辦（匯入腳本會列出來要人看），不要猜一個日期。
    注意斜線格式**不收** —— 匯入腳本負責先正規化成 ISO 再送進來。"""
    assert _parse_shoot_date(raw) is None
