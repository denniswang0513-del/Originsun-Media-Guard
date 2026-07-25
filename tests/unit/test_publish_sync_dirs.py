"""publish_update.remote_dirs_for — NAS 同步前要建哪些遠端目錄。

2026-07-25 加 frontend/img 時實際踩到：目錄項的 dest 本身沒建 → scp -r 失敗
（`realpath ...: No such file`）。這個純函式把兩種前置需求鎖住。
"""
import publish_update


def _dirs(tmp_path, paths, make):
    for rel, is_dir in make.items():
        p = tmp_path / rel
        (p if is_dir else p.parent).mkdir(parents=True, exist_ok=True)
        if not is_dir:
            p.write_text("x", encoding="utf-8")
    return publish_update.remote_dirs_for(str(tmp_path), paths, "/nas/code")


def test_dir_entry_creates_itself(tmp_path):
    """目錄項：scp -r `src/. → dest/` 要求 dest 本身存在。"""
    out = _dirs(tmp_path, ["routers"], {"routers": True})
    assert "/nas/code/routers" in out


def test_nested_file_entry_creates_parent_only(tmp_path):
    """帶子目錄的檔案項：只需父層。"""
    out = _dirs(tmp_path, ["frontend/media-log.html"],
                {"frontend/media-log.html": False})
    assert "/nas/code/frontend" in out
    assert "/nas/code/frontend/media-log.html" not in out


def test_nested_dir_entry_creates_full_path(tmp_path):
    out = _dirs(tmp_path, ["frontend/img"], {"frontend/img": True})
    assert "/nas/code/frontend/img" in out


def test_top_level_file_needs_only_root(tmp_path):
    out = _dirs(tmp_path, ["version.json"], {"version.json": False})
    assert out == ["/nas/code"]


def test_root_always_present_and_deduped(tmp_path):
    out = _dirs(tmp_path, ["frontend/media-log.html", "frontend/img"],
                {"frontend/media-log.html": False, "frontend/img": True})
    assert out.count("/nas/code") == 1
    assert set(out) == {"/nas/code", "/nas/code/frontend", "/nas/code/frontend/img"}


def test_real_sync_list_covers_every_entry(tmp_path):
    """實際清單：每一項都必須有對應的遠端目錄被建立（防漏建再發生）。"""
    import os
    base = os.path.dirname(os.path.abspath(publish_update.__file__))
    out = set(publish_update.remote_dirs_for(
        base, publish_update.NAS_SYNC_PATHS, "/nas/code"))
    for rel in publish_update.NAS_SYNC_PATHS:
        need = (f"/nas/code/{rel}" if os.path.isdir(os.path.join(base, rel))
                else f"/nas/code/{os.path.dirname(rel)}" if os.path.dirname(rel)
                else "/nas/code")
        assert need in out, f"{rel} 的遠端目錄沒被建立：{need}"
