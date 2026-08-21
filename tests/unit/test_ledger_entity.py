# -*- coding: utf-8 -*-
"""兩本帳（公司實體）v2 — scope 判定與 entity 化防回歸釘（docs/LEDGER_ENTITY_PLAN.md §7）。

守的是一句話：**合夥人只看得到母公司報表那一層，「我的帳」一個數字都不能漏**。

v2 語意（§0）：'parent'＝母公司（預設；既有資料全歸此）、'mine'＝我的帳。
v1 的 'own' 值與 finance_parent key **已廢棄** —— 本檔尾端有廢值掃描釘。

  ① scope 矩陣：`core.ledger.allowed_entities` 是「誰看得到哪本帳」的唯一正本，
     純函式直接打（不經 HTTP、不碰 DB）。v2 分兩層 level：view（報表，合夥人
     可及）/ full（記帳寫入面，合夥人不在）。
  ② `require_entity` 行為：401/403/422 與空值落點（合夥人不帶參數要自動落
     parent；finance_mine 者自動落 mine）。**合夥人 level="full" 403 是 v2 核心**。
  ③ 原始碼掃描釘：7 張表的 entity 欄（server_default 'parent'）、月結複合
     unique、報表引擎取數層的 entity WHERE、api_finance view/full 分層、
     月結鎖帳每處帶 entity、cashflow 母公司 CRM 域、main.py migration
     （含 v1→v2 own→parent fixup）、RBAC 常數、前端鏡射。
  ④ 合夥人鐵則（plan §2.3）：ledger 正本要留著警語；payload 的 entity 預設
     必須是 None（🔴 給實體值當預設＝舊前端整包 model_dump 寫回會把另一本帳
     的列洗過去 —— exclude_unset 教訓）。
  ⑤ 廢值釘：runtime 程式碼不再出現 entity 語意的 'own'（只允許 main.py 的
     migration fixup 句與 plan 文件本身）。

掃描斷言一律只掃**目標檔**，不掃 tests/（避免自己檔裡的說明文字被自己掃到）。
"""
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import UniqueConstraint
from starlette.requests import Request

from core.auth import ALL_MODULES, TAB_ACCESS, create_token
from core.ledger import allowed_entities, require_entity
from core.schemas import (BankAccountPayload, CashEntryPayload,
                          FinanceAdjustmentPayload, InvoicePayload,
                          LoanPayload, PaymentRequestPayload)
from db.models import (BankAccount, CrmCashEntry, CrmInvoice,
                       CrmPaymentRequest, FinanceAdjustment, FinanceLoan,
                       FinanceMonthClose)

REPO = Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── ① scope 矩陣（allowed_entities 純函式，view/full 兩層）─────────────

SCOPE_MATRIX = [
    # Lv3 → 兩本兩層全開
    ({"access_level": 3, "modules": []}, "view", {"parent", "mine"}),
    ({"access_level": 3, "modules": []}, "full", {"parent", "mine"}),
    # legacy token（role 字串、無 access_level）→ 同 Lv3
    ({"role": "admin"}, "view", {"parent", "mine"}),
    ({"role": "admin"}, "full", {"parent", "mine"}),
    # 合夥人：唯一一把 finance_partner → 母公司**報表層**而已；full 空 set
    ({"access_level": 1, "modules": ["finance_partner"]}, "view", {"parent"}),
    ({"access_level": 1, "modules": ["finance_partner"]}, "full", set()),
    # 母公司記帳者：crm_invoices AND money_view → parent 兩層都開
    ({"access_level": 1, "modules": ["crm_invoices", "money_view"]},
     "view", {"parent"}),
    ({"access_level": 1, "modules": ["crm_invoices", "money_view"]},
     "full", {"parent"}),
    # 我的帳：finance_mine 一把 → mine 兩層都開
    ({"access_level": 1, "modules": ["finance_mine"]}, "view", {"mine"}),
    ({"access_level": 1, "modules": ["finance_mine"]}, "full", {"mine"}),
    # 只有 tab 鑰匙沒有金額鑰匙 → 進不了財務（維持既有語意）
    ({"access_level": 1, "modules": ["crm_invoices"]}, "view", set()),
    ({"access_level": 1, "modules": ["crm_invoices"]}, "full", set()),
    # 只有金額鑰匙沒有 tab 鑰匙 → 一樣不行
    ({"access_level": 1, "modules": ["money_view"]}, "view", set()),
    ({"access_level": 1, "modules": ["money_view"]}, "full", set()),
    # modules 空 / 缺鍵 / None
    ({"access_level": 1, "modules": []}, "view", set()),
    ({"access_level": 1}, "view", set()),
    ({"access_level": 1, "modules": None}, "view", set()),
]


