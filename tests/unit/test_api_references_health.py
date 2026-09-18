"""/health 2026-09-19 特徵測試：routers/api_references.py 的三支純序列化／寫入函式。

只釘住現在的行為（改動 30 天 5 次、覆蓋率 <40%、這三支沒有任何直接測試）。
不判對錯；行為要改就連這檔一起改。
"""
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from core.schemas import ReferencePatch
from routers.api_references import apply_ref_patch, ref_dict, shot_dict


def _ref(**kw):
    base = dict(id="r1", url="", title=None, note=None, description=None, thumb_url=None,
                curated=None, provider=None, video_id=None, facets=None, research=None,
                created_at=None, updated_at=None, archive_status=None, archive_tries=None,
                archived_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_ref_dict_derives_video_meta_from_url_only_when_columns_are_empty():
    d = ref_dict(_ref(url="https://www.youtube.com/watch?v=abcdefghijk"))
    assert (d["provider"], d["video_id"]) == ("youtube", "abcdefghijk")
    assert d["embed_url"] and "abcdefghijk" in d["thumb_url"]
    # 存了 provider 就以存的為準；embed_url 永遠即時推導（欄位沒存它）
    d = ref_dict(_ref(url="https://www.youtube.com/watch?v=abcdefghijk",
                      provider="vimeo", video_id="999", thumb_url="/t.jpg"))
    assert (d["provider"], d["video_id"], d["thumb_url"]) == ("vimeo", "999", "/t.jpg")
    assert "abcdefghijk" in d["embed_url"]


def test_ref_dict_research_flag_and_null_defaults():
    ts = datetime(2026, 9, 19, tzinfo=timezone.utc)
    full = ref_dict(_ref(created_at=ts))
    assert "research" in full and full["research"]["rows"]
    assert full["created_at"] == ts.isoformat() and full["updated_at"] is None
    assert (full["title"], full["note"], full["curated"], full["archive_tries"]) == ("", "", False, 0)
    assert "research" not in ref_dict(_ref(), research=False)


def test_apply_ref_patch_public_link_allow_list_and_facet_shapes():
    ref = _ref()
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="facet", key="brand", value=[]), "guest", public=True)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="field", key="url", value="https://x"), "guest", public=True)
    assert e.value.status_code == 403
    out = apply_ref_patch(ref, ReferencePatch(kind="field", key="title", value="  Hi  "), "guest", public=True)
    assert ref.title == "Hi" and out["updated_at"]
    # 登入路徑：單選族收字串、多選族只收 list（去空白＋去重）
    apply_ref_patch(ref, ReferencePatch(kind="facet", key="studio", value="  Acme "), "u")
    assert ref.facets["studio"] == "Acme"
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="facet", key="brand", value="not-a-list"), "u")
    assert e.value.status_code == 422
    apply_ref_patch(ref, ReferencePatch(kind="facet", key="brand", value=[" a ", "a", "b"]), "u")
    assert ref.facets["brand"] == ["a", "b"]
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="nope"), "u")
    assert e.value.status_code == 422


def test_apply_ref_patch_url_sync_and_research_cell_optimistic_lock():
    ref = _ref()
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="field", key="url", value="ftp://x"), "u")
    assert e.value.status_code == 422
    apply_ref_patch(ref, ReferencePatch(kind="field", key="url", value=" https://youtu.be/abcdefghijk "), "u")
    assert ref.url == "https://youtu.be/abcdefghijk"
    assert (ref.provider, ref.video_id) == ("youtube", "abcdefghijk")
    assert ref.thumb_url and "abcdefghijk" in ref.thumb_url
    assert isinstance(ref.updated_at, datetime)

    ref = _ref(research={"rows": [{"id": "row1", "cells": {
        "idea": {"answer": "old", "updated_at": "2026-09-19T10:00:00+00:00", "updated_by": "a"}}}]})
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="research", row_id="nope", col="idea", value="x"), "b")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        apply_ref_patch(ref, ReferencePatch(kind="research", row_id="row1", col="idea", value="x",
                                            base_updated_at="2026-09-19T09:00:00+00:00"), "b")
    assert e.value.status_code == 409 and e.value.detail["server_answer"] == "old"
    out = apply_ref_patch(ref, ReferencePatch(kind="research", row_id="row1", col="idea", value="new",
                                              base_updated_at="2026-09-19T11:00:00+00:00"), "b")
    assert ref.research["rows"][0]["cells"]["idea"] == {
        "answer": "new", "updated_at": out["updated_at"], "updated_by": "b"}


def test_shot_dict_fills_defaults_and_normalizes_annotations():
    s = SimpleNamespace(id="s1", image_url=None, timecode=None, caption=None, annotations=None,
                        sort_order=None, created_by=None, created_at=None)
    d = shot_dict(s)
    assert d["id"] == "s1" and d["image_url"] == "" and d["sort_order"] == 0
    assert d["created_at"] is None and d["annotations"]["shapes"] == []
