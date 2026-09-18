"""/health 2026-09-19 特徵測試：services/website/project_service.py 的 old_slugs 追加與主作品鏡射。

只釘住現在的行為（模組 30 天改 5 次、覆蓋率 <40%；這幾支沒有直接測試——
09-18 那份 health 只釘了 normalize_old_slug_input）。不判對錯。
"""
from types import SimpleNamespace

from core.crm_logic import WORK_WIRE_FIELD_MAP
from services.website.project_service import (
    append_old_slug_if_changed,
    append_old_slug_if_changed_work,
    mirror_main_work_to_project,
    work_completeness_dict,
)


def test_old_slug_is_appended_once_and_only_when_it_really_changes():
    p = SimpleNamespace(public_slug="old-a", public_old_slugs=None)
    assert append_old_slug_if_changed(p, "old-a") is False          # 沒變
    assert p.public_old_slugs is None
    assert append_old_slug_if_changed(p, "new-b") is True
    assert p.public_old_slugs == ["old-a"]
    # 同一個舊 slug 已在清單裡 → 不重複
    p.public_slug = "old-a"
    assert append_old_slug_if_changed(p, "new-c") is False
    assert p.public_old_slugs == ["old-a"]


def test_old_slug_skips_empty_and_numeric_slugs():
    p = SimpleNamespace(public_slug="", public_old_slugs=[])
    assert append_old_slug_if_changed(p, "x") is False
    p = SimpleNamespace(public_slug="1234", public_old_slugs=[])   # 純數字＝number 路由，不進 redirect map
    assert append_old_slug_if_changed(p, "x") is False
    assert p.public_old_slugs == []


def test_switching_back_to_a_former_slug_removes_it_from_old_slugs():
    # a → b（old=[a]），再 b → a：a 從舊清單移除、b 進來，避免 redirect 自循環
    sc = SimpleNamespace(slug="b", old_slugs=["a"])
    assert append_old_slug_if_changed_work(sc, "a") is True
    assert sc.old_slugs == ["b"]


def test_mirror_main_work_copies_every_wire_field_and_backfills_number_only_when_missing():
    sc = SimpleNamespace(number=7, **{f: f"v_{f}" for f in WORK_WIRE_FIELD_MAP.values()})
    project = SimpleNamespace(public_number=None, **{k: None for k in WORK_WIRE_FIELD_MAP})
    mirror_main_work_to_project(sc, project)
    for wire_key, field in WORK_WIRE_FIELD_MAP.items():
        assert getattr(project, wire_key) == f"v_{field}"
    assert project.public_number == 7
    project.public_number = 3
    sc.number = 9
    mirror_main_work_to_project(sc, project)
    assert project.public_number == 3                                # 已有編號不覆寫


def test_work_completeness_dict_reads_the_showcase_shape():
    sc = SimpleNamespace(video_url="", youtube_id="abc123", extra_videos=None, gallery=None,
                         cover_url="", featured_image="f.jpg", description="", credits=None,
                         credits_text="")
    c = work_completeness_dict(sc)
    assert c["video"] is True and c["images"] is True
    assert c["description"] is False