@pytest.mark.parametrize("payload,level,expected", SCOPE_MATRIX)
def test_allowed_entities_matrix(payload, level, expected):
    assert allowed_entities(payload, level=level) == expected


@pytest.mark.parametrize("level", ["view", "full"])
def test_allowed_entities_anonymous_is_empty(level):
    """未登入（payload=None）→ 空 scope，一本都看不到。"""
    assert allowed_entities(None, level=level) == set()


# ── ② require_entity 行為（真 token + starlette Request）───────────────

def _tok(**claims) -> str:
    return create_token({"sub": "u", "username": "u",
                         "access_level": 1, "modules": [], **claims})


def _req(token=None) -> Request:
    headers = []
    if token is not None:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": headers, "query_string": b""})


def _status(request, entity="", level="view"):
    with pytest.raises(HTTPException) as ei:
        require_entity(request, entity, level=level)
    return ei.value.status_code


def test_require_entity_anonymous_401():
    assert _status(_req()) == 401


def test_require_entity_no_finance_scope_403():
    """登入但沒有任何財務檢視權 → 403（不是 401、也不是靜默放行）。"""
    assert _status(_req(_tok(modules=["crm_projects"]))) == 403


def test_partner_defaults_to_parent():
    """合夥人不帶 entity → 自動落 scope 裡那本（parent）。前端不帶參數也拿得到。"""
    r = _req(_tok(modules=["finance_partner"]))
    assert require_entity(r, "") == "parent"


def test_partner_cannot_read_mine():
    """🔴 合夥人指名要我的帳 → 403。這條紅了＝私帳金額對合夥人攤開。"""
    assert _status(_req(_tok(modules=["finance_partner"])), "mine") == 403


def test_partner_explicit_parent_view_ok():
    r = _req(_tok(modules=["finance_partner"]))
    assert require_entity(r, "parent") == "parent"


def test_partner_full_level_403():
    """🔴 v2 核心：合夥人打寫入面（level="full"）→ 403，連母公司那本都不行。
    報表唯讀的邊界就在這一條 —— 紅了＝合夥人能記帳/摸原始帳列。"""
    assert _status(_req(_tok(modules=["finance_partner"])),
                   "parent", level="full") == 403


def test_partner_unknown_entity_422():
    assert _status(_req(_tok(modules=["finance_partner"])), "xxx") == 422


def test_mine_scope_defaults_to_mine():
    """finance_mine 者不帶 entity → scope 沒有 parent，自動落 mine。"""
    r = _req(_tok(modules=["finance_mine"]))
    assert require_entity(r, "") == "mine"


@pytest.mark.parametrize("level", ["view", "full"])
def test_mine_scope_cannot_read_parent(level):
    """只有我的帳鑰匙 → 母公司那本 403（方向反過來一樣要擋，兩層皆然）。"""
    assert _status(_req(_tok(modules=["finance_mine"])),
                   "parent", level=level) == 403


def test_parent_bookkeeper_defaults_to_parent():
    r = _req(_tok(modules=["crm_invoices", "money_view"]))
    assert require_entity(r, "", level="full") == "parent"


