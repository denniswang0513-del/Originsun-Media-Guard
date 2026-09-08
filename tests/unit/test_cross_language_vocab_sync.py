# -*- coding: utf-8 -*-
"""後端的值域清單，與它在前端那份抄不掉的鏡射，要逐值相等。

前端是 vanilla JS、沒有 build step，抄不到 Python 的常數，所以「同一份清單各留
一份」是必要之惡。問題從來不是鏡射本身，是**沒有東西會在它們漂開時出聲**：
2026-08-30 體檢掃出 45 組跨語言鏡射，把判準放到對測試最有利的程度（只要有測試
同時提到常數名和前端路徑就算），也只有 5 組沾得上；逐個打開看，真在做等值比對
的只有 `ALL_MODULES`（`test_rbac_module_sync.py`，全 repo 守得最好的一支）。

漂開的症狀一律是靜默的，而且不對稱：
  · 前端**少**一個 → 那個值在畫面上選不到，但帳上已經在用的列一打開編輯就
    被迫改成別的（「專案外包」身上咬過一次，當時是 46% 的最大宗類別）
  · 前端**多**一個 → 選得到，送上去被後端 422，或更糟：存進去了但沒有對映，
    那筆錢在三表裡變成「未歸類」

這裡照 `test_rbac_module_sync.py` 的作法，用 regex 從原始碼比對。

🔴 刻意**不**納入的：`routers/crm/petty.FALLBACK_ITEMS` 與 cashbook / payments
的 `_CATEGORIES`。那兩份前端清單在自己的註解裡就寫明是「斷線時的 fallback，
正本是後端 finance_category_map」—— 它們本來就該比後端那份常數長，寫等值斷言
會把「不是規格的東西」釘成規格。沒有真實不變式的地方就不要硬加測試。
"""
import re

from tests.unit._srcscan import repo_src


def _js_str_array(src: str, decl: str) -> list:
    """`... decl = [ 'a', 'b' ]` 的字串元素（照原順序）。"""
    m = re.search(rf"{decl}\s*=\s*\[([^\]]*)\]", src)
    assert m, f"找不到 {decl}（前端改寫法了？那就把這支測試改成新的取法，別刪掉它）"
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


def _js_obj_field(src: str, decl: str, field: str) -> list:
    """`decl = [ {field: 'a', …}, {field: 'b', …} ]` 裡每個物件的 field 值。"""
    m = re.search(rf"{decl}\s*=\s*\[([\s\S]*?)\n\];", src)
    assert m, f"找不到 {decl}"
    return re.findall(rf"\b{field}\s*:\s*['\"]([^'\"]+)['\"]", m.group(1))


# ── 財務：收支對映的處理方式 ────────────────────────────────────

def test_treatment_options_cover_exactly_the_backend_domain():
    """🔴 這是「這筆錢怎麼進報表」的值域。前端少一個 → 那種帳在編輯視窗裡
    選不到；多一個 → 存得進去但三表不認得它，那筆錢變成未歸類。"""
    from routers.api_finance import TREATMENTS
    got = _js_obj_field(repo_src("frontend/tabs/finance/fin-utils.js"),
                        "export const TREATMENT_OPTIONS", "v")
    assert set(got) == set(TREATMENTS), (
        "fin-utils.js TREATMENT_OPTIONS 與 api_finance.TREATMENTS 不同步："
        f"前端多了 {sorted(set(got) - set(TREATMENTS))}，"
        f"少了 {sorted(set(TREATMENTS) - set(got))}")
    assert len(got) == len(set(got)), "TREATMENT_OPTIONS 有重複的值"


# ── 專案管線 ───────────────────────────────────────────────────

