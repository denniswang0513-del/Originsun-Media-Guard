# -*- coding: utf-8 -*-
"""會種資料的 e2e 一律要有生產防護（2026-08-23 事故後補的）。

事故經過：`ui_project_invoices.py http://127.0.0.1:8000`。那些 UI e2e 在 BASE
含 :8000 時會 `sys.path.insert(0, C:/OriginsunAgent)`，本意是「用生產那份 code
產 token」，副作用是 `config.load_settings` 跟著讀到**生產的 settings.json**、
`db.session` 連上生產庫。seed() 於是把一個測試客戶、兩個測試專案、兩張假發票寫進
了真帳；那次執行又在中途失敗，清理沒跑到 —— 生產的應收帳款憑空多 100,000、
累積損益多 364,952，是後來對財務數字才發現的。

清乾淨了，但「清乾淨」不是修好。修好是讓它不可能再發生。

🔴 第二輪審查抓到第一版的防護只認 BASE 裡有沒有 :8000，但生產還有一個入口
foundry.originsun-studio.com（cloudflared → master 8000）。

🔴 第三輪量測到更根本的一件事：**那個 sys.path.insert 從來沒做到它宣稱的事**。
每支腳本都在它之前就 import 了 create_token（綁的是 dev 的函式物件），而且
dev 與 master 的 jwt_secret 本來就是同一把 —— 它唯一的實際作用，就是把
db.session 指向生產，也就是事故成因本身。所以那一行已經整批刪除，不是修好它。

因此這一版的測試分兩層：
  · **行為**（真的呼叫、真的期待 SystemExit）—— 閘門本身用這個驗
  · **原始碼掃描** —— 只留給「跨 15 個檔案的結構事實」這種執行不出來的東西
"""
import re
import sys
from pathlib import Path

import pytest

from tests.unit._srcscan import code_only, func_body

E2E = Path(__file__).resolve().parents[1] / "e2e"

#: 會改到目標機器狀態的訊號。
#: 🔴 判準是「有沒有副作用」，不是「有沒有寫 DB」——
#:    2026-08-24 加了一支改 settings.json 的 e2e（記帳費設定），它不碰資料庫，
#:    卻會改掉那台機器的設定；打到生產就是改生產設定，比種幾筆測試資料更難察覺。
#:    當時這條測試把它判成「唯讀」並要求拿掉防護 —— 判準錯了，不是防護錯了。
SEED_MARKS = ("from db.session import", "get_session_factory",
              'settings.json"', "settings.json'")

DEV = "postgresql+asyncpg://u:p@192.168.1.132:5432/mediaguard_dev"
PROD = "postgresql+asyncpg://u:p@192.168.1.132:5432/mediaguard"
DEV_BASE = "http://127.0.0.1:8001"


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


def _guard():
    sys.path.insert(0, str(E2E.parents[1]))
    from tests.e2e import _guard as g
    return g


def _dsn(monkeypatch, url):
    """讓防護「以為」等一下會連到 url 那個庫。

    patch 的是 `db.session.get_database_url` 本身 —— 防護在函式內才
    `from db.session import`，所以每次呼叫都會重讀模組屬性，patch 得到。
    """
    monkeypatch.setattr("db.session.get_database_url", lambda: url)


# ── 閘門本身：用行為驗，不是掃字串 ────────────────────────────

def test_it_refuses_a_production_database(monkeypatch):
    """🔴 這條就是事故那一次。網址是 dev、庫是生產 —— 必須中止。"""
    _dsn(monkeypatch, PROD)
    with pytest.raises(SystemExit) as e:
        _guard().refuse_prod_seed(DEV_BASE)
    assert e.value.code, "只印訊息不中止＝沒有防護（實測那次就是「跑下去了」）"


def test_it_lets_the_dev_database_through(monkeypatch):
    """防護要**能放行**。只會擋的閘門會被人拿掉。"""
    _dsn(monkeypatch, DEV)
    assert _guard().seed_blocked_reason(DEV_BASE) is None


def test_the_cloudflared_production_entrance_is_refused(monkeypatch):
    """🔴 foundry.originsun-studio.com → master 8000。庫名那道故意放行，
    證明擋下來的是網址那道。"""
    _dsn(monkeypatch, DEV)
    assert _guard().seed_blocked_reason("https://foundry.originsun-studio.com")


