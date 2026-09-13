# -*- coding: utf-8 -*-
"""私帳案的收入分案記帳 `ledger_detail.by_parent`（owner 2026-09-13「為何不加起來？」）。

一個私帳案 X 可以承接多個母帳案 A、B。以前 X 只存一個總數（contract_amount）與一份工項
（split），A 改收款方式時只能「整個換成 A 的」或「擋下來」；現在每案各記一份
`{amount, split, synced_total, at}`，X 的金額與工項只吃**差額** —— owner 自己填的那部分
（合約額 − Σ份額、手改的工項）永遠不被系統動到。

不變式：contract_amount = Σ by_parent[*].amount + 自己的；split[k] = Σ by_parent[*].split[k] + 自己的。
"""
from core.ledger_project import (BY_PARENT_KEY, BY_PARENT_PENDING_KEY, MIRROR_TOTAL_KEY, apply_source_fee,
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
        assert n["shares"] == [{"name": "母帳案 A", "amount": 100000, "source": ""}, {"name": "母帳案 B", "amount": 80000, "source": ""}]
        assert "份額 母帳案 A 100,000、母帳案 B 80,000" in n["text"] and "案源 源日" in n["text"]
        # 各案案源不同 → 案源寫「混合」、份額後面帶案源
        dm, _ = set_parent_share(d, 0, "A", 100000, {}, source="代開發票")
        dm, _ = set_parent_share(dm, 0, "B", 80000, {}, source="源日")
        m = link_note("company", ("m", "X", 180000), dm, shares=[("A", 100000, "代開發票"), ("B", 80000, "源日")])
        assert "案源 混合" in m["text"] and "A 100,000（代開發票）、B 80,000（源日）" in m["text"]
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


class TestPerParentSource:
    """代辦費分案（owner 2026-09-13）：每案份額帶 source，代辦費／個人稅款只算標那種案源的份額。"""

    def _mixed(self):
        # A 代開 100,000、B 源日 80,000、owner 自己填 20,000（跟 X 的案源走）
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {"導演": 100000}, source="代開發票")
        d, c = set_parent_share(d, c, "B", 80000, {"剪接": 80000}, source="源日")
        return d, c + 20000

    def test_share_keeps_source_and_norm_detail_validates_it(self):
        d, _ = self._mixed()
        assert parent_shares(d)["A"]["source"] == "代開發票" and parent_shares(d)["B"]["source"] == "源日"
        junk = norm_detail({BY_PARENT_KEY: {"A": {"amount": 1, "source": "亂填"}}})
        assert "source" not in parent_shares(junk)["A"]
        d2, _ = set_parent_share(d, 0, "A", 100000, {})          # source 沒給＝沿用
        assert parent_shares(d2)["A"]["source"] == "代開發票"

    def test_fee_bases_follow_each_parents_source_and_owner_part_follows_x(self):
        from core.ledger_project import fee_bases
        d, c = self._mixed()
        assert fee_bases(c, d) == (100000, 0)                       # X 是源日 → 自己那 20,000 不抽
        d["source"] = "代開發票"
        assert fee_bases(c, d) == (120000, 0)                       # X 改代開 → 自己那 20,000 也抽，B 仍不抽
        d["source"] = "執行業務所得"
        assert fee_bases(c, d) == (100000, 20000)
        assert fee_bases(50000, norm_detail({"source": "代開發票"})) == (50000, 0)     # 沒分案記錄＝整案
        assert fee_bases(50000, norm_detail({"source": "源日"})) == (0, 0)

    def test_apply_source_fee_charges_only_the_agency_shares(self):
        d, c = self._mixed()                                        # X 源日、A 代開 100,000
        out = apply_source_fee(c, d)
        assert out["invoice_fee"] == 8000                           # 100,000 × 8%，不是 200,000
        assert out["tax_fee"] == 4762 and out["buy_invoice"] == 3238
        # A 換回源日 → 沒有代開份額了 → 三欄歸零（不是留著舊數字）
        d2, _ = set_parent_share(out, c, "A", 100000, {"導演": 100000}, source="源日")
        out2 = apply_source_fee(c, d2)
        assert (out2["invoice_fee"], out2["tax_fee"], out2["buy_invoice"]) == (0, 0, 0)

    def test_mixed_agency_and_professional_income_compute_both(self):
        from core.ledger_project import withholding
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {}, source="代開發票")
        d, c = set_parent_share(d, c, "B", 42000, {}, source="執行業務所得")
        out = apply_source_fee(c, d)
        assert out["invoice_fee"] == 8000 and out["personal_tax"] == withholding(42000)

    def test_manual_fee_still_respected_with_shares(self):
        d, c = self._mixed()
        d["invoice_fee"] = 5000
        out = apply_source_fee(c, d, keep={"invoice_fee"})
        assert out["invoice_fee"] == 5000 and "invoice_fee" in out["manual"]
        assert out["buy_invoice"] == 5000 - out["tax_fee"]

    def test_source_mixed_flag(self):
        from core.ledger_project import source_mixed
        d, _ = self._mixed()
        assert source_mixed(d) is True
        one, _ = set_parent_share({"source": "源日"}, 0, "A", 1, {}, source="源日")
        assert source_mixed(one) is False
        assert source_mixed(norm_detail({"source": "代開發票"})) is False


