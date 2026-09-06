"""RBAC 模組清單跨語言同步 — 前端是 vanilla JS 沒有 build step，抄不掉，
所以正本在 Python、前端各留一份鏡射，由這裡用 regex 從原始碼比對。漏改任何
一處，這裡會 fail。

  1. core/auth.py ALL_MODULES               — 後端 source of truth
  2. frontend/js/shared/tab-config.js       — PERMISSION_GROUPS（前端分組/可見性）
  3. frontend/js/admin/user-mgmt.js         — MODULE_LABELS（權限編輯器中文標籤）
  4. core/auth.py TAB_ACCESS                — 誰進得去某個 tab（閘門本身的參數）
     ↔ tab-config.js TAB_EXTRA_ACCESS       — 同一件事的前端鏡射
"""
import re
from pathlib import Path

from core.auth import ALL_MODULES, TAB_ACCESS, tab_modules

_ROOT = Path(__file__).resolve().parents[2]
_TAB_CONFIG = "frontend/js/shared/tab-config.js"


def _js(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _js_body(const: str, path: str = _TAB_CONFIG) -> str:
    """JS 常數的值（`X = ` 到句末 `;` 之間）。

    不去配對外層的 {} / []：下面每個消費者都只在這段裡再抓自己要的東西，
    留著外層括號完全無妨，而「要嘛大括號要嘛中括號」寫成字元類別（還要在
    f-string 裡跳脫大括號）是這個檔最難讀的一行，還會接受頭尾不成對的東西。
    """
    m = re.search(rf"{const}\s*=\s*(.*?);\n", _js(path), re.DOTALL)
    assert m, f"{path} 找不到 {const} 定義（寫法改了？）"
    return m.group(1)


def _js_keys(const: str, path: str = _TAB_CONFIG) -> set:
    """JS 物件常數的鍵集合。"""
    return set(re.findall(r"(\w+)\s*:", _js_body(const, path)))


def _tab_config_modules() -> set:
    """抽 PERMISSION_GROUPS 每個 group 的 modules 陣列內容。"""
    keys = set()
    for arr in re.findall(r"modules:\s*\[([^\]]*)\]", _js_body("PERMISSION_GROUPS")):
        keys.update(re.findall(r"'([a-z_]+)'", arr))
    return keys


def _module_labels_keys() -> set:
    return _js_keys("MODULE_LABELS", "frontend/js/admin/user-mgmt.js")


def test_tab_config_matches_backend():
    assert _tab_config_modules() == set(ALL_MODULES), (
        "tab-config.js PERMISSION_GROUPS 與 core/auth.py ALL_MODULES 不同步"
    )


def test_module_labels_matches_backend():
    assert _module_labels_keys() == set(ALL_MODULES), (
        "user-mgmt.js MODULE_LABELS 與 core/auth.py ALL_MODULES 不同步 — "
        "缺 key 的模組在權限編輯器會顯示裸 key 而非中文標籤"
    )


def test_no_duplicate_modules_across_groups():
    all_keys = []
    for arr in re.findall(r"modules:\s*\[([^\]]*)\]", _js_body("PERMISSION_GROUPS")):
        all_keys.extend(re.findall(r"'([a-z_]+)'", arr))
    assert len(all_keys) == len(set(all_keys)), "同一模組出現在多個權限群組"


def test_tab_extra_access_matches_backend():
    """🔴 前端拿 TAB_ACCESS 決定 tab 看不看得見，所以那份鏡射要一字不差。

    漂掉的症狀不對稱、兩邊都難發現：前端**少**一個模組 → 那個人打 API 進得去、
    畫面上卻沒有那個 tab；前端**多**一個 → 側欄畫得出來，點進去每支請求 403。
    """
    front = {k: tuple(re.findall(r"'([^']+)'", v))
             for k, v in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]",
                                    _js_body("TAB_EXTRA_ACCESS"))}
    # 前端只列「額外」的，後端列完整名單 —— 把同名那個補回去再比。
    # 方向刻意是「前端 → 後端」：反過來（從後端濾掉同名那個）的話，後端某列
    # 忘了列自己的模組也會比成相等，而那正是要抓的漂移之一。
    assert {k: (k, *extra) for k, extra in front.items()} == TAB_ACCESS, \
        f"TAB_EXTRA_ACCESS 與 core/auth.TAB_ACCESS 不同步：{front} vs {TAB_ACCESS}"


