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

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    """讀 repo 內的原始碼（跟 cwd 無關）。"""
    return (_ROOT / rel).read_text(encoding="utf-8")



from core.money import (_PENDING_OWNER, _PREFILTER, MONEY_FIELDS,
                        REGISTRY_EXEMPT, SCANNER_EXACT, MoneyRedactRoute, redact)

# 整支就是錢 → 有授權才進得去。路徑參數填不存在的值：守衛在 handler 之前跑，
# 不會真的動到資料。
#
# 這張表只服務**正面**那條（授權了不該被擋）。反面（沒授權要 403）由下方
# `test_money_paths_are_guarded` 掃真的路由表獨佔 —— 一開始兩邊各有一張清單，
# 當場就漂了（`expenses` / `quotation-templates` 在這裡、正則裡沒有）。
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


# CRM 之外的兩支「整支都是錢」的 router。路由掃描只走 CRM 前綴，所以它們的
# 反面得自己驗 —— 🔴 路由掃描只走 CRM 前綴，這兩支不在裡面。實測：把
# `api_finance._guard` 的 `check_money(request)` 拿掉，整份測試照樣全綠。
NON_CRM_MONEY_PATHS = [p for p in MONEY_ONLY_PATHS
                       if not p.startswith("/api/v1/crm/")]


@pytest.mark.parametrize("path", NON_CRM_MONEY_PATHS)
def test_non_crm_money_routers_reject_without_grant(app_client, as_user, path):
    """有帳務/專案模組但沒有金額權 → 財務管理與現金流整支 403。"""
    h = as_user(modules=["crm_invoices", "crm_projects"])
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
    """🔴 刪鍵不是歸零（list/dict 混排都要吃）。

    歸零的話前端的 `x.contract_amount || 0` 會把 0 畫成真的金額 —— 等值比較
    就是在釘這件事：鍵得**消失**，不是變成 0。
    """
    data = {"name": "A", "contract_amount": 500000,
            "staff": [{"staff_name": "王", "days": 3, "rate": 8000, "cost": 24000}],
            "meta": {"nested": {"daily_rate": 9000, "keep": 1}}}
    out = redact(data)
    assert out["name"] == "A" and "contract_amount" not in out
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
# 手抄清單漏掉一個欄位是**靜默**的：畫面正常、測試全綠、只是多回一個數字
# （`transfer_fee` 就是這樣漏的 —— 帳款匯費，每一支專案清單都在回）。

# 🔴 錢字清單從 `_PREFILTER` 推導，不另抄一份。兩邊各寫一份必定分岔，而這支
# 測試的存在意義正是「別靠人記得」—— 自己身上重演同一個疏漏最沒說服力。
# 錨在詞首或底線：`curated` 不該因為字中夾著 `rate` 就要人來豁免它
# （實測全部 model 欄位，加錨之後只有它掉出來，沒有任何真的金額欄漏掉）。
_MONEY_RE = re.compile("(^|_)(" + "|".join(t.decode() for t in _PREFILTER) + ")")


def _moneyish(name: str) -> bool:
    """欄名像不像錢：詞彙比對（預篩那份）＋ 幾個必須完整同名的（SCANNER_EXACT）。"""
    return bool(_MONEY_RE.search(name)) or name in SCANNER_EXACT


