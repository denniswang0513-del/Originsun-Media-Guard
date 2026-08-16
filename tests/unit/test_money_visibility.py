# -*- coding: utf-8 -*-
"""金額檢視授權 `money_view`（owner 2026-08-15「預設不要看到金額，除非我授權」）。

政策、名單與每一條排除的理由都在 `core/money.py`；設計背景在
`docs/MONEY_VISIBILITY.md`。這裡只驗行為，不重抄理由（同一段話抄三份，改的時候
只會改到一份）。

兩層各自要有正反面：
  ① 整支擋：帳務／報價／成本明細／費率史 —— 沒授權 403、有授權不是 403
  ② 欄位抹除：夾帶金額的端點把鍵**刪掉**（不是歸零）
  ③ 兩道會自己長大的網：名單錨在 `db/models.py` 的欄位、守衛錨在真的路由表
"""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRouter
from fastapi.testclient import TestClient

from core.money import (_PREFILTER, MONEY_FIELDS, REGISTRY_EXEMPT,
                        MoneyRedactRoute, redact)

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


# ── 兩道「會自己長大」的網 ─────────────────────────────────
# 上面那些是手寫清單，守得住今天。這兩條錨在**會自己長的東西**上（DB 欄位、
# 路由表），所以明天有人加了東西忘了表態，它們才會紅。
#
# 這正是 2026-08-15 第一版缺的：docs 承諾「列舉式，將來誰新增端點漏了會紅」，
# 實際落地的卻是手抄路徑清單 —— 而同一次疏漏就漏掉了 `transfer_fee`
# （帳款匯費，每一支專案清單都在回）。

_MONEYISH = re.compile(r"amount|cost|rate|price|fee|budget|balance|salary|profit")


def test_registry_covers_money_columns():
    """🔴 名單要跟得上 model：掃 db/models.py 的 Column 名，像錢的都要表態。

    人對著 model 抄一遍名單，而 model 會自己長 —— 同一個人同一次疏漏會同時漏掉
    名單與手寫測試，沒有任何一步會變紅。錨在 schema 才抓得到。
    """
    src = (Path(__file__).resolve().parents[2] / "db" / "models.py").read_text(encoding="utf-8")
    cols = set(re.findall(r"^\s{4}(\w+)\s*=\s*Column\(", src, re.M))
    missing = sorted(c for c in cols
                     if _MONEYISH.search(c)
                     and c not in MONEY_FIELDS and c not in REGISTRY_EXEMPT)
    assert not missing, (
        f"這些 model 欄位名字像錢，卻既不在 MONEY_FIELDS 也不在 REGISTRY_EXEMPT："
        f"{missing}。是錢就加進 MONEY_FIELDS；不抹就加進 REGISTRY_EXEMPT 並寫下理由。")


def test_money_paths_are_guarded():
    """🔴 守衛要跟得上路由表：路徑本身就在說「我是錢」的 CRM GET，一律要 403。

    手抄 18 條路徑守不住「明天有人加了 /payments/foo 忘了掛守衛」。這條掃真的
    路由表，新端點只要路徑帶那些字就自動進來被驗。
    """
    import main
    from routers.crm._shared import CRM_PREFIX

    from core.auth import create_token
    tok = create_token({"sub": "u", "username": "u", "access_level": 1,
                        "modules": ["crm_invoices", "crm_quotes", "crm_projects"]})
    h = {"Authorization": "Bearer " + tok}
    from fastapi.testclient import TestClient
    client = TestClient(main.app, raise_server_exceptions=False)

    money_path = re.compile(
        r"/(invoices|payments|cash-entries|payables|receivables|quotations"
        r"|cost-lines|cost-line-templates|cost-groups|cost-summary"
        r"|financial-summary|rate-history)")
    checked, leaked = 0, []
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(CRM_PREFIX) or "GET" not in (getattr(route, "methods", None) or set()):
            continue
        # `/public/…` 是刻意匿名的工作面（手機雜支登記頁在用）—— 它們不該 403，
        # 身上的金額欄由第二層抹（例如拍攝日預算 budget_amount）。
        if not money_path.search(path) or "/public/" in path:
            continue
        probe = re.sub(r"\{[^}]+\}", "__probe__", path)
        checked += 1
        if client.get(probe, headers=h).status_code != 403:
            leaked.append(path)

    assert checked >= 15, f"只掃到 {checked} 支金額端點，掃描邏輯可能壞了"
    assert not leaked, f"{len(leaked)} 支路徑就是錢的端點沒擋：{leaked}"


def test_prefilter_tokens_cover_every_money_field():
    """🔴 預篩是**嚴格超集**才安全：任何一個 MONEY_FIELDS 的鍵，它的字面裡
    都必須含至少一個 token —— 否則那個欄位會整份跳過抹除、直接洩出去。

    加新欄位（例如某個 `..._deposit`）時若不含任何 token，這條會紅，逼人補
    token 而不是靜默漏抹。
    """
    tokens = [t.decode() for t in _PREFILTER]
    uncovered = sorted(f for f in MONEY_FIELDS if not any(t in f for t in tokens))
    assert not uncovered, f"這些欄位不含任何預篩 token，會整份跳過抹除：{uncovered}"


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
