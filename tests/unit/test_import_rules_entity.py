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


# ── 匯入預覽的欄位與側欄（owner 2026-08-30）──────────────────

def test_columns_appear_only_when_this_book_has_that_thing():
    """🔴 「沒有貸款就不要貸款期別」「發票文字可以拿掉」—— 判斷用**這本帳
    有沒有那種東西**，不是寫死「私帳沒有」。生產實查：貸款 5 筆全在母公司、
    發票 401 張全在母公司，私帳各 0；他哪天真的去借款或開發票，欄位要自己回來。
    """
    js = js_code_only(repo_src(JS))
    assert "_stmtHasLoans = () => !!((_stmtPreview || {}).loan_count || 0)" in js
    fn = js.split("function _stmtAllocLabel(")[1].split("\nfunction ")[0]
    assert "d.invoices" in fn and "d.payment_requests" in fn
    assert "'請款單'" in fn and "'發票'" in fn
    py = code_only(func_body(repo_src(STMT), "async def _build_statement_preview("))
    assert '"loan_count": len(loans)' in py


def test_a_rule_can_target_any_depth_of_the_tree():
    """🔴 規則原本只存 category（路徑前兩層的鏡射），第三層以後表達不出來 ——
    而私帳有 3,346 筆收支就掛在第三層（2026-08-30 實查），正是最需要自動分類的
    那一批。所以規則多帶 `taxonomy_node_id`。"""
    m = repo_src("db/models.py")
    seg = m.split("class BankImportRule(")[1].split("\nclass ")[0]
    assert "taxonomy_node_id = Column(String(32), nullable=True)" in seg
    assert "taxonomy_node_id VARCHAR(32)" in repo_src("main.py")
    # 分類器把節點一起回；一律三元組（條件式回傳會讓呼叫端得猜拿到幾個值）
    cls = code_only(func_body(repo_src("core/bank_statement.py"), "def _classify("))
    assert 'return cat, direction, (r[4] if len(r) > 4 else "")' in cls
    assert 'return "", 0, ""' in cls
    # 套用到未歸類的歷史列時也要寫節點（只寫 category 的話，樹狀篩選看不到）
    fn = code_only(func_body(repo_src(STMT), "async def apply_rules_to_unclassified("))
    assert "e.taxonomy_node_id = node" in fn


def test_private_only_subviews_are_hidden_in_the_parent_book():
    """owner 2026-08-30：「crm 系統裡頭不用出現 家用、證券投資」「器材清單這些
    清單是私帳的，跟母公司沒關係」。這些整個是私帳的東西，不是換個帳本看同一份
    資料 —— 母公司模式整項不出現（有 finance_mine 也一樣）。"""
    fin = js_code_only(repo_src("frontend/tabs/finance/finance.js"))
    assert "if (!mineMode || !((window._modules || []).includes('finance_mine')))" in fin
    html = repo_src("frontend/tabs/finance/finance.html")
    for sub in ("household", "securities", "gear", "receivable"):
        seg = html.split(f'data-subview="{sub}"')[0].rsplit("<button", 1)[1]
        assert "fin-nav-mine-only" in seg, sub
