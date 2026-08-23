# -*- coding: utf-8 -*-
"""e2e 的生產防護 —— 會種資料的測試絕對不能寫到生產庫。

🔴 2026-08-23 真的發生過：`ui_project_invoices.py http://127.0.0.1:8000`。
那些 UI e2e 有一行

    if ":8000" in BASE:
        sys.path.insert(0, r"C:\\OriginsunAgent")

本意是「用生產那份 code 產 token」，但副作用是 `config.load_settings` 跟著讀到
**生產的 settings.json** → `db.session` 連上 **mediaguard（生產庫）**。於是
seed() 把測試客戶、兩個測試專案、兩張假發票寫進了生產；那次執行又在中途失敗，
清理沒跑到 —— 生產的應收帳款憑空多了 100,000、累積損益多了 36 萬，是隔了一段
時間對財務數字時才發現的。

## 為什麼不能只看網址

第一版只擋 `":8000" in BASE`。但生產**還有一個入口**：
`https://foundry.originsun-studio.com`（cloudflared → master 8000）。從那個
網址跑的話：

  · `refuse_prod_seed` 放行（字串裡沒有 :8000）
  · 那個 `sys.path.insert` 也**不會**觸發

於是 API 打生產、種子寫 dev —— 分裂成兩邊，比原本的事故更難查（畫面上什麼都
對，只是資料不在同一個庫）。

所以真正的閘門在**連到哪個庫**，不在網址長什麼樣。`assert_dev_db()` 在開任何
session 之前把 `db.session.get_database_url()` 解析出來，庫名不是 dev 就中止。
網址檢查留著當第一道便宜的防線，但它不是唯一那道。

純唯讀的生產驗證（只點畫面、不開 session）不受影響，也不該受影響 —— 那是我們
唯一能在生產上看畫面的辦法。
"""
from __future__ import annotations

import os
import re
import sys

#: 允許被寫入的庫。`MG_E2E_DB` 讓別的開發機用自己的庫名，但不給預設值 ——
#: 沒設就只認 mediaguard_dev，設錯了會直接擋下來而不是靜默放行。
_ALLOWED_DB = os.environ.get("MG_E2E_DB", "mediaguard_dev")


def _db_name(url: str) -> str:
    """從 SQLAlchemy DSN 取庫名（?query 之前的最後一段）。"""
    return re.sub(r"[?#].*$", "", (url or "")).rstrip("/").rsplit("/", 1)[-1]


def _stop(*lines: str) -> None:
    print("")
    for ln in lines:
        print(ln)
    sys.exit(2)


def assert_dev_db() -> str:
    """🔴 真正的閘門：解析出「等一下會連到哪個庫」，不是 dev 就中止。

    在 `db.session` 開任何 session 之前呼叫。回傳庫名（方便測試斷言）。
    """
    from db.session import get_database_url
    name = _db_name(get_database_url())
    if name != _ALLOWED_DB:
        _stop(f"🔴 拒絕執行：這支 e2e 會種資料，而它連到的是「{name}」，不是"
              f"「{_ALLOWED_DB}」。",
              "   測試資料會進真帳，中途失敗還清不掉（2026-08-23 已經發生過一次）。",
              "   要驗生產請用不種資料的檢查，拿生產既有資料看。")
    return name


def refuse_prod_seed(base: str) -> str:
    """網址 + 庫名兩道。BASE 指向生產、或解析出來的庫不是 dev，就中止。

    網址那道只是便宜的早期攔截（訊息比較好懂）；**庫名那道才是保證**。
    """
    if ":8000" in (base or "") or "foundry." in (base or ""):
        _stop(f"🔴 拒絕執行：這支 e2e 會種資料，而 BASE（{base}）指向生產。",
              "   要驗生產請用不種資料的檢查，拿生產既有資料看。")
    return assert_dev_db()