@pytest.mark.parametrize("level", ["view", "full"])
def test_parent_bookkeeper_cannot_read_mine(level):
    """母公司記帳者沒有 finance_mine → 我的帳 403（兩層皆然）。"""
    assert _status(_req(_tok(modules=["crm_invoices", "money_view"])),
                   "mine", level=level) == 403


@pytest.mark.parametrize("entity", ["parent", "mine"])
@pytest.mark.parametrize("level", ["view", "full"])
def test_admin_reads_both_at_both_levels(entity, level):
    r = _req(_tok(access_level=3))
    assert require_entity(r, entity, level=level) == entity


# ── ③ 原始碼掃描釘（防回歸）───────────────────────────────────────────

ENTITY_MODELS = [CrmInvoice, CrmPaymentRequest, CrmCashEntry, BankAccount,
                 FinanceAdjustment, FinanceLoan, FinanceMonthClose]


@pytest.mark.parametrize("model", ENTITY_MODELS,
                         ids=[m.__name__ for m in ENTITY_MODELS])
def test_models_entity_column_server_default_parent(model):
    """7 張錢流表都要有 entity 欄，NOT NULL 且 server_default 'parent'。

    server_default 是既有資料與「不帶 entity 的舊寫入」的安全網 —— 掉了它，
    新列的 entity 會是 NULL，兩本帳的 WHERE 都撈不到那筆。'parent' 是 v2 值
    （'own' 已廢棄）——退回 'own' 會讓新列從兩本帳的查詢裡消失。
    """
    col = model.__table__.c["entity"]
    assert col.nullable is False, f"{model.__name__}.entity 可空"
    assert col.server_default is not None and col.server_default.arg == "parent", \
        f"{model.__name__}.entity server_default 不是 'parent'"


def test_month_close_unique_is_composite():
    """🔴 month 單欄 unique 必須讓位給 (entity, month)：兩本帳各自鎖各自的月。

    month 還掛著單欄 unique 的話，母公司一鎖 2026-05、我的帳就再也鎖不了同月。
    """
    t = FinanceMonthClose.__table__
    assert not t.c["month"].unique, "FinanceMonthClose.month 還是單欄 unique"
    composites = [tuple(c.name for c in cons.columns)
                  for cons in t.constraints if isinstance(cons, UniqueConstraint)]
    assert ("entity", "month") in composites, \
        f"缺 (entity, month) 複合 unique，現有：{composites}"


FS_SRC = _src("services/finance_statements.py")
FLOW_MODELS = ("CrmInvoice", "CrmPaymentRequest", "CrmCashEntry",
               "FinanceAdjustment", "BankAccount", "FinanceLoan")


def test_load_inputs_signature_takes_entity():
    """報表引擎唯一取數咽喉 `_load_inputs` 要收 entity（plan §4）。"""
    assert re.search(r"def _load_inputs\([^)]*\bentity\b", FS_SRC), \
        "_load_inputs 簽名沒有 entity 參數"


def test_engine_functions_default_entity_parent():
    """引擎八函式（_load_inputs/_advance_state/compute_live/statements_for_period/
    month_close_extras/drilldown/dashboard_summary/tax_package）簽名全帶
    `entity: str = "parent"` —— 預設值退回 'own' 或掉參數＝呼叫端不帶 entity
    時整份報表混帳。"""
    n = FS_SRC.count('entity: str = "parent"')
    assert n >= 8, f'finance_statements 只有 {n} 處 entity: str = "parent"（< 8）'


@pytest.mark.parametrize("model", FLOW_MODELS)
def test_load_inputs_filters_each_flow_source(model):
    """六個錢流來源都要帶 entity WHERE —— 漏一個，兩本帳的報表就混在一起。"""
    assert f"{model}.entity == entity" in FS_SRC, \
        f"finance_statements 沒有以 {model}.entity 過濾"