def test_frontend_fee_preview_uses_the_same_bases_as_the_backend():
    """前端試算不能用整案算：後端算 8,000、前端送 14,400 → 被當成人改過而凍住（代辦費分案）。"""
    from tests.unit._srcscan import js_func_body, repo_src
    src = repo_src("frontend/tabs/finance/subviews/projects.js")
    assert "function _feeBases(" in src and "parent_shares" in src
    body = js_func_body(src, "const _syncFee = ")
    assert "_feeBases(c, src)" in body and "Math.round(base * pct / 100)" in body
    assert "_proTax(bases.pro)" in body
    assert "Math.round(c * pct" not in body, "代辦費試算又用整案算了"


class TestPolishRound1:
    """/polish 2026-09-13 階段一的發現（BUG-10、11、14）。"""

    def test_stale_with_a_share_record_never_falls_back_to_the_shared_key(self):
        # BUG-10：A 是代開分身（synced_total 0），B 加上去後 X 的 mirror_total=30000 → A 不能被說成落後 −30000
        d, _ = set_parent_share({}, 0, "A", 100000, {}, synced_total=0)
        d[MIRROR_TOTAL_KEY] = 30000
        assert mirror_stale(d, 0, "A") == (None, 0)
        assert mirror_stale(d, 30000) == (False, 0)                 # 沒指定案的舊讀法照舊

    def test_push_share_source_comes_from_the_parent_not_from_x(self):
        # BUG-11：這次明確指定的 > 這案舊份額的 > 源日；**不看 X 的 source**（可能已被別案翻成代開）
        from routers.crm.project_links import _push_share_source
        assert _push_share_source("代開發票", {"source": "源日"}) == "代開發票"
        assert _push_share_source("", {"source": "源日"}) == "源日"
        assert _push_share_source("", {}) == "源日"
        from tests.unit._srcscan import code_only, flow_body, projects_src
        body = code_only(flow_body(projects_src(), "async def mirror_project_to_mine("))
        assert "share_src = _push_share_source(source, old_share)" in body and "source=share_src" in body
        assert 'source=keep["source"]' not in body

    def test_pending_add_records_this_push_not_a_running_sum(self):
        # BUG-14：待認領時 add 不能累加（錢沒動、記錄卻長大）→ 視同 overwrite
        from tests.unit._srcscan import code_only, func_body, projects_src
        body = code_only(func_body(projects_src(), "def _push_share_amount("))
        assert 'if mode == "add" and not pending:' in body


