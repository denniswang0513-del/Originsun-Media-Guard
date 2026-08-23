# -*- coding: utf-8 -*-
"""會種資料的 e2e 一律要有生產防護（2026-08-23 事故後補的）。

事故經過：`ui_project_invoices.py http://127.0.0.1:8000`。那些 UI e2e 在 BASE
含 :8000 時會 `sys.path.insert(0, C:/OriginsunAgent)`，本意是「用生產那份 code
產 token」，副作用是 `config.load_settings` 跟著讀到**生產的 settings.json**、
`db.session` 連上生產庫。seed() 於是把一個測試客戶、兩個測試專案、兩張假發票寫進
了真帳；那次執行又在中途失敗，清理沒跑到 —— 生產的應收帳款憑空多 100,000、
累積損益多 364,952，是後來對財務數字才發現的。

清乾淨了，但「清乾淨」不是修好。修好是讓它不可能再發生。

🔴 第二輪審查抓到第一版的防護**還有一個洞**：它只認 BASE 裡有沒有 :8000，但生產
還有一個入口 foundry.originsun-studio.com（cloudflared → master 8000）。從那個
網址跑的話防護放行，而上面那個 sys.path.insert 也不會觸發 —— API 打生產、種子寫
dev，兩邊分裂，比原本的事故更難查（畫面全對，只是資料不在同一個庫）。

所以真正的閘門改成**解析出「等一下會連到哪個庫」**（`assert_dev_db`），網址檢查
降級成第一道便宜的攔截。下面的測試也跟著釘庫名那一道，不再只釘字串。
"""
import re
import sys
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


def _guard_src():
    return (E2E / "_guard.py").read_text(encoding="utf-8")


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
    assert re.search(r"sys\.exit\([1-9]", _guard_src()), \
        "沒有真的中止（只印訊息會被忽略）"


def test_the_real_gate_is_the_database_not_the_url():
    """🔴 網址檢查擋不住 foundry.originsun-studio.com（cloudflared → 生產 8000）：
    那條路上連 sys.path.insert 都不會觸發，於是 API 打生產、種子寫 dev ——
    兩邊分裂，比原本的事故更難查。

    所以保證必須來自「等一下會連到哪個庫」，不是網址長什麼樣。"""
    src = _guard_src()
    assert "def assert_dev_db(" in src, "沒有以庫名為準的閘門"
    gate = src[src.index("def assert_dev_db("):]
    assert "get_database_url" in gate, "沒有去解析真正會連到的 DSN"
    assert "_ALLOWED_DB" in gate, "沒有把允許的庫名收成一個常數"
    # refuse_prod_seed 必須把庫名那道也走過 —— 只擋網址等於沒補到洞
    rp = src[src.index("def refuse_prod_seed("):]
    assert "assert_dev_db()" in rp, "refuse_prod_seed 沒有回頭走庫名那一道"
    assert "foundry." in rp, "網址那道漏了 cloudflared 的生產入口"


def test_the_allowed_database_is_not_defaulted_open():
    """MG_E2E_DB 沒設時只認 mediaguard_dev —— 不能退成「什麼都放行」。"""
    assert 'os.environ.get("MG_E2E_DB", "mediaguard_dev")' in _guard_src()


def test_db_name_parsing_handles_real_dsns():
    """庫名要從真的 DSN 解得出來（含 query string 的那種）。"""
    sys.path.insert(0, str(E2E.parents[1]))
    from tests.e2e._guard import _db_name
    assert _db_name("postgresql+asyncpg://u:p@192.168.1.132:5432/mediaguard_dev") \
        == "mediaguard_dev"
    assert _db_name("postgresql+asyncpg://u:p@h:5432/mediaguard") == "mediaguard"
    assert _db_name("postgresql://u:p@h/mediaguard_dev?sslmode=require") == "mediaguard_dev"


def test_readonly_scripts_are_not_forced_to_carry_the_guard():
    """純唯讀的生產驗證**不該**被擋 —— 那是我們唯一能在生產上看畫面的辦法。
    這條釘住判準是「有沒有寫 DB」，不是「有沒有吃 BASE」。"""
    for p in sorted(E2E.glob("ui_*.py")):
        src = p.read_text(encoding="utf-8")
        if any(m in src for m in SEED_MARKS):
            continue
        assert "refuse_prod_seed" not in src, \
            f"{p.name} 不寫 DB，不該擋掉生產驗證"