AF_SRC = _src("routers/api_finance.py")
# 對帳單匯入 2026-08-21 從 api_finance.py 尾端搬成獨立檔（純搬家）。守衛的釘
# 要跟著涵蓋它 —— 只掃原檔的話，搬過去的 10 個 _guard 就從此不受任何釘子管。
AFS_SRC = _src("routers/api_finance_stmt.py")


def test_api_finance_has_no_bare_check_money_call():
    """舊守衛語意已內含在 require_entity 的 parent full scope 判定裡（api_finance
    `_guard` 的註解就是這麼說的）—— 檔裡再出現這個呼叫＝有人疊了舊守衛。"""
    assert "check_money(request)" not in AF_SRC


def test_api_finance_view_endpoints_exactly_seven():
    """view 層（合夥人可及）＝不帶 level 的 `_guard(request, entity)` 呼叫，
    恰好 7 處：dashboard/statements/drilldown/tax-package/accounts GET/
    category-map GET/unmapped GET（plan §2.4）。

    多了＝有寫入面被降到報表層（合夥人摸得到）；少了＝報表端點被鎖成 full
    （合夥人整個看不到，功能壞）。兩個方向都要爆。
    """
    n = len(re.findall(r"_guard\(request, entity\)", AF_SRC))
    assert n == 7, f"不帶 level 的 _guard(request, entity) 有 {n} 處（應為 7）"


def test_statement_import_has_no_view_level_endpoint():
    """對帳單匯入整段都是記帳寫入面，一支報表端點都沒有 —— 不帶 level 的
    `_guard` 一處都不該出現。出現了＝合夥人（唯讀）摸得到匯入這條路。"""
    n = len(re.findall(r"_guard\(request(?:, [a-z_.\"]+)?\)(?!\s*#)", AFS_SRC))
    assert n == 0, f"api_finance_stmt 有 {n} 處沒指定 level 的 _guard"


def test_api_finance_full_level_floor():
    """其餘端點全部 level="full"（銀行/對帳/明細/調整/貸款/bulk-assign/
    category-map 寫入/setup-wizard；含帳戶推導的二次驗證）。地板取 40 ——
    少於地板＝有寫入端點掉了 full 分層。"""
    n = AF_SRC.count('level="full"')
    assert n >= 40, f'api_finance 的 level="full" 只出現 {n} 次（< 40）'


def test_statement_import_full_level_floor():
    """對帳單匯入（分類規則 CRUD／預覽／匯入／草稿 CRUD）全部 level="full"。
    地板取 9（搬家當下是 10）。"""
    n = AFS_SRC.count('level="full"')
    assert n >= 9, f'api_finance_stmt 的 level="full" 只出現 {n} 次（< 9）'


CF_SRC = _src("routers/crm/finance.py")


# 🔴 帳務域的守衛掃描要蓋**兩個**檔案：2026-08-21 把發票檔那 500 行搬到
# invoice_files.py 時，有 4 處 require_entity 跟著搬過去 —— 只掃 finance.py 的話
# 那 4 處從此沒人看著，而且下限 >= 12 還是會過，看不出少了東西。
@pytest.mark.parametrize("rel,floor", [("routers/crm/finance.py", 12),
                                       ("routers/crm/invoice_files.py", 3)])
def test_crm_finance_require_entity_all_full_level(rel, floor):
    """CRM 帳務逐筆端點（invoices/payments/cash-entries）的 require_entity
    一律 level="full"：這裡是原始帳列，合夥人的 view scope 不可及（plan §2.4）。
    漏帶 level＝預設 view＝合夥人拿得到母公司原始帳列。"""
    src = _src(rel)
    calls = [m.start() for m in re.finditer(r"require_entity\(request", src)]
    assert len(calls) >= floor, \
        f"{rel} 只掃到 {len(calls)} 處 require_entity 呼叫，掃描器八成壞了"
    for pos in calls:
        window = src[pos:pos + 120]
        assert 'level="full"' in window, \
            f"{rel} 這處 require_entity 沒帶 level=\"full\"：{window.splitlines()[0]!r}"


