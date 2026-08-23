# -*- coding: utf-8 -*-
"""記帳費設定（owner 2026-08-24：「寫一個按鈕設定會計費用…之後換會計調整這個按鈕就好」）。

費率之前只活在人的記憶裡，被講錯兩次、兩次都寫進了生產帳。這支證明它現在
真的存得住、改得動、而且**舊的留著**（歷史期別要用當時的費率算）。

🔴 這支會改 settings.json（不是資料庫），所以自己備份／還原那個 key ——
   跑完必須跟跑之前一模一樣。
"""
import copy
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會改設定 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "finance"]})
API = BASE + "/api/v1/finance"
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def call(path, method="GET", body=None):
    r = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + T, "Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(r, timeout=60).read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode("utf-8")[:200]}


def read_settings_key():
    from config import load_settings
    return copy.deepcopy((load_settings().get("finance") or {}).get("bookkeeping_fee"))


def restore(orig):
    """🔴 直接改檔，不走 save_settings —— 那支是 **merge-on-save**，`pop` 掉的
    key 會被檔案裡的舊值合併回來（memory 記過的「merge-on-save 墓碑」）。
    這支測試前是「沒設定過」，要真的還原成沒設定過就只能改檔。"""
    import io
    import json as _json
    import os
    p = os.path.join(r"E:\Dev\Originsun-Media-Guard", "settings.json")
    s = _json.load(io.open(p, encoding="utf-8"))
    fin = s.get("finance") or {}
    if orig is None:
        fin.pop("bookkeeping_fee", None)
    else:
        fin["bookkeeping_fee"] = orig
    s["finance"] = fin
    io.open(p, "w", encoding="utf-8").write(
        _json.dumps(s, ensure_ascii=False, indent=2))


ORIG = read_settings_key()
print(f"[0] 備份原設定：{'（未設定過）' if ORIG is None else f'{len(ORIG)} 筆費率'}")

try:
    print("")
    print("[1] 讀得到費率 + 未來幾期各收多少")
    d = call("/bookkeeping-fee")
    if d.get("_status"):
        print("  !!", d); sys.exit(1)
    check(bool(d.get("rates")), "有費率表", f"{len(d.get('rates') or [])} 筆")
    up = d.get("upcoming") or []
    check(len(up) == 6, "回了接下來六期", str(len(up)))
    check(all(x["fee"] > 0 for x in up), "每期都算得出金額")
    by = {x["month"]: x["fee"] for x in up}
    print("    ", by)
    # 出廠預設下：2026-09 起 5,000、2027-05 是 10,000
    if "2026-09" in by:
        check(by["2026-09"] == 5000, "2026-09 是 5,000（owner 特別交代的那一期）",
              str(by["2026-09"]))

    print("")
    print("[2] 改費率 —— 換會計就是按這顆")
    r = call("/bookkeeping-fee", "PUT",
             {"effective_from": "2027-01", "monthly": 3000, "months_per_year": 13})
    check(not r.get("_status"), "存得起來", str(r.get("_body", "")))
    d2 = call("/bookkeeping-fee")
    by2 = {x["month"]: x["fee"] for x in (d2.get("upcoming") or [])}
    check(by2.get("2027-01") == 6000, "2027-01 起變成 6,000", str(by2.get("2027-01")))
    check(by2.get("2026-09") == 5000, "🔴 2026-09 沒被追溯改掉", str(by2.get("2026-09")))

    print("")
    print("[3] 🔴 舊費率留著 —— 歷史期別要用當時的算")
    effs = [x["effective_from"] for x in (d2.get("rates") or [])]
    print("    ", effs)
    check("0000-00" in effs and "2026-09" in effs, "舊的兩筆都還在", str(effs))
    check(effs == sorted(effs), "依生效日排好序")

    print("")
    print("[4] 同一個生效月再存一次 ＝ 修正，不是疊加")
    call("/bookkeeping-fee", "PUT",
         {"effective_from": "2027-01", "monthly": 3500, "months_per_year": 13})
    d3 = call("/bookkeeping-fee")
    effs3 = [x["effective_from"] for x in (d3.get("rates") or [])]
    check(effs3.count("2027-01") == 1, "沒有變成兩筆", str(effs3))
    by3 = {x["month"]: x["fee"] for x in (d3.get("upcoming") or [])}
    check(by3.get("2027-01") == 7000, "改成新的那個數字", str(by3.get("2027-01")))

    print("")
    print("[5] 亂填要擋下來（悄悄存進去比報錯危險）")
    for body, why in (
        ({"effective_from": "2027", "monthly": 3000, "months_per_year": 13}, "年月格式"),
        ({"effective_from": "2027-13", "monthly": 3000, "months_per_year": 13}, "13 月"),
        ({"effective_from": "2027-03", "monthly": 0, "months_per_year": 13}, "月費 0"),
        ({"effective_from": "2027-03", "monthly": 3000, "months_per_year": 11}, "一年 11 個月"),
    ):
        r = call("/bookkeeping-fee", "PUT", body)
        check(r.get("_status") == 422, f"{why} → 422", str(r.get("_status")))

finally:
    print("")
    print("[清理] 還原設定")
    restore(ORIG)
    now = read_settings_key()
    check(now == ORIG, "settings.json 跟跑之前一模一樣",
          f"{'未設定' if now is None else len(now)}")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