class TestPolishRound2:
    """/polish 2026-09-13 階段一第 2 輪（BUG-16～19）。"""

    def test_push_amount_keys_off_the_shares_source_not_x(self):
        # BUG-16：X 已被 A 翻成代開；推 B（company、沒指定案源）→ B 是源日 → 金額＝掛給我的成本行，不是 B 的合約額
        from routers.crm.project_links import _push_share_amount
        old = {"amount": 0, "split": {}}
        assert _push_share_amount("overwrite", False, "源日", old, 30000, 500000) == 30000
        assert _push_share_amount("overwrite", False, "代開發票", old, 30000, 500000) == 500000
        assert _push_share_amount("overwrite", False, "代開發票", {"amount": 120000, "split": {}, "source": "代開發票"}, 30000, 500000) == 120000
        assert _push_share_amount("add", False, "源日", {"amount": 10000, "split": {}}, 30000, 500000) == 40000
        assert _push_share_amount("add", True, "源日", {"amount": 10000, "split": {}}, 30000, 500000) == 30000   # 待認領：add 視同取代
        from tests.unit._srcscan import code_only, flow_body, projects_src
        body = code_only(flow_body(projects_src(), "async def mirror_project_to_mine("))
        assert "share_src = _push_share_source(source, old_share)" in body
        assert "amount = _push_share_amount(mode, claim, share_src, old_share, mir[\"total\"]," in body
        assert 'if keep["source"] == "代開發票":' not in body

    def test_legacy_claim_takes_the_items_even_when_only_part_of_the_money_was_mirrored(self):
        # BUG-18：只認金額不認工項 → 下一次重新同步把鏡射來的工項再加一次（{A:30000} → {A:60000}）
        from core.ledger_project import legacy_claim
        d = legacy_claim({"split": {"導演": 30000}, MIRROR_TOTAL_KEY: 30000}, 50000, "A")
        assert parent_shares(d)["A"] == {"amount": 30000, "split": {"導演": 30000}, "synced_total": 30000, "at": "", "source": "源日"}
        d2, c2 = set_parent_share(d, 50000, "A", 30000, {"導演": 30000}, synced_total=30000)   # CRM 沒變、重新同步
        assert d2["split"] == {"導演": 30000} and c2 == 50000

    def test_fees_are_zeroed_when_no_share_bears_them_anymore(self):
        # BUG-19：X 源日、A 代開 100,000 → 費用 8000；A 解除 → 沒有代開份額了 → 三欄要歸零，不能留舊數字
        from core.ledger_project import drop_parent_share, withholding
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {}, source="代開發票")
        d = apply_source_fee(c, d)
        assert d["invoice_fee"] == 8000
        d, c = drop_parent_share(d, c, "A")
        d = apply_source_fee(c, d)
        assert (d["invoice_fee"], d["tax_fee"], d["buy_invoice"]) == (0, 0, 0)
        # personal_tax 同理：執行業務所得的份額沒了就歸零（手改的不動）
        d, c = set_parent_share({"source": "源日"}, 0, "B", 42000, {}, source="執行業務所得")
        d = apply_source_fee(c, d)
        assert d["personal_tax"] == withholding(42000)
        d, c = set_parent_share(d, c, "B", 42000, {}, source="源日")
        d = apply_source_fee(c, d)
        assert d["personal_tax"] == 0
        # 沒分案記錄、案源源日、使用者自己填的數字：跟以前一樣不動
        plain = apply_source_fee(1000, norm_detail({"source": "源日", "invoice_fee": 77}))
        assert plain["invoice_fee"] == 77


