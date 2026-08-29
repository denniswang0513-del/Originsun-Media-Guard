# -*- coding: utf-8 -*-
"""逐案損益：CRM 專案帳目撐起來的兩個費用欄（core.ledger_project.apply_crm_costs）。

算式本身（實收／檢查／案源費率）釘在 test_ledger_source_rules.py。
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    """讀 repo 內的原始碼（跟 cwd 無關 —— tests/unit 的慣例）。"""
    return (_ROOT / rel).read_text(encoding="utf-8")

# ── CRM 專案帳目 → 逐案損益的兩個費用欄（owner 2026-08-28，規則 B2）────
def test_crm_costs_add_up_with_the_manual_ones():
    """甲案（owner 2026-08-28 拍板）：**CRM 合計 ＋ 手填那幾筆**，兩邊都算。

    先前是 B2「有 CRM 就用它」＝覆蓋，那讓「CRM 上沒有、在私帳這邊手動加的
    委外」在有成本行的案子上憑空消失。"""
    from core.ledger_project import apply_crm_costs

    manual = {"misc": 0, "outsource": 7777, "personal_tax": 300}
    # CRM 沒資料 → 手填原樣、沒有來源標記
    d, src = apply_crm_costs(manual, None)
    assert d["outsource"] == 7777 and src == {}
    d, src = apply_crm_costs(manual, {"misc": 0, "outsource": 0})
    assert d["outsource"] == 7777 and src == {}, "0 等於沒資料"
    # CRM 有資料 → 相加，並把兩邊各是多少交給前端拆開顯示
    d, src = apply_crm_costs(manual, {"misc": 1687, "outsource": 12000})
    assert (d["misc"], d["outsource"]) == (1687, 7777 + 12000)
    assert src == {"misc": {"crm": 1687, "manual": 0},
                   "outsource": {"crm": 12000, "manual": 7777}}
    assert d["personal_tax"] == 300, "只碰那兩欄"
    assert manual["outsource"] == 7777, "不可就地改到呼叫端的 dict"


def test_only_the_manual_part_is_persisted():
    """🔴 落庫的只有**手填**那部分，CRM 合計是讀取時加上去的。

    前端那格放的也是手填值 —— 放合計就會在存檔時把 CRM 算出來的數字存成一份
    會走味的副本（CRM 那邊改了、這裡停在舊數字）。兩端要講同一件事。"""
    src = _read("routers/api_finance_projects.py")
    put = src[src.index("async def update_project_ledger"):]
    put = put[:put.index("await session.commit()")]
    assert "crm_now = (await _crm_costs(session, ent, project_id))" in put
    js = _read("frontend/tabs/finance/subviews/projects.js")
    render = js[js.index("const costRows ="):js.index("const splitRows =")]
    assert "from.manual" in render, "輸入框放手填那部分"
    assert "from.crm" in render, "CRM 那部分要看得見（標籤與合計小字）"


def test_people_cost_excludes_the_admin_misc_phase():
    """人員費用排除 `phase='行政雜支'` 的成本行 —— 那個階段跟
    crm_project_expenses 講的是同一件事，兩邊都算就是重複計算。"""
    src = _read("routers/api_finance_projects.py")
    fn = src[src.index("async def _crm_costs"):src.index("async def _rollups")]
    assert 'CrmProjectCostLine.phase != "行政雜支"' in fn
    # 兩張表都要用專案的 entity 圈範圍（它們自己沒有 entity 欄）
    assert "CrmProject.entity == ent" in fn
    # 單案讀取要能收斂成索引查詢，不為了兩個數字掃全帳本
    assert "_pid_where(" in fn


# ── 私帳專案的雜支不跟公司請款（owner 2026-08-28）──────────────────
def test_admin_misc_excludes_company_reimbursed():
    """C-1：跟公司請過款的花費**不算私帳成本**。

        自己吸收 500        → 私帳成本 500
        跟公司請款拿回 500  → 私帳成本 0（錢是公司出的）

    不排除的話逐案損益會憑空多一筆他沒承擔的成本、實收被壓低。
    """
    src = _read("routers/api_finance_projects.py")
    fn = src[src.index("async def _crm_costs"):src.index("async def _rollups")]
    assert "CrmProjectExpense.claim_id.is_(None)" in fn


def test_mine_project_can_never_be_claimed_from_the_company():
    """C-2：**私帳的專案不可能跟公司請款**（owner 2026-08-28 原話）。

    🔴 一條規則、**沒有例外** —— 連收支明細「源日請款」推來的也擋。
    （做過一版留了源日請款的例外，當天被 owner 收回。別再加回去。）

    兩道：源頭不帶私帳專案（`_petty_project_id`），送出再擋一次。
    """
    src = _read("routers/crm/petty.py")
    fn = src[src.index("async def _assert_not_mine_project"):
             src.index("async def _petty_project_id")]
    assert 'CrmProject.entity == "mine"' in fn
    assert "status_code=409" in fn
    assert "CrmCashEntry.expense_id" not in fn, "不可再放行源日請款來的"
    # 守衛掛在送出那條路上
    sub = src[src.index("async def _submit_for"):src.index("aps = await _build_aps")]
    assert "_assert_not_mine_project(session, rows)" in sub


def test_petty_push_does_not_carry_a_mine_project():
    """源頭：收支明細推去零用金時不帶私帳專案 —— 否則使用者會先建好一張
    註定送不出去的單據。資訊沒掉：收支列那邊的專案連結還在。"""
    src = _read("routers/crm/petty.py")
    fn = src[src.index("async def _petty_project_id"):src.index("async def _submit_for")]
    assert 'p.entity == "mine"' in fn and "return None" in fn
    push = src[src.index("async def _push_from_cash"):]
    assert "await _petty_project_id(" in push, "推送那條路要用它"


def test_outsource_has_two_writers_and_both_say_so():
    """🔴 `outsource` 同時被兩個地方寫：CRM 成本行（apply_crm_costs，覆蓋顯示值）
    與專案外包請款單（_apply_outsource，增量累加）。

    目前無實例（私帳專案有成本行的＝0），但這是 owner 要拍板的語意問題。
    這個測試只確保**兩邊的註解都指向對方** —— 下一個讀到其中一邊的人不會以為
    自己是唯一的寫入者，然後「順手統一」掉另一邊。
    """
    lp = _read("core/ledger_project.py")
    fin = _read("routers/crm/finance.py")
    assert "_apply_outsource" in lp, "apply_crm_costs 要指向另一個寫入者"
    assert "apply_crm_costs" in fin[fin.index("async def _apply_outsource"):
                                   fin.index("def _mine_or_admin_write")]