def test_registry_covers_money_columns():
    """🔴 名單要跟得上 model：掃 db/models.py 的 Column 名，像錢的都要表態。

    人對著 model 抄一遍名單，而 model 會自己長 —— 同一個人同一次疏漏會同時漏掉
    名單與手寫測試，沒有任何一步會變紅。錨在 schema 才抓得到。
    """
    db = Path(__file__).resolve().parents[2] / "db"
    # models_website/ 也要掃 —— `budget_range` 就住在那裡（inquiry.py）。
    # models.py 2026-08-31 拆成套件：掃 db/models/ 底下的段檔（glob 抓 _*.py，
    # __init__ 只有再匯出沒有 Column）。下面那條 >300 的地板就是**這一刻**
    # 接住拆檔的：它在拆完的第一次跑就紅了，而不是靜默變成空集合。
    src = "\n".join(p.read_text(encoding="utf-8")
                    for p in [*sorted((db / "models").glob("_*.py")),
                              *sorted((db / "models_website").glob("*.py"))])
    cols = set(re.findall(r"^\s{4}(\w+)\s*=\s*Column\(", src, re.M))
    # 🔴 地板：這條掃描靠正則認 `Column(`，換成 `mapped_column`、改縮排、或把
    # model 拆檔都會讓它靜默變成空集合 —— 空集合的 `missing` 也是空的，測試照樣
    # 全綠。姊妹測試（掃路由表）本來就有這道地板，這支當初漏了。
    assert len(cols) > 300, f"只掃到 {len(cols)} 個 Column，掃描器八成壞了"

    # 🔴 第二條軸：中文尾註。第一條軸（英文詞彙表）的召回率綁在「下一個人會不會
    # 剛好選中我列的字」上 —— `estimated`/`actual` 就是被人找到之後才手動補進
    # 詞彙表的，失效模式跟它要取代的「人抄名單」一模一樣。而作者用中文寫
    # 「# 預估金額」的機率，遠高於他選中某個英文 token。
    zh = re.compile(r"金額|費用|單價|價格|款項|薪|成本|預算|營收|匯費|稅額|現值|淨值|餘額")
    noted = {m.group(1) for m in re.finditer(
        r"^\s{4}(\w+)\s*=\s*Column\([^\n]*#\s*(.*)$", src, re.M) if zh.search(m.group(2))}

    def caught(col):
        """兩條軸任一命中就算「這名字在說它是錢」。"""
        return _moneyish(col) or col in noted

    missing = sorted(c for c in cols
                     if caught(c)
                     and c not in MONEY_FIELDS and c not in REGISTRY_EXEMPT)
    assert not missing, (
        f"這些 model 欄位名字像錢，卻既不在 MONEY_FIELDS 也不在 REGISTRY_EXEMPT："
        f"{missing}。是錢就加進 MONEY_FIELDS；不抹就加進 REGISTRY_EXEMPT 並寫下理由。")
    # 反向：豁免表裡不准有「這條規則本來就掃不到」的死筆 —— 有人憑直覺加了
    # 一筆，沒有任何一步會告訴他不需要，那張表就開始長無效內容。
    dead = sorted(k for k in REGISTRY_EXEMPT if not caught(k))
    assert not dead, f"REGISTRY_EXEMPT 這幾筆根本不會被掃到，是死筆：{dead}"


def test_pending_owner_items_are_still_pending():
    """🔴 `_PENDING_OWNER` 是「還沒拍板」不是「已經封死」。

    owner 決定要抹的那天，鍵要從那張表**搬進** `MONEY_FIELDS` —— 而不是留一則
    過期的註解說「待決」。兩邊同時有，就是有人只做了一半。
    """
    both = sorted(set(_PENDING_OWNER) & MONEY_FIELDS)
    assert not both, (
        f"這幾筆同時在 _PENDING_OWNER 與 MONEY_FIELDS：{both}。"
        "拍板了就把它從待決表移走，別讓下一個人以為還沒決定。")


def test_the_net_is_never_narrower_than_what_it_guards():
    """🔴 網子不准比它要守的清單窄。

    詞彙表少一個字，「掃 model 找漏網」對那一類欄位就完全失明 —— 一個號稱會
    自己長大的網，比它要 backstop 的手抄清單還窄（`ex_tax` 與 `total_contract`
    實際發生過）。

    註：`MONEY_FIELDS` 有 8 個不是 Column 而是**算出來的鍵**（`cost = days ×
    rate` 那類）。這條只驗詞彙涵蓋，不要求它們在 model 裡找得到。
    """
    blind = sorted(f for f in MONEY_FIELDS if not _moneyish(f))
    assert not blind, f"這些已知的錢欄位，掃描正則自己認不出來：{blind}"


