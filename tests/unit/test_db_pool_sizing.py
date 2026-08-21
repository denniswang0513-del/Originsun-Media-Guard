# -*- coding: utf-8 -*-
"""連線池大小按角色分（2026-08-21 那次 503 的根因）。

實測：NAS Postgres `max_connections=50`，常態 46 條在用 —— 機隊 9 台每台
握 5 條閒置（尖峰可到 15），加上 master、dev、NAS 官網容器，一有突發就
`asyncpg.TooManyConnectionsError: sorry, too many clients already`，
使用者在畫面上看到的是 503。

  🔴 master 要維持 5/10 —— 它服務整個 UI，縮了會變成排隊。
  🔴 其他一律小池，而且**任何例外都要落到小池**：猜錯方向要往「省連線」猜，
     猜成大池會把整個機隊一起拖垮。
"""
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "db/session.py"
UTILS = "frontend/js/shared/utils.js"


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


def test_master_keeps_the_big_pool():
    from db.session import _pool_sizes
    import db.session as m
    orig = None
    try:
        import core.topology as topo
        orig = topo.is_master_machine
        topo.is_master_machine = lambda: True
        assert _pool_sizes() == (5, 10)
        topo.is_master_machine = lambda: False
        assert _pool_sizes() == (2, 3)
    finally:
        if orig is not None:
            import core.topology as topo
            topo.is_master_machine = orig
    assert m is not None


def test_failure_falls_back_to_the_small_pool():
    """🔴 判定不出來時要省連線，不能預設成大池。"""
    import core.topology as topo
    from db.session import _pool_sizes
    orig = topo.is_master_machine
    try:
        def boom():
            raise RuntimeError("settings 讀不到")
        topo.is_master_machine = boom
        assert _pool_sizes() == (2, 3), "例外時落到大池 —— 會把機隊拖垮"
    finally:
        topo.is_master_machine = orig


def test_engine_actually_uses_it():
    """算出來要真的用上去 —— 只加一支函式不接線是白做的。"""
    body = _body("async def init_db(")
    assert "pool, overflow = _pool_sizes()" in body
    assert "pool_size=pool" in body and "max_overflow=overflow" in body
    assert "pool_size=5" not in body, "還寫死著 5"


def test_it_reuses_the_single_authority_for_master_detection():
    """判定 master 只有一個權威來源（core/topology）—— 另立第二套判準
    就會出現「兩邊答案不一樣」的節點。"""
    body = _body("def _pool_sizes(")
    assert "from core.topology import is_master_machine" in body
    for smell in ("master_server", "gethostname", "192.168"):
        assert smell not in body, f"自己又判了一次 master（{smell}）"


# ── 那句誤導的錯誤訊息 ────────────────────────────────────────────

def test_503_is_not_described_as_a_permission_problem():
    """🔴 「載入失敗（503）— 需要「福委會」與「金額檢視」權限」把人整整
    帶偏一輪（我自己就先去查權限了）。503 是資料庫，不是權限。"""
    js = repo_src(UTILS)
    body = js[js.index("export function tabLoadError("):]
    body = body[:body.index("\nexport function httpError")]
    assert "503" in body and "資料庫" in body
    assert "不是權限問題" in body
    # 「要求某個權限」這件事只能出現在 403 的分支裡。
    # （503 那句刻意寫「不是權限問題」，所以不能只找「權限」兩個字 ——
    #   我第一版就是這樣寫，被自己的斷言咬到。）
    i403 = body.index("status === 403")
    i503 = body.index("status === 503")
    assert body.index("沒有權限") > i403
    tail = body[i503:body.index("status >= 500")]
    for claim in ("需要「", "找管理員開通"):
        assert claim not in tail, f"503 竟然在要求權限（{claim}）"


def test_tabs_use_the_shared_helper_not_a_hardcoded_sentence():
    for path, need in (("frontend/tabs/hr_benefits/hr_benefits.js", "福委會"),
                       ("frontend/tabs/hr_leave/hr_leave.js", "請補修")):
        src = repo_src(path)
        assert "tabLoadError(" in src, f"{path} 沒有用共用 helper"
        assert f"— 需要「{need}" not in src, f"{path} 還寫死著「需要權限」"


def test_helper_covers_the_no_network_case():
    """status 0 ＝ 根本沒連上，講「載入失敗（0）」等於沒說。"""
    js = repo_src(UTILS)
    body = js[js.index("export function tabLoadError("):]
    assert "status === 0" in body and "連不到伺服器" in body
