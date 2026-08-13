# -*- coding: utf-8 -*-
"""專案工作流的純邏輯（core/project_flow.py）。

規格 docs/PROPOSAL_PLANNER.md §14。這裡釘住三件事：
  ① 亮燈只看資料、**不看當前階段**（owner：「有時候進度是多方前進」）
  ② 訊號撈不到寧可顯示未完成，不准謊報完成
  ③ 「略過」不是「未完成」（不上官網的案子不該卡在官網那盞燈）
"""
from core import project_flow as pf


class TestTemplate:
    def test_item_keys_are_globally_unique(self):
        """item_key 全域唯一是資料契約 —— 手動勾選用它當 DB 的鍵。"""
        keys = [ik for _tk, _tl, items in pf.TRACKS for ik, *_ in items]
        assert len(keys) == len(set(keys)), "item_key 重複了"

    def test_every_item_is_auto_or_manual(self):
        for _tk, _tl, items in pf.TRACKS:
            for ik, _lb, kind, _hint in items:
                assert kind in (pf.AUTO, pf.MANUAL), f"{ik} 的 kind 不合法"

    def test_gates_reference_real_items(self):
        """閘門引用的項目必須存在 —— 打錯字會變成永遠不缺這項（靜默失效）。"""
        for status, keys in pf.GATES.items():
            for k in keys:
                assert k in pf.ITEMS, f"{status} 的閘門引用了不存在的 {k}"
        for k in pf.ADVISORY:
            assert k in pf.ITEMS


class TestBuild:
    def test_missing_fact_counts_as_not_done(self):
        """撈不到訊號 → 未完成。謊報完成會讓人以為交付了。"""
        out = pf.build({})
        for t in out["tracks"]:
            for r in t["items"]:
                assert r["state"] in (pf.OFF,), f"{r['key']} 不該是 {r['state']}"

    def test_none_means_skip_not_incomplete(self):
        out = pf.build({"published": None, "work_ready": None})
        deliver = next(t for t in out["tracks"] if t["key"] == "deliver")
        skipped = [r for r in deliver["items"] if r["state"] == pf.SKIP]
        assert {r["key"] for r in skipped} == {"published", "work_ready"}
        # 略過的項目不進分母 —— 否則永遠 3/5 看起來像沒做完
        assert deliver["total"] == len(deliver["items"]) - 2

    def test_lights_ignore_current_stage(self):
        """🔴 核心保證：製作軌的燈不需要專案已經進到「製作」階段。"""
        out = pf.build({"staffed": True, "footage_in": True})
        prod = next(t for t in out["tracks"] if t["key"] == "prod")
        on = {r["key"] for r in prod["items"] if r["state"] == pf.ON}
        assert on == {"staffed", "footage_in"}
        # build() 的參數裡沒有 status —— 結構上就不可能看階段
        import inspect
        assert "status" not in inspect.signature(pf.build).parameters

    def test_manual_item_reads_stored_value(self):
        stored = {"shooting": {"checked": True, "by": "阿明", "at": "2026-08-14", "note": "第一天"}}
        out = pf.build({}, stored)
        prod = next(t for t in out["tracks"] if t["key"] == "prod")
        row = next(r for r in prod["items"] if r["key"] == "shooting")
        assert row["state"] == pf.ON and row["by"] == "阿明" and row["note"] == "第一天"

    def test_garbage_stored_value_does_not_crash(self):
        for junk in (None, [], "x", {"shooting": "yes"}, {"不存在的key": {"checked": True}}):
            out = pf.build({}, junk)
            assert out["tracks"]

    def test_detail_is_attached_to_auto_rows(self):
        out = pf.build({"quote": True}, None, {"quote": "報價單 2 版"})
        biz = next(t for t in out["tracks"] if t["key"] == "biz")
        assert next(r for r in biz["items"] if r["key"] == "quote")["detail"] == "報價單 2 版"


