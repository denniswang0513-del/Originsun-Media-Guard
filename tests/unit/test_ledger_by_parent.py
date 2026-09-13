# -*- coding: utf-8 -*-
"""私帳案的收入分案記帳 `ledger_detail.by_parent`（owner 2026-09-13「為何不加起來？」）。

一個私帳案 X 可以承接多個母帳案 A、B。以前 X 只存一個總數（contract_amount）與一份工項
（split），A 改收款方式時只能「整個換成 A 的」或「擋下來」；現在每案各記一份
`{amount, split, synced_total, at}`，X 的金額與工項只吃**差額** —— owner 自己填的那部分
（合約額 − Σ份額、手改的工項）永遠不被系統動到。

不變式：contract_amount = Σ by_parent[*].amount + 自己的；split[k] = Σ by_parent[*].split[k] + 自己的。
"""
from core.ledger_project import (BY_PARENT_KEY, BY_PARENT_PENDING_KEY, MIRROR_TOTAL_KEY,
                                 drop_parent_share, mirror_stale, norm_detail, parent_shares,
                                 set_parent_share)


def _x():
    """X：A 給 100,000（導演 60,000 ＋ 剪接 40,000）、B 給 80,000（剪接 80,000）、owner 自己填 20,000 顧問。"""
    d, c = set_parent_share({}, 0, "A", 100000, {"導演": 60000, "剪接": 40000}, synced_total=100000, at="2026-09-01")
    d, c = set_parent_share(d, c, "B", 80000, {"剪接": 80000}, synced_total=80000, at="2026-09-05")
    d["split"]["顧問"] = 20000
    c += 20000
    return d, c


class TestSetParentShare:
    def test_builds_totals_from_shares_and_keeps_owner_extra(self):
        d, c = _x()
        assert c == 200000
        assert d["split"] == {"導演": 60000, "剪接": 120000, "顧問": 20000}
        assert parent_shares(d)["A"] == {"amount": 100000, "split": {"導演": 60000, "剪接": 40000},
                                         "synced_total": 100000, "at": "2026-09-01"}

    def test_replacing_one_parent_moves_only_the_delta(self):
        d, c = _x()
        # A 改後期代開：收入變成 A 的合約額 150,000，工項不動
        d2, c2 = set_parent_share(d, c, "A", 150000, parent_shares(d)["A"]["split"])
        assert c2 == 250000                                       # +50,000，B 與顧問不動
        assert d2["split"] == {"導演": 60000, "剪接": 120000, "顧問": 20000}
        assert parent_shares(d2)["B"]["amount"] == 80000
        # A 重新同步：工項變成 導演 70,000（剪接拿掉）、金額 70,000
        d3, c3 = set_parent_share(d2, c2, "A", 70000, {"導演": 70000}, synced_total=70000, at="2026-09-13")
        assert c3 == 170000
        assert d3["split"] == {"導演": 70000, "剪接": 80000, "顧問": 20000}   # B 的剪接 80,000 留著
        assert parent_shares(d3)["A"]["synced_total"] == 70000

    def test_owner_hand_edits_below_the_share_never_go_negative(self):
        d, c = _x()
        d["split"]["剪接"] = 10000        # owner 手改成比 A+B 的剪接還低
        d2, c2 = set_parent_share(d, c, "A", 0, {})   # A 整個撤掉
        assert d2["split"].get("剪接", 0) == 0 and "導演" not in d2["split"]
        assert c2 == 100000

    def test_claim_records_share_without_touching_money(self):
        d = norm_detail({"split": {"剪接": 120000}, BY_PARENT_PENDING_KEY: True})
        d2, c2 = set_parent_share(d, 120000, "A", 40000, {"剪接": 40000}, claim=True)
        assert c2 == 120000 and d2["split"] == {"剪接": 120000}
        assert parent_shares(d2) == {"A": {"amount": 40000, "split": {"剪接": 40000}, "synced_total": 0, "at": ""}}
        assert d2.get(BY_PARENT_PENDING_KEY) is True      # 由呼叫端在全部認領完才清

    def test_synced_total_and_at_default_to_previous_values(self):
        d, c = _x()
        d2, _ = set_parent_share(d, c, "B", 90000, {"剪接": 90000})
        assert parent_shares(d2)["B"]["synced_total"] == 80000
        assert parent_shares(d2)["B"]["at"] == "2026-09-05"


