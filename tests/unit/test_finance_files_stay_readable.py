# -*- coding: utf-8 -*-
"""財務的檔案要維持「一次讀得完」。

這條規則本來只寫在 CLAUDE.md 規則 B 裡（「超過 500 行強制分段讀」「2,000 行
是硬上限，超過的部分會被截斷 —— AI 不會告訴你它沒看完」），沒有任何東西在
守它。結果 2026-08-30 體檢時發現 `routers/crm/finance.py` 2,996 行、
`core/finance_logic.py` 2,548 行，兩個都是三週內改動最頻繁的財務檔 ——
也就是**最常被半盲修改**的兩個檔。

超過上限不會噴錯。它的症狀是「AI 改壞了旁邊那半個它沒讀到的檔」，
而那看起來就像一次普通的失誤。所以這裡把它變成會紅的東西。

門檻取 2,000（單次讀取上限）而不是更小的數字：這是**硬事實**，不是風格偏好。
真的需要放寬時，連同「為什麼這個檔非得這麼大」一起寫進 EXEMPT。
"""
import pathlib

READ_LIMIT = 2000
WARN_AT = 1600      # 逼近上限就該規劃切法了，不要等到超過

ROOTS = ("core/finance_logic", "routers/crm", "services")
EXTRA = ("core/crm_logic.py", "core/money.py", "core/ledger.py",
         "core/cash_taxonomy.py", "routers/api_finance.py",
         "routers/api_finance_stmt.py", "services/finance_statements.py")

EXEMPT = {
    # 還沒切、且不在這一輪範圍內的。要加東西進來，先問「這個檔憑什麼例外」。
}


def _repo():
    return pathlib.Path(__file__).resolve().parents[2]


def _finance_files():
    root = _repo()
    seen = {}
    for r in ROOTS:
        d = root / r
        if d.is_dir():
            for p in d.glob("*.py"):
                seen[p.relative_to(root).as_posix()] = p
    for rel in EXTRA:
        p = root / rel
        if p.exists():
            seen[rel] = p
    return seen


def _lines(p):
    return len(p.read_text(encoding="utf-8").splitlines())


def test_the_scan_actually_finds_the_finance_files():
    """🔴 掃不到東西要當失敗。目錄改名之後這支會安安靜靜地永遠綠燈。"""
    files = _finance_files()
    assert len(files) >= 20, f"只掃到 {len(files)} 個財務檔，掃描本身可能壞了"
    assert "core/finance_logic/_core.py" in files
    assert "routers/crm/finance.py" in files


def test_no_finance_file_exceeds_what_can_be_read_in_one_pass():
    over = {rel: _lines(p) for rel, p in _finance_files().items()
            if rel not in EXEMPT and _lines(p) > READ_LIMIT}
    assert not over, (
        f"這些檔超過單次讀取上限 {READ_LIMIT} 行：{over}。"
        "拆檔的作法見 core/finance_logic/__init__.py 的檔頭 —— 先用 AST 建呼叫圖、"
        "算強連通分量確認零環，再把界畫在既有分節註解上，就能做到零函式搬家。")


def test_report_the_files_that_are_getting_close(capsys):
    """不是斷言，是把「快滿了」的檔印出來（-s 看得到）。"""
    near = sorted(((_lines(p), rel) for rel, p in _finance_files().items()
                   if WARN_AT < _lines(p) <= READ_LIMIT), reverse=True)
    with capsys.disabled():
        for n, rel in near:
            print(f"\n  [接近上限] {rel} {n} 行（上限 {READ_LIMIT}）")


def test_the_finance_logic_package_has_no_import_cycles():
    """三塊之間只能單向：_core ← _statements ← _flows。

    有反向邊就是循環 import —— 那會在**啟動時**炸，不是靜默的，但它會把拆檔
    這件事變成不可逆（誰也不敢再動界線）。所以在這裡先擋住。
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
