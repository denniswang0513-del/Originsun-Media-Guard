# -*- coding: utf-8 -*-
"""兩本帳 §8「專案共用、錢分帳」的錢牆測試。

owner 2026-08-24 拍板：mine 專案（owner 私帳）與客戶全面共用 —— 名稱/階段/
看板大家可見，**只有錢分帳**。三道牆：
1. redact_mine：有 money_view 但無 mine scope → mine 物件子樹的金額鍵抹掉。
2. money_dep 的 project_id 檢查（mine 專案 money 端點 403）— 掃描釘。
3. 統計聚合排除 mine（客戶績效金額合計、母公司專案毛利）— 掃描釘。
"""
from pathlib import Path

from core.money import MONEY_FIELDS, redact_mine

ROOT = Path(__file__).resolve().parent.parent.parent


def test_redact_mine_strips_money_on_mine_objects_only():
    data = {
        "projects": [
            {"id": "a", "entity": "parent", "name": "母公司案", "contract_amount": 100},
            {"id": "b", "entity": "mine", "name": "私帳案", "contract_amount": 999,
             "amount_receivable": 5, "status": "結案"},
        ],
        "total": 2,
    }
    out = redact_mine(data)
    assert out["projects"][0]["contract_amount"] == 100          # 母公司照舊
    assert "contract_amount" not in out["projects"][1]           # mine 抹掉
    assert "amount_receivable" not in out["projects"][1]
    # 非金額欄保留 —— 專案本身是共用的
    assert out["projects"][1]["name"] == "私帳案"
    assert out["projects"][1]["status"] == "結案"
    assert out["total"] == 2


def test_redact_mine_is_deletion_not_zeroing():
    out = redact_mine({"entity": "mine", "contract_amount": 999})
    # 鍵不存在（三態慣例），不是 0 —— 抹成 0 是謊報「合約金額 0 元」
    assert "contract_amount" not in out


def test_redact_mine_untouched_without_mine():
    data = {"projects": [{"entity": "parent", "contract_amount": 1}]}
    assert redact_mine(data) == data


def test_redact_mine_nested_detail_payload():
    data = {"project": {"entity": "mine", "contract_amount": 7,
                        "client": {"name": "共用客戶"}}}
    out = redact_mine(data)
    assert "contract_amount" not in out["project"]
    assert out["project"]["client"]["name"] == "共用客戶"


def test_money_fields_cover_project_money_columns():
    # mine 抹除依賴 MONEY_FIELDS 收錄專案的錢欄 —— 掉了任何一個，mine 專案的
    # 那個欄位就對 money_view 持有者裸奔
    for key in ("contract_amount", "amount_receivable", "amount_received"):
        assert key in MONEY_FIELDS, key


# ── 掃描釘（防手滑移除；行為由上面的純函式測試扛）─────────────────────

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_pin_project_serializer_carries_entity():
    src = _read("routers/crm/projects.py")
    assert '"entity": p.entity or "parent"' in src


def test_pin_migration_adds_project_entity():
    assert ("ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS entity"
            in _read("main.py"))


def test_pin_money_dep_gates_mine_projects():
    src = _read("core/money.py")
    assert 'request.path_params.get("project_id")' in src
    assert "私帳專案" in src


def test_pin_stats_exclude_mine():
    # 述詞正本在 core/ledger.not_mine —— 兩個聚合點都必須用它（字面散落的
    # entity != "mine" 沒有可 grep 的名字，第三個聚合點就會從零重新決定）
    assert "not_mine(CrmProject.entity)" in _read("routers/crm/clients.py")
    assert "not_mine(CrmProject.entity)" in _read("services/finance_statements.py")


def test_not_mine_predicate_behaviour():
    from core.ledger import MINE, not_mine

    class Col:   # 最小 SQLAlchemy 欄位替身：!= 回一個可比對的標記
        def __ne__(self, other):
            return ("ne", other)
    assert not_mine(Col()) == ("ne", MINE)


def test_pin_redact_route_handles_mine_branch():
    src = _read("core/money.py")
    assert "redact_mine" in src
    assert 'b\'"mine"\'' in src


def test_pin_project_list_supports_entity_filter():
    """列表要能按帳本篩，且前端預設母公司。

    私帳 402 案匯入時全帶新的 updated_at（列表按 updated_at desc）→ 不篩就整片
    壓在最上面把公司的案子埋掉（2026-08-24 owner 回報）。共用≠混在一起。
    """
    api = _read("routers/crm/projects.py")
    assert "entity: str = Query" in api
    assert "CrmProject.entity == entity" in api
    state = _read("frontend/tabs/crm/crm-projects-state.js")
    # 預設因人而異：有我的帳權限＝兩本都看（owner「跟我有關的專案我都要看到」），
    # 其他人＝母公司（不淹沒同事的列表）
    assert "_defaultEntity()" in state
    fn = state.split("function _defaultEntity()")[1].split("}")[0]
    assert "finance_mine" in fn and "'parent'" in fn
    html = _read("frontend/tabs/crm/crm-projects.html")
    assert 'id="proj-filter-entity"' in html


