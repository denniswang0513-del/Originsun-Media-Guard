# -*- coding: utf-8 -*-
"""連結私帳（owner 2026-08-29「費用要給王士源的，直接在私帳建立專案、同步收入」）。

公司把後期發給我做：公司那邊記成本、我這邊記收入。兩本帳各記各的 ——
不是重複計帳，也不是換帳本（那是隔壁的「推送至私帳」）。

生產庫實查（2026-08-29）：16 案 / 68 行 / 1,149,286 的成本行掛給我，
其中在私帳有分身的＝0。這顆按鈕要補的就是這 115 萬。
"""
from types import SimpleNamespace as NS

from core.ledger_project import (MIRROR_SOURCE, mirror_amount, mirror_detail,
                                 mirror_lines)
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

ME = "me-staff-id"
OTHER = "someone-else"


def _line(item, actual=None, est=None, who=ME, est_who=None, phase="後期製作"):
    return NS(item_name=item, phase=phase, actual_amount=actual,
              estimated_amount=est, actual_staff_id=who, estimated_staff_id=est_who)


def test_only_my_lines_count():
    """認人只認**實際**派給誰 —— 預估掛我、實際換人做的不是我的收入。"""
    got = mirror_lines([_line("剪輯", 20000),
                        _line("導演", 30000, who=OTHER),
                        _line("調光", 8000, who=None, est_who=ME)], ME)
    assert got["total"] == 28000
    assert got["split"] == {"剪輯": 20000, "調光": 8000}


def test_actual_wins_over_estimate():
    """實際優先、沒填才用預估（跟 CRM 其他地方同口徑）。"""
    assert mirror_amount(_line("剪輯", 18000, 20000)) == 18000
    assert mirror_amount(_line("剪輯", None, 20000)) == 20000
    assert mirror_amount(_line("剪輯")) == 0


def test_unfilled_lines_are_dropped():
    """🔴 兩欄都空＝這工項還沒發生，不能記成收入 ——
    把估算當收入記進去，私帳的營收就會領先現實（生產庫的
    「2026 泛亞工程空拍專案」正是這種，合計 None）。"""
    got = mirror_lines([_line("專案轉檔11月"), _line("剪輯", 0)], ME)
    assert got == {"lines": [], "split": {}, "total": 0}


def test_same_item_twice_is_summed():
    """🔴 同名工項要相加 —— 生產庫的 OMRON 那案就有兩行導演、兩行腳本。
    dict 直接指派會讓後面那行吃掉前面那行（少算一筆收入）。"""
    got = mirror_lines([_line("導演", 10000, phase="前期製作"),
                        _line("導演", 20000, phase="前期製作")], ME)
    assert got["split"] == {"導演": 30000}
    assert got["total"] == 30000 and len(got["lines"]) == 2


def test_mirror_detail_is_income_only():
    """鏡射案只帶收入分項與案源，成本欄全 0 —— 委外／代開費用是**我自己的**
    成本，公司管不著，建立時不猜。"""
    d = mirror_detail({"剪輯": 20000})
    assert d["split"] == {"剪輯": 20000}
    assert d["source"] == MIRROR_SOURCE == "源日"
    assert d["outsource"] == 0 and d["invoice_fee"] == 0 and d["misc"] == 0


def test_source_is_cash_receipt():
    """源日＝現金收款：不抽代辦費、不算源頭代扣。內部轉單本來就沒有那些
    （手動建的 109 個歷史案也都是這個值）。"""
    from core.ledger_project import apply_source_fee, client_wire
    d = mirror_detail({"剪輯": 46000})
    assert apply_source_fee(46000, d) == d          # 不動任何費用欄
    assert client_wire(46000, d) == 46000           # 公司匯給我的＝全額


# ── 端點守衛（讀原始碼斷言）──
# 🔴 一律經過 `code_only`：不剝註解的話，「這個函式**不可以**碰 p.contract_amount」
# 這種說明文字自己就會讓 not-in 斷言變綠（_srcscan 檔頭記的那個坑）。
def _src():
    return repo_src("routers/crm/projects.py")


def _fn(src: str, name: str) -> str:
    return code_only(func_body(src, f"async def {name}("))


def test_both_endpoints_are_gated_on_the_private_ledger():
    """只有私帳 full scope 的帳號能按（跟搬帳本同一道門）。"""
    src = _src()
    for name in ("check_project_mirror", "mirror_project_to_mine"):
        fn = _fn(src, name)
        assert 'require_entity(request, "mine", level="full")' in fn, name


def test_who_am_i_comes_from_the_single_resolver():
    """🔴 「我」＝帳號綁的 crm_staff，從 `core.identity` 現查（不寫死人名、
    也不信 JWT 裡的舊值 —— admin 重綁後要立刻生效）。"""
    src = code_only(_src())
    assert "resolve_current_staff" in src
    assert "999be75c" not in src, "不可以把人的 staff id 寫死在程式裡"


def test_linking_twice_is_blocked():
    """雙擊／兩個分頁／重送 —— 一案只能有一個分身，否則收入記兩次。"""
    src = _src()
    assert "source_project_id == project_id" in src
    fn = _fn(src, "mirror_project_to_mine")
    assert "_mirror_blocked_reason" in fn and "409" in fn


def test_linking_existing_keeps_my_own_costs():
    """🔴 連結既有案只覆蓋收入那半邊（split + contract_amount）。
    整包寫回去會把他在私帳填的委外／代開費用洗成 0 ——
    那是 v2.0 就咬過的「整包 model_dump 寫回」同一顆雷。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    seg = fn.split("if target_id:")[1]
    assert "keep = norm_detail(t.ledger_detail)" in seg
    assert 'keep["split"], keep["source"] = mir["split"], MIRROR_SOURCE' in seg
    assert "t.ledger_detail = keep" in seg


def test_mine_cache_is_invalidated():
    """新建的是私帳案 —— is_mine_project 的 60 秒 id-set 快取要清，
    否則這一分鐘內它還被當成母公司的（同 move-ledger 的理由）。"""
    assert "invalidate_mine_projects()" in _fn(_src(), "mirror_project_to_mine")


def test_the_parent_project_is_not_touched():
    """母公司那一列一個欄位都不動 —— 它跟客戶的合約與成本都還在原地。
    只要有人往 `p.` 寫東西，這條就會亮。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    body = fn.split("async with factory() as session:")[1]
    for bad in ("p.entity =", "p.contract_amount =", "p.crm_pushed =",
                "p.ledger_detail =", "p.updated_at ="):
        assert bad not in body, bad


def test_new_mirror_row_goes_through_the_single_create_path():
    """建立走 `new_ledger_project`（帳本新增專案的單一正本）——
    自己 insert 一份 CrmProject 就會漏掉 amount_receivable 初始化，
    那案永遠不進應收帳款（owner 2026-08-26 實測過的坑）。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    assert "new_ledger_project(" in fn
    # 連結既有那條路換掉了營收 → 應收要一起重算（同一支 resync_receivable）
    assert "resync_receivable(t, keep)" in fn
    # `new_ledger_project` 自己有沒有初始化應收，由 test_project_entity_wall
    # 的 test_new_and_edited_cases_land_in_receivable 擁有 —— 這裡再抄一份，
    # 就是兩個檔案要一起改


def test_button_only_shows_for_parent_projects():
    """已經搬到私帳的案子沒有「公司付給我」這回事 —— 按鈕不畫。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-detail.js"))
    seg = js.split("actions.innerHTML")[1].split("crm-detail-close")[0]
    assert "_mine && _toMine" in seg and "proj-mirror-mine" in seg