def test_crm_finance_month_open_calls_all_carry_entity():
    """🔴 月結鎖帳 entity 化：crm/finance.py 每一處 `_assert_month_open(` 都要
    帶 entity（更新時新舊都查）。用預設值＝拿我的帳的鎖去擋母公司的帳（反之亦然）。

    呼叫會跨行（entity= 在續行），所以從呼叫點往後取一段窗口找，不逐行比。
    """
    calls = [m.start() for m in re.finditer(r"_assert_month_open\(", CF_SRC)]
    assert len(calls) >= 9, \
        f"只掃到 {len(calls)} 處 _assert_month_open 呼叫，掃描器八成壞了"
    for pos in calls:
        window = CF_SRC[pos:pos + 180]
        assert "entity" in window, \
            f"這處 _assert_month_open 沒帶 entity：{window.splitlines()[0]!r}"


def test_update_never_moves_a_row_between_ledgers():
    """🔴 更新不得換帳本（owner 2026-08-19 拍板）：payload 帶了與該列現值不同的
    entity → 422，六張表一條規矩。

    這條統一過：api_finance（銀行帳戶/調整/貸款）本來就 422，crm/finance
    （發票/請款/收支）曾實作成「驗兩邊 scope 後真的搬過去」，與 plan §2.4
    「更新維持既有值」相反。留著兩個答案，第三階段私人專案（錢流 entity 跟著
    專案跑）會踩到。真要換帳本＝刪掉重建，留得下痕跡。"""
    fin_src = _src("routers/api_finance.py")
    for who in ("帳戶不可跨帳本搬移", "調整列不可跨帳本搬移", "貸款不可跨帳本搬移"):
        assert who in fin_src, f"api_finance 少了「{who}」的 422 守衛"
    # CRM 那三支共用 _entity_for_write —— 搬移分支必須是 raise，不是再驗一次 scope
    i = CF_SRC.index("def _entity_for_write(")
    body = CF_SRC[i:i + 1600]
    assert "不可跨帳本搬移" in body and "status_code=422" in body, \
        "_entity_for_write 沒有把「換帳本」擋成 422"
    assert body.count("require_entity(") == 1, \
        "_entity_for_write 還留著搬移路徑的第二次 require_entity（該分支已改 422）"
    for fn in ("update_invoice", "update_payment", "update_cash_entry"):
        j = CF_SRC.index(f"async def {fn}(")
        seg = CF_SRC[j:j + 2000]
        assert "_entity_for_write(" in seg, f"{fn} 沒走 _entity_for_write"
        assert ".entity = " not in seg, \
            f"{fn} 還在寫回 entity —— 更新既然不能換帳本，那行是 no-op"


def test_shared_month_guards_default_parent():
    """_shared 三支月結守衛預設 entity='parent'：petty/costs（母公司 CRM 域）
    吃預設不改呼叫點（plan §3）。預設漂掉＝母公司域的鎖帳整批失效。"""
    src = _src("routers/crm/_shared.py")
    for fn in ("_locked_month_set", "_assert_month_open", "_assert_rows_open"):
        assert re.search(
            rf"def {fn}\([^)]*entity: str = \"parent\"", src), \
            f"_shared.{fn} 的 entity 預設不是 'parent'"


def test_cashflow_milestones_and_forecast_pinned_to_parent_full():
    """付款節點/現金流預測綁專案＝母公司 CRM 域（plan §2.4）：兩區都要
    `_guard(request, "parent", level="full")` 鎖死 —— 合夥人的 view scope
    不能摸到專案側的錢；month-close GET 則是 view（合夥人看得到鎖帳狀態）。"""
    src = _src("routers/api_cashflow.py")
    i_ms = src.index('@router.get("/milestones")')
    i_fc = src.index('@router.get("/forecast")')
    i_mc = src.index('@router.get("/month-close")')
    pin = '_guard(request, "parent", level="full")'
    assert pin in src[i_ms:i_fc], "milestones 區沒鎖 parent full"
    assert pin in src[i_fc:i_mc], "forecast 區沒鎖 parent full"
    mc_get = src[i_mc:src.index('@router.post("/month-close")')]
    assert "_guard(request, entity)" in mc_get, \
        "month-close GET 不是 view 層（合夥人要能看鎖帳狀態）"


