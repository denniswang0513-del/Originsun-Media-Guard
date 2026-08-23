# -*- coding: utf-8 -*-
"""會種資料的 e2e 一律要有生產防護（2026-08-23 事故後補的）。

🔴 事故經過：`ui_project_invoices.py http://127.0.0.1:8000`。那些 UI e2e 有一行

    if ":8000" in BASE:
        sys.path.insert(0, r"C:\\OriginsunAgent")

本意是「用生產那份 code 產 token」，副作用是 `config.load_settings` 跟著讀到
**生產的 settings.json**、`db.session` 連上生產庫。seed() 於是把一個測試客戶、
兩個測試專案、兩張假發票寫進了真帳；那次執行又在中途失敗，清理沒跑到 ——
生產的應收帳款憑空多 100,000、累積損益多 364,952，是後來對財務數字才發現的。

清乾淨了，但「清乾淨」不是修好。修好是讓它不可能再發生。
"""
import re
from pathlib import Path

E2E = Path(__file__).resolve().parents[1] / "e2e"

#: 種資料的訊號 —— 用 db.session 直接寫庫
SEED_MARKS = ("from db.session import", "get_session_factory")


def _seeding_scripts():
    out = []
    for p in sorted(E2E.glob("*.py")):
        if p.name.startswith("_"):
            continue
        src = p.read_text(encoding="utf-8")
        # 只管**吃 BASE 參數**的：不吃參數的跑不到生產
        if "sys.argv[1]" not in src:
            continue
        if any(m in src for m in SEED_MARKS):
            out.append((p.name, src))
    return out


def test_there_is_at_least_one_such_script():
    """守衛測試自己要先確定掃得到東西 —— 掃不到的話它永遠綠，什麼都沒守。"""
    assert _seeding_scripts(), "掃不到任何『吃 BASE 又會種資料』的 e2e —— 這條測試失效了"


def test_every_seeding_e2e_refuses_production():
    """吃 BASE 又會寫 DB 的，一律要呼叫 refuse_prod_seed。"""
    missing = [n for n, src in _seeding_scripts() if "refuse_prod_seed(BASE)" not in src]
    assert not missing, (
        "這幾支 e2e 會種資料又吃 BASE 參數，卻沒有生產防護：" + "、".join(missing)
        + "。加上 `from tests.e2e._guard import refuse_prod_seed` 與 "
          "`refuse_prod_seed(BASE)`。")


def test_the_guard_runs_before_anything_touches_the_db():
    """順序也要對：防護要在第一次 import db / 呼叫 seed 之前。
    放在檔案後半段的話，種子早就寫進去了。"""
    for name, src in _seeding_scripts():
        gi = src.index("refuse_prod_seed(BASE)")
        for later in ("db.session", "asyncio.run(seed", "asyncio.run(clean"):
            j = src.find(later)
            if j >= 0:
                assert gi < j, f"{name}: 防護排在 `{later}` 後面才跑"


def test_the_guard_actually_exits():
    """🔴 只印訊息不中止＝沒有防護。實測那次就是「跑下去了」。"""
    src = (E2E / "_guard.py").read_text(encoding="utf-8")
    body = src[src.index("def refuse_prod_seed"):]
    assert 'if ":8000" in' in body, "沒有判斷生產 port"
    assert re.search(r"sys\.exit\([1-9]", body), "沒有真的中止（只印訊息會被忽略）"


def test_readonly_scripts_are_not_forced_to_carry_the_guard():
    """純唯讀的生產驗證**不該**被擋 —— 那是我們唯一能在生產上看畫面的辦法。
    這條釘住判準是「有沒有寫 DB」，不是「有沒有吃 BASE」。"""
    for p in sorted(E2E.glob("ui_*.py")):
        src = p.read_text(encoding="utf-8")
        if any(m in src for m in SEED_MARKS):
            continue
        assert "refuse_prod_seed" not in src, \
            f"{p.name} 不寫 DB，不該擋掉生產驗證"
