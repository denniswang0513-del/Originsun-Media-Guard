# -*- coding: utf-8 -*-
"""請款單「項目」下拉的來源 —— 有會計對映的 ∪ 帳上在用的，由後端供。

owner 2026-08-24：「請款單項目希望有代收代付」。

代收代付的會計對映（payment → passthrough / 1100）**本來就在**，缺的只是前端那份
寫死的清單。而查下去發現那份清單往兩個方向都漂了（2026-08-24 生產實測）：

  · 有會計對映卻選不到 6 項：代收代付／代收薪資／代發薪資／勞報／後期雜支／現金代收
  · 🔴 **帳上已經有請款單在用、編輯視窗卻選不到自己** 2 項：勞報、後期雜支
      —— 那些單一打開編輯就會被迫改成別的項目
  · ⚠ 選得到卻沒有會計對映 1 項：獎金 —— 選了那筆錢會變成三表裡的「未歸類科目」
      （已補上 payment/獎金 → direct_expense 6120，比照收支那側）

同一個病咬過兩次：「專案外包」（歷史匯入 371/806 筆、46% 的最大宗類別當時不在
清單裡）、收支明細（寫死 27 項少 5 項）。所以這次不是加一個字串，是把清單搬到
後端 —— 前端那份降級成斷線時的 fallback。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

SRC = "routers/crm/finance.py"
JS = "frontend/tabs/crm/crm-payments.js"


# ── 後端才是正本 ──────────────────────────────────────────────

def test_the_options_endpoint_serves_the_category_list():
    body = code_only(func_body(repo_src(SRC), "async def payment_options("))
    assert '"categories"' in body, "/payments/options 沒有供項目清單"


def test_it_reads_both_sources():
    """讀對映、也讀帳上在用的。行為由下面那組假 session 的測試驗 ——
    這條只釘「讀的是 payment 那一側的對映」（讀成 cash 的話會多出營業稅之類的）。"""
    body = code_only(func_body(repo_src(SRC), "async def _payment_categories("))
    assert 'FinanceCategoryMap.source == "payment"' in body, "讀錯來源"


# ── 行為：union 的兩半缺一不可 ────────────────────────────────
#
# 🔴 這組原本只有原始碼掃描（檢查那兩個查詢的字串在不在），而破壞驗證證明那擋不住
#    —— 把查詢留著、結果丟掉（used = []）就整個溜過去。只有真的跑一次才擋得住。

class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """兩次 execute：第一次是對映清單、第二次是帳上用量。"""

    def __init__(self, mapped, used):
        self._queue = [[(m,) for m in mapped], list(used)]

    async def execute(self, *_a, **_k):
        return _Res(self._queue.pop(0) if self._queue else [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


def _categories(mapped, used):
    import asyncio

    from routers.crm import finance as F
    orig = F._get_factory

    async def _fake_factory():
        return lambda: _FakeSession(mapped, used)

    F._get_factory = _fake_factory
    try:
        return asyncio.run(F._payment_categories("parent"))
    finally:
        F._get_factory = orig


def test_a_mapped_but_unused_category_shows_up():
    """代收代付就是這一種：對映早就有（passthrough/1100），但還沒有人用過 ——
    只看「帳上在用的」的話它永遠進不了下拉。"""
    out = _categories(mapped=["代收代付", "專案外包"], used=[("專案外包", 371)])
    assert "代收代付" in out, out


def test_a_used_but_unmapped_category_shows_up():
    """🔴 勞報、後期雜支就是這一種：帳上已經有請款單在用，卻不在對映裡 ——
    只看對映的話，那些單一打開編輯就選不到自己，會被迫改成別的項目。"""
    out = _categories(mapped=["專案外包"], used=[("專案外包", 371), ("勞報", 1)])
    assert "勞報" in out, out


def test_usage_order_wins_and_there_are_no_duplicates():
    out = _categories(mapped=["專案外包", "代收代付", "薪資"],
                      used=[("薪資", 99), ("專案外包", 371)])
    assert out[:2] == ["薪資", "專案外包"], f"沒照使用次數排：{out}"
    assert out.count("專案外包") == 1, f"兩個來源都有的重複了：{out}"
    assert "代收代付" in out, out


def test_blank_categories_are_dropped():
    """帳上有 category 為空的舊列 —— 空字串不該變成下拉裡的一個選項。"""
    out = _categories(mapped=["專案外包", ""], used=[(None, 5), ("", 3), ("專案外包", 1)])
    assert "" not in out and None not in out, out


def test_inactive_mappings_do_not_show_up():
    """對映可以在後台停用 —— 停用的不該還出現在下拉裡。"""
    body = code_only(func_body(repo_src(SRC), "async def _payment_categories("))
    assert "FinanceCategoryMap.active.is_(True)" in body, "沒過濾停用的對映"


def test_the_order_follows_actual_usage():
    """使用者天天選的那幾個不該被字母序推到下面（專案外包佔 371/806）。"""
    body = code_only(func_body(repo_src(SRC), "async def _payment_categories("))
    assert "order_by(sa_func.count().desc())" in body, "沒有照使用次數排"


def test_it_is_scoped_to_the_ledger():
    """兩本帳：我的帳用過的項目不該外洩到母公司的下拉，反之亦然。"""
    body = code_only(func_body(repo_src(SRC), "async def _payment_categories("))
    assert "CrmPaymentRequest.entity == entity" in body, "沒有依帳本過濾"
    caller = code_only(func_body(repo_src(SRC), "async def payment_options("))
    assert "_payment_categories(ent)" in caller, "算了帳本卻沒傳進去"


# ── 前端只是 fallback ─────────────────────────────────────────

def test_the_frontend_list_is_a_fallback_not_the_source():
    src = js_code_only(repo_src(JS))
    assert "let _CATEGORIES" in src, "還是 const —— 後端供的清單蓋不上去"
    assert "_CATEGORIES = o.categories" in src, "沒有採用後端供的清單"


def test_options_are_loaded_before_the_first_render():
    """🔴 renderList() 會畫快速新增列，那一列的項目下拉直接讀 _CATEGORIES。
    跟 loadPayments() 平行跑的話，先到的通常是清單 —— 快速新增列就用 fallback
    畫出來了，而且不會再重畫。使用者看到的是一份過期的選單。"""
    body = js_code_only(func_body(repo_src(JS), "export async function initCrmPaymentsTab("))
    # 釘的是「那個 await 在那批平行載入之前」—— 不能只找第一個 loadPayments()，
    # 篩選列的事件處理器裡也有一個，那個不會在 init 時跑。
    assert "await _loadPayOptions();" in body, "沒有等選項到齊就往下走"
    i_opt = body.index("await _loadPayOptions();")
    i_all = body.index("await Promise.all([")
    assert i_opt < i_all, "選項還是跟清單平行載入"
    assert "_loadPayOptions()" not in body[i_all:], "選項又被塞回平行那批了"


def test_both_dropdowns_share_the_one_list():
    """快速新增列與編輯表單共用同一份 —— 兩份就會有一邊漏掉新項目。"""
    src = js_code_only(repo_src(JS))
    assert src.count("_CATEGORIES") >= 3, "清單的使用點少於預期，可能有人又抄了一份"
    for fn in ("function _buildEditFields(", "function _quickAddRow("):
        assert "_CATEGORIES" in js_code_only(func_body(repo_src(JS), fn)), \
            f"{fn} 沒有用共用的那份清單"
