# -*- coding: utf-8 -*-
"""對帳單匯入的分類：規則與值域都**按帳本分家**（owner 2026-08-29）。

「這裡的分類規則不需要和 crm 共用」＋「分類的填寫方式也需要和私帳同步，
不是用 crm 的」。兩本帳的類別值域根本不重疊：

    母公司 → finance_category_map 的平面科目（行政／薪資／交際應酬…）
    私帳   → cash_taxonomy_nodes 那棵樹（公司_專案／家用_變動支出…）

共用一份規則的下場是：私帳可以設出一條「→ 交際應酬」的規則，而私帳的報表
沒有那個類別 —— 那些列會靜靜落到「未歸類」，看起來卻像已經分好了。

既有 36 條全歸母公司（ALTER 的 DEFAULT 'parent' 就是回填），私帳從 0 開始
自己設 —— owner 拍板。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

STMT = "routers/api_finance_stmt.py"
JS = "frontend/tabs/finance/subviews/recon.js"


def _fn(name):
    return code_only(func_body(repo_src(STMT), name))


def test_the_rules_table_is_scoped_by_ledger():
    """欄位在、預設是 parent（既有 36 條的歸屬），索引也帶上它。"""
    m = repo_src("db/models.py")
    seg = m.split("class BankImportRule(")[1].split("\nclass ")[0]
    assert 'entity = Column(String(16), nullable=False, server_default="parent")' in seg
    assert '"ix_bankrule_acct", "entity"' in seg
    mig = repo_src("main.py")
    assert "ALTER TABLE bank_import_rules ADD COLUMN IF NOT EXISTS " in mig
    assert "entity VARCHAR(16) NOT NULL DEFAULT 'parent'" in mig


def test_every_rule_read_and_write_filters_by_ledger():
    """🔴 五個進出口全部要帶帳本。漏一個就是「私帳看得到母公司的規則」或
    「私帳的規則套不到自己的列上」，而且兩種都不會噴錯。"""
    assert "BankImportRule.entity == ent" in _fn("async def _load_import_rules(")
    assert "BankImportRule.entity == ent" in _fn("async def list_import_rules(")
    assert "entity=ent" in _fn("async def create_import_rule(")
    for name in ("async def update_import_rule(", "async def delete_import_rule("):
        assert "_owned_rule(session, rule_id, ent)" in _fn(name), name


def test_a_rule_from_the_other_ledger_cannot_be_touched():
    """query 的 entity 只驗得了「有沒有這本帳的權限」，驗不了「這一條是誰的」
    —— 規則會改變已匯入資料的分類，跨帳本改得到就是能動別人的帳。"""
    body = _fn("async def _owned_rule(")
    assert '(r.entity or "parent") != ent' in body and "403" in body


def test_the_preview_applies_this_ledgers_rules():
    """預覽那條路也要帶 —— 它才是規則真正生效的地方。"""
    assert "_load_import_rules(session, acct_id, ent)" in \
        _fn("async def _build_statement_preview(")


def test_the_category_whitelist_is_per_ledger():
    """規則只能填**這本帳**認得的類別。私帳的值域來自分類樹的路徑鏡射，
    不是整份 finance_category_map（那是兩本共用的）。"""
    body = _fn("async def _known_categories(")
    assert 'if ent != "mine":' in body and "return flat" in body
    assert "mirror_from_path(n[\"path\"])[0]" in body
    # 規則驗證與預覽的「未對映」提醒共用同一份
    assert "_known_categories(session, ent)" in _fn("async def _assert_known_category(")
    assert "mapped = await _known_categories(session, ent)" in \
        _fn("async def _build_statement_preview(")


# ── 填寫方式：私帳走分類樹 ────────────────────────────────────

def test_the_private_ledger_picks_a_tree_node_not_a_flat_string():
    """🔴 私帳的分類是一棵樹，選的是**節點**。只寫 category 字串的話，那一列
    沒有 taxonomy_node_id —— 收支明細的分類篩選與路徑顯示都看不到它。"""
    js = js_code_only(repo_src(JS))
    # 私帳那一格用的是**一排會長的下拉**（類別→項目→子項目…），跟收支明細
    # 同一支共用元件 —— owner 2026-08-30：單一下拉列完整路徑在這個窄欄裡
    # 每一項都被截成「公司 ▸ 薪水…」，七個選項長得一模一樣。
    assert "taxSelects(box, {" in js and "cash-tax-picker.js" in js
    fn = js.split("function _stmtTaxDraw(")[1].split("\nfunction ")[0]
    assert "opts.tree" in fn and "byId" in fn and "keepOne: true" in fn
    assert "taxonomy_node_id: x.taxonomy_node_id || null" in js, "apply 沒把節點送出去"


def test_the_mirror_rule_is_not_reimplemented_on_write():
    """寫入端用既有的 `_sync_taxonomy` 從節點推 category/item/sub_item ——
    在這裡自己拼第二份鏡射，就是那三欄開始漂的起點。"""
    body = _fn("async def apply_bank_statement(")
    assert "_sync_taxonomy(" in body and "taxonomy_node_id" in body


def test_the_category_list_is_cached_per_ledger():
    """🔴 原本是單一 `_cashCats` 且呼叫時沒帶 entity —— 後端預設 parent，
    於是私帳的匯入視窗永遠拿到母公司的類別（owner 2026-08-29 撞到的就是這個）。
    存成一個變數還會讓「先開母公司再切私帳」的人繼續看到上一本的清單。"""
    js = js_code_only(repo_src(JS))
    assert "const _cashOpts = {}" in js
    assert "_cashCats = " not in js, "還留著會被兩本帳共用的那個變數"
    fn = js.split("async function _ensureCashOpts(")[1].split("\nasync function")[0]
    assert "const ent = finEntity();" in fn
    assert "entity=' + encodeURIComponent(ent)" in fn
