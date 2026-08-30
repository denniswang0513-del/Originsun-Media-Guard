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
from tests.unit._srcscan import (call_args, code_only, func_body, js_code_only,
                                 js_func_body, repo_src)

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
    不是整份 finance_category_map（那是兩本共用的）。

    🔴 值域住在 `routers/crm/_shared`（`cash_category_texts` 隔壁）而不是散在各個
    router：問這個問題的有五處 —— 寫入時擋人、規則面板的下拉、匯入預覽的
    「科目未對映」提醒、收支明細的選項端點、卡單的 AI 建議白名單。修在消費端
    就得一個一個記得，而漏掉的那個不會噴錯：它給出一排存進去會落到「未歸類」
    的選項，畫面上看起來卻像分好了。"""
    body = code_only(func_body(repo_src("routers/crm/_shared.py"),
                               "async def ledger_category_domain("))
    assert 'if ent != "mine":' in body
    assert "cash_category_texts(session, ent)" in body,         "母公司走平面科目，而且那份也按帳本過濾（對映表 2026-08-30 起分家）"
    assert "mirror_from_path" in body and "path_map" in body, \
        "私帳的值域＝樹上每個節點鏡射出來的 category"
    # 五個問的人全部走它，沒有人自己讀兩本共用的那份平面科目
    for src, name in (
            (STMT, "async def _assert_known_category("),
            (STMT, "async def list_import_rules("),
            (STMT, "async def _build_statement_preview("),
            ("routers/crm/finance.py", "async def cash_entry_options("),
            ("routers/api_finance_card.py", "async def ai_suggest_categories(")):
        fn = code_only(func_body(repo_src(src), name))
        assert "ledger_categor" in fn, (src, name)
        assert "cash_category_texts" not in fn, (src, name)
    # 🔴 值域與畫面用的樹是一組（深淺不同：值域含停用節點、樹只放啟用的），
    # 兩個都要的地方走同一支 —— 各自寫一遍就是同一份契約散成兩份
    for src, name in ((STMT, "async def _build_statement_preview("),
                      ("routers/crm/finance.py", "async def cash_entry_options(")):
        fn = code_only(func_body(repo_src(src), name))
        assert "ledger_categories_and_tree(" in fn, (src, name)
        assert "load_nodes(" not in fn, (src, name)
    # 規則面板畫的是規則端點自己回的值域，不是收支明細那份快取
    fn = js_func_body(js_code_only(repo_src(JS)), "function _rulesRender(")
    assert "_ruleCats" in fn and "_cashOpts" not in fn and "_opts()" not in fn
    # 卡單匯入套的也要是這本帳的規則（漏傳 ent ＝ 拿母公司的規則分私帳的卡費）
    card = code_only(func_body(repo_src("routers/api_finance_card.py"),
                               "async def preview_card_statement("))
    assert call_args(card, "_load_import_rules")[0][-1] == "ent"


# ── 填寫方式：私帳走分類樹 ────────────────────────────────────

def test_the_private_ledger_picks_a_tree_node_not_a_flat_string():
    """🔴 私帳的分類是一棵樹，選的是**節點**。只寫 category 字串的話，那一列
    沒有 taxonomy_node_id —— 收支明細的分類篩選與路徑顯示都看不到它。"""
    js = js_code_only(repo_src(JS))
    # 私帳那一格用的是**一排會長的下拉**（類別→項目→子項目…），跟收支明細
    # 同一支共用元件 —— owner 2026-08-30：單一下拉列完整路徑在這個窄欄裡
    # 每一項都被截成「公司 ▸ 薪水…」，七個選項長得一模一樣。
    assert "taxSelects(box, {" in js and "cash-tax-picker.js" in js
    fn = js_func_body(js, "function _stmtTaxInto(")
    assert "opts.tree" in fn and "byId" in fn and "keepOne: true" in fn
    assert "taxonomy_node_id: x.taxonomy_node_id || null" in js, "apply 沒把節點送出去"


def test_the_mirror_rule_is_not_reimplemented_on_write():
    """寫入端用既有的 `_sync_taxonomy` 從節點推 category/item/sub_item ——
    在這裡自己拼第二份鏡射，就是那三欄開始漂的起點。"""
    body = _fn("async def apply_bank_statement(")
    assert "_sync_taxonomy(" in body and "taxonomy_node_id" in body
    # 🔴 而且要帶算好的路徑表 —— 不帶的話一張 52 列的對帳單就是 52 趟撈整棵樹
    assert all(len(a) >= 4 for a in call_args(body, "_sync_taxonomy"))
    # 🔴 卡單那條路也是（它是最後一個自己來的寫入端）：自己 `split("_", 1)` 是
    # core.cash_taxonomy.split_category 的第三份，而不掛節點寫出來的是
    # 「有類別、不在樹上」的孤兒 —— 收支明細的樹狀篩選看不到那些列。
    card = code_only(func_body(repo_src("routers/api_finance_card.py"),
                               "async def apply_card_statement("))
    assert 'split("_"' not in card, "拆類別的規則只有 core.cash_taxonomy 一份"
    assert all(len(a) >= 4 for a in call_args(card, "_sync_taxonomy"))


def test_the_category_list_is_cached_per_ledger():
    """🔴 原本是單一 `_cashCats` 且呼叫時沒帶 entity —— 後端預設 parent，
    於是私帳的匯入視窗永遠拿到母公司的類別（owner 2026-08-29 撞到的就是這個）。
    存成一個變數還會讓「先開母公司再切私帳」的人繼續看到上一本的清單。"""
    js = js_code_only(repo_src(JS))
    assert "const _cashOpts = {}" in js
    assert "_cashCats = " not in js, "還留著會被兩本帳共用的那個變數"
    fn = js_func_body(js, "async function _ensureCashOpts(")
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
    fn = js_func_body(js, "function _stmtAllocLabel(")
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
    # 套用到未歸類的歷史列時，**兩個分支**都走 `_sync_taxonomy` —— 節點與三欄的
    # 一致性只有那一份規則。只帶 category 的規則自己寫 `e.category = cat` 的話，
    # 那一列變成「有類別、不在樹上」的孤兒：樹狀篩選看不到，畫面上卻像分好了。
    fn = code_only(func_body(repo_src(STMT), "async def apply_rules_to_unclassified("))
    assert "e.taxonomy_node_id =" not in fn, "節點與三欄不可以在這裡自己寫"
    calls = call_args(fn, "_sync_taxonomy")
    by_kind = {("node" if "taxonomy_node_id" in a[2] else "cat"): a for a in calls}
    assert set(by_kind) == {"node", "cat"}, f"兩個分支都要走它，實際：{calls}"
    # 🔴 節點那一支要把算好的路徑表傳進去（第四個參數）—— 不傳的話
    # `_sync_taxonomy` 每一列自己撈一次整棵樹（它的 docstring 就在講這件事），
    # 而這條路一次掃的是整本帳所有沒分類的列。
    assert "path_map(session, ent)" in fn
    assert len(by_kind["node"]) >= 4, by_kind["node"]


def test_private_only_subviews_are_hidden_in_the_parent_book():
    """owner 2026-08-30：「crm 系統裡頭不用出現 家用、證券投資」「器材清單這些
    清單是私帳的，跟母公司沒關係」。這些整個是私帳的東西，不是換個帳本看同一份
    資料 —— 母公司模式整項不出現（有 finance_mine 也一樣）。"""
    fin = js_code_only(repo_src("frontend/tabs/finance/finance.js"))
    assert "if (!mineMode || !((window._modules || []).includes('finance_mine')))" in fin
    html = repo_src("frontend/tabs/finance/finance.html")
    # 🔴 gear 不在這一組：2026-08-30 起它跟著帳本切（母公司那本從零建），
    # 跟執行專案同一種處理。留在這裡的是「整個就是私帳的東西」那幾個。
    for sub in ("household", "securities", "receivable"):
        seg = html.split(f'data-subview="{sub}"')[0].rsplit("<button", 1)[1]
        assert "fin-nav-mine-only" in seg, sub


def test_petty_claim_from_the_import_reuses_the_existing_flow():
    """🔴 「源日請款」2026-08-27 就做過（收支明細每列的選單）—— owner 2026-08-30
    要的是**匯入當下**就能勾。所以是同一條路的第二個入口，不是第二套流程：
    走零用金既有的 `_push_from_cash`（會計項目白名單、「對不出項目就擋下、
    不落其他」、重推防線都在那支裡面）。"""
    body = code_only(func_body(repo_src(STMT), "async def _push_one_petty("))
    assert "_push_from_cash(session, staff" in body
    apply_fn = code_only(func_body(repo_src(STMT), "async def apply_bank_statement("))
    assert "petty_rows.append((r, ce))" in apply_fn
    # 🔴 請款人從 token 解（不收前端傳值），而且**解一次**不是每列解 ——
    # resolve_current_staff 會另開 session 跑兩個查詢，放迴圈裡就是 2N 次
    assert "resolve_current_staff" in apply_fn
    assert "resolve_current_staff" not in body, "身分不可以在迴圈裡逐列解"


def test_a_failed_petty_push_does_not_fail_the_whole_import():
    """🔴 推不動的不能讓整批匯入白做（那批帳已經是對的）—— 收集理由回報，
    而且前端**一定要講出來**：不講的話使用者以為都送出去了，那筆錢就跟公司
    要不回來。"""
    apply_fn = code_only(func_body(repo_src(STMT), "async def apply_bank_statement("))
    assert "petty_failed.append(" in apply_fn
    assert '"petty_failed": petty_failed' in apply_fn
    js = js_code_only(repo_src(JS))
    assert "r.petty_failed || []" in js and "alert(" in js


def test_the_petty_toggle_only_shows_on_private_outflow_rows():
    """私帳的**流出**列才有這回事（那是我先墊、要跟公司收回來的錢）。
    收入列、母公司的列都沒有。"""
    js = js_code_only(repo_src(JS))
    fn = js.split("const _stmtCanPetty =")[1].split(";")[0]
    assert "finIsMine()" in fn and "(r.amount || 0) < 0" in fn
    assert "petty_claim: !!x.petty_claim" in js, "apply 沒把旗標送出去"


# ── 規則的部分更新（2026-08-30 bug）─────────────────────────

def test_updating_a_rule_only_touches_the_fields_that_were_sent():
    """🔴 PUT 是**部分更新**：沒送的欄位不可以被 payload 的預設值洗掉。

    實際踩到的：停用鈕只送 `active`，整包寫回就把 `only_direction` 洗成 0
    （不限）—— 一條「薪資 只支出」的規則停用再啟用，之後收付兩邊都會中，
    而畫面上不會有任何提示。這是 memory 裡記過的老坑（整包 model_dump 寫回
    ＋ 欄位有預設值 ＝ 該欄被洗掉）在另一個欄位上重演。

    修在**寫入端**而不是叫前端記得每次都送全部：呼叫端有三個，
    「記得送」是守不住的。
    """
    body = _fn("async def _apply_rule_payload(")
    assert "exclude_unset=True" in body, "沒有分辨『有送』與『送了預設值』"
    # 每一個可寫欄位都要在 touched() 之後才寫
    for field in ("keyword", "category", "taxonomy_node_id", "bank_account_id",
                  "only_direction", "sort_order", "active", "note"):
        assert f'touched("{field}")' in body, field
    # 新增那條路仍然必填（必填由端點驗，不是靠 schema 擋在門外 ——
    # schema 擋的話，只送 {"active": false} 的部分更新會被 422 掉）
    assert "關鍵字與類別都要填" in body and "require_all and" in body
    sch = repo_src("core/schemas.py")
    seg = sch.split("class BankImportRulePayload(")[1].split("\nclass ")[0]
    assert 'keyword: str = ""' in seg and 'category: str = ""' in seg
    # 前端那顆停用鈕只送它要改的那一欄
    fn = js_func_body(js_code_only(repo_src(JS)), "_fr.ruleToggle = async (")
    assert "JSON.stringify({ active })" in fn
    assert "keyword" not in fn, "又把整條規則重送一次了"
