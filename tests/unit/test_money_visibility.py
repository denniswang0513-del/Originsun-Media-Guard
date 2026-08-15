# -*- coding: utf-8 -*-
"""金額檢視授權 `money_view`（owner 2026-08-15「預設不要看到金額，除非我授權」）。

在此之前「錢」只擋到「你得先登入」：2026-08-14 補的 `_crm_read_guard` 擋掉了
匿名，但公司內任何一個有帳號的人 `GET /api/v1/crm/projects` 就拿得到全部
contract_amount / amount_receivable / profit_target_pct。

兩層各自要有正反面（設計與理由：docs/MONEY_VISIBILITY.md）：
  ① 整支擋：帳務／報價／成本明細／費率史 —— 沒授權 403、有授權不是 403
  ② 欄位抹除：夾帶金額的端點把鍵**刪掉**（不是歸零 —— `|| 0` 會變成
     「這案子合約金額是 0 元」那種謊報）
  ③ 泛名不准進名單：`total` 在 CRM 是列數（五軌完成條的分母）、`expense` 在
     雜支端點是物件 —— 收進去會靜默打壞現有畫面
"""
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi.testclient import TestClient

from core.money import MONEY_FIELDS, MoneyRedactRoute, redact

# 整支就是錢 → 沒 money_view 要 403。路徑參數填不存在的值：守衛在 handler
# 之前跑，不會真的動到資料。
MONEY_ONLY_PATHS = [
    "/api/v1/crm/invoices",
    "/api/v1/crm/payments",
    "/api/v1/crm/payments/advances",
    "/api/v1/crm/cash-entries",
    "/api/v1/crm/payables/summary",
    "/api/v1/crm/receivables/summary",
    "/api/v1/crm/quotations",
    "/api/v1/crm/quotations/stats",
    "/api/v1/crm/quotation-templates",
    "/api/v1/crm/cost-line-templates",
    "/api/v1/crm/projects/__probe__/expenses",
    "/api/v1/crm/projects/__probe__/financial-summary",
    "/api/v1/crm/projects/__probe__/cost-lines",
    "/api/v1/crm/projects/__probe__/cost-groups",
    "/api/v1/crm/projects/__probe__/cost-summary",
    "/api/v1/crm/staff/__probe__/rate-history",
    "/api/v1/finance/accounts",
    "/api/v1/cashflow/forecast",
]

# 這幾支**另外**還收 Lv3（handler 內的 `_check_auth`）—— 費率史是薪資史，
# 給了金額權也不代表看得到別人的調薪紀錄。所以只驗「沒授權要 403」那一面，
# 正面留給管理員（下方 test_admin_always_sees_money）。
ALSO_ADMIN_ONLY = {"/api/v1/crm/staff/__probe__/rate-history"}


@pytest.mark.parametrize("path", MONEY_ONLY_PATHS)
def test_money_only_endpoints_reject_without_grant(app_client, as_user, path):
    """🔴 有帳務/專案模組但沒有金額權 → 403。

    這批端點拿掉數字就什麼都不剩，所以擋入口而不是抹欄位。
    """
    h = as_user(modules=["crm_invoices", "crm_projects", "crm_quotes"])
    assert app_client.get(path, headers=h).status_code == 403, \
        f"{path} 沒有 money_view 也讀得到"


@pytest.mark.parametrize(
    "path", [p for p in MONEY_ONLY_PATHS if p not in ALSO_ADMIN_ONLY])
def test_money_only_endpoints_open_with_grant(app_client, as_user, path):
    """反面：授權了就不該再被權限擋掉（單元環境沒有 DB → 503 是正常的）。"""
    h = as_user(modules=["crm_invoices", "crm_projects", "crm_quotes", "money_view"])
    assert app_client.get(path, headers=h).status_code not in (401, 403), \
        f"{path} 授權了還是被擋"


def test_admin_always_sees_money(app_client, as_user):
    """管理員（Lv3）一律通過 —— 他們本來就能改所有東西，另外擋是自欺。"""
    h = as_user(modules=[], access_level=3)
    assert app_client.get("/api/v1/crm/invoices",
                          headers=h).status_code not in (401, 403)


# ── 第二層：欄位抹除 ────────────────────────────────────────

def test_redact_drops_keys_not_zeroes_them():
    """🔴 刪鍵不是歸零：前端 `x.contract_amount || 0` 會把 0 畫成真的金額。"""
    out = redact({"name": "A", "contract_amount": 500000, "status": "製作"})
    assert "contract_amount" not in out
    assert out == {"name": "A", "status": "製作"}


def test_redact_walks_nested_lists_and_dicts():
    data = {"staff": [{"staff_name": "王", "days": 3, "rate": 8000, "cost": 24000}],
            "meta": {"nested": {"daily_rate": 9000, "keep": 1}}}
    out = redact(data)
    assert out["staff"][0] == {"staff_name": "王", "days": 3}
    assert out["meta"]["nested"] == {"keep": 1}


def test_rate_is_redacted_because_days_stays():
    """🔴 `cost = days × rate`，而 days（檔期）是要留給企劃看的 —— 所以 rate
    系列一定要抹，否則拿人名去 /crm/staff 查日費、乘上天數就還原了。"""
    for k in ("rate", "rate_override", "daily_rate", "hourly_rate", "default_rate"):
        assert k in MONEY_FIELDS, f"{k} 不在名單裡 → cost 抹了等於沒抹"
    assert "days" not in MONEY_FIELDS, "days 是檔期不是錢，抹掉企劃就看不到誰在哪幾天"


@pytest.mark.parametrize("key", ["total", "expense", "amount", "quantity", "tax_rate"])
def test_generic_names_stay_out_of_the_registry(key):
    """🔴 泛名不准進名單（2026-08-15 實查，收了會靜默打壞現有畫面）：

    - `total` 在 CRM 是**列數**（clients/projects/quotes/finance 的 len(rows)），
      flow.py 還拿它當五軌完成條的分母 → 抹掉＝進度條變 0/0
    - `expense` 在雜支端點是 `{"id": ...}` 這個**物件**的鍵（公開登記頁在用）
    - `amount`/`quantity`/`tax_rate` 同理：不是錢，或只出現在整支 403 的端點上
    """
    assert key not in MONEY_FIELDS


def _stub_app(payload_factory):
    """掛了 MoneyRedactRoute 的極小 app —— 驗的是 route class 本身，不碰 DB。"""
    r = APIRouter(route_class=MoneyRedactRoute)

    @r.get("/probe")
    async def probe():
        return payload_factory()

    app = FastAPI()
    app.include_router(r, prefix="/x")
    return TestClient(app, raise_server_exceptions=False)


def test_route_class_redacts_for_ungranted_and_passes_through_for_granted(as_user):
    body = {"name": "A", "contract_amount": 1, "rows": [{"cost": 2, "days": 3}]}
    c = _stub_app(lambda: dict(body))

    plain = c.get("/x/probe", headers=as_user(modules=["crm_projects"])).json()
    assert plain == {"name": "A", "rows": [{"days": 3}]}

    granted = c.get("/x/probe", headers=as_user(modules=["money_view"])).json()
    assert granted == body, "有授權卻被抹了"


def test_route_class_leaves_anonymous_responses_without_money_untouched():
    """沒有金額的回應原樣返回 —— 不該因為經過這層就被重新序列化。"""
    c = _stub_app(lambda: {"ok": True, "total": 7})
    assert c.get("/x/probe").json() == {"ok": True, "total": 7}
