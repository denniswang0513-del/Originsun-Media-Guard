# -*- coding: utf-8 -*-
"""專案工作流的純邏輯（core/project_flow.py）。

規格 docs/PROPOSAL_PLANNER.md §14。這裡釘住三件事：
  ① 亮燈只看資料、**不看當前階段**（owner：「有時候進度是多方前進」）
  ② 訊號撈不到寧可顯示未完成，不准謊報完成
  ③ 「略過」不是「未完成」（不上官網的案子不該卡在官網那盞燈）
"""
from core import project_flow as pf


def _track(built, key):
    """build() 出來的某一軌 —— 自訂項那組測試共用。"""
    return next(t for t in built["tracks"] if t["key"] == key)


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


class TestWinsProposal:
    """「這次推進會不會把衛星提案標成案」＝「推進對話框要不要問成案原因」。

    🔴 兩個呼叫端共用這一支（`_sync_linked_proposals` 真的去標、`flow._payload`
    決定問不問）。它存在的理由就是那兩條 False 分支 —— 分兩處寫的時候，
    前端只算得出 `next === '製作'`，於是多筆提案的專案會**白問一次**，
    使用者打完的成案原因被後端靜默丟掉。
    """

    def test_single_proposal_entering_win_stage(self):
        assert pf.wins_proposal("製作", 1, False) is True

    def test_every_win_stage_counts(self):
        for s in pf.WIN_STATUSES:
            assert pf.wins_proposal(s, 1, False) is True, s

    def test_multiple_proposals_never_auto_win(self):
        """🔴 同一案提三個 concept 時只有一個會贏，誰贏由專案負責人指定。"""
        for n in (2, 3, 7):
            assert pf.wins_proposal("製作", n, False) is False, n

    def test_no_proposal_at_all(self):
        assert pf.wins_proposal("製作", 0, False) is False

    def test_already_won_does_not_ask_again(self):
        assert pf.wins_proposal("製作", 1, True) is False

    def test_non_win_stage(self):
        for s in ("提案", "洽詢", "未成案", "", "投標"):
            assert pf.wins_proposal(s, 1, False) is False, s


class TestGates:
    @staticmethod
    def _tracks(**states):
        """假的 tracks 結構 —— missing_for 只讀 items 的 key/state。"""
        return [{"key": "x", "items": [{"key": k, "state": v}
                                       for k, v in states.items()]}]

    @staticmethod
    def _keys(bag):
        return [m["key"] for m in bag]

    def test_missing_lists_only_unfinished(self):
        out = pf.missing_for(self._tracks(won=pf.ON, quote_won=pf.OFF), "製作")
        assert self._keys(out["blocking"]) == ["quote_won"]

    def test_skip_is_not_missing(self):
        tracks = self._tracks(**{k: pf.SKIP for k in pf.GATES["歸檔"]})
        out = pf.missing_for(tracks, "歸檔")
        assert out == {"blocking": [], "advisory": []}

    def test_settlement_is_advisory_never_blocking(self):
        """owner 決策點 5：款項未結清不擋結案，只提醒。"""
        out = pf.missing_for(self._tracks(approved=pf.ON, settled=pf.OFF), "結案")
        assert self._keys(out["advisory"]) == ["settled"]
        assert out["blocking"] == []

    def test_blocking_and_advisory_go_to_separate_bags(self):
        """兩袋分開回 —— 前端有兩處要用，各自 filter 一次就是同一段判斷寫兩份。"""
        out = pf.missing_for(self._tracks(approved=pf.OFF, settled=pf.OFF), "結案")
        assert self._keys(out["blocking"]) == ["approved"]
        assert self._keys(out["advisory"]) == ["settled"]
        # 缺項與燈號列共用同一個 dest 欄位名（見 core.project_flow.missing_for）
        assert out["blocking"][0]["dest"] == pf.ITEM_DEST["approved"]

    def test_no_gate_for_unknown_target(self):
        for target in ("", "未成案"):
            assert pf.missing_for([], target) == {"blocking": [], "advisory": []}


