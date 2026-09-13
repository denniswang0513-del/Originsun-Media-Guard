# -*- coding: utf-8 -*-
"""補填提醒（owner 2026-09-13「新增提醒區塊，有沒寫的工作日誌、哪一天沒填的專案日誌，好讓同事補填，持續出現到填完為止」）。

純規則、不碰 DB：路由（routers/api_me.py `/me/reminders`）撈好資料丟進來。只提醒**在職**的人（owner：「只有在職需要」——
`core.hr_logic.is_active_staff`）；到職日之前不算；週末／假日表的假日／已核准的假不算；今天不算（今天還在填）。
"""
from datetime import date, timedelta

from core.journal_logic import week_start_of
from core.leave_logic import workdays_between

#: 專案紀錄往回看幾天、週記往回看幾週（再久的舊帳不追 —— 提醒是催「最近沒填的」，不是稽核）
LOG_LOOKBACK_DAYS = 30
JOURNAL_LOOKBACK_WEEKS = 6


def missing_log_days(today: date, hire_date, filled: dict, holidays=None, leave_days=()) -> dict:
    """`{"missing": [date…], "pending": [date…]}`：近 30 天（到職日起）每個工作日，一列都沒有＝沒填（missing）；
    只有草稿列（存了內容沒時數）＝未完成（pending）。`filled`＝`{date: {status…}}`（不含計畫列）。"""
    start = today - timedelta(days=LOG_LOOKBACK_DAYS)
    if hire_date and hire_date > start:
        start = hire_date
    off = set(leave_days or ())
    missing, pending = [], []
    for d in workdays_between(start, today - timedelta(days=1), holidays):
        if d in off:
            continue
        sts = filled.get(d)
        if not sts:
            missing.append(d)
        elif all(s == "pending" for s in sts):
            pending.append(d)
    return {"missing": missing, "pending": pending}


def missing_journal_weeks(today: date, hire_date, statuses: dict) -> list:
    """還沒送出的週記：近 6 個**已經過完**的週（本週不算 —— 週記是週一寫上一週的），到職那一週起。
    `statuses`＝`{week_start: status}`（none／draft／submitted）。回 `[(week_start, status)…]`，舊的在前。"""
    this_week = week_start_of(today)
    first = week_start_of(hire_date) if hire_date else None
    out = []
    for k in range(JOURNAL_LOOKBACK_WEEKS, 0, -1):
        ws = this_week - timedelta(days=7 * k)
        if first and ws < first:
            continue
        st = statuses.get(ws) or "none"
        if st != "submitted":
            out.append((ws, st))
    return out
