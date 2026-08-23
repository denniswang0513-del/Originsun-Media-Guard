# -*- coding: utf-8 -*-
"""publish 末段拉 redirect map 要重試（2026-08-24 v2.4.159 發版實測到的 race）。

`sync_redirects_to_nas()` 就跑在 `docker restart website-api` 之後。容器還在起來
的話，Website_Nginx（8090）會回 **502**，而原本的實作只試一次就放棄。

那一次剛好轉址集沒變（NAS 上是 8/22 產生的 244 條，API 當下也是 244 條），
所以零影響 —— 但下次集合真的變了，同一個 race 會讓 NAS **靜默留著舊的 301**。
訊息還寫「軟 301 fallback 仍生效」，讀起來像沒事。這是一年後才會有人發現的錯。

🔴 這裡釘的是**行為**（真的重試幾次、什麼情況不重試），不是原始碼字串。
"""
import urllib.error

import pytest

import publish_update


@pytest.fixture
def no_side_effects(monkeypatch):
    """把 SSH / nginx 同步 / sleep 隔離掉 —— 這支函式會真的 ssh 到 NAS。"""
    real_exists = publish_update.os.path.exists
    # 只讓 SSH key 那一問回 True，其餘照舊（整個 os.path.exists 蓋掉會波及別處）
    monkeypatch.setattr(publish_update.os.path, "exists",
                        lambda p: True if p == publish_update.SSH_KEY_PATH else real_exists(p))
    monkeypatch.setattr(publish_update, "sync_nginx_conf_to_nas", lambda: True)
    slept = []
    monkeypatch.setattr(publish_update.time, "sleep", lambda s: slept.append(s))
    return slept


def _urlopen_raising(codes, calls):
    """依序丟出 codes 裡的 HTTP 錯誤；用完就換成連線錯誤（永遠不會成功）。"""
    def fake(url, timeout=None):
        calls.append(url)
        i = len(calls) - 1
        if i < len(codes):
            raise urllib.error.HTTPError(url, codes[i], "boom", {}, None)
        raise urllib.error.URLError("refused")
    return fake


def test_a_502_is_retried_not_given_up_on(monkeypatch, no_side_effects):
    """502 ＝ 容器還沒起來，要再等 —— 不能只試一次就走。"""
    calls = []
    monkeypatch.setattr("urllib.request.urlopen",
                        _urlopen_raising([502] * 10, calls))
    assert publish_update.sync_redirects_to_nas() is False
    assert len(calls) == 6, f"只試了 {len(calls)} 次（應該重試到 6 次）"
    assert len(no_side_effects) == 5, "重試之間沒有等"


def test_it_succeeds_once_the_container_comes_up(monkeypatch, no_side_effects):
    """🔴 重點不是「會重試」，是**重試成功後真的拿到資料往下走**。"""
    calls = []
    deployed = {}

    class _Resp:
        def read(self):
            return b'{"items": {"/old": "/new"}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 502, "boom", {}, None)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    # 攔下真的 scp/docker —— 只確認「有拿到 items 才會走到部署」
    monkeypatch.setattr(publish_update.subprocess, "run",
                        lambda *a, **k: deployed.setdefault("ran", True))
    publish_update.sync_redirects_to_nas()
    assert len(calls) == 3, f"第 3 次才成功，卻打了 {len(calls)} 次"
    assert deployed.get("ran"), "拿到 redirect map 之後沒有往下部署"


def test_a_404_is_not_retried(monkeypatch, no_side_effects):
    """🔴 404 不是「還沒起來」，是端點不見了 —— 重試六次只是白等一分鐘。
    分辨得出暫時性與永久性，才不會把發版流程拖慢。"""
    calls = []
    monkeypatch.setattr("urllib.request.urlopen",
                        _urlopen_raising([404], calls))
    assert publish_update.sync_redirects_to_nas() is False
    assert len(calls) == 1, f"404 也重試了（打了 {len(calls)} 次）"
    assert not no_side_effects, "404 還在那邊等"


def test_giving_up_says_the_nas_copy_may_be_stale(capsys, monkeypatch, no_side_effects):
    """🔴 放棄時的訊息要講對後果。

    原本只說「軟 301 fallback 仍生效」—— 那是真的，但讀起來像沒事；實際狀況是
    NAS 上的硬 301 **維持上一次的版本**，可能已經過期。
    """
    calls = []
    monkeypatch.setattr("urllib.request.urlopen",
                        _urlopen_raising([502] * 10, calls))
    publish_update.sync_redirects_to_nas()
    out = capsys.readouterr().out
    assert "上一次" in out and "過期" in out, \
        "放棄時沒講清楚 NAS 上留著的是舊版（讀起來會以為沒事）"