def test_money_paths_are_guarded(app_client, as_user):
    """🔴 守衛要跟得上路由表：路徑本身就在說「我是錢」的 CRM GET，一律要 403。

    手抄清單守不住「明天有人加了 /payments/foo 忘了掛守衛」。這條掃真的路由表，
    新端點只要路徑帶那些字就自動進來被驗 —— 沒授權的一律要 403。
    """
    import main
    from routers.crm._shared import CRM_PREFIX

    h = as_user(modules=["crm_invoices", "crm_quotes", "crm_projects"])
    money_path = re.compile(
        r"/(invoices|payments|cash-entries|payables|receivables"
        r"|quotations|quotation-templates|expenses"
        r"|cost-lines|cost-line-templates|cost-groups|cost-summary"
        r"|financial-summary|rate-history)")
    checked, leaked, scanned = 0, [], set()
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(CRM_PREFIX) or "GET" not in (getattr(route, "methods", None) or set()):
            continue
        # `/public/…` 是刻意匿名的工作面（手機雜支登記頁在用）—— 它們不該 403，
        # 身上的金額欄由第二層抹（例如拍攝日預算 budget_amount）。
        if not money_path.search(path) or "/public/" in path:
            continue
        probe = re.sub(r"\{[^}]+\}", "__probe__", path)
        scanned.add(probe)
        checked += 1
        if app_client.get(probe, headers=h).status_code != 403:
            leaked.append(path)

    assert checked >= 15, f"只掃到 {checked} 支金額端點，掃描邏輯可能壞了"
    assert not leaked, f"{len(leaked)} 支路徑就是錢的端點沒擋：{leaked}"
    # 🔴 掃描不准比手抄清單窄 —— 那張清單正是它要 backstop 的東西。正則少一個
    # 字（例如 `expenses`），那幾支就靜默地不在守備範圍內。
    hand = {p for p in MONEY_ONLY_PATHS if p.startswith(CRM_PREFIX)}
    assert hand <= scanned, f"手抄清單有、掃描沒涵蓋到：{sorted(hand - scanned)}"


def test_every_crm_route_has_the_redact_class():
    """🔴 第二層是掛在 **CRM_PREFIX 這個命名空間**上的性質，不是掛在某個
    router 物件上。

    這兩件事今天幾乎重合，而「幾乎」就是洞：`include_router` 用
    `route_class_override=type(route)` 保留子 router 自己的 class，所以
    `public_router` 那批（公開／手機頁，最可能夾帶金額的去處）原本整組沒有第二層。
    把性質釘在命名空間上，將來有人在 crm/ 底下新建第三個 APIRouter 也會被接住。
    """
    import main
    from core.money import MoneyRedactRoute as _RC
    from routers.crm._shared import CRM_PREFIX

    naked = sorted({r.path for r in main.app.routes
                    if getattr(r, "path", "").startswith(CRM_PREFIX)
                    and not isinstance(r, _RC)})
    assert not naked, f"{len(naked)} 條 CRM 路由沒有金額抹除層：{naked[:8]}"


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


# ── 私帳案對沒有私帳權限的人：整個看不到（owner 2026-08-28 最終拍板）────
# 同一天走過三版：①只抹金額、案子照看 ②推進管線就放寬金額 ③連案子都看不到。
# 前兩版都被 owner 收回，最終是③。這幾個測試就是不讓它再被改回去。
def test_mine_project_amounts_are_always_redacted():
    """`crm_pushed` **不放寬金額** —— 推進管線只決定案子出不出現在管線。"""
    from core.money import redact_mine

    for pushed in (0, 1):
        out = redact_mine({"id": "p", "entity": "mine", "crm_pushed": pushed,
                           "name": "案子", "contract_amount": 500000})
        assert "contract_amount" not in out, f"crm_pushed={pushed} 也要抹"
        assert out["name"] == "案子", "刪的是金額鍵，不是整個物件"


def test_mine_projects_are_hidden_from_viewers_without_scope():
    """沒有私帳權限 → 私帳案**整列不出現**（不是只抹金額）。

    🔴 單筆回 **404 不是 403** —— 403 等於承認「有這個案子只是你不能看」，
    那本身就是洩漏。
    """
    # 規則有名字、住在 core（散落的字面沒有可 grep 的名字）
    led = _read("core/ledger.py")
    assert "def hide_mine_projects(request)" in led
    src = _read("routers/crm/projects.py")
    assert "hide_mine_projects" in src
    lst = src[src.index("async def list_projects("):src.index("async def create_project")]
    # 述詞走 core.ledger.not_mine（那個 helper 的存在理由就是不要有第三份字面）
    assert "not_mine(CrmProject.entity)" in lst
    assert 'CrmProject.entity != "mine"' not in src, "不可再寫字面"
    one = src[src.index("async def get_project(project_id"):][:900]
    assert "_hide_mine(request)" in one and "status_code=404" in one
    assert "status_code=403" not in one