class TestMissingFacts:
    """`missing_facts` 這支述詞本身的行為。

    ⚠️ 這裡的斷言拿 AUTO_KEYS 自己造輸入，所以**測不到** router 有沒有漏算
    訊號（第一版誤以為測得到）—— 那個由 `test_flow_facts_align.py` 實際呼叫
    `_gather_facts` 來守。
    """

    def test_empty_facts_reports_every_auto_key(self):
        assert set(pf.missing_facts({})) == set(pf.AUTO_KEYS)

    def test_complete_facts_reports_nothing(self):
        assert pf.missing_facts({k: False for k in pf.AUTO_KEYS}) == ()

    def test_manual_keys_are_not_required_facts(self):
        """手動項的值來自 DB 不是 facts —— 別把它們也當成「漏算」。"""
        assert not (set(pf.MANUAL_KEYS) & set(pf.missing_facts({})))


class TestApplyCheck:
    def test_manual_round_trip(self):
        new, err = pf.apply_check(None, "shooting", True, "開機", who="小美", when="2026-08-14")
        assert err == "" and new["shooting"]["checked"] is True
        assert new["shooting"]["by"] == "小美"

    def test_auto_item_cannot_be_ticked(self):
        """🔴 自動訊號手動勾＝把「資料說了算」這條規則打破。"""
        stored, err = pf.apply_check(None, "staffed", True, "", who="x", when="y")
        assert err and stored is None

    def test_unknown_key_rejected(self):
        stored, err = pf.apply_check({}, "nope", True, "", who="x", when="y")
        assert err == "未知的項目" and stored == {}

    def test_note_length_capped(self):
        stored, err = pf.apply_check(None, "shooting", True, "x" * (pf.MAX_NOTE + 1),
                                     who="a", when="b")
        assert "上限" in err and stored is None

    def test_unchecking_keeps_audit_trail(self):
        s, _ = pf.apply_check(None, "shooting", True, "", who="甲", when="1")
        s2, _ = pf.apply_check(s, "shooting", False, "", who="乙", when="2")
        assert s2["shooting"]["checked"] is False and s2["shooting"]["by"] == "乙"


class TestGates:
    @staticmethod
    def _tracks(**states):
        """假的 tracks 結構 —— missing_for 只讀 items 的 key/state。"""
        return [{"key": "x", "items": [{"key": k, "state": v}
                                       for k, v in states.items()]}]

    def test_missing_lists_only_unfinished(self):
        out = pf.missing_for(self._tracks(won=pf.ON, quote_won=pf.OFF), "製作")
        assert [m["key"] for m in out] == ["quote_won"]

    def test_skip_is_not_missing(self):
        tracks = self._tracks(**{k: pf.SKIP for k in pf.GATES["歸檔"]})
        assert pf.missing_for(tracks, "歸檔") == []

    def test_settlement_is_advisory_never_blocking(self):
        """owner 決策點 5：款項未結清不擋結案，只提醒。"""
        out = pf.missing_for(self._tracks(approved=pf.ON, settled=pf.OFF), "結案")
        settled = [m for m in out if m["key"] == "settled"]
        assert settled and settled[0]["advisory"] is True

    def test_blocking_items_are_not_advisory(self):
        out = pf.missing_for(self._tracks(approved=pf.OFF, settled=pf.OFF), "結案")
        by_key = {m["key"]: m["advisory"] for m in out}
        assert by_key == {"approved": False, "settled": True}

    def test_no_gate_for_unknown_target(self):
        assert pf.missing_for([], "") == []
        assert pf.missing_for([], "未成案") == []


class TestStageView:
    def test_next_status_follows_pipeline(self):
        assert pf.stage_view("提案")["next"] == "製作"
        assert pf.stage_view("製作")["next"] == "結案"

    def test_presale_entry_points_all_jump_to_proposal(self):
        """投標/開發/洽詢是三種起點，不是要依序走完。"""
        for s in ("投標", "開發", "洽詢"):
            assert pf.stage_view(s)["next"] == "提案"

    def test_terminal_states_have_no_next(self):
        for s in ("歸檔", "未成案"):
            v = pf.stage_view(s)
            assert v["next"] == "" and v["is_terminal"] is True

    def test_lost_is_flagged_and_off_pipeline(self):
        v = pf.stage_view("未成案")
        assert v["is_lost"] is True and v["index"] == -1

    def test_unknown_status_does_not_crash(self):
        v = pf.stage_view("")
        assert v["index"] == -1 and v["next"] == ""