def test_pin_project_ledger_router_guarded():
    """逐案損益（/my-ledger.html 的專案分頁）：兩支端點都要走帳本守衛。

    它回的是逐案的錢（合約/已收/應收/掛帳支出/應付），沒有 _guard 就等於
    繞過兩本帳的牆 —— 2026-08-24 實測合夥人打 ?entity=mine 應得 403。
    """
    src = _read("routers/api_finance_projects.py")
    assert src.count("_guard(request, entity") >= 2
    assert 'level="full"' in src
    # 單案端點還要驗「這一列屬於哪本帳」（query 參數只驗得了人、驗不了資料）
    assert "這個專案不屬於目前的帳本" in src
    assert "api_finance_projects" in _read("main.py"), "router 沒註冊＝靜默 404"


def test_ledger_compute_formula():
    """實收/檢查算式（Sheet 反推、402 案驗證過）——正本只有後端這一份。"""
    from core.ledger_project import compute, norm_detail
    d = norm_detail({"outsource": 12000, "tax_fee": 3905, "buy_invoice": 2655,
                     "invoice_fee": 6560, "split": {"剪輯": 28000, "調光": 29440,
                                                    "動態攝影": 6000}})
    net, check = compute(82000, d)
    assert net == 63440          # 82,000 − 12,000 − 6,560（文心藝所 Lucas Arruda 實例）
    assert check == 0            # 工項 63,440 剛好等於實收
    # 個人稅款那條路（報稅型：典藏藝術家庭 媒體顧問）
    net2, _ = compute(42000, norm_detail({"personal_tax": 5086}))
    assert net2 == 36914


def test_norm_detail_drops_zero_items_and_junk():
    """0 值工項＝使用者把格子清空＝刪掉；非數字不進庫。"""
    from core.ledger_project import norm_detail
    d = norm_detail({"outsource": 5, "split": {"剪輯": 0, "調光": 100, "壞的": "x"}})
    assert d["split"] == {"調光": 100}
    assert d["outsource"] == 5
    assert d["misc"] == 0        # 缺鍵補 0，形狀固定


# ── 器材側的 §8 錢牆（遞迴，不是手列兩個鍵）────────────────────────────

def _req(modules):
    from starlette.requests import Request

    from core.auth import create_token
    t = create_token({"sub": "u", "username": "u", "access_level": 1,
                      "modules": modules})
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": [(b"authorization", ("Bearer " + t).encode())],
                    "query_string": b""})


_EQUIP = {"entity": "mine", "name": "A7S3", "purchase_cost": 180000,
          "monthly_depreciation": 5000, "maintenance_total": 12000,
          "depreciation_months": 36,
          "maintenances": [{"id": "m1", "cost": 3000, "note": "清感光元件"}]}


def test_mine_equipment_hides_every_money_key_not_just_two():
    """🔴 手列鍵的那版只蓋了 purchase_cost 與 monthly_depreciation —— 詳情裡的
    maintenance_total 與每筆保養的 cost 照樣外流。跟著 MONEY_FIELDS 遞迴走。"""
    from routers.api_equipment import _strip_mine_money
    out = _strip_mine_money(dict(_EQUIP), _req(["money_view", "crm_invoices"]))
    assert "purchase_cost" not in out and "monthly_depreciation" not in out
    assert "maintenance_total" not in out
    assert "cost" not in out["maintenances"][0]
    # 刪鍵不歸零；非錢的欄位一個都不能少
    assert out["name"] == "A7S3" and out["depreciation_months"] == 36
    assert out["maintenances"][0]["note"] == "清感光元件"


def test_mine_scope_sees_its_own_equipment_money():
    from routers.api_equipment import _strip_mine_money
    out = _strip_mine_money(dict(_EQUIP),
                            _req(["money_view", "crm_invoices", "finance_mine"]))
    assert out["maintenance_total"] == 12000
    assert out["maintenances"][0]["cost"] == 3000


def test_parent_equipment_untouched():
    from routers.api_equipment import _strip_mine_money
    out = _strip_mine_money(dict(_EQUIP, entity="parent"),
                            _req(["money_view", "crm_invoices"]))
    assert out["purchase_cost"] == 180000


def test_editable_cost_fields_match_the_payload_schema():
    """🔴 兩份清單必須同步：schema 多一欄 → PUT 收下、norm_detail 丟掉（回 200
    但數字不見）；COST_FIELDS 多一欄 → UI 畫得出來卻送不上去。"""
    from core.ledger_project import COST_KEYS
    from core.schemas import LedgerDetailPayload
    non_cost = {"split", "contract_amount", "close_date"}
    assert set(LedgerDetailPayload.model_fields) - non_cost == set(COST_KEYS)


def test_pin_project_ledger_ap_matches_ap_open_payments():
    """🔴 預支不是應付（那是先給出去的週轉金，核銷後才變成成本）。混進來會讓
    同一個專案在逐案損益與應付帳款上出現兩個數字。"""
    src = _read("routers/api_finance_projects.py")
    fn = src.split("async def _rollups")[1].split("\n@router")[0]
    assert "is_advance == 0" in fn
    assert 'payment_status != "已付款"' in fn
    assert "ap_open_payments" in src, "口徑正本要留名字，否則第三個聚合點又會從零決定一次"
