# -*- coding: utf-8 -*-
"""科目對映按帳本分家（owner 2026-08-30「母公司的就是母公司，私帳就是私帳」）。

`finance_category_map` 原本兩本帳共用一張，而兩本帳的**收支類別值域根本不重疊**：

    母公司 → 平的科目（行政／薪資／交際應酬…）
    私帳   → cash_taxonomy_nodes 那棵樹鏡射出來的複合鍵（家用_變動支出…）

不分家的下場（owner 實際看到的）：母公司的分類規則下拉列出 75 項，其中 38 項是
私帳的類別。設下去照樣生效，那筆錢就分到一個公司報表沒有的類別，然後在三表裡
變成「未歸類」—— 而且不會噴錯。

🔴 只有 `source='cash'` 分家。`payment`／`invoice` 是公司流程的詞彙，兩本帳講的
是同一件事（私帳的 34 張請款用的就是母公司那批類別），分了會把私帳的請款打斷。
"""
from tests.unit._srcscan import migration_sql, code_only, func_body, repo_src
from tests.unit._srcscan import finance_src


def test_the_mapping_table_is_scoped_by_ledger():
    """欄位在、預設 parent（既有列的歸屬），唯一鍵也帶上它。"""
    from tests.unit._srcscan import models_src
    seg = models_src().split(
        "class FinanceCategoryMap(")[1].split("\nclass ")[0]
    assert 'entity = Column(String(16), nullable=False, server_default="parent")' in seg
    # 🔴 唯一鍵要帶 entity：分家之後兩本帳可以各有一個同名類別（母公司的「其他」
    # 與私帳的「其他」對到不同科目）。舊的 (source, category_text) 會擋住其中一個。
    assert '"uq_fincatmap_entity_source_text"' in seg
    assert '"entity", "source", "category_text", unique=True' in seg
    mig = migration_sql()
    assert ("ALTER TABLE finance_category_map ADD COLUMN IF NOT EXISTS "
            in mig and "entity VARCHAR(16) NOT NULL DEFAULT 'parent'" in mig)
    assert "DROP CONSTRAINT IF EXISTS uq_fincatmap_source_text" in mig
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_fincatmap_entity_source_text" in mig


def test_the_backfill_asks_the_taxonomy_tree_not_the_underscore():
    """回填判定＝**這個類別在不在私帳的分類樹裡**。

    看有沒有底線會判錯：`公司_專案支出` 有底線但不在樹上，它是母公司的。
    生產庫核對過這條規則跟「哪本帳實際在用」零矛盾。
    """
    body = code_only(func_body(repo_src("db/seed_finance.py"),
                               "async def backfill_category_map_entity("))
    assert "ledger_category_domain(session, \"mine\")" in body
    assert 'FinanceCategoryMap.source == "cash"' in body, "只判 cash，別動共用的那兩種"
    # 🔴 只跑一次：不然使用者手動改判之後，下次開機就被改回去
    assert "category_map_entity_backfilled" in body and "return 0" in body   # 2026-09-06：sentinel 改 settings 旗標（手動先加一列不會讓 backfill 永遠不跑）
    # 分類樹還沒種好時不要亂判（回填排在 seed_cash_taxonomy 之後）
    assert "if not mine:" in body
    boot = repo_src("main.py")
    assert boot.index("seed_cash_taxonomy(_ftax)") < boot.index(
        "backfill_category_map_entity(_ftax)"), "回填要排在分類樹種好之後"


def test_every_read_of_the_cash_half_is_scoped():
    """讀取端全部要帶帳本 —— 漏一個就是那本帳看到別人的類別，而且不會噴錯。"""
    shared = code_only(func_body(repo_src("routers/crm/_shared.py"),
                                 "async def cash_category_texts("))
    assert "FinanceCategoryMap.entity == ent" in shared
    dom = code_only(func_body(repo_src("routers/crm/_shared.py"),
                              "async def ledger_category_domain("))
    assert "cash_category_texts(session, ent)" in dom
    # 零用金是母公司的東西（本檔的專案查詢都帶 not_mine）
    petty = code_only(func_body(repo_src("routers/crm/petty.py"),
                                "async def _petty_item_domain("))
    assert 'FinanceCategoryMap.entity == "parent"' in petty
    # 分類樹改名 → 對映的鍵跟著改，只能改這本帳那一筆
    ren = code_only(func_body(finance_src(),
                              "async def _rename_category_map("))
    assert ren.count("FinanceCategoryMap.entity == ent") == 2


def test_the_statements_engine_scopes_cash_but_shares_the_rest():
    """🔴 三表的 `cat_map` 是 `(source, category_text)` 的字典，決定科目與
    treatment。兩本帳哪天有同名類別，不過濾就會一邊蓋掉另一邊 —— 那批帳會被
    算進錯的科目。payment／invoice 兩本共用，照舊全收。"""
    fs = repo_src("services/finance_statements.py")
    body = code_only(func_body(fs, "async def _load_inputs("))
    assert "category_map_scope(entity)" in body                 # 述詞只有 category_map_scope 一份
    scope = code_only(func_body(fs, "def category_map_scope("))
    assert 'FinanceCategoryMap.source != "cash"' in scope and "FinanceCategoryMap.entity == entity" in scope


def test_the_admin_endpoints_are_scoped_too():
    """後台那三支（清單／批次存檔／待歸類佇列）也要按帳本。

    待歸類佇列**兩邊都要**過濾：只過濾對映不過濾資料的話，母公司會看到一整排
    「私帳有在用、母公司沒對映」的類別排隊等它處理。
    """
    src = repo_src("routers/api_finance.py")
    scope = code_only(func_body(src, "def _map_scope("))
    assert "category_map_scope(ent)" in scope                    # 轉呼叫 services.finance_statements 那一份
    for name in ("async def list_category_map(", "async def list_unmapped_categories("):
        body = code_only(func_body(src, name))
        assert "_map_scope(ent)" in body, name
    up = code_only(func_body(src, "async def upsert_category_map("))
    assert "_guard(request, entity, level=\"full\")" in up, "私帳也要編得動自己那半"
    assert 'row_ent = ent if it.source == "cash" else "parent"' in up
    assert 'm.entity or "parent"' in up and "m.source, m.category_text" in up, \
        "upsert 的 key 要帶帳本，不然在私帳存一次就改到母公司那筆"


def test_the_unmapped_queue_filters_the_rows_by_ledger_as_well():
    body = code_only(func_body(repo_src("routers/api_finance.py"),
                               "async def list_unmapped_categories("))
    assert "ent_col == ent" in body
    assert "CrmCashEntry.entity" in body and "CrmPaymentRequest.entity" in body
