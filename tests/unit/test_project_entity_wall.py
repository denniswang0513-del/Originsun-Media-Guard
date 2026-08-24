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
    assert 'CrmProject.entity != "mine"' in _read("routers/crm/clients.py")
    assert 'CrmProject.entity != "mine"' in _read("services/finance_statements.py")


def test_pin_redact_route_handles_mine_branch():
    src = _read("core/money.py")
    assert "redact_mine" in src
    assert 'b\'"mine"\'' in src