def test_the_pipeline_order_matches_and_the_lost_status_sits_at_the_end():
    """前端的 STATUS_ORDER ＝ 後端 PIPELINE 再接上「未成案」。

    順序有意義（它決定看板欄位與排序），所以比的是 list 不是 set。
    """
    from core.project_flow import LOST, PIPELINE
    got = _js_str_array(repo_src("frontend/tabs/crm/crm-projects-state.js"),
                        "export const STATUS_ORDER")
    assert got == [*PIPELINE, LOST], (
        f"crm-projects-state.js STATUS_ORDER 與 core.project_flow.PIPELINE 不同步："
        f"{got} vs {[*PIPELINE, LOST]}")


def test_the_presale_statuses_are_a_prefix_of_the_pipeline():
    """成案前的那幾個階段是管線的前綴 —— 不是另一份自由的清單。

    漂開的後果：某個階段的案子在「成案前」的篩選與統計裡憑空消失或憑空出現，
    而兩邊的數字都看起來像真的。
    """
    from core.project_flow import PIPELINE
    got = _js_str_array(repo_src("frontend/tabs/crm/crm-projects-state.js"),
                        "export const PRESALE_STATUSES")
    assert got == list(PIPELINE[:len(got)]), (
        f"PRESALE_STATUSES 不是 PIPELINE 的前綴：{got} vs {list(PIPELINE)}")


# ── 人事：假別與狀態 ────────────────────────────────────────────

#: 找不到寫死清單時的兩條路：真的改成從後端字彙拿（放行），或只是改名／換寫法（要紅）。
#: 沒有這道檢查，這兩支測試會在前端一改寫法時靜默退場，跨語言同步等於沒在守。
_NO_LIST_MSG = ("hr_leave.js 找不到 {decl}，而且它也沒有從 /me/leave/summary 的 vocab 拿字彙。\n"
                "要嘛把清單改回可比對的字面陣列，要嘛真的改吃 summary.vocab —— 不能只是換個寫法讓這條測試消失。")


def _leave_js_reads_vocab() -> bool:
    """前端改成吃後端字彙（§7.1 的本意）：有讀 summary 的 vocab 就算數。"""
    src = repo_src("frontend/tabs/hr_leave/hr_leave.js")
    return "vocab" in src and ("summary" in src or "_sum" in src)


def _leave_js_list(decl: str, obj_keys: bool = False):
    """hr_leave.js 的 LEAVE_TYPES／STATUS_PILL；前端改成從 summary.vocab 拿（不寫死）時回 None。"""
    src = repo_src("frontend/tabs/hr_leave/hr_leave.js")
    if obj_keys:
        m = re.search(rf"{decl}\s*=\s*\{{([^}}]*)\}}", src)
        return re.findall(r"['\"]([^'\"]+)['\"]\s*:", m.group(1)) if m else None
    m = re.search(rf"{decl}\s*=\s*\[([^\]]*)\]", src)
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)) if m else None


def test_leave_types_match():
    """2026-09-07 假勤重整：後端字彙分兩份 —— core.hr_logic.LEAVE_TYPES（舊、鏡射用）⊂
    core.leave_logic.ALL_LEAVE_TYPES（新端點認的）。前端那份寫死的清單要**夾在兩者之間**：
    不能少一個舊值（帳上在用的列一打開就被迫改別的），也不能多一個後端不認的（送上去 422）。
    前端改成從 /me/leave/summary.vocab 拿時（§7.1 的本意）就沒有寫死清單可比，跳過。"""
    from core.hr_logic import LEAVE_TYPES
    from core.leave_logic import ALL_LEAVE_TYPES
    got = _leave_js_list("const LEAVE_TYPES")
    if got is None:
        assert _leave_js_reads_vocab(), _NO_LIST_MSG.format(decl="const LEAVE_TYPES")
        return
    assert set(LEAVE_TYPES) <= set(got) <= set(ALL_LEAVE_TYPES), (
        f"hr_leave.js 的假別要介於 hr_logic.LEAVE_TYPES 與 leave_logic.ALL_LEAVE_TYPES 之間：{got}")
    assert len(got) == len(set(got))


