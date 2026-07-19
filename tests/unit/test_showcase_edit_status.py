"""showcase-edit 發布狀態 — token 端點的白名單純函式測試。

安全鐵則：rebuild 狀態經 rebuild_status_public 過濾後**絕不**含 output_tail /
error 內文（build log 不可洩漏給 token 持有者）。
"""
from core.crm_logic import rebuild_status_public

NOW = 1_752_800_000.0


class TestRebuildStatusPublic:
    def test_whitelist_only_four_keys(self):
        raw = {
            "state": "running", "pending_count": 3,
            "auto_rebuild_fires_at": NOW + 42, "last_success_at": NOW - 100,
            "output_tail": "SECRET build log", "error": "Traceback ...",
            "started_at": NOW, "finished_at": None,
        }
        out = rebuild_status_public(raw, NOW)
        assert set(out.keys()) == {"state", "pending_count", "auto_fires_in_sec", "last_success_at"}
        assert "SECRET" not in str(out) and "Traceback" not in str(out)

    def test_fires_in_sec_math(self):
        out = rebuild_status_public({"auto_rebuild_fires_at": NOW + 42.4}, NOW)
        assert out["auto_fires_in_sec"] == 42

    def test_fires_in_past_clamped_to_zero(self):
        out = rebuild_status_public({"auto_rebuild_fires_at": NOW - 5}, NOW)
        assert out["auto_fires_in_sec"] == 0

    def test_no_timer_is_none(self):
        assert rebuild_status_public({"state": "idle"}, NOW)["auto_fires_in_sec"] is None

    def test_none_input_degrades_to_idle(self):
        # 精簡 agent 無 website services → raw=None 安全降級
        out = rebuild_status_public(None, NOW)
        assert out == {"state": "idle", "pending_count": 0,
                       "auto_fires_in_sec": None, "last_success_at": None}

    def test_pending_count_coerced_int(self):
        assert rebuild_status_public({"pending_count": None}, NOW)["pending_count"] == 0


class TestAiDescriptionContext:
    """_ai_description_context 的 NULL 防呆 — 2026-07-19 歐姆龍案例：
    作品 description 與專案 public_description 皆 NULL → existing=None →
    .strip() 炸 500，前端只看到通用「AI 產生問題失敗」。"""

    async def test_both_descriptions_null_returns_empty_string(self):
        from routers.crm.showcase import _ai_description_context

        class _Project:
            client_id = None
            name = "測試專案"
            public_description = None   # DB NULL

        class _Showcase:
            project_id = "p1"
            id = "p1"
            title = "測試作品"
            description = None          # DB NULL
            ai_reference_files = None
            ai_reference_notes = None

        class _FakeSession:
            async def get(self, model, pk):
                return _Project()

        name, client, existing, ref = await _ai_description_context(_FakeSession(), _Showcase())
        assert existing == ""
        assert name == "測試作品"