class TestPolishFinalReview:
    """/polish 收尾 review 的 7 項（BUG-22～28）。"""

    def test_legacy_claim_takes_the_whole_contract_for_agency_mirrors(self):
        # BUG-23：舊的代開分身 contract＝母帳合約額、mirror_total＝成本行（本來就 mt < c）→ 要整筆認，
        # 不然收款方式來回一次 X 從 100,000 變 197,000
        from core.ledger_project import legacy_claim
        d = legacy_claim({"source": "代開發票", "split": {"導演": 3000}, MIRROR_TOTAL_KEY: 3000}, 100000, "A")
        assert parent_shares(d)["A"]["amount"] == 100000
        d2 = legacy_claim({"source": "源日", "split": {"導演": 3000}, MIRROR_TOTAL_KEY: 3000}, 100000, "A")
        assert parent_shares(d2)["A"]["amount"] == 3000                # 源日照舊只認鏡射量

    def test_zeroing_respects_keep_and_marks_manual(self):
        # BUG-24：有份額但沒代開／執行業務所得基數 → 歸零前要看 keep：owner 在源日分身手填的個人稅款要留住、標手動
        d, c = set_parent_share({"source": "源日"}, 0, "A", 50000, {}, source="源日")
        d["personal_tax"] = 3000
        out = apply_source_fee(c, d, keep={"personal_tax"})
        assert out["personal_tax"] == 3000 and "personal_tax" in out["manual"]
        d["invoice_fee"] = 700
        out2 = apply_source_fee(c, d, keep={"invoice_fee"})
        assert out2["invoice_fee"] == 700 and "invoice_fee" in out2["manual"]
        # 沒送（不在 keep）、也沒標手動 → 照樣歸零
        out3 = apply_source_fee(c, norm_detail({**out, "manual": []}))
        assert out3["personal_tax"] == 0

    def test_contract_never_goes_negative(self):
        # BUG-26：owner 手動把合約改得比份額低再解除 → 不能存負數
        from core.ledger_project import drop_parent_share
        d, c = set_parent_share({}, 0, "A", 50000, {})
        d2, c2 = drop_parent_share(d, 20000, "A")
        assert c2 == 0

    def test_push_claims_only_parents_that_were_already_linked(self):
        # BUG-22：X 連著 A（沒份額 → 待認領）時推**新的** B：B 的錢要真的加進 X，不是 claim
        from routers.crm.project_links import _claim_on_push
        assert _claim_on_push(True, ["A"], "A") is True
        assert _claim_on_push(True, ["A"], "B") is False
        assert _claim_on_push(False, ["A"], "A") is False
        from tests.unit._srcscan import code_only, flow_body, projects_src
        body = code_only(flow_body(projects_src(), "async def mirror_project_to_mine("))
        assert "claim = _claim_on_push(pending, linked_before, p.id)" in body and "claim=claim)" in body

    def test_mirror_check_judges_stale_per_parent_and_shows_this_parents_share(self):
        # BUG-27：推送對話框那支要跟詳情那行同一套規則（逐案判落後、比這案的份額不是整案）
        from tests.unit._srcscan import code_only, func_body, projects_src
        chk = code_only(func_body(projects_src(), "async def check_project_mirror("))
        assert 'mirror_stale(linked.ledger_detail, mir["total"], p.id)' in chk
        assert "share = parent_shares(" in chk and '"amount": share["amount"] if share else' in chk

    def test_no_false_persist_comment_before_the_409(self):
        # BUG-28：409 前 t.ledger_detail = keep 不會落庫（PUT 整個 rollback），註解與賦值都拿掉
        from tests.unit._srcscan import code_only, func_body, projects_src
        fn = code_only(func_body(projects_src(), "async def apply_billing_mode("))
        assert "t.ledger_detail = keep" not in fn          # 只透過 _store_mirror_detail 落庫


