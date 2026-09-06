# -*- coding: utf-8 -*-
"""timesheets.sheet_key（Sheet 列被總表改鍵欄位前的原鍵）是 VARCHAR 欄。

2026-09-06 生產事故：三個寫入點直接塞 manual_dup_key 的 tuple → asyncpg
「expected str, got tuple」→ 總表單改／批次改／衝突「用 Sheet 版本」全部 HTTP 500。
規則：寫入一律 sheet_key_of（字串）、拉取比對一律 sheet_key_tuple 解回 tuple，兩邊只有 core.hr_logic 一份。
"""
from datetime import date, datetime, timezone

from core.hr_logic import manual_dup_key, sheet_key_of, sheet_key_tuple
from tests.unit._srcscan import code_only, repo_src


def test_sheet_key_is_a_string_and_round_trips_to_the_dup_key():
    when = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)      # 台北 09-04 00:00
    key = sheet_key_of(" 劉禮瑜 ", when, " 北投梅庭 ")
    assert isinstance(key, str)
    assert sheet_key_tuple(key) == manual_dup_key("劉禮瑜", when, "北投梅庭") == ("劉禮瑜", date(2026, 9, 4), "北投梅庭")


def test_sheet_key_without_date_or_project_still_round_trips():
    assert sheet_key_tuple(sheet_key_of("王士源", None, "")) == ("王士源", None, "")
    assert sheet_key_tuple(None) is None and sheet_key_tuple("") is None
    assert sheet_key_tuple("garbage") is None and sheet_key_tuple("a\x1fnot-a-date\x1fb") is None


def test_every_sheet_key_write_uses_the_string_codec():
    """所有 `.sheet_key =` 都要走 sheet_key_of；讀側解鍵走 sheet_key_tuple。"""
    for path in ("services/timesheet_self.py", "services/timesheet_conflicts.py", "services/timesheet_ingest.py"):
        src = code_only(repo_src(path))
        for line in src.splitlines():
            if ".sheet_key =" in line:
                assert "sheet_key_of(" in line, (path, line.strip())
                assert "manual_dup_key(" not in line, (path, line.strip())
    ingest = code_only(repo_src("services/timesheet_ingest.py"))
    assert "sheet_key_tuple(sk) or manual_dup_key(n, d, p)" in ingest, "拉取比對要把存的字串解回 tuple，不然永遠對不上"
