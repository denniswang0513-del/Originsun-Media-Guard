# -*- coding: utf-8 -*-
"""core/nas_auth — SMB 認證韌性層（2026-09-10 循環杯備份 2 連敗的治本）。

`drive_map` 讓路徑跨機可攜（T:\\ → UNC），這支讓「翻好的 UNC 真的開得起來」：
session 瞬斷時重連一次再試，再不行給一句看得懂的話。
"""
import os

import pytest

from core import nas_auth
from core.nas_auth import (AUTH_ERRNOS, ensure_ready, friendly, guard,
                           is_auth_error, is_unc, share_root, share_roots)

NAS = r"\\192.168.1.132\Project_Longterm"


def _oserr(winerror: int, filename: str = ""):
    """做一顆帶 winerror 的 OSError（非 Windows 上 OSError 不會自己長 winerror）。"""
    e = OSError(winerror, "boom", filename)
    if getattr(e, "winerror", None) != winerror:
        e.winerror = winerror   # type: ignore[attr-defined]
    return e


class TestPathParsing:
    def test_is_unc(self):
        assert is_unc(NAS)
        assert is_unc("//192.168.1.132/Project_Longterm")
        assert not is_unc(r"T:\專案")
        assert not is_unc("")

    def test_share_root_strips_subpath(self):
        assert share_root(NAS + r"\20260910_循環杯\A001") == NAS

    def test_share_root_of_root_itself(self):
        assert share_root(NAS) == NAS
        # 錯誤訊息裡那個帶尾斜線的形式（ntpath.dirname 的產物）也要認得
        assert share_root(NAS + "\\") == NAS

    def test_share_root_needs_both_host_and_share(self):
        assert share_root(r"\\192.168.1.132") == ""
        assert share_root(r"T:\x") == ""
        assert share_root("") == ""

    def test_share_root_works_without_ntpath(self):
        """Linux 容器也會呼叫到（os.path 在那邊是 posixpath，切不出 UNC）。"""
        assert share_root("//192.168.1.130/storage 01/x") == r"\\192.168.1.130\storage 01"

    def test_share_roots_dedupes_case_insensitively(self):
        got = share_roots([NAS + r"\a", NAS.upper() + r"\b", r"E:\卡匣", "",
                           r"\\192.168.1.130\storage 01\c"])
        assert got == [NAS, r"\\192.168.1.130\storage 01"]


class TestErrorClassification:
    @pytest.mark.parametrize("code", sorted(AUTH_ERRNOS))
    def test_session_errors_are_retryable(self, code):
        assert is_auth_error(_oserr(code))

    def test_access_denied_is_not_an_auth_error(self):
        """5 = 這個帳號真的沒權限，重連幾次都一樣 —— 蓋成認證問題會誤導人。"""
        assert not is_auth_error(_oserr(5))

    def test_disk_full_is_not_an_auth_error(self):
        assert not is_auth_error(_oserr(112))

    def test_plain_exception_is_not_an_auth_error(self):
        assert not is_auth_error(ValueError("nope"))


class TestFriendly:
    def test_1326_names_the_share_and_says_what_to_check(self):
        msg = friendly(_oserr(1326, NAS + "\\"), NAS)
        assert "1326" in msg
        assert NAS in msg
        assert "192.168.1.132" in msg
        assert "認證管理員" in msg

    def test_non_credential_codes_dont_say_auth_failed(self):
        """67 是分享區名字不對，不是帳密問題 —— 別把人指去翻認證管理員。"""
        msg = friendly(_oserr(67, r"\\192.168.1.132\NoSuchShare"))
        assert "連線失敗" in msg and "認證失敗" not in msg
        assert "認證管理員" not in msg

    def test_falls_back_to_the_filename_on_the_exception(self):
        """實案的錯誤字串就長這樣：路徑只在 exception 的 filename 上。"""
        assert NAS in friendly(_oserr(1326, NAS + "\\"))

    def test_non_auth_error_gets_no_message(self):
        assert friendly(_oserr(112)) == ""
        assert friendly(ValueError("x")) == ""


