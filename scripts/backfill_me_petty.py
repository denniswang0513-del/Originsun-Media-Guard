# -*- coding: utf-8 -*-
"""me_petty 回填 —— 零用金鑰匙從 me_finance 拆出（2026-08-19）後的一次性遷移。

拆 key 那一刻起，只有 me_finance 的帳號會**靜默失去**零用金入口。此腳本把
現有 me_finance 持有者補上 me_petty，維持行為不變；之後 owner 想單獨收回，
在使用者管理把「我的請款」取消勾選即可。

🔴 走 auth API（後端 _persist_user 雙寫 users.json + DB），絕不直寫 DB ——
   直寫會讓兩份使用者資料漂掉。冪等，重跑無害。管理員帳號跳過（Lv3 全通，
   不需要模組）。

用法：
    python scripts/backfill_me_petty.py            # dry-run（dev 8001）
    python scripts/backfill_me_petty.py --apply    # 實際寫入 dev
    python scripts/backfill_me_petty.py --base http://127.0.0.1:8000 --apply  # prod（發版後跑）
"""
import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import admin_session  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    s = admin_session("backfill_me_petty")
    users = s.get(f"{args.base}/api/v1/auth/users", timeout=15).json()
    if isinstance(users, dict):
        users = users.get("users", [])

    hits = 0
    for u in users:
        mods = u.get("modules") or []
        if (u.get("access_level") or 0) >= 3:
            continue                       # 管理員全通，不掛模組
        if "me_finance" not in mods or "me_petty" in mods:
            continue
        hits += 1
        print(f"{'APPLY' if args.apply else 'DRY  '} {u['username']}: +me_petty")
        if args.apply:
            r = s.put(f"{args.base}/api/v1/auth/users/{u['username']}",
                             json={"modules": mods + ["me_petty"]},
                             timeout=15)
            r.raise_for_status()
    print(f"{'寫入' if args.apply else '待補'} {hits} 個帳號")


if __name__ == "__main__":
    main()