def test_leave_statuses_match_the_status_pill_map():
    """前端把狀態拿去查 CSS class —— 查不到就畫成沒有樣式的裸文字。同上：夾在舊 LEAVE_STATUSES 與新 REQUEST_STATUSES 之間。"""
    from core.hr_logic import LEAVE_STATUSES
    from core.leave_logic import REQUEST_STATUSES
    got = _leave_js_list("const STATUS_PILL", obj_keys=True)
    if got is None:
        assert _leave_js_reads_vocab(), _NO_LIST_MSG.format(decl="const STATUS_PILL")
        return
    assert set(LEAVE_STATUSES) <= set(got) <= set(REQUEST_STATUSES), (
        f"STATUS_PILL 的狀態要介於 hr_logic.LEAVE_STATUSES 與 leave_logic.REQUEST_STATUSES 之間：{sorted(got)}")


# ── 參考影片庫：八族分類 ────────────────────────────────────────

def test_reference_facets_match():
    """八族分類是 DB 欄位名 —— 前端漂掉的那一族，使用者填了會存不進去。"""
    from routers.api_references import FACET_KEYS
    got = _js_obj_field(repo_src("frontend/tabs/proposals/reference-page.js"),
                        "const FACETS", "key")
    assert got == list(FACET_KEYS), (
        f"reference-page.js FACETS 與 api_references.FACET_KEYS 不同步："
        f"{got} vs {list(FACET_KEYS)}")


# ── 個人工作台的模組鍵 ──────────────────────────────────────────

def test_me_module_keys_match_the_permission_group():
    """🔴 `tab-config.js` 的 'me' 群組同時也被 `test_rbac_module_sync` 拿去比
    `ALL_MODULES`，所以那邊只保證「這些鍵是合法模組」。這裡補的是另一半：
    **後端 api_me 認得的那幾把鍵，一把不多一把不少地出現在那個群組裡**。
    少一把 → 那張卡的權限勾不到；多一把 → 勾得到但工作台不會畫那張卡。
    """
    from routers.api_me import ME_MODULE_KEYS
    src = repo_src("frontend/js/shared/tab-config.js")
    m = re.search(r"\{\s*id:\s*'me'\s*,[^}]*modules:\s*\[([^\]]*)\]", src)
    assert m, "tab-config.js 找不到 'me' 那個權限群組"
    got = re.findall(r"'([^']+)'", m.group(1))
    assert set(got) == set(ME_MODULE_KEYS), (
        f"tab-config.js 的 me 群組與 api_me.ME_MODULE_KEYS 不同步："
        f"前端多了 {sorted(set(got) - set(ME_MODULE_KEYS))}，"
        f"少了 {sorted(set(ME_MODULE_KEYS) - set(got))}")


# ── 這支測試本身不能悄悄失效 ────────────────────────────────────

def test_the_scan_helpers_actually_find_things():
    """🔴 每個取法都靠 regex 命中前端的寫法。前端改寫法（換成物件、拆檔、
    改名）時，上面每一支都會 assert 「找不到」而不是默默通過 —— 但這支再多守
    一層：確認取出來的東西不是空的。空清單和空清單永遠相等。
    """
    from core.hr_logic import LEAVE_TYPES
    from core.project_flow import PIPELINE
    from routers.api_finance import TREATMENTS
    from routers.api_me import ME_MODULE_KEYS
    from routers.api_references import FACET_KEYS
    for name, seq in [("TREATMENTS", TREATMENTS), ("PIPELINE", PIPELINE),
                      ("LEAVE_TYPES", LEAVE_TYPES), ("FACET_KEYS", FACET_KEYS),
                      ("ME_MODULE_KEYS", ME_MODULE_KEYS)]:
        assert len(seq) >= 3, f"{name} 只剩 {len(seq)} 個值，正本本身可能壞了"