def test_main_migration_covers_seven_tables_and_composite_index():
    """main.py startup ALTER 區：7 張表各一句 entity 欄（DEFAULT 'parent'）+
    月結複合 unique index。（model 有欄、DB 沒欄＝生產第一筆寫入直接炸。）

    v1 的 own→parent fixup 已移除：那批 SET DEFAULT / UPDATE 只對 dev DB 有意義，
    2026-08-19 確認七表 own=0、default 全 'parent' 後即無事可做 —— 留著等於每次
    開機對七張錢流表各做一次全表掃描＋一次 ACCESS EXCLUSIVE DDL。"""
    src = _src("main.py")
    for table in ("crm_invoices", "crm_payment_requests", "crm_cash_entries",
                  "bank_accounts", "finance_adjustments", "finance_loans",
                  "finance_month_close"):
        assert (f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                "entity VARCHAR(16) NOT NULL DEFAULT 'parent'") in src, \
            f"main.py 缺 {table} 的 entity ALTER"
    assert "uq_month_close_entity_month" in src, "缺月結複合 unique index"
    assert "DROP CONSTRAINT IF EXISTS finance_month_close_month_key" in src, \
        "沒拆掉 month 單欄 unique（複合 index 蓋上去也沒用）"


def test_all_modules_appends_two_ledger_keys_at_tail():
    """finance_partner / finance_mine 都要在 ALL_MODULES 且排在 me_petty 之後
    —— 新 key 一律 append 尾端（modules[0] 決定 admin 落地頁，插前面會改掉
    所有管理員的首頁）。v1 的 finance_parent 已廢棄，不准回魂。"""
    for key in ("finance_partner", "finance_mine"):
        assert key in ALL_MODULES, f"ALL_MODULES 缺 {key}"
        assert ALL_MODULES.index(key) > ALL_MODULES.index("me_petty"), \
            f"{key} 沒排在 me_petty 之後"
    assert "finance_parent" not in ALL_MODULES, \
        "廢棄的 v1 key finance_parent 回魂了"


def test_tab_access_lets_partner_into_finance_tab():
    """合夥人（只有 finance_partner）也要進得了財務 tab（plan §2.1）。"""
    assert TAB_ACCESS["crm_invoices"] == ("crm_invoices", "finance_partner")


def test_frontend_mirrors_two_ledger_keys():
    """前端鏡射（RBAC 3 處同步的另外兩處，輕量存在釘）：
    tab-config.js 要認得兩把 key、user-mgmt.js 要有兩個 label（否則使用者管理
    勾不了）。⚠ 「我的帳」**刻意沒有 SPA tab**（owner 2026-08-19：走外部連結
    /my-ledger.html＋每次重新登入，側欄零入口）—— TAB_MAP 不准有 finance_mine
    落點，加回來＝把私帳入口攤在共用側欄上。"""
    tc = _src("frontend/js/shared/tab-config.js")
    assert "finance_partner" in tc, "tab-config.js 缺 finance_partner"
    assert "finance_mine" in tc, "tab-config.js 缺 finance_mine（PERMISSION_GROUPS）"
    assert not re.search(r"finance_mine:\s*'tab_", tc), \
        "TAB_MAP 出現 finance_mine tab 落點 —— 我的帳刻意無 SPA 入口，不准加回來"
    um = _src("frontend/js/admin/user-mgmt.js")
    assert "finance_partner" in um and "finance_mine" in um, \
        "user-mgmt.js MODULE_LABELS 缺兩本帳 key 的 label"

    # 獨立頁本身要存在、要有登入表單、且不吃既有登入態（重新驗證是存在理由）
    ml = _src("frontend/my-ledger.html")
    assert "login-form" in ml and "finance_mine" in ml, \
        "my-ledger.html 缺登入表單或權限閘門"
    assert "localStorage.getItem('auth_token')" not in ml, \
        "my-ledger.html 不准讀既有 token 自動登入 —— 每次都要重新打帳密"


