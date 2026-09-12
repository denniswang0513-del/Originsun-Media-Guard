# -*- coding: utf-8 -*-
"""團隊清單的人員排序（owner 2026-09-07「吳宇晨、陳偉建的排序在最下面」——兩位是兼職）：
在職 → 兼職 → 其他，規則只有 core.hr_logic.staff_rank 一份；團隊的一週與週記大家都用它。"""
from core.hr_logic import staff_rank
from tests.unit._srcscan import code_only, func_body, repo_src


def test_rank_order():
    # 空白狀態＝舊資料沒填，跟 is_active_staff 一樣算在職（同一份 ACTIVE_STATUSES）；None＝沒綁人員檔案才排最後
    assert staff_rank("在職") == staff_rank("") < staff_rank("兼職") < staff_rank("專案") == staff_rank(None)
    names = ["陳偉健", "王士源", "吳宇晨", "蘇家弘"]
    st = {"陳偉健": "兼職", "吳宇晨": "兼職", "王士源": "在職", "蘇家弘": "在職"}
    assert sorted(names, key=lambda n: (staff_rank(st.get(n)), n)) == ["王士源", "蘇家弘", "吳宇晨", "陳偉健"]


def test_team_week_and_journal_use_the_same_rank():
    me = code_only(func_body(repo_src("routers/api_me.py"), "async def team_week("))
    assert "staff_rank(staff_st.get(kv[0]))" in me and "CrmStaff.name, CrmStaff.status" in me
    j = repo_src("routers/api_journal.py")
    wk = code_only(func_body(j, "async def week_journals("))
    assert "staff_rank(st.get(u))" in wk and "shells.sort(key=lambda s: order(s.username))" in wk and "pending = sorted(pending, key=order)" in wk
    assert "async def _staff_status_by_username(" in j
