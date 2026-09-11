# -*- coding: utf-8 -*-
"""發版閘門 `publish_update.master_restarted`：要證明主控端**真的**重啟成新版。

為什麼值得一支測試：這道閘門擋的是 v2.5.0 那種事故 —— NAS 先拿到新碼、master 沒重啟
（DB migration 只有 master 會跑）→ office-api 拿新 ORM 查舊 schema、整段 500。
而第一版閘門是空門（只看 /version 的 version 欄位，那個每 request 重讀磁碟，沒重啟
也回新版）。這裡把「什麼才算證明」釘住：port 要先斷、回來時 `running` 要是新版。
"""
from publish_update import master_restarted, notes_look_mangled


def _clock():
    """可控的 sleep：不真的等，只記錄。"""
    calls = []
    return calls, (lambda s: calls.append(s))


def test_a_restart_that_never_happens_is_caught():
    """port 從頭到尾都活著 → 舊行程還在跑舊碼，不准放行。"""
    _, sleep = _clock()
    ok, why = master_restarted("2.6.0", alive=lambda: True, running=lambda: "2.6.0",
                               sleep=sleep, drop_tries=3, up_tries=3)
    assert not ok and "重啟沒有發生" in why


def test_the_version_on_disk_does_not_count_only_the_running_one():
    """🔴 空門的形狀：磁碟已是新版、行程回報的 running 還是舊版 → 要擋。"""
    alive = iter([True, False, True, True])            # 斷一下再回來
    _, sleep = _clock()
    ok, why = master_restarted("2.6.0", alive=lambda: next(alive, True),
                               running=lambda: "2.5.9",   # 舊碼回來了
                               sleep=sleep, drop_tries=5, up_tries=3)
    assert not ok and "2.5.9" in why and "2.6.0" in why


def test_old_code_without_the_running_field_is_not_a_pass():
    """舊版沒有 `running` 欄位（回 ""）→ 那就是舊碼，不是「暫時還沒起來」。"""
    alive = iter([False])
    _, sleep = _clock()
    ok, why = master_restarted("2.6.0", alive=lambda: next(alive, True),
                               running=lambda: "", sleep=sleep, drop_tries=2, up_tries=2)
    assert not ok


def test_dropped_then_back_with_the_new_version_passes():
    alive = iter([True, True, False])
    probes = iter([None, None, "2.6.0"])               # 前兩次連不上（重啟中）
    calls, sleep = _clock()
    ok, why = master_restarted("2.6.0", alive=lambda: next(alive, True),
                               running=lambda: next(probes, "2.6.0"),
                               sleep=sleep, drop_tries=5, up_tries=5)
    assert ok and "2.6.0" in why
    assert calls, "應該有真的等（用注入的 sleep）"


def test_never_coming_back_times_out():
    alive = iter([False])
    _, sleep = _clock()
    ok, why = master_restarted("2.6.0", alive=lambda: next(alive, True),
                               running=lambda: None, sleep=sleep, drop_tries=2, up_tries=3)
    assert not ok and "沒有回來" in why


def test_fullwidth_punctuation_eaten_between_chinese_is_caught():
    """`修正?報價單` —— 中文活著、只有全形標點被吃：`?` 兩邊都貼著中文。"""
    assert notes_look_mangled("修正?報價單?PDF")
    assert notes_look_mangled("匯款通知?NAS 短網址")
    # 作者自己打的問號：在句尾或後面接空白，不算
    assert not notes_look_mangled("修好了嗎? 先發一版")
    assert not notes_look_mangled("這樣可以嗎?")