class TestDeepLinks:
    """「去完成」deep-link（§14.4）。

    連結靠兩段字串接（item → 模組鍵 → tab），任一段對不上畫面**都不會報錯**：
    只會安安靜靜少一條連結。這組就是那兩段的守衛。
    （「誰進得去這個目的地」不在這裡 —— 那是 core.auth.TAB_ACCESS，
    由 test_rbac_module_sync 守。）
    """

    def test_every_auto_item_has_a_destination(self):
        assert sorted(pf.AUTO_KEYS - set(pf.ITEM_DEST)) == []

    def test_manual_items_have_no_destination(self):
        assert not set(pf.MANUAL_KEYS) & set(pf.ITEM_DEST)

    def test_destination_keys_are_real_rbac_modules(self):
        """打錯字的話 `payload_grants` 對每個人都回 False —— 連管理員都會看到
        「你沒有這個模組權限」。"""
        from core.auth import ALL_MODULES
        for key in pf.DESTS:
            assert key in ALL_MODULES, f"{key} 不是合法的模組鍵"

    # 另一段（目的地在 TAB_MAP 裡有 tab）是跨語言比對 → 住在
    # test_rbac_module_sync，那個檔就是為這種比對存在的。

    def test_only_unlit_auto_rows_carry_a_destination(self):
        out = pf.build({"quote": True, "published": None},
                       {"shooting": {"checked": True}})
        rows = {r["key"]: r for t in out["tracks"] for r in t["items"]}
        assert "dest" not in rows["quote"], "亮著的燈不該帶去處"
        assert "dest" not in rows["published"], "略過的燈不該帶去處"
        assert "dest" not in rows["shooting"], "手動項不該帶去處"
        assert rows["settled"]["dest"] == "crm_invoices"


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


class TestCustomItems:
    """自訂項（owner 2026-08-15：「裡頭的項目細節也是要可以新增刪除」）。

    範本那五軌是全公司通用的；「這個案子額外要做的事」放專案自己的 blob。
    """

    def _added(self, label="補拍空景", track="prod", stored=None):
        return pf.add_custom(stored or {}, track, label,
                             key_seed="abc123", who="me", when="2026-08-15 10:00")

    def test_add_then_it_shows_up_in_that_track(self):
        cur, err = self._added()
        assert err == ""
        rows = _track(pf.build({}, cur), "prod")["items"]
        mine = [r for r in rows if r.get("custom")]
        assert len(mine) == 1
        assert mine[0]["label"] == "補拍空景" and mine[0]["state"] == pf.OFF
        # 範本項一個都沒少（自訂項是加在後面，不是取代）
        assert len(rows) == len(pf.TRACKS[2][2]) + 1

    def test_custom_can_be_checked_and_counts_toward_the_track(self):
        cur, _ = self._added()
        key = next(iter(cur[pf.CUSTOM_BUCKET]))
        cur, err = pf.apply_check(cur, key, True, "已補", who="me", when="now")
        assert err == ""
        t = _track(pf.build({}, cur), "prod")
        assert [r for r in t["items"] if r["key"] == key][0]["state"] == pf.ON
        assert t["done"] >= 1

    def test_removing_keeps_the_manual_checks(self):
        """🔴 兩者住同一個 blob —— 刪自訂項不能把手動勾選一起弄丟。"""
        cur, _ = pf.apply_check({}, "shooting", True, "", who="me", when="now")
        cur, _ = pf.add_custom(cur, "prod", "補拍", key_seed="x1",
                               who="me", when="now")
        key = next(iter(cur[pf.CUSTOM_BUCKET]))
        cur, err = pf.remove_custom(cur, key)
        assert err == ""
        rows = {r["key"]: r for r in _track(pf.build({}, cur), "prod")["items"]}
        assert rows["shooting"]["state"] == pf.ON, "刪自訂項把手動勾選弄丟了"
        assert not any(r.get("custom") for r in rows.values())

    def test_template_items_cannot_be_removed(self):
        cur, _ = self._added()
        _, err = pf.remove_custom(cur, "shooting")
        assert "不能刪除" in err

    def test_bad_input_is_refused(self):
        assert self._added(track="nope")[1] == "未知的軌道"
        assert self._added(label="   ")[1] == "請填項目名稱"
        assert "上限" in self._added(label="x" * 200)[1]

    def test_cap_per_project(self):
        cur = {}
        for i in range(pf.MAX_CUSTOM):
            cur, err = pf.add_custom(cur, "prod", f"項目{i}", key_seed=f"k{i}",
                                     who="me", when="now")
            assert err == ""
        _, err = pf.add_custom(cur, "prod", "再一個", key_seed="over",
                               who="me", when="now")
        assert "上限" in err

    def test_garbage_in_the_blob_is_dropped_not_crashed(self):
        for junk in ({pf.CUSTOM_BUCKET: "x"}, {pf.CUSTOM_BUCKET: {"a": 1}},
                     {pf.CUSTOM_BUCKET: {"a": {"track": "nope", "label": "x"}}},
                     {pf.CUSTOM_BUCKET: {"a": {"track": "prod", "label": "  "}}}):
            assert not any(r.get("custom")
                           for t in pf.build({}, junk)["tracks"] for r in t["items"])