# 掃到但**不是 tab 級的門** —— 是某幾支端點自己放寬，tab 本身沒有跟著開。
# 列在這裡是要逼人做決定（是漏改正本，還是真的只放寬這幾支端點），而不是
# 讓掃描默默漏掉。
_NOT_TAB_GATES = {
    ("crm_invoices", "crm_projects"):
        "api_cashflow：付款節點是專案側也要看的子功能，整個財務管理 tab 沒開放",
    ("crm_projects", "preprod_proposals"):
        "crm/proposal_assets：提案資產上傳（提案庫 tab 的按鈕），不是專案管理 tab 的門",
    ("timesheets", "me_finance"):
        "timesheets/project_options：專案下拉也給員工頁（只有 me_finance 的人拿到含本人最近填過的），不是工作追蹤 tab 的門",
}


def test_tab_access_matches_the_real_router_gates():
    """🔴 把「單一正本」從宣告變成事實 —— 掃 routers/ 比對真閘門。

    沒有這支的話，`TAB_ACCESS` 只是**第三份**清單：它剛落地時就已經漏了五個
    tab（portal / preprod_locations / footage / equipment / intel），而漏掉的
    後果是工作流的燈對進得去的人說「你沒有這個模組權限」、側欄也不給那個 tab。
    下一個在 router 裡直接寫 `check_admin_or_module(request, "a", "b")` 的人
    會在這裡被擋下來。
    """
    tabs = _js_keys("TAB_MAP")
    gates, root = {}, _ROOT / "routers"
    pat = re.compile(r"check_admin_or_module\(\s*request,\s*((?:[\"'][a-z_]+[\"']\s*,?\s*)+)\)")
    for path in sorted(root.rglob("*.py")):
        for raw in pat.findall(path.read_text(encoding="utf-8")):
            keys = tuple(re.findall(r"[\"']([a-z_]+)[\"']", raw))
            # 只看「第一個 key 是某個 tab」的閘門 —— 那才是 tab 級的門。
            # 單 key 的不算漂移（tab_modules 的預設就是它）。
            if len(keys) > 1 and keys[0] in tabs:
                gates.setdefault(keys[0], set()).add(keys)

    stray = {tab: sorted(v - {tab_modules(tab)} - _NOT_TAB_GATES.keys())
             for tab, v in gates.items()}
    stray = {t: v for t, v in stray.items() if v}
    assert not stray, (
        "這些 tab 的閘門沒有走 core.auth.tab_modules（或與 TAB_ACCESS 不一致）："
        f"{stray}；正本 = {TAB_ACCESS}。"
        "若它其實是端點級放行而不是 tab 級的門，加進 _NOT_TAB_GATES 並寫下理由。")


def test_flow_destinations_are_real_tabs():
    """工作流「去完成」的目的地都要在 TAB_MAP 裡有 tab。

    前端拿 `TAB_MAP[模組鍵]` 換 section id —— 換不到就**靜默不畫連結**，
    畫面上不會有任何錯誤，只是那盞燈永遠帶不了人去做那件事。
    住這裡是因為它同樣是「Python 常數 vs tab-config.js」的比對。
    """
    from core.project_flow import DESTS
    missing = DESTS - _js_keys("TAB_MAP")
    assert not missing, f"這些目的地在 TAB_MAP 裡沒有 tab：{sorted(missing)}"
