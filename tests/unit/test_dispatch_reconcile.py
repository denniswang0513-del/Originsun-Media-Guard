# -*- coding: utf-8 -*-
"""後端派發的失聯改派。

前端 heartbeat（app.js）在 2026-08-13 事故後補上了這件事，後端派發沒有 ——
而後端派發（排程、備份鏈接的分散式轉檔）正好都是沒人看著的時候在跑。

對帳的依據是共享 proxy root 上的**實際產出**，不是「那台還活著嗎」：
一台活著但任務被 OTA 重啟吃掉，跟一台直接斷電，對交付結果是同一件事。
"""
import os

import pytest

from core import dispatch_reconcile as dr


@pytest.fixture(autouse=True)
def _clean():
    dr.reset()
    yield
    dr.reset()


def _touch(p, size=1024):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\0" * size)
    return p


class TestProducedStems:
    def test_counts_only_finished_output(self, tmp_path):
        d = str(tmp_path / "HostDispatch_A")
        _touch(os.path.join(d, "A001_proxy.mov"))
        _touch(os.path.join(d, "A002_proxy.part.mov"))    # 還在寫
        _touch(os.path.join(d, "A003_proxy.mov"), size=0)  # 砍斷留下的殼
        _touch(os.path.join(d, "._A004_proxy.mov"))        # macOS sidecar
        assert dr.produced_stems(d) == {"a001"}

    def test_missing_dir_is_empty(self, tmp_path):
        assert dr.produced_stems(str(tmp_path / "nope")) == set()

    def test_outstanding_is_assigned_minus_produced(self, tmp_path):
        d = str(tmp_path / "HostDispatch_A")
        _touch(os.path.join(d, "A001_proxy.mov"))
        assigned = ["//nas/src/A001.MP4", "//nas/src/A002.MP4"]
        assert dr.outstanding(assigned, [d]) == ["//nas/src/A002.MP4"]


class _Fleet:
    """假機隊：控制誰答得出狀態、誰在忙，並記下派了什麼給誰。"""

    def __init__(self, monkeypatch, status_map):
        self.status_map = status_map          # ip -> dict | None
        self.sent = []                        # (ip, sources, dest)
        monkeypatch.setattr(dr, "_host_status", lambda ip: self.status_map.get(ip))
        monkeypatch.setattr(dr, "post_transcode", self._post)
        self.alerts = []
        monkeypatch.setattr(dr, "_notify_lost",
                            lambda rec, lost, pending, recovered:
                            self.alerts.append((len(pending), recovered)))

    def _post(self, ip, sources, dest_dir, project_name):
        self.sent.append((ip, list(sources), dest_dir))
        return True


IDLE = {"busy": False, "queue_length": 0, "active_jobs": {}}
BUSY = {"busy": True, "queue_length": 0, "active_jobs": {}}


def _register_two_hosts(tmp_path, produced_by_a=("A001",)):
    """A 分到 A001+A002、B 分到 B001（已完成）。A 的產出由參數決定。"""
    root = str(tmp_path / "PROJ")
    dest_a = os.path.join(root, "HostDispatch_A")
    dest_b = os.path.join(root, "HostDispatch_B")
    for stem in produced_by_a:
        _touch(os.path.join(dest_a, f"{stem}_proxy.mov"))
    _touch(os.path.join(dest_b, "B001_proxy.mov"))

    dr.register(root, "PROJ", [
        {"name": "A", "ip": "10.0.0.1:8000", "dest_dir": dest_a,
         "sources": ["//src/A001.MP4", "//src/A002.MP4"]},
        {"name": "B", "ip": "10.0.0.2:8000", "dest_dir": dest_b,
         "sources": ["//src/B001.MP4"]},
    ])
    return root


def test_unreachable_host_work_goes_to_the_live_one(tmp_path, monkeypatch):
    """🔴 A 斷線 → 它沒轉完的 A002 要改派給 B，而且只有 A002。"""
    _register_two_hosts(tmp_path)
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": None, "10.0.0.2:8000": IDLE})

    for _ in range(dr.DEAD_STRIKES):
        dr.reconcile_tick()

    assert len(fleet.sent) == 1, f"應該只派一批，實際 {fleet.sent}"
    ip, sources, dest = fleet.sent[0]
    assert ip == "10.0.0.2:8000"
    assert sources == ["//src/A002.MP4"], "已經產出的 A001 不該重轉"
    assert dest.endswith("HostDispatch_Takeover_B")
    assert fleet.alerts == [(1, True)], "失聯要出聲（排程沒人在看畫面）"


def test_one_missed_poll_is_not_a_failure(tmp_path, monkeypatch):
    """網路抖一下不算掛掉 —— 沒到 DEAD_STRIKES 不准改派。"""
    _register_two_hosts(tmp_path)
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": None, "10.0.0.2:8000": IDLE})

    for _ in range(dr.DEAD_STRIKES - 1):
        dr.reconcile_tick()

    assert fleet.sent == []


def test_alive_but_idle_with_unfinished_share_is_reassigned(tmp_path, monkeypatch):
    """機器活著、任務卻不見了（OTA／重啟吃掉）—— 對交付結果跟斷電一樣。"""
    monkeypatch.setattr(dr, "START_GRACE_SEC", 0)
    _register_two_hosts(tmp_path)
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": IDLE, "10.0.0.2:8000": IDLE})

    for _ in range(dr.IDLE_STRIKES):
        dr.reconcile_tick()

    assert [s for _, s, _ in fleet.sent] == [["//src/A002.MP4"]]


