# -*- coding: utf-8 -*-
"""一次性：deploy v2.4.193 到生產 8000（跑完即刪，不進 git）。"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
sys.stdout.reconfigure(encoding="utf-8")
from core.auth import create_token  # noqa: E402

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["finance_mine"]})
H = {"Authorization": "Bearer " + T, "Content-Type": "application/json"}

body = {
    "version": "2.4.193",
    "notes": ("家用往來：代墊=資產不是提取（科目 1310、六類別搬掛、BS 新「家用代墊」線 663,253）"
              "＋🏠 家用記帳子視圖（餘額卡/快速記一筆/月度/最近列）。資產儀表板 SUPERSEDED 收"
              "備用金/其他資產免雙計；新帳戶期初配權益調整。皇冠合成帳本 diff=0、端到端記一筆 ALL PASS。"),
}
req = urllib.request.Request("http://127.0.0.1:8001/api/v1/deploy_to_prod",
                             data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                             headers=H, method="POST")
d = json.load(urllib.request.urlopen(req, timeout=30))
print("submit:", d)
job = d["job_id"]
for i in range(60):
    time.sleep(5)
    req = urllib.request.Request(
        f"http://127.0.0.1:8001/api/v1/publish/status?job_id={job}", headers=H)
    try:
        st = json.load(urllib.request.urlopen(req, timeout=15))
    except Exception as e:
        print("poll err:", e)
        continue
    if st.get("status") != "running":
        print("status:", st.get("status"))
        print(st.get("log", "")[-900:])
        print("message:", st.get("message", ""))
        break
    if i % 4 == 0:
        print(f"  …running ({(i + 1) * 5}s)")
else:
    print("TIMEOUT")
