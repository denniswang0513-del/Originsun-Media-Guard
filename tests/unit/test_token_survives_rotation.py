# -*- coding: utf-8 -*-
"""jwt_secret 輪替時，**已經發出去的分享連結不可以跟著死**。

背景：外流的 jwt_secret 真正危及的是**登入 token**（純 JWT、沒有 DB 當後盾，
有 secret 就能自簽一個管理員）。分享連結不同 —— 它們同時存在 DB，驗證是逐字
比對，而且 payload 帶亂數（`new_share_token` 的 `n`），所以就算 secret 外流也
產不出對得上的字串。兩者若共用同一道簽章檢查，輪替就會連坐殺掉印出來的 QR
與寄給客戶的網址。

這份測試把那條分界釘住：
  - 分享 token：換 secret 後**仍然有效**（不驗簽章，比對 DB）
  - 登入 token：換 secret 後**立刻失效**（這正是輪替的目的）
  - 分享 token 一定帶亂數，否則逐字比對擋不住「有 secret 的人重現同一串」
  - 沒有亂數的**舊** token 仍然有效（不然輪替還是把既有連結全殺了）
"""
import time

import pytest

from core import auth


@pytest.fixture
def rotate(monkeypatch):
    """把 jwt_secret 換掉（模擬輪替）。"""
    def _set(secret):
        monkeypatch.setattr(auth, "_get_secret", lambda: secret)
    _set("secret-A")
    return _set


def _share_token(pid="prop-1", scope="plan_share"):
    return auth.create_token({"sub": pid, "scope": scope}, expires_days=3650)


# ── 輪替後：分享連結活著，登入 token 死掉 ────────────────────

def test_share_token_survives_rotation(rotate):
    """🔴 這條紅了就代表輪替會殺掉客戶手上的網址與現場的 QR。"""
    token = _share_token()
    assert auth.decode_unverified(token)["sub"] == "prop-1"
    rotate("secret-B")                      # ← 輪替
    payload = auth.decode_unverified(token)
    assert payload and payload["sub"] == "prop-1" and payload["scope"] == "plan_share"


def test_login_token_dies_on_rotation(rotate):
    """🔴 這條紅了就代表輪替沒有達到目的 —— 偷走 secret 的人還是管理員。"""
    login = auth.create_token({"sub": "admin", "username": "admin", "access_level": 3})
    assert auth.verify_token(login)["access_level"] == 3
    rotate("secret-B")
    assert auth.verify_token(login) is None


def test_forged_login_token_with_leaked_secret_is_admin(rotate):
    """說明**為什麼**要輪替：拿到舊 secret 的人可以自簽一個管理員。
    輪替之後同一把 secret 簽出來的東西就不再被接受（上一條測的就是這個）。"""
    forged = auth.create_token({"sub": "x", "username": "x", "access_level": 3})
    assert (auth.verify_token(forged) or {}).get("access_level") == 3


# ── 逐字比對才是分享連結真正的鎖 ──────────────────────────

def test_stored_token_match_is_exact():
    real = _share_token()
    assert auth.stored_token_matches(real, real)
    assert not auth.stored_token_matches(real, real + "x")
    assert not auth.stored_token_matches(real, real[:-1])
    assert not auth.stored_token_matches("", real)
    assert not auth.stored_token_matches(real, "")
    assert not auth.stored_token_matches(None, None)


def test_share_token_has_entropy_so_it_cannot_be_reproduced(rotate):
    """🔴 分享 token **一定要帶亂數**。

    payload 的 sub/scope/exp/iat 完全由時間決定 —— 沒有亂數的話，任何拿到
    jwt_secret 的人只要知道 row id 又猜中發放的那一秒，就能重現出一模一樣的
    字串，逐字比對就形同虛設。這條測試釘住那個亂數。
    （這個缺口是寫這份測試時才發現的：原本用 create_token 鑄，同一秒鑄兩次
    會產生完全相同的字串。）
    """
    a = auth.new_share_token("prop-1", "plan_share", 3650)
    b = auth.new_share_token("prop-1", "plan_share", 3650)     # 同一秒、同輸入
    assert a != b, "同輸入鑄兩次得到相同字串 → 有 secret 就重現得出來"
    assert not auth.stored_token_matches(a, b)
    assert auth.decode_unverified(a)["sub"] == "prop-1"        # 仍解得開


def test_old_tokens_without_nonce_still_work(rotate):
    """既有的舊 token 沒有 `n` 欄位，不可以因此被判失效（不然還是全斷）。"""
    legacy = auth.create_token({"sub": "prop-1", "scope": "plan_share"},
                               expires_days=3650)
    rotate("secret-B")
    p = auth.decode_unverified(legacy)
    assert p and p["sub"] == "prop-1" and "n" not in p
    assert auth.stored_token_matches(legacy, legacy)


# ── decode_unverified 的邊界 ────────────────────────────────

def test_decode_unverified_still_rejects_expired():
    """不驗簽章不代表不看過期。"""
    expired = auth.create_token({"sub": "a", "scope": "s"}, expires_days=-1)
    assert auth.decode_unverified(expired) is None


@pytest.mark.parametrize("junk", ["", None, "abc", "a.b", "a.b.c.d", "....", "not.a.jwt"])
def test_decode_unverified_rejects_junk(junk):
    assert auth.decode_unverified(junk) is None


def test_decode_unverified_never_used_as_authentication():
    """守住語意：它解得開**任何人**造的 payload，所以呼叫端一定要再比對 DB。
    這條存在的目的是讓「把它當認證用」的改動在 review 時看起來很刺眼。"""
    import base64
    import json
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": "admin", "access_level": 3,
                    "exp": time.time() + 999}).encode()).rstrip(b"=").decode()
    homemade = f"x.{body}.no-signature-at-all"
    assert auth.decode_unverified(homemade)["access_level"] == 3   # 解得開！
    assert auth.verify_token(homemade) is None                     # 但不是憑證
