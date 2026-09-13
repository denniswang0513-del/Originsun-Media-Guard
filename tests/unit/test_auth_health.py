# -*- coding: utf-8 -*-
"""core/auth.py 的密碼雜湊與兩個 Depends 工廠 —— 特徵測試（/health 2026-09-13）。

這幾支 30 天內改了 24 次、卻一支測試都沒有：hash_password／verify_password 是
登入的根、require_role／require_access_level 是每條路由的門。這裡只把**現在的
行為釘住**，不判斷對錯；行為要改就改這裡的斷言，但要知道自己在改什麼。
"""
import pytest
from fastapi import HTTPException

from core import auth


class TestPasswordHash:
    def test_format_is_salt_dollar_hash(self):
        h = auth.hash_password("pw")
        salt_hex, hash_hex = h.split("$", 1)
        assert len(bytes.fromhex(salt_hex)) == 16
        assert len(bytes.fromhex(hash_hex)) == 32      # sha256

    def test_same_salt_is_deterministic_and_different_salt_differs(self):
        salt = b"\x01" * 16
        assert auth.hash_password("pw", salt) == auth.hash_password("pw", salt)
        assert auth.hash_password("pw", salt) != auth.hash_password("pw", b"\x02" * 16)
        assert auth.hash_password("pw") != auth.hash_password("pw")   # 隨機 salt

    def test_verify_round_trip(self):
        stored = auth.hash_password("correct horse")
        assert auth.verify_password("correct horse", stored) is True
        assert auth.verify_password("wrong", stored) is False
        assert auth.verify_password("", stored) is False

    @pytest.mark.parametrize("stored", ["", "no-dollar", "zz$zz", "$", None])
    def test_verify_malformed_stored_is_false_not_exception(self, stored):
        assert auth.verify_password("pw", stored) is False


def _dep(d):
    """Depends(_check) → 拿裡面那支 async _check。"""
    return d.dependency


class TestRequireRole:
    async def test_no_token_is_401(self, monkeypatch):
        monkeypatch.setattr(auth, "_extract_token", lambda request: None)
        with pytest.raises(HTTPException) as e:
            await _dep(auth.require_role("admin"))(request=None)
        assert e.value.status_code == 401

    async def test_wrong_role_is_403(self, monkeypatch):
        monkeypatch.setattr(auth, "_extract_token", lambda request: {"role_name": "viewer"})
        with pytest.raises(HTTPException) as e:
            await _dep(auth.require_role("admin", "editor"))(request=None)
        assert e.value.status_code == 403

    async def test_role_name_wins_over_legacy_role(self, monkeypatch):
        payload = {"role_name": "admin", "role": "viewer"}
        monkeypatch.setattr(auth, "_extract_token", lambda request: payload)
        assert await _dep(auth.require_role("admin"))(request=None) is payload

    async def test_legacy_role_field_still_accepted(self, monkeypatch):
        payload = {"role": "editor"}
        monkeypatch.setattr(auth, "_extract_token", lambda request: payload)
        assert await _dep(auth.require_role("editor"))(request=None) is payload


class TestRequireAccessLevel:
    async def test_no_token_is_401(self, monkeypatch):
        monkeypatch.setattr(auth, "_extract_token", lambda request: None)
        with pytest.raises(HTTPException) as e:
            await _dep(auth.require_access_level(1))(request=None)
        assert e.value.status_code == 401

    async def test_below_min_level_is_403_and_at_least_passes(self, monkeypatch):
        monkeypatch.setattr(auth, "_extract_token", lambda request: {"access_level": 1})
        with pytest.raises(HTTPException) as e:
            await _dep(auth.require_access_level(2))(request=None)
        assert e.value.status_code == 403
        assert (await _dep(auth.require_access_level(1))(request=None))["access_level"] == 1

    async def test_legacy_role_maps_to_level_only_when_access_level_absent(self, monkeypatch):
        # 沒有 access_level 欄位 → 用 LEGACY_ROLE_LEVELS 換算（admin=3）
        monkeypatch.setattr(auth, "_extract_token", lambda request: {"role": "admin"})
        assert await _dep(auth.require_access_level(3))(request=None) == {"role": "admin"}
        # 明確給 access_level=0 就是 0，不回退看 role
        monkeypatch.setattr(auth, "_extract_token", lambda request: {"access_level": 0, "role": "admin"})
        with pytest.raises(HTTPException) as e:
            await _dep(auth.require_access_level(1))(request=None)
        assert e.value.status_code == 403
