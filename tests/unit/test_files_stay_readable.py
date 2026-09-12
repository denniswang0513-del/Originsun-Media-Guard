# -*- coding: utf-8 -*-
"""沒有檔案可以悄悄長到「一次讀不完」。

CLAUDE.md 規則 B 說 2,000 行是單次讀取硬上限，超過的部分會被截斷、而 AI 不會
告訴你它沒看完。這條規則從來沒有東西在守，結果 2026-08-30 體檢時發現
`routers/crm/finance.py` 2,996 行、`core/finance_logic.py` 2,548 行 ——
兩個都是當時改動最頻繁的財務檔，也就是**最常被半盲修改**的兩個。

超過上限不會噴錯。症狀是「AI 改壞了旁邊那半個它沒讀到的檔」，而那看起來就像
一次普通的失誤。所以這裡把它變成會紅的東西。

🔴 量法：Python 的 `splitlines()`（數 `\\n`）。**不要用 PowerShell 的
`(Get-Content f | Measure-Object -Line).Lines`** —— 那個寫法低估 18–35%
（丟空行，而且 `Get-Content` 不指定 `-Encoding UTF8` 時在含中文的檔上還會少切
行）。CLAUDE.md 裡原本教的就是那個壞掉的版本，`db/models.py` 因此回報 1,389、
實際 2,121，剛好跨過上限那條線的錯誤那一邊。
"""
import pathlib

READ_LIMIT = 2000
WARN_AT = 1600      # 逼近上限就該規劃切法了，不要等到超過

SCAN_DIRS = ("core", "routers", "services", "db", "frontend", "utils", "tests")
SCAN_FILES = ("main.py", "core_engine.py", "tts_engine.py", "transcriber.py",
              "publish_update.py", "bootstrap.py", "notifier.py", "config.py")
SKIP = ("node_modules", "__pycache__", ".venv", "python_embed", "/dist/",
        "/website/", "/e2e/screenshots/")

# ── 已經超標的（2026-08-30 用對的量法重數）──────────────────────
#
# 🔴 這份名單只能變短，不能變長。要加東西進來，代表你正在讓第六個檔越過那條線
#    —— 先問「這個檔憑什麼例外」，把理由寫在旁邊，而且要有人在 review 看到。
#
# 拆檔的作法見 `core/finance_logic/__init__.py` 的檔頭：先用 AST 建呼叫圖、算
# 強連通分量確認零環，再把界畫在既有的分節註解上，就能做到零函式搬家（那次
# 139 個頂層定義的 AST 逐字相同）。
EXEMPT = {
    # frontend/app.js 2026-09-03 拆掉分散式派發（js/app/remote-dispatch.js）與進度條
    # （js/app/progress.js）後回到上限之內；剩 Socket.IO、分組導覽、排程彈窗
    "core_engine.py":
        "備份/轉檔/串接/驗證四個引擎共用暫停停止狀態，切點不明顯",
    # frontend/tabs/finance/subviews/recon.js 2026-09-03 移掉對帳工作台後回到上限之內
    # db/models.py 2026-08-31 已拆成套件（db/models/，最大段 701 行）——
    # 它曾同時是 fan-in 85／2,121 行／三週改 47 次的最大爆炸半徑
    "frontend/tabs/crm/crm-cashbook.js":
        "收支明細主畫面（清單＋編輯＋匯入三合一）",
    # 2026-09-08：掃描擴到 .html 才發現兩頁早就越線了（「整支 SPA 寫在單一 <script> 裡」的獨立頁）。
    # frontend/my.html 2026-09-09 拆到 frontend/js/my/（掃描測試改讀 _srcscan.my_page_src()）；
    # frontend/showcase-edit.html 2026-09-12 拆到 frontend/js/showcase-edit/ 六支（_srcscan.showcase_edit_src()）。
}


def _repo():
    return pathlib.Path(__file__).resolve().parents[2]


def _files():
    root = _repo()
    out = {}
    for d in SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            s = p.as_posix()
            # .html 也要掃：頁面把整支 SPA 寫在 <script> 裡是這個 repo 的常態，
            # 只掃 .py／.js 的話 my.html 就是這樣無聲越過 2,000 行的（2026-09-08 才發現）
            # .css 也要掃（2026-09-11 加）：crm.css 曾經長到 2,286 行而這裡全綠 ——
            # 樣式表一樣是 AI 要整支讀完才敢改的東西
            if p.suffix in (".py", ".js", ".html", ".css") and not any(x in s for x in SKIP):
                out[p.relative_to(root).as_posix()] = p
    for f in SCAN_FILES:
        p = root / f
        if p.exists():
            out[f] = p
    return out