class TestPolishRound4:
    """驗證 review（第 4 輪）的 5 項（BUG-29～33）。"""

    def test_first_share_freezes_hand_filled_fees_as_manual(self):
        # BUG-31：源日 X 上 owner 手填的個人稅款從沒被標手動（舊碼對源日早退）；第一次進分案世界要把它凍住，
        # 之後推送／解除（不帶 keep 的 apply_source_fee）才不會把它洗成 0
        d = norm_detail({"source": "源日", "personal_tax": 3000, "invoice_fee": 500})
        d2, _ = set_parent_share(d, 50000, "A", 0, {}, claim=True)
        assert {"personal_tax", "invoice_fee"} <= set(d2["manual"])
        out = apply_source_fee(50000, d2)
        assert out["personal_tax"] == 3000 and out["invoice_fee"] == 500
        # 案源本來就抽那種費的不凍（那是試算值，照舊自動）
        e = norm_detail({"source": "代開發票", "invoice_fee": 4000})
        e2, _ = set_parent_share(e, 50000, "A", 0, {}, claim=True)
        assert "invoice_fee" not in (e2.get("manual") or [])

    def test_push_paths_use_the_shares_source_everywhere(self):
        from tests.unit._srcscan import code_only, flow_body, func_body, projects_src
        push = code_only(func_body(projects_src(), "async def mirror_project_to_mine("))
        # BUG-32：降成 keep 的保護看份額的案源，不看 X 的
        assert '(source or keep.get("source") or MIRROR_SOURCE) != "代開發票"' not in push
        assert '_push_share_source(source, parent_shares(keep).get(p.id) or {}) != "代開發票"' in push
        # BUG-30：待認領期間已認領過的案再推 → 不再 claim（差額要進 X）
        assert "claim = _claim_on_push(pending, linked_before, p.id) and p.id not in parent_shares(keep)" in push
        # 第 7 輪起換回不再翻 X 的案源，「別案還走代開」的判斷跟著拿掉
        bm = code_only(flow_body(projects_src(), "async def apply_billing_mode("))
        assert "others_agency" not in bm


class TestPolishRound5:
    """第 5 輪驗證 review（BUG-34～40）：金額守恆已確認，這輪是 X 層級案源／費用的副作用。"""

    def test_fee_bases_never_exceed_the_contract(self):
        # BUG-40：owner 把合約額改得比 Σ份額低（實際發票 90,000）→ 代辦費基數不能超過營收
        from core.ledger_project import fee_bases
        d, _ = set_parent_share({"source": "源日"}, 0, "B", 100000, {}, source="代開發票")
        assert fee_bases(90000, d) == (90000, 0)
        d2, _ = set_parent_share(d, 0, "C", 42000, {}, source="執行業務所得")
        assert fee_bases(120000, d2) == (100000, 20000)          # 兩種基數合計也不超過營收

    def test_map_link_on_a_recordless_x_with_other_parents_marks_pending(self):
        # BUG-38：X 是 Q 的舊分身（沒記錄、_m22 沒跑到），對應表再連 P → 不能只寫 P 的 0 份額就當沒事，
        # 要標待認領，之後推 Q 才是 claim 不會重複加
        from tests.unit._srcscan import code_only, func_body, projects_src
        fn = code_only(func_body(projects_src(), "async def _write_link("))
        assert "keep[BY_PARENT_PENDING_KEY] = True" in fn
        assert "_clear_pending_if_all_claimed(session, mine, keep)" in fn        # BUG-36：keep 路徑也能清旗標

    def test_mirror_check_guards_partially_claimed_pending_like_link_note(self):
        # BUG-37：部分認領的待認領 X，對沒記錄的那案不能退回 X 層級 mirror_total（那是別案剛寫的）
        from tests.unit._srcscan import code_only, func_body, projects_src
        chk = code_only(func_body(projects_src(), "async def check_project_mirror("))
        assert "shared = n_parents > 1 and share is None" in chk

    def test_delete_master_project_unlinks_first(self):
        # BUG-39：刪母帳案不解除連結 → X 留著孤兒份額，後端算它、詳情 API 不回它 → 前後端基數分叉 → 代辦費被凍住
        from tests.unit._srcscan import code_only, func_body, projects_src
        fn = code_only(func_body(projects_src(), "async def delete_project("))
        assert "await _write_link(session, project, None)" in fn


def test_frontend_fee_bases_clamp_like_the_backend():
    # BUG-41：後端 fee_bases 夾在營收內，前端 _feeBases 也要夾，不然合約改低時前端送 8,000 後端算 7,200 → 凍住
    from tests.unit._srcscan import js_func_body, repo_src
    body = js_func_body(repo_src("frontend/tabs/finance/subviews/projects.js"), "function _feeBases(")
    assert "agency = Math.min(agency, c)" in body and "pro = Math.min(pro, Math.max(0, c - agency))" in body


