# -*- coding: utf-8 -*-
"""合併同案的復原快照要涵蓋 Timesheet 的每一個欄位。

`services.timesheet_self._SNAP_COLS` 是手寫的欄位清單。之後有人往 `Timesheet` 加欄位、
忘了加進來，合併還是會成功，「復原合併」也還是會成功 —— 只是被併掉那幾列的新欄位靜默變成
預設值。同一種坑在收支拆項的 `_split_to_dict` 咬過一次（漏欄＝重存時靜默丟資料）。
"""
from services.timesheet_self import _SNAP_COLS


def test_snapshot_columns_match_the_timesheet_table_exactly():
    from db.models import Timesheet
    cols = {c.name for c in Timesheet.__table__.columns}
    missing = sorted(cols - set(_SNAP_COLS))
    extra = sorted(set(_SNAP_COLS) - cols)
    assert not missing, (
        f"Timesheet 新增了欄位但沒進 _SNAP_COLS：{missing}。\n"
        "合併不會出錯，但「復原合併」會把這些欄位靜默還原成預設值 —— 加欄位就要同時加進快照。")
    assert not extra, f"_SNAP_COLS 有 Timesheet 已經沒有的欄位：{extra}（復原時會 TypeError）"
    assert len(_SNAP_COLS) == len(set(_SNAP_COLS)), "_SNAP_COLS 有重複"