def _lines(p):
    return len(p.read_text(encoding="utf-8", errors="replace").splitlines())


def test_the_scan_actually_covers_the_repo():
    """🔴 掃不到東西要當失敗。目錄改名或 SKIP 寫太寬之後，下面每一支都會
    安安靜靜地永遠綠燈，而它們要守的東西早就沒人看著了。"""
    files = _files()
    assert len(files) >= 400, f"只掃到 {len(files)} 個檔，掃描本身可能壞了"
    for must in ("main.py", "db/models/_crm.py", "frontend/app.js",
                 "core/finance_logic/_core.py", "routers/crm/finance.py"):
        assert must in files, f"掃描漏了 {must}"


def test_no_new_file_crosses_the_read_limit():
    over = {rel: _lines(p) for rel, p in _files().items()
            if rel not in EXEMPT and _lines(p) > READ_LIMIT}
    assert not over, (
        f"這些檔超過單次讀取上限 {READ_LIMIT} 行：{over}。\n"
        "拆檔的作法見 core/finance_logic/__init__.py 的檔頭（AST 呼叫圖 → 強連通\n"
        "分量 → 把界畫在既有分節註解上 ＝ 零函式搬家）。真的拆不動再進 EXEMPT，\n"
        "**連同「這個檔憑什麼例外」一起寫**。")


def test_the_exemption_list_has_no_dead_entries():
    """名單裡的檔要真的還在、而且真的還超標。

    改名或刪掉之後那個豁免會悄悄留著；拆完之後留著它，等於把好不容易拆下來的
    檔重新開放給無限成長。
    """
    files = _files()
    gone = sorted(rel for rel in EXEMPT if rel not in files)
    assert not gone, f"EXEMPT 裡這些檔不存在了，請移除：{gone}"
    fixed = sorted(rel for rel in EXEMPT if _lines(files[rel]) <= READ_LIMIT)
    assert not fixed, (
        f"這些檔已經在上限之內了，把它們從 EXEMPT 移除：{fixed}")


def test_report_where_the_pressure_is(capsys):
    """不是斷言，是把「快滿了」與「已豁免但還在長」印出來（`-s` 看得到）。"""
    files = _files()
    near = sorted(((_lines(p), rel) for rel, p in files.items()
                   if rel not in EXEMPT and WARN_AT < _lines(p) <= READ_LIMIT),
                  reverse=True)
    with capsys.disabled():
        if near:
            print(f"\n  [接近 {READ_LIMIT} 行上限]")
            for n, rel in near:
                print(f"    {n:5d}  {rel}")
        print("\n  [已豁免]")
        for rel in sorted(EXEMPT, key=lambda r: -_lines(files[r])):
            print(f"    {_lines(files[rel]):5d}  {rel}  — {EXEMPT[rel]}")


# ── 財務目錄額外守：那兩個檔是拆出來的，不准長回去 ──────────────

def test_the_finance_logic_package_has_no_import_cycles():
    """三塊之間只能單向：_core ← _statements ← _flows。

    有反向邊就是循環 import —— 那會在啟動時炸（不是靜默的），但它會把拆檔
    這件事變成不可逆：誰也不敢再動界線。所以在這裡先擋住。
    """
    import ast

    root = _repo() / "core" / "finance_logic"
    rank = {"_core": 0, "_statements": 1, "_flows": 2}
    for mod, i in rank.items():
        tree = ast.parse((root / f"{mod}.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module in rank:
                assert rank[n.module] < i, (
                    f"{mod}.py import 了 {n.module} —— 方向反了（會變循環 import）")


def test_the_split_finance_files_stay_split():
    """🔴 拆完的檔最容易的退化方式不是長回去，是**新東西全往同一塊塞**。

    四個 CRM 帳務檔與三個 finance_logic 子模組都要維持在上限之內；其中任何
    一個又越線，代表當初那條界畫錯了（或該再切一刀），不是把它加進 EXEMPT。
    """
    files = _files()
    split = [r for r in files if r.startswith(("routers/crm/", "core/finance_logic/"))]
    assert len(split) >= 10, f"財務那批只掃到 {len(split)} 個檔"
    over = {r: _lines(files[r]) for r in split if _lines(files[r]) > READ_LIMIT}
    assert not over, f"拆過的財務檔又超標了：{over}"