class TestGuard:
    def test_passes_through_the_return_value(self):
        assert guard(NAS, lambda a, b=0: a + b, 1, b=2) == 3

    def test_non_auth_error_is_raised_immediately_without_reconnect(self, monkeypatch):
        calls = []
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff",
                            lambda r, **kw: calls.append(r) or True)

        def boom():
            raise _oserr(112)

        with pytest.raises(OSError):
            guard(NAS, boom)
        assert calls == []

    @pytest.mark.skipif(os.name != "nt", reason="重連是 Windows 專屬，其他平台整支 no-op")
    def test_auth_error_reconnects_then_retries_once(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff",
                            lambda r, **kw: True)
        seen = {"n": 0}

        def flaky():
            seen["n"] += 1
            if seen["n"] == 1:
                raise _oserr(1326, NAS + "\\")
            return "ok"

        assert guard(NAS, flaky) == "ok"
        assert seen["n"] == 2

    @pytest.mark.skipif(os.name != "nt", reason="重連是 Windows 專屬")
    def test_gives_up_after_one_retry(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff",
                            lambda r, **kw: True)
        seen = {"n": 0}

        def always_bad():
            seen["n"] += 1
            raise _oserr(1326, NAS + "\\")

        with pytest.raises(OSError):
            guard(NAS, always_bad)
        assert seen["n"] == 2, "只重試一次，不准變成重試迴圈"

    @pytest.mark.skipif(os.name != "nt", reason="重連是 Windows 專屬")
    def test_no_retry_when_reconnect_fails(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff", lambda r, **kw: False)
        seen = {"n": 0}

        def always_bad():
            seen["n"] += 1
            raise _oserr(1326, NAS + "\\")

        with pytest.raises(OSError):
            guard(NAS, always_bad)
        assert seen["n"] == 1


@pytest.mark.skipif(os.name != "nt", reason="重連是 Windows 專屬，其他平台整支 no-op")
class TestReconnectWithBackoff:
    """15 秒 ×5（owner 2026-09-10 拍板）。等待用 monkeypatch 掉，測試不真的睡。"""

    @pytest.fixture(autouse=True)
    def _fast_and_clean(self, monkeypatch):
        self.slept = []
        monkeypatch.setattr(nas_auth, "_wait",
                            lambda s, stop: self.slept.append(s) or True)
        nas_auth._down_until.clear()      # 冷卻閘是模組層狀態，測試之間要清乾淨
        yield
        nas_auth._down_until.clear()

    def test_the_owner_settings_are_what_ships(self):
        assert (nas_auth.RECONNECT_ATTEMPTS, nas_auth.RECONNECT_DELAY_SEC) == (5, 15)

    def test_success_on_first_try_never_waits(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe", lambda r: None)
        assert nas_auth.reconnect_with_backoff(NAS) is True
        assert self.slept == []

    def test_recovers_on_a_later_attempt(self, monkeypatch):
        tries = {"n": 0}

        def probe(_r):
            tries["n"] += 1
            return None if tries["n"] >= 3 else _oserr(1326, _r)

        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe", probe)
        assert nas_auth.reconnect_with_backoff(NAS) is True
        assert self.slept == [15, 15], "第 3 次才成功 → 中間等了兩次"

    def test_gives_up_after_five_attempts_with_four_waits(self, monkeypatch):
        tries = {"n": 0}
        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe",
                            lambda r: tries.__setitem__("n", tries["n"] + 1)
                            or _oserr(1326, r))
        assert nas_auth.reconnect_with_backoff(NAS) is False
        assert tries["n"] == 5, "試 5 次"
        assert self.slept == [15, 15, 15, 15], "最後一次失敗後不再空等"

    def test_stop_request_aborts_the_wait(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "_wait", lambda s, stop: False)
        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe", lambda r: _oserr(1326, r))
        assert nas_auth.reconnect_with_backoff(NAS, should_stop=lambda: True) is False
        # 被中斷不算「這個 share 判死」——使用者只是按了停止
        assert nas_auth._cooldown_left(NAS) == 0

    def test_a_failed_cycle_puts_the_share_in_cooldown(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe", lambda r: _oserr(1326, r))
        assert nas_auth.reconnect_with_backoff(NAS) is False
        assert nas_auth._cooldown_left(NAS) > 0

    def test_cooldown_stops_every_file_running_its_own_cycle(self, monkeypatch):
        """沒有這道閘，NAS 掛掉時 5000 個檔案各燒 60 秒＝83 小時殭屍任務。"""
        calls = {"n": 0}
        monkeypatch.setattr(nas_auth, "reconnect",
                            lambda r: calls.__setitem__("n", calls["n"] + 1) or True)
        monkeypatch.setattr(nas_auth, "_probe", lambda r: _oserr(1326, r))

        nas_auth.reconnect_with_backoff(NAS)
        assert calls["n"] == 5
        for _ in range(20):                       # 後續 20 個檔案
            assert nas_auth.reconnect_with_backoff(NAS) is False
        assert calls["n"] == 5, "冷卻期內一次都不該再打 WNetAddConnection2"

    def test_success_clears_the_cooldown(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe", lambda r: _oserr(1326, r))
        nas_auth.reconnect_with_backoff(NAS)
        assert nas_auth._cooldown_left(NAS) > 0

        nas_auth._down_until.clear()               # 冷卻期滿
        monkeypatch.setattr(nas_auth, "_probe", lambda r: None)
        assert nas_auth.reconnect_with_backoff(NAS) is True
        assert nas_auth._cooldown_left(NAS) == 0

    def test_cooldown_is_per_share(self, monkeypatch):
        other = r"\\192.168.1.130\storage 01"
        monkeypatch.setattr(nas_auth, "reconnect", lambda r: True)
        monkeypatch.setattr(nas_auth, "_probe",
                            lambda r: _oserr(1326, r) if r == NAS else None)
        assert nas_auth.reconnect_with_backoff(NAS) is False
        assert nas_auth.reconnect_with_backoff(other) is True, "別台不該被連坐"


class TestWait:
    def test_returns_true_after_waiting_out_the_delay(self):
        assert nas_auth._wait(0.01, None) is True

    def test_returns_false_as_soon_as_stop_is_requested(self):
        import time as _t
        t0 = _t.monotonic()
        assert nas_auth._wait(30, lambda: True) is False
        assert _t.monotonic() - t0 < 1, "按停止不該還要等完 30 秒"


class TestEnsureReady:
    def test_no_unc_paths_is_a_no_op(self, monkeypatch):
        probed = []
        monkeypatch.setattr(nas_auth, "_probe", lambda r: probed.append(r))
        ok, why = ensure_ready([r"E:\卡匣", r"D:\本機備份", ""])
        assert (ok, why) == (True, "")
        assert probed == []

    @pytest.mark.skipif(os.name != "nt", reason="前置檢查在非 Windows 直接放行")
    def test_reachable_share_passes_without_reconnecting(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "_probe", lambda r: None)
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff",
                            lambda r, **kw: pytest.fail("通的時候不該去重連"))
        assert ensure_ready([NAS + r"\專案"]) == (True, "")

    @pytest.mark.skipif(os.name != "nt", reason="前置檢查在非 Windows 直接放行")
    def test_reconnect_rescues_a_dead_session(self, monkeypatch):
        state = {"up": False}
        monkeypatch.setattr(nas_auth, "_probe",
                            lambda r: None if state["up"] else _oserr(1326, r))
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff",
                            lambda r, **kw: state.__setitem__("up", True) or True)
        assert ensure_ready([NAS + r"\專案"]) == (True, "")

    @pytest.mark.skipif(os.name != "nt", reason="前置檢查在非 Windows 直接放行")
    def test_still_dead_fails_fast_with_a_readable_reason(self, monkeypatch):
        monkeypatch.setattr(nas_auth, "_probe", lambda r: _oserr(1326, r))
        monkeypatch.setattr(nas_auth, "reconnect_with_backoff", lambda r, **kw: False)
        ok, why = ensure_ready([NAS + r"\專案"])
        assert not ok
        assert "1326" in why and NAS in why

    @pytest.mark.skipif(os.name != "nt", reason="前置檢查在非 Windows 直接放行")
    def test_each_share_is_probed_once(self, monkeypatch):
        probed = []
        monkeypatch.setattr(nas_auth, "_probe", lambda r: probed.append(r))
        ensure_ready([NAS + r"\a", NAS + r"\b", r"\\192.168.1.130\storage 01\c"])
        assert probed == [NAS, r"\\192.168.1.130\storage 01"]