def test_busy_host_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "START_GRACE_SEC", 0)
    _register_two_hosts(tmp_path)
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": BUSY, "10.0.0.2:8000": IDLE})

    for _ in range(5):
        dr.reconcile_tick()

    assert fleet.sent == []
    assert dr.active_records(), "還在跑的紀錄不能被結案"


def test_record_does_not_linger_forever(tmp_path, monkeypatch):
    """一台卡在「忙碌」永遠不結束（ffmpeg 掛死）不能被盯到天荒地老。"""
    monkeypatch.setattr(dr, "MAX_RECORD_AGE_SEC", 0)
    _register_two_hosts(tmp_path)
    _Fleet(monkeypatch, {"10.0.0.1:8000": BUSY, "10.0.0.2:8000": BUSY})

    dr.reconcile_tick()

    assert dr.active_records() == []


def test_grace_period_covers_slow_start(tmp_path, monkeypatch):
    """POST 回 200 到 worker 真的開跑之間有落差，不能當成掉了。"""
    _register_two_hosts(tmp_path)   # START_GRACE_SEC 維持預設
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": IDLE, "10.0.0.2:8000": IDLE})

    for _ in range(5):
        dr.reconcile_tick()

    assert fleet.sent == []


def test_no_takeover_available_alerts_instead_of_silence(tmp_path, monkeypatch):
    """兩台都掉了 → 沒人接手。這種時候更要出聲，不能默默結束。"""
    _register_two_hosts(tmp_path)
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": None, "10.0.0.2:8000": None})

    for _ in range(dr.DEAD_STRIKES):
        dr.reconcile_tick()

    assert fleet.sent == []
    assert fleet.alerts and fleet.alerts[-1][1] is False
    assert dr.active_records() == [], "全員失聯後這筆該結案"


def test_failed_dispatch_is_reassigned_on_first_tick(tmp_path, monkeypatch):
    """派發當場就失敗的份額，原本只留一行 warning 就沒了。"""
    root = str(tmp_path / "PROJ")
    dr.register(root, "PROJ", [
        {"name": "A", "ip": "10.0.0.1:8000", "failed": True,
         "dest_dir": os.path.join(root, "HostDispatch_A"),
         "sources": ["//src/A001.MP4"]},
        {"name": "B", "ip": "10.0.0.2:8000",
         "dest_dir": os.path.join(root, "HostDispatch_B"),
         "sources": []},
    ])
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": IDLE, "10.0.0.2:8000": IDLE})

    dr.reconcile_tick()

    assert [s for _, s, _ in fleet.sent] == [["//src/A001.MP4"]]


def test_takeover_host_is_watched_too(tmp_path, monkeypatch):
    """接手的那台也可能掛 —— 改派出去的份額同樣要被盯著。"""
    _register_two_hosts(tmp_path)
    fleet = _Fleet(monkeypatch, {"10.0.0.1:8000": None, "10.0.0.2:8000": IDLE})
    for _ in range(dr.DEAD_STRIKES):
        dr.reconcile_tick()
    assert len(fleet.sent) == 1

    # 換 B 斷線，它接手的 A002 要再被算成未完成
    fleet.status_map["10.0.0.2:8000"] = None
    for _ in range(dr.DEAD_STRIKES):
        dr.reconcile_tick()

    assert dr.active_records() == [], "兩台都掉了就沒得改派，該結案"
    assert fleet.alerts[-1][1] is False


def test_poison_file_stops_after_max_attempts(tmp_path, monkeypatch):
    """每台接手都產不出來的檔（來源本身壞了）不准把整個機隊輪過一遍。

    H1 拿到一支怎麼轉都不會有產出的檔，另外四台閒著隨時可以接手 ——
    沒有次數上限的話，它會被 H2→H3→H4→H5 一路傳下去，每台各浪費一輪。
    """
    monkeypatch.setattr(dr, "START_GRACE_SEC", 0)
    root = str(tmp_path / "PROJ")
    dr.register(root, "PROJ", [
        {"name": "H1", "ip": "10.0.0.1:8000",
         "dest_dir": os.path.join(root, "HostDispatch_H1"),
         "sources": ["//nas/src/card1/BAD.MP4"]},
    ] + [
        # 閒著沒分到檔的主機 —— 隨時可以接手
        {"name": f"H{i}", "ip": f"10.0.0.{i}:8000",
         "dest_dir": os.path.join(root, f"HostDispatch_H{i}"), "sources": []}
        for i in (2, 3, 4, 5)
    ])
    fleet = _Fleet(monkeypatch, {f"10.0.0.{i}:8000": IDLE for i in range(1, 6)})

    for _ in range(30):
        dr.reconcile_tick()

    bad_sends = [s for _, srcs, _ in fleet.sent for s in srcs if s.endswith("BAD.MP4")]
    assert len(bad_sends) == dr.MAX_ATTEMPTS, f"壞檔被改派 {len(bad_sends)} 次"
    assert dr.active_records() == [], "對帳不能永遠不結案"
    assert fleet.alerts[-1][1] is False, "放棄的檔案要出聲，不能默默丟掉"