def test_visible_does_not_mean_writable():
    """看得到不等於改得動 —— 那筆錢的歸屬還是私帳的。"""
    src = _read("routers/crm/projects.py")
    assert "私帳案的金額欄只有帳本主人能修改" in src
    seg = src[src.index('if (project.entity or "parent") == "mine":'):][:600]
    assert "crm_pushed" not in seg, "寫入守衛只看 entity，不看 crm_pushed"


def test_every_project_enumeration_decides_about_mine():
    """🔴 掃描：**列舉**專案的查詢都要對「私帳案給不給看」表態。

    owner 2026-08-28「連專案都看不到」。三支專案端點擋了，但這條規則會被下一個
    新端點靜默繞過 —— 所以用掃描釘住，新增的列舉點要嘛套規則、要嘛進豁免名單
    並寫理由。

    分界：**列舉**（選單／清單／挑選視窗）要表態；**用 id 反查名字**不必 ——
    那是使用者已經在看的那一列，藏掉名字只會讓畫面難懂而擋不住任何東西。
    """
    import re

    # 豁免：用 id 反查、或本來就不是給人看的（伺服器端比對）。
    EXEMPT = {
        # id 反查名字（viewer 已經有那一列）
        "routers/api_cashflow.py", "routers/api_equipment.py",
        "routers/api_locations.py", "routers/api_portal.py",
        "routers/api_proposals.py", "routers/api_references.py",
        "routers/crm/staff.py", "routers/crm/clients.py",
        # 伺服器端名稱比對／匯入去重，不回給前端當清單
        # 🔴 2026-08-30 finance.py 拆成四個檔，做「CSV 匯入時用專案名比對」的那段
        # 落到 payments.py —— 豁免的理由沒變（不回給前端當清單），只是換了檔案。
        "routers/crm/payments.py",
        # 工時：查表在 services/timesheet_lookup（is_mine 表態）；router 裡剩的
        # select(CrmProject) 是 burn 摘要與預算回寫，端點整支守 _require_mine_admin
        "routers/api_timesheets.py",
        # 手填的專案下拉（進行中案的 id／名，不帶錢）：CRM tab（timesheets 模組）與員工頁
        # /me/timesheet_options（me_finance）都用；員工填工時本來就要選到私帳案名（對映只認私帳）
        "services/timesheet_manual.py",
        "routers/crm/proposal_assets.py", "routers/crm/flow.py",
        # 帳本自己的視角（entity 已經圈定範圍）
        "routers/api_finance_projects.py", "routers/api_finance_stmt.py",
        "services/finance_statements.py",
        # 拆項的未收案選單：require_entity(mine, level="full") 守在端點上 ——
        # 進得來的人本來就有私帳 full scope，比 hide_mine_projects 更嚴
        "routers/crm/cash_splits.py",
        # 官網／公開頁（另一套可見性：作品要上架才出得去）
        "services/website/project_service.py",
        "services/website/initiative_service.py",
        "services/media_log_catchup.py", "routers/crm/media_log.py",
    }
    hits = []
    for rel in ("routers", "services"):
        for f in sorted((_ROOT / rel).rglob("*.py")):
            key = f.relative_to(_ROOT).as_posix()
            if key in EXEMPT:
                continue
            src = f.read_text(encoding="utf-8")
            if not re.search(r"select\(\s*CrmProject[.,)]", src):
                continue
            if ("hide_mine_projects" in src or "not_mine(CrmProject.entity)" in src
                    or "is_mine(CrmProject.entity)" in src):
                continue
            hits.append(key)
    assert not hits, (
        "這些檔案列舉了專案卻沒對私帳可見性表態 —— 套 "
        "core.ledger.hide_mine_projects / not_mine / is_mine，或加進 EXEMPT 並寫理由：\n  "
        + "\n  ".join(hits))
