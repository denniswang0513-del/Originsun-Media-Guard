"""core/crm_logic.py 官網作品純函式（1:N 改造）— work_stage / work_completeness /
project_works_summary。"""
from core.crm_logic import project_works_summary, work_completeness, work_stage


class TestWorkStage:
    def test_published_wins_over_prod_stage(self):
        assert work_stage(True, "製作中") == "已上線"

    def test_prod_stage_passthrough(self):
        assert work_stage(False, "製作中") == "製作中"
        assert work_stage(False, "不上官網") == "不上官網"

    def test_none_and_unknown_default_to_pending(self):
        assert work_stage(False, None) == "待製作"
        assert work_stage(False, "待製作") == "待製作"
        assert work_stage(False, "怪值") == "待製作"


class TestWorkCompleteness:
    def test_all_empty(self):
        c = work_completeness()
        assert c == {"video": False, "images": False, "description": False, "credits": False}

    def test_video_any_source(self):
        assert work_completeness(video_url="https://youtu.be/x")["video"]
        assert work_completeness(youtube_id="abc123")["video"]
        assert work_completeness(extra_videos=[{"url": "https://youtu.be/y"}])["video"]
        assert not work_completeness(video_url="  ", extra_videos=[])["video"]

    def test_images_any_source(self):
        assert work_completeness(gallery=[{"url": "a.jpg"}])["images"]
        assert work_completeness(cover_url="c.jpg")["images"]
        assert work_completeness(featured_image="f.jpg")["images"]
        assert not work_completeness(gallery=[])["images"]

    def test_description_strips_whitespace(self):
        assert work_completeness(description="有內容")["description"]
        assert not work_completeness(description="   ")["description"]

    def test_credits_blocks_or_text(self):
        assert work_completeness(credits=[{"entries": []}])["credits"]
        assert work_completeness(credits_text="導演 王小明")["credits"]
        assert not work_completeness(credits=[], credits_text=" ")["credits"]


class TestProjectWorksSummary:
    def test_empty_project_not_all_live(self):
        s = project_works_summary([])
        assert s["total"] == 0 and s["all_live"] is False

    def test_partial_progress(self):
        s = project_works_summary([
            {"stage": "已上線", "verified": True},
            {"stage": "製作中", "verified": False},
            {"stage": "待製作", "verified": False},
        ])
        assert s["live"] == 1 and s["pending"] == 2 and s["all_live"] is False
        assert s["verified"] == 1

    def test_skipped_excluded_from_denominator(self):
        s = project_works_summary([
            {"stage": "已上線", "verified": False},
            {"stage": "不上官網", "verified": False},
        ])
        assert s["skipped"] == 1 and s["pending"] == 0 and s["all_live"] is True

    def test_all_skipped_is_not_all_live(self):
        s = project_works_summary([{"stage": "不上官網"}])
        assert s["all_live"] is False

    def test_verified_only_counts_live(self):
        s = project_works_summary([{"stage": "製作中", "verified": True}])
        assert s["verified"] == 0
