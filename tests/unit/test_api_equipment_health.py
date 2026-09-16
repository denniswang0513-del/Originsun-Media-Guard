# -*- coding: utf-8 -*-
"""/health 2026-09-17 特徵測試：釘住 `routers/api_equipment.mark_checked_out`／`mark_returned`
（30 天改 8 次、覆蓋 19.7%、沒有測試檔）。兩支是「器材庫直接領用」與「行事曆場次已領／已還」
共用的狀態規則；只釘現況、不判對錯；整檔可刪。
"""
from datetime import datetime
from types import SimpleNamespace

from routers.api_equipment import mark_checked_out, mark_returned


def _pair():
    return (SimpleNamespace(status="在庫", updated_at=None),
            SimpleNamespace(person="", out_at=None, returned_at=None, condition_note=None))


def test_checkout_sets_out_at_and_flips_equipment_to_out():
    equip, co = _pair()
    now = datetime(2026, 9, 17, 9, 0)
    mark_checked_out(equip, co, now, "小明")
    assert co.out_at is now and co.person == "小明"
    assert equip.status == "出勤" and equip.updated_at is now


def test_checkout_person_only_fills_a_blank_and_is_capped_at_64():
    equip, co = _pair()
    co.person = "已填的人"
    mark_checked_out(equip, co, datetime(2026, 9, 17), "別人")
    assert co.person == "已填的人"            # 領用列已經有人就不改
    equip, co = _pair()
    mark_checked_out(equip, co, datetime(2026, 9, 17), "")
    assert co.person == ""                     # 空字串不動（器材庫端點就是這樣呼叫的）
    equip, co = _pair()
    mark_checked_out(equip, co, datetime(2026, 9, 17), "甲" * 100)
    assert len(co.person) == 64


def test_return_sets_returned_at_and_flips_equipment_back():
    equip, co = _pair()
    equip.status = "出勤"
    now = datetime(2026, 9, 18, 18, 0)
    mark_returned(equip, co, now, "  鏡頭蓋不見了  ")
    assert co.returned_at is now and co.condition_note == "鏡頭蓋不見了"
    assert equip.status == "在庫" and equip.updated_at is now


def test_return_note_blank_keeps_existing_note_and_is_capped_at_255():
    equip, co = _pair()
    co.condition_note = "上次的備註"
    mark_returned(equip, co, datetime(2026, 9, 18), "   ")
    assert co.condition_note == "上次的備註"
    mark_returned(equip, co, datetime(2026, 9, 18), None)
    assert co.condition_note == "上次的備註"
    mark_returned(equip, co, datetime(2026, 9, 18), "乙" * 300)
    assert len(co.condition_note) == 255