class TestPolishRound7:
    """第 7 輪：X 的案源不再被系統翻（永遠是 owner 自己那部分的）；畫面案源由份額算（display_source）。"""

    def test_display_source_is_derived_from_shares_and_owner_part(self):
        from core.ledger_project import display_source
        assert display_source(50000, norm_detail({"source": "源日"})) == "源日"          # 沒份額＝X 的
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {}, source="代開發票")
        assert display_source(c, d) == "代開發票"                                          # 全是 A、owner 0 → 代開
        assert display_source(c + 20000, d) == "混合"                                      # owner 多 20,000（源日）
        d2, c2 = set_parent_share(d, c, "B", 80000, {}, source="源日")
        assert display_source(c2, d2) == "混合"
        e, ce = set_parent_share({"source": "執行業務所得"}, 100000, "A", 0, {}, source="源日", claim=True)
        assert display_source(ce, e) == "執行業務所得"                                    # A 0 份額不算

    def test_x_source_is_never_flipped_by_the_system(self):
        from tests.unit._srcscan import code_only, func_body, projects_src, repo_src
        pl_src = repo_src("routers/crm/project_links.py")
        assert 'keep["source"] = want_source' not in pl_src
        assert 'keep["source"] = MIRROR_SOURCE' not in pl_src and 'before["source"] = MIRROR_SOURCE' not in pl_src
        push = code_only(func_body(projects_src(), "async def mirror_project_to_mine("))
        assert 'keep["source"] = keep.get("source") or MIRROR_SOURCE' in push       # 只補空，不覆寫

    def test_agency_amount_switches_to_the_invoice_face_when_the_share_was_not_agency(self):
        # 第 7 輪 #3：源日份額 30,000 的案改走代開 → 金額換成母帳合約額，不是沿用 30,000
        from routers.crm.project_links import _push_share_amount
        assert _push_share_amount("overwrite", False, "代開發票", {"amount": 30000, "split": {}, "source": "源日"}, 30000, 100000) == 100000
        assert _push_share_amount("overwrite", False, "代開發票", {"amount": 120000, "split": {}, "source": "代開發票"}, 30000, 100000) == 120000

    def test_drop_of_an_agency_share_releases_the_manual_fee(self):
        # 第 7 輪 #5：純代開分身、owner 手調代辦費 9,000 → 解除 P 後不能留 9,000 在合約 0 的 X 上
        from core.ledger_project import drop_parent_share
        d, c = set_parent_share({"source": "源日"}, 0, "P", 100000, {}, source="代開發票")
        d["invoice_fee"] = 9000
        d = apply_source_fee(c, d, keep={"invoice_fee"})
        assert "invoice_fee" in d["manual"]
        d2, c2 = drop_parent_share(d, c, "P")
        assert c2 == 0 and d2["invoice_fee"] == 0 and "invoice_fee" not in (d2.get("manual") or [])


