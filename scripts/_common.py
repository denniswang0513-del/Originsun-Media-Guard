# -*- coding: utf-8 -*-
"""匯入腳本共用底座 —— dev/prod 庫切換、表頭定位、日期正規化。

為什麼收在這裡：這三段本來各躺在 import_cashbook / import_invoices /
import_petty_cash 裡，三份**一字不差**，而三個 docstring 各自寫「與 X 同一套
寫法，不得漂移」指向不同的檔 —— 繞成一圈，等於沒有正本。真正需要防漂移的東西
就該只有一份可以改。
"""
import re


def resolve_db_url(prod: bool) -> str:
    """dev/prod 庫切換的單一正本。

    🔴 只換庫名、不動整串 —— memory 有一筆把 `database_url` 整串取代、害 dev
    連進生產庫的前科。dry-run 報告器與實際寫入必須走同一支，否則「看的是 dev、
    寫的是 prod」這種事會靜默發生。
    """
    from config import load_settings
    url = load_settings().get("database_url", "")
    return (url.replace("/mediaguard_dev", "/mediaguard") if prod
            else (url if url.endswith("_dev") else url + "_dev"))


def find_header(rows: list, mark: str) -> int:
    """回表頭列的索引（該列有一格恰好等於 mark）。找不到直接中止。"""
    for i, r in enumerate(rows):
        if any(c.strip() == mark for c in r):
            return i
    raise SystemExit(f"找不到表頭列（沒有任何一列含「{mark}」）")


def parse_date(v: str):
    """'2024/01/02' / '2024-1-2' → 該日的 UTC 午夜。壞值回 None（報告會列出來）。

    🔴 日期只把 Sheet 的斜線格式正規化，實際的 datetime **交給
    routers.crm._shared._parse_shoot_date 造** —— 這個 repo 的日期欄慣例是
    UTC 午夜（台北是 UTC+8，UTC 午夜換算台北仍同一天，_fmt_day 與前端取 ISO
    前 10 碼才會一致）。這裡自己 `datetime(y,m,d).date()` 會被寫成**台北**午夜
    ＝前一天 16:00Z，前端顯示就少一天（2026-08-19 第一版匯入實際踩到，394 筆全中）。
    """
    v = (v or "").strip()
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", v)
    if not m:
        return None
    from routers.crm._shared import _parse_shoot_date
    return _parse_shoot_date(f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}")
