# -*- coding: utf-8 -*-
"""e2e 的生產防護 —— 會種資料的測試絕對不能寫到生產庫。

🔴 2026-08-23 真的發生過：`ui_project_invoices.py http://127.0.0.1:8000`。
那些 UI e2e 有一行

    if ":8000" in BASE:
        sys.path.insert(0, r"C:\\OriginsunAgent")

seed() 於是把測試客戶、兩個測試專案、兩張假發票寫進了生產；那次執行又在中途
失敗，清理沒跑到 —— 生產的應收帳款憑空多了 100,000、累積損益多了 36 萬，是隔了
一段時間對財務數字時才發現的。

## 那一行的本意是「用生產那份 code 產 token」。它從來沒做到過

2026-08-23 事後量測，兩件事各自就足以讓它失效：

  · **import 順序**：每一支腳本都在那一行**之前**就 `from core.auth import
    create_token`。綁進去的是 dev 的函式物件，`_cached_secret` 也早就從 dev 的
    config 解析完了。改 `sys.path` 對一個已經 import 好的模組沒有任何作用。
  · **secret 本來就同一把**：dev 與 master 的 `jwt_secret` 雜湊一致（三邊共用，
    見 memory `reference_jwt_secret_topology`）。就算順序是對的，簽出來的 token
    也一模一樣。

所以那一行**唯一**的實際作用，是把 `config` / `db.session` 指向生產 —— 也就是
上面那場事故的成因本身。它已經從所有 e2e 移除，`test_e2e_prod_guard.py` 有一條
測試守著不准回來。

## 為什麼閘門在「連到哪個庫」，不在網址

第一版只擋 `":8000" in BASE`。但生產還有第二個入口
（`https://foundry.originsun-studio.com`，cloudflared → master 8000），從那裡跑
的話網址那道整個放行。而且下一個入口出現時，denylist 是**預設放行**的 ——
所以網址這道改成 allowlist（只認已知的 dev 來源），真正的保證則是解析出
`db.session.get_database_url()` 會連到哪個庫。env 的 `DATABASE_URL` 也走這支，
讀 settings.json 是看不到的。

純唯讀的生產驗證（只點畫面、不開 session）不受影響，也不該受影響 —— 那是我們
唯一能在生產上看畫面的辦法。
"""
from __future__ import annotations

import os
import re
import sys

#: 已知的 dev 來源（allowlist）。dev 一律 8001；foundrytest 是它的對外通道。
#: 用 allowlist 是因為 denylist 對「還沒想到的生產入口」是預設放行的。
_DEV_ORIGIN = re.compile(
    r"^https?://(?:localhost|127\.0\.0\.1|\[::1\]):8001(?:[/?#]|$)"
    r"|^https?://foundrytest\.", re.I)


def _allowed_db() -> str:
    """允許被寫入的庫。`MG_E2E_DB` 讓別的開發機用自己的庫名。

    **在呼叫時讀**，不是 import 時 —— 這樣它可以被 monkeypatch，測試才驗得到
    真正的行為而不是去掃原始碼字串。
    """
    return os.environ.get("MG_E2E_DB", "mediaguard_dev")


def _stop(*lines: str) -> None:
    print("")
    for ln in lines:
        print(ln)
    sys.exit(2)


def seed_blocked_reason(base: str | None = None) -> str | None:
    """可以在這裡種資料嗎？可以回 `None`，不可以回**原因字串**。

    這是這道閘的**唯一一份規則**。兩個呼叫端只是後果不同：獨立腳本中止
    （`refuse_prod_seed`），pytest 跳過（conftest 的 `dev_db_only`）——
    規則本身不准有第二個定義（抄壞的後果就是上面那場事故）。
    """
    if base is not None and not _DEV_ORIGIN.match(base or ""):
        return (f"🔴 拒絕執行：這支 e2e 會種資料，而 BASE（{base}）不是已知的 dev 來源。\n"
                "   要驗生產請用不種資料的檢查，拿生產既有資料看。")
    # 🔴 真正的閘門：解析出「等一下會連到哪個庫」。必須在 db.session 開任何
    #    session 之前呼叫。走 get_database_url() 而不是讀 settings ——
    #    env 的 DATABASE_URL 只有這支看得到。
    from db.session import get_database_url, database_name
    name = database_name(get_database_url())
    allowed = _allowed_db()
    if name != allowed:
        return (f"🔴 拒絕執行：這支 e2e 會種資料，而它連到的是「{name}」，"
                f"不是「{allowed}」。\n"
                "   測試資料會進真帳，中途失敗還清不掉（2026-08-23 已經發生過一次）。\n"
                "   要驗生產請用不種資料的檢查，拿生產既有資料看。")
    return None


def refuse_prod_seed(base: str) -> None:
    """獨立 e2e 腳本用：不能種就直接中止（exit 2）。"""
    reason = seed_blocked_reason(base)
    if reason:
        _stop(*reason.split("\n"))
