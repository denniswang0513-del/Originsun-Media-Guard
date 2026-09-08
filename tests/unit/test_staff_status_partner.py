# -*- coding: utf-8 -*-
"""員工多一個身份「合夥」（owner 2026-09-08）。

規則只有一句：合夥＝在職那一層——凡是問「在職嗎」的地方都算在職（假勤時數帳、里程碑負責人、福委會、零用金代登、
兼職排班能不能勾），團隊清單排序跟在職同層。字彙要在人力庫的兩個下拉、徽章、排序、後端正本五處同步。
"""
from tests.unit._srcscan import repo_src


def test_partner_counts_as_active_everywhere_the_rule_lives():
    from core.hr_logic import ACTIVE_STATUSES, STAFF_PARTNER, is_active_staff, staff_rank
    assert STAFF_PARTNER == "合夥" and "合夥" in ACTIVE_STATUSES and "" in ACTIVE_STATUSES
    assert is_active_staff("合夥") and is_active_staff("在職") and is_active_staff(None) and not is_active_staff("兼職")
    assert staff_rank("合夥") == staff_rank("在職") < staff_rank("兼職") < staff_rank("專案")
    src = repo_src("core/hr_logic.py")
    assert "CrmStaff.status.in_([s for s in ACTIVE_STATUSES if s])" in src, "SQL 版要跟純函式版讀同一份清單"


def test_partner_is_in_the_staff_vocabulary_everywhere():
    html = repo_src("frontend/tabs/crm/crm-staff.html")
    assert html.count('<option value="合夥">合夥</option>') == 2, "篩選下拉＋表單下拉"
    js = repo_src("frontend/tabs/crm/crm-staff.js")
    assert "'合夥': 'crm-staff-badge-合夥'" in js and "'合夥': 0" in js and "{value:'合夥',label:'合夥'}" in js
    assert ".crm-staff-badge-合夥" in repo_src("frontend/tabs/crm/crm.css")


def test_strict_active_filters_were_moved_onto_the_shared_predicate():
    """福委會全員發放、零用金代登下拉、零用金人員清單：原本硬比 status == '在職'，合夥（與空白）會整個不見。"""
    for f in ("routers/crm/benefits.py", "routers/crm/petty.py"):
        src = repo_src(f)
        assert 'CrmStaff.status == "在職"' not in src, f
        assert "active_staff_where()" in src or "is_active_staff(" in src, f
    assert "['在職', '合夥'].includes(staffStatus)" in repo_src("frontend/js/admin/user-mgmt.js"), "合夥可以幫兼職排班"