def test_an_unrecognised_origin_is_refused_by_default(monkeypatch):
    """🔴 allowlist 的重點：**還沒想到的**生產入口是預設擋下。
    denylist 對下一個入口是預設放行 —— 那正是上一版的洞。"""
    _dsn(monkeypatch, DEV)
    g = _guard()
    for base in ("https://newtunnel.example.com", "http://127.0.0.1:8000",
                 "http://192.168.1.107:8000"):
        assert g.seed_blocked_reason(base), f"{base} 沒被擋下來"


def test_the_allowed_database_is_read_at_call_time(monkeypatch):
    """MG_E2E_DB 讓別的開發機用自己的庫名 —— 但要在呼叫時讀才可能被覆寫，
    而且沒設時只認 mediaguard_dev（不能退成什麼都放行）。"""
    _dsn(monkeypatch, "postgresql+asyncpg://u:p@h/mediaguard_qa")
    g = _guard()
    assert g.seed_blocked_reason(DEV_BASE), "沒設 MG_E2E_DB 時不該放行別的庫"
    monkeypatch.setenv("MG_E2E_DB", "mediaguard_qa")
    assert g.seed_blocked_reason(DEV_BASE) is None, "MG_E2E_DB 是 import 時讀的（改不動）"


def test_database_name_parses_real_dsns():
    """庫名要從真的 DSN 解得出來（含 query string 的那種）。"""
    sys.path.insert(0, str(E2E.parents[1]))
    from db.session import database_name
    assert database_name(DEV) == "mediaguard_dev"
    assert database_name(PROD) == "mediaguard"
    assert database_name("postgresql://u:p@h/mediaguard_dev?sslmode=require") \
        == "mediaguard_dev"


# ── 跨檔案的結構事實：執行不出來，只能掃 ──────────────────────

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


def test_the_production_path_hack_is_gone():
    """🔴 那一行不准回來。

    它宣稱「用生產那份 code 產 token」，但 create_token 在它之前就 import 了
    （綁 dev 的函式物件），而且 dev 與 master 的 jwt_secret 是同一把 ——
    它從來沒產生過任何效果，除了把 db.session 指向生產庫。
    """
    offenders = [p.name for p in sorted(E2E.glob("*.py"))
                 if p.name != "_guard.py"
                 and "OriginsunAgent" in p.read_text(encoding="utf-8")]
    assert not offenders, (
        "這幾支 e2e 又把生產路徑塞回 sys.path / os.chdir 了：" + "、".join(offenders)
        + "。那一行沒有任何正面作用，只會讓 db.session 連上生產庫。")


def test_the_rule_has_exactly_one_definition():
    """🔴 conftest 的 dev_db_only 以前自己讀 settings 再 rsplit 一次 ——
    兩份規則對「哪些庫算 dev」講的話不一樣，而且那一份看不到 env 的
    DATABASE_URL（NAS 容器上會判錯）。現在它只能借用同一支。"""
    src = (E2E / "conftest.py").read_text(encoding="utf-8")
    # code_only：docstring 裡**講**了為什麼不再讀 settings，那不算違規
    seg = code_only(func_body(src, "def dev_db_only("))
    assert "seed_blocked_reason" in seg, "conftest 沒有借用共用那條規則"
    assert "rsplit" not in seg, "conftest 又自己解析了一次 DSN"
    assert "load_settings" not in seg, \
        "conftest 又自己讀 settings —— 那看不到 env 的 DATABASE_URL"


def test_readonly_scripts_are_not_forced_to_carry_the_guard():
    """純唯讀的生產驗證**不該**被擋 —— 那是我們唯一能在生產上看畫面的辦法。
    這條釘住判準是「有沒有寫 DB」，不是「有沒有吃 BASE」。"""
    for p in sorted(E2E.glob("ui_*.py")):
        src = p.read_text(encoding="utf-8")
        if any(m in src for m in SEED_MARKS):
            continue
        assert "refuse_prod_seed" not in src, \
            f"{p.name} 不寫 DB，不該擋掉生產驗證"


def test_the_guard_does_not_import_db_at_module_level():
    """🔴 防護必須在**呼叫時**才解析 DSN —— module 層 import 的話，
    「在 db.session 被載入前先判斷」這件事就沒意義了。"""
    src = (E2E / "_guard.py").read_text(encoding="utf-8")
    head = src[:src.index("def seed_blocked_reason(")]
    assert not re.search(r"^from db\.|^import db\b", head, re.M), \
        "_guard 在 module 層就 import 了 db —— 解析時機會提前到 import 那一刻"
