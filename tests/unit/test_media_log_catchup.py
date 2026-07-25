"""影像紀錄補算的純函式（services/media_log_catchup.py）。

不碰 DB/磁碟/時鐘 —— 用簡單物件模擬 ProjectMediaFile。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.media_log_catchup import _needs_catchup, _pending_by_project

ASSETS = "https://assets.originsun-studio.com/medialog"


def _f(**kw):
    kw.setdefault("thumb_url", "")
    kw.setdefault("media_type", "image")
    kw.setdefault("duration_sec", None)
    return SimpleNamespace(**kw)


# ── _needs_catchup ──────────────────────────────────────────

def test_no_thumb_needs_catchup():
    assert _needs_catchup(_f(thumb_url=""), ASSETS) is True


def test_thumb_on_assets_host_is_done():
    assert _needs_catchup(_f(thumb_url=f"{ASSETS}/p_a.webp"), ASSETS) is False


def test_legacy_thumb_needs_migration():
    """舊落點的縮圖（uploads/ 相對路徑）要遷到圖床。"""
    assert _needs_catchup(
        _f(thumb_url="/uploads/projects/p/media_log/a.webp"), ASSETS) is True


def test_no_assets_base_only_fills_missing():
    """圖床未設定時，有縮圖的就別動（不知道要遷去哪）。"""
    assert _needs_catchup(_f(thumb_url="/uploads/x.webp"), "") is False
    assert _needs_catchup(_f(thumb_url=""), "") is True


def test_sql_clause_matches_python_predicate():
    """SQL 下推版必須與純函式同義（下推是效能優化，不能改語意）——
    對每種 thumb_url 值，用真 SQLite 比對 WHERE 與 _needs_catchup 的判定
    （避免自己詮釋 SQLAlchemy 表達式）。SQL 由 _needs_catchup_clause 的語意手寫，
    兩處若漂移這裡會紅。"""
    import sqlite3

    samples = [None, "", f"{ASSETS}/p_a.webp",
               "/uploads/projects/p/media_log/a.webp"]
    for base in ("", ASSETS):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (thumb_url TEXT)")
        con.executemany("INSERT INTO t VALUES (?)", [(s,) for s in samples])
        if base:
            rows = con.execute(
                "SELECT thumb_url FROM t WHERE "
                "thumb_url IS NULL OR thumb_url = '' OR thumb_url NOT LIKE ?",
                (f"{base}%",)).fetchall()
        else:
            rows = con.execute(
                "SELECT thumb_url FROM t WHERE thumb_url IS NULL OR thumb_url = ''"
            ).fetchall()
        sql_hits = {r[0] for r in rows}
        py_hits = {s for s in samples if _needs_catchup(_f(thumb_url=s), base)}
        assert sql_hits == py_hits, f"base={base!r}: SQL {sql_hits} != PY {py_hits}"
        con.close()


# ── _pending_by_project ─────────────────────────────────────

def _now():
    return datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def test_quiet_project_is_pending():
    cutoff = _now()
    old = cutoff - timedelta(minutes=5)      # 已安靜
    rows = [_f(project_id="A", id="1", created_at=old),
            _f(project_id="A", id="2", created_at=old)]
    out = _pending_by_project(rows, {}, cutoff)
    assert set(out) == {"A"} and len(out["A"]) == 2


def test_still_uploading_project_skipped():
    """最後一筆還沒安靜夠久 → 本輪跳過（不轟炸群組）。"""
    cutoff = _now()
    rows = [_f(project_id="A", id="1", created_at=cutoff - timedelta(minutes=5)),
            _f(project_id="A", id="2", created_at=cutoff + timedelta(minutes=1))]
    assert _pending_by_project(rows, {}, cutoff) == {}


def test_already_notified_excluded():
    cutoff = _now()
    t = cutoff - timedelta(minutes=5)
    rows = [_f(project_id="A", id="1", created_at=t)]
    # 水位已蓋過這筆 → 不再通知
    assert _pending_by_project(rows, {"A": t}, cutoff) == {}


def test_new_file_after_mark_included():
    cutoff = _now()
    mark = cutoff - timedelta(hours=1)
    newer = cutoff - timedelta(minutes=5)
    rows = [_f(project_id="A", id="1", created_at=mark),          # 已通知
            _f(project_id="A", id="2", created_at=newer)]         # 新的
    out = _pending_by_project(rows, {"A": mark}, cutoff)
    assert set(out) == {"A"} and [f.id for f in out["A"]] == ["2"]
