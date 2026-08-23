# -*- coding: utf-8 -*-
"""e2e 的生產防護 —— 會種資料的測試絕對不能打到生產。

🔴 2026-08-23 真的發生過：`ui_project_invoices.py http://127.0.0.1:8000`。
那些 UI e2e 有一行

    if ":8000" in BASE:
        sys.path.insert(0, r"C:\\OriginsunAgent")

本意是「用生產那份 code 產 token」，但副作用是 `config.load_settings` 跟著讀到
**生產的 settings.json** → `db.session` 連上 **mediaguard（生產庫）**。於是
seed() 把測試客戶、兩個測試專案、兩張假發票寫進了生產；那次執行又在中途失敗，
清理沒跑到 —— 生產的應收帳款憑空多了 100,000、累積損益多了 36 萬，是隔了一段
時間對財務數字時才發現的。

所以：**會種資料的 e2e，一律在最上面呼叫 refuse_prod_seed(BASE)。**
純唯讀的（只點畫面、不寫 DB）不需要，也不該加 —— 那會讓生產驗證做不了。
"""
from __future__ import annotations

import sys


def refuse_prod_seed(base: str) -> None:
    """BASE 指向生產（8000）就直接中止，不留任何餘地。

    不做「只是警告」——警告會被忽略，而這件事的代價是別人的真帳。
    要在生產上驗畫面，寫一支**不種資料**的檢查（例如 scratchpad 的 prod_check），
    拿生產自己既有的資料來看。
    """
    if ":8000" in (base or ""):
        print("")
        print("🔴 拒絕執行：這支 e2e 會種資料，而 BASE 指向生產（:8000）。")
        print("   種子是走 db.session 寫的，而 :8000 會讓 settings 解析到生產庫 ——")
        print("   測試資料會進真帳，中途失敗還清不掉（2026-08-23 已經發生過一次）。")
        print("   要驗生產請用不種資料的檢查，拿生產既有資料看。")
        sys.exit(2)
