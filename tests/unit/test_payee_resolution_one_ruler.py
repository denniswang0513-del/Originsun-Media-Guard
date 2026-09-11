# -*- coding: utf-8 -*-
"""「收款人那段字 → 人」只有一把尺：三個入口都要走 `routers.crm._shared.PayeeResolver`。

出納會把同一個人寫成「停車費 史丹」「志廷」「junior」。應付面板用代稱解析、
員工工作台卻用 `payee_name == 姓名` 字面比對的話，同一個人在一邊是一組、在另一邊
永遠看不到自己那幾筆 —— 而兩邊都不會報錯。所以這裡釘：三個入口都用同一支、
而且不准再有字面比對。
"""
import re

from tests.unit._srcscan import code_only, func_body, repo_src

ENTRY_POINTS = {
    "routers/crm/cash.py": "async def payables_summary(",
    "routers/crm/payouts.py": "async def create_payout(",
    "routers/api_me.py": None,          # 整支檔（那段在一個很長的 workspace 端點裡）
}


def test_every_entry_point_uses_the_shared_resolver():
    for path, header in ENTRY_POINTS.items():
        src = repo_src(path)
        body = code_only(func_body(src, header)) if header else code_only(src)
        assert "PayeeResolver" in body, f"{path} 沒有走 PayeeResolver"


def test_no_literal_payee_name_comparison_where_a_person_is_meant():
    """`payee_name == <某個姓名>` 這種字面比對＝那個人被寫成別的字就看不到。"""
    for path in ("routers/api_me.py", "routers/crm/payouts.py"):
        body = code_only(repo_src(path))
        hits = re.findall(r"payee_name\s*==\s*[A-Za-z_]", body)
        assert not hits, f"{path} 還有字面比對收款人：{hits}"


def test_group_payables_is_fed_resolved_staff_not_bank_columns():
    """cash.py 餵給 group_payables 的是 (payment, staff)，不是手抄的三個銀行欄位。"""
    body = code_only(func_body(repo_src("routers/crm/cash.py"), "async def payables_summary("))
    assert "who.resolve(p.payee_name)" in body
    assert "st.id_number if st else None" not in body, "回到舊的手抄四元組了"