# ── ④ 合夥人鐵則釘（plan §2.3）────────────────────────────────────────

def test_ledger_source_carries_partner_iron_rule():
    """core/ledger.py 是 scope 正本，§2.3 鐵則要留在檔頭警語裡 —— 設定合夥人
    帳號的人看的是這個檔，不是 plan。刪了警語，下一個人就會把那把橫切鑰匙
    一起發出去。"""
    src = _src("core/ledger.py")
    docstring = src.split('"""')[1]
    assert "money_view" in docstring and "絕不給" in docstring, \
        "core/ledger.py 檔頭的合夥人鐵則警語不見了"


ENTITY_PAYLOADS = [InvoicePayload, PaymentRequestPayload, CashEntryPayload,
                   BankAccountPayload, FinanceAdjustmentPayload, LoanPayload]


@pytest.mark.parametrize("schema", ENTITY_PAYLOADS,
                         ids=[s.__name__ for s in ENTITY_PAYLOADS])
def test_payload_entity_default_is_none(schema):
    """🔴 payload 的 entity 預設必須是 None（None＝建立落 'parent'、更新維持既有值）。

    給實體值當預設的話，舊前端整包 model_dump 寫回會把另一本帳的列洗過去
    （plan §5 的 PUT 洗欄位陷阱；同 memory 的 exclude_unset 教訓）。
    """
    field = schema.model_fields["entity"]
    assert field.default is None, \
        f"{schema.__name__}.entity 預設是 {field.default!r}，不是 None"


# ── ⑤ 廢值釘（plan §0/§7）：'own' 不准再出現在 runtime 程式碼 ──────────

# main.py 刻意不在名單：它的 own→parent fixup 句是唯一合法殘留
# （由 test_main_migration_covers_seven_tables_and_composite_index 正面釘）。
OWN_BAN_FILES = [
    "core/ledger.py",
    "db/models.py",
    "services/finance_statements.py",
    "routers/api_finance.py",
    "routers/api_finance_stmt.py",
    "routers/api_cashflow.py",
    "routers/crm/finance.py",
    "routers/crm/invoice_files.py",
    "routers/crm/_shared.py",
]


@pytest.mark.parametrize("rel", OWN_BAN_FILES)
def test_deprecated_own_literal_gone_from_runtime(rel):
    """v1 的 'own' 實體值已廢棄（§0）——runtime 檔案裡出現引號字面值 'own'
    ＝有人把舊語意寫回來（查詢撈 'own' 的列＝永遠空集合，靜默漏帳）。"""
    hits = re.findall(r"['\"]own['\"]", _src(rel))
    assert not hits, f"{rel} 還有 {len(hits)} 處廢棄的 'own' 字面值"


def test_batch_pay_locks_are_per_ledger():
    """🔴 鎖月要看**這一列自己那本帳**。

    batch_pay 本來對「新付款日」用 `any(... for locked in locked_by_entity.values())`
    —— 我的帳鎖了某個月，母公司的批次付款就整批 409；而同一支函式裡「舊付款日」
    那條是對的。同一個判斷兩套規則。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src('routers/crm/finance.py'),
                               'async def batch_pay('))
    assert 'for locked in locked_by_entity.values()' not in body, \
        '又用跨帳本的鎖月判斷了'
    assert 'locked = locked_by_entity[p.entity or "parent"]' in body
    assert body.count('in locked') == 2, '新舊付款日都要看同一本帳的鎖'