class TestPolishRound8:
    """第 8 輪：X 案源不翻之後，所有「看 X.source 決定費用」的讀法都要改看份額（基數）。"""

    def test_expected_cash_in_deducts_the_fee_when_any_share_is_agency(self):
        # #1（高）：推送建的 X（源日）→ A 改代開：代辦費 8,000 已扣，應收要 92,000 不是 100,000
        from core.ledger_project import expected_cash_in, receivable_fields, client_wire
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {}, source="代開發票")
        d = apply_source_fee(c, d)
        assert d["invoice_fee"] == 8000
        assert expected_cash_in(c, d) == 92000 == client_wire(c, d)
        assert receivable_fields(c, 92000, d)[1] == "全額到帳"
        # 源日 X、owner 手填（凍住）的代辦費、沒有代開份額 → 跟以前一樣不扣
        e = norm_detail({"source": "源日", "invoice_fee": 500, "manual": ["invoice_fee"]})
        assert expected_cash_in(1000, e) == 1000

    def test_link_note_fee_sentence_follows_the_bases_not_x_source(self):
        # #2：X 源日、A 代開 → 句子要寫代辦費明細，不能寫「不抽代辦費」
        from core.ledger_project import link_note
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {}, source="代開發票")
        d = apply_source_fee(c, d)
        n = link_note("passthrough", ("m", "X", c), d)
        assert "代辦費 8% ＝ 8,000" in n["text"] and "不抽代辦費" not in n["text"]
        plain = link_note("company", ("m", "X", 1000), norm_detail({"source": "源日"}))
        assert "不抽代辦費" in plain["text"]

    def test_revert_restores_the_shares_previous_source(self):
        # #6：以執行業務所得推送的份額，切代開再換回 → 回執行業務所得，不是一律源日
        d, c = set_parent_share({"source": "源日"}, 0, "A", 40000, {}, source="執行業務所得")
        d2, c2 = set_parent_share(d, c, "A", 100000, {}, source="代開發票", prev_source=True)
        assert parent_shares(d2)["A"]["prev_source"] == "執行業務所得"
        d3, c3 = set_parent_share(d2, c2, "A", 100000, {}, source=None, restore_source=True)
        assert parent_shares(d3)["A"]["source"] == "執行業務所得" and "prev_source" not in parent_shares(d3)["A"]
        # 沒記 prev 的（舊資料）退回源日
        d4, _ = set_parent_share({"source": "源日"}, 0, "B", 1, {}, source="代開發票")
        d5, _ = set_parent_share(d4, 1, "B", 1, {}, restore_source=True)
        assert parent_shares(d5)["B"]["source"] == "源日"

    def test_dropping_an_agency_share_always_releases_the_manual_fee_like_revert(self):
        # #4：換回無條件放掉代辦費手動旗標，解除代開份額也要一樣（不只最後一個）
        from core.ledger_project import drop_parent_share
        d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {}, source="代開發票")
        d, c = set_parent_share(d, c, "B", 100000, {}, source="代開發票")
        d["invoice_fee"] = 15000
        d = apply_source_fee(c, d, keep={"invoice_fee"})
        d2, c2 = drop_parent_share(d, c, "B")
        assert "invoice_fee" not in (d2.get("manual") or []) and d2["invoice_fee"] == 8000

    def test_hand_filled_tax_fee_is_frozen_with_the_fee_group(self):
        # #9：源日 X 手填了稅金（代辦費 0）→ 第一次分案時整組凍住，不被歸零
        d = norm_detail({"source": "源日", "tax_fee": 500})
        d2, _ = set_parent_share(d, 10000, "A", 0, {}, claim=True)
        assert "invoice_fee" in d2["manual"]
        assert apply_source_fee(10000, d2)["tax_fee"] == 500

    def test_push_rules_round8(self):
        from tests.unit._srcscan import code_only, func_body, projects_src, repo_src
        push = code_only(func_body(projects_src(), "async def mirror_project_to_mine("))
        # #5：代開份額按「加上去」＝取代（金額不加、工項不能翻倍）
        assert 'if mode == "add" and share_src == "代開發票":' in push and 'mode = "overwrite"' in push
        link = code_only(func_body(projects_src(), "async def _write_link("))
        # #8：對應表連結時 X 沒記錄、而且就是這案的舊形狀連結 → legacy_claim，不是 0 份額
        assert "legacy_claim(keep, int(mine.contract_amount or 0), parent.id)" in link
        # #3：解除時私帳側的舊指標一起清，pending 才數得對
        assert "t.source_project_id = None" in link
        # #7：前端混合案源時費率／已扣除也要送
        js = repo_src("frontend/tabs/finance/subviews/projects.js")
        assert "_feeBases(Number(g2('fpl-contract')) || 0, sEl.value).agency > 0" in js