class TestDropParentShare:
    def test_unlink_removes_that_parents_money_and_items(self):
        d, c = _x()
        d2, c2 = drop_parent_share(d, c, "A")
        assert c2 == 100000
        assert d2["split"] == {"剪接": 80000, "顧問": 20000}
        assert "A" not in parent_shares(d2) and "B" in parent_shares(d2)

    def test_dropping_unknown_parent_is_a_no_op(self):
        d, c = _x()
        assert drop_parent_share(d, c, "Z") == (d, c)


class TestNormDetail:
    def test_keeps_by_parent_and_pending_and_drops_garbage(self):
        d = norm_detail({BY_PARENT_KEY: {"A": {"amount": "100", "split": {"x": 5, "y": 0}, "synced_total": 7, "at": "2026-01-01"},
                                         "": {"amount": 1}, "B": "junk", "C": {"amount": 0}},
                         BY_PARENT_PENDING_KEY: True})
        assert d[BY_PARENT_KEY] == {"A": {"amount": 100, "split": {"x": 5}, "synced_total": 7, "at": "2026-01-01"},
                                    "C": {"amount": 0, "split": {}, "synced_total": 0, "at": ""}}
        assert d[BY_PARENT_PENDING_KEY] is True
        assert BY_PARENT_KEY not in norm_detail({"split": {}})
        assert BY_PARENT_PENDING_KEY not in norm_detail({BY_PARENT_PENDING_KEY: False})


class TestMirrorStalePerParent:
    def test_per_parent_when_shares_exist(self):
        d, _ = _x()
        assert mirror_stale(d, 100000, "A") == (False, 0)
        assert mirror_stale(d, 120000, "A") == (True, 20000)
        assert mirror_stale(d, 80000, "B") == (False, 0)

    def test_falls_back_to_legacy_mirror_total(self):
        d = norm_detail({MIRROR_TOTAL_KEY: 50000})
        assert mirror_stale(d, 50000, "A") == (False, 0)
        assert mirror_stale(d, 50000) == (False, 0)
        assert mirror_stale(norm_detail({}), 50000, "A") == (None, 0)


class TestLinkNoteShares:
    def test_sentence_lists_each_parents_share_and_pending(self):
        from core.ledger_project import link_note
        d = norm_detail({"source": "源日"})
        n = link_note("company", ("m", "私帳案 X", 200000), d, shares=[("母帳案 A", 100000), ("母帳案 B", 80000)])
        assert n["shares"] == [{"name": "母帳案 A", "amount": 100000}, {"name": "母帳案 B", "amount": 80000}]
        assert "份額 母帳案 A 100,000、母帳案 B 80,000" in n["text"]
        assert n["shares_pending"] is False
        p = link_note("company", ("m", "X", 1), d, shares_pending=True)
        assert p["shares_pending"] is True and "待認領" in p["text"]
        one = link_note("company", ("m", "X", 1), d)
        assert one["shares"] == [] and "份額" not in one["text"]


class TestWritersGoThroughShares:
    """分身收入的寫入點只准經過 set_parent_share／drop_parent_share —— 直接 `t.contract_amount = 某數`
    就是回到「整個換掉、別案的錢不見」的老路（owner 2026-09-13）。"""

    def test_project_links_never_assigns_contract_directly(self):
        import re
        from tests.unit._srcscan import repo_src
        src = repo_src("routers/crm/project_links.py")
        bad = [m.group(0) for m in re.finditer(r"\b\w+\.contract_amount\s*=(?!\s*new_contract\b)(?!=)[^\n]*", src)]
        assert not bad, "分身的 contract_amount 只能接 set_parent_share／drop_parent_share 回的 new_contract：\n" + "\n".join(bad)
        assert src.count("t.contract_amount = new_contract") >= 3        # 推送、收款方式、解除連結三處

    def test_norm_detail_keeps_by_parent_so_puts_do_not_wipe_it(self):
        d = norm_detail({"split": {}, BY_PARENT_KEY: {"A": {"amount": 5}}, "garbage": 1})
        assert BY_PARENT_KEY in d and "garbage" not in d
