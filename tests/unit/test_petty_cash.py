# -*- coding: utf-8 -*-
"""零用金請款的權限邊界（docs/PETTY_CASH_PLAN.md §4）。

這裡守的是一句話裡的**兩個相反方向**：

    「自己的錢一律看得到；別人的錢要授權。」

前半句會被 `money_view` 的預設（不給）打壞 —— 只要有人把 `total_claim` 之類
的鍵加進 `MONEY_FIELDS`，員工就看不到自己墊了多少。
後半句會被 own-scope 的寬鬆打壞 —— 只要有人在 `/petty/me` 那族接受 client 傳
`staff_id`，或忘了在財務視角掛 `money_dep`，全公司的錢就攤開了。

兩個方向各有正反面，缺一邊測試就會在「守衛整個被拿掉」時照樣全綠。
"""
import re
from pathlib import Path

import pytest

from core.money import MONEY_FIELDS, _OWN_SCOPE
from core.schemas import PettyExpensePayload

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PROJECT_HTML = (FRONTEND / "project.html").read_text(encoding="utf-8")

OWN_SCOPE_PATHS = ["/api/v1/crm/petty/me", "/api/v1/crm/petty/options"]
FINANCE_PATHS = [
    "/api/v1/crm/petty/accounts",
    "/api/v1/crm/petty/claims",
    "/api/v1/crm/petty/unbound-labels",
]
WRITE_PATHS = [
    ("post", "/api/v1/crm/petty/expenses", {"actual": 100, "summary": "咖啡"}),
    ("post", "/api/v1/crm/petty/submit", {}),
    ("put", "/api/v1/crm/petty/expenses/__probe__", {"actual": 1}),
    ("delete", "/api/v1/crm/petty/expenses/__probe__", None),
]


# ── 匿名：一支都不准進來（寫入路徑不在 GET 掃描那條網子裡）──────────────
@pytest.mark.parametrize("method,path,body", WRITE_PATHS)
def test_writes_reject_anonymous(app_client, method, path, body):
    kw = {"json": body} if body is not None else {}
    assert getattr(app_client, method)(path, **kw).status_code == 401


# ── 自己的錢：沒有 money_view 也要看得到 ──────────────────────────────
@pytest.mark.parametrize("path", OWN_SCOPE_PATHS)
def test_own_scope_needs_no_money_view(app_client, as_user, path):
    """🔴 員工預設沒有 `money_view`（owner 的政策就是不給）。

    如果這裡變成 403，畫面上的症狀是「我墊的錢自己看不到」—— 那不是資安，
    是把功能關掉。單元環境沒有 DB → 503/409 都算過，釘的是**不是被權限擋掉**。
    """
    r = app_client.get(path, headers=as_user(modules=["me_finance"]))
    assert r.status_code not in (401, 403), f"{path} 被權限擋掉：{r.status_code}"


@pytest.mark.parametrize("method,path,body", WRITE_PATHS)
def test_own_scope_writes_need_no_money_view(app_client, as_user, method, path, body):
    kw = {"headers": as_user(modules=["me_finance"])}
    if body is not None:
        kw["json"] = body
    r = getattr(app_client, method)(path, **kw)
    assert r.status_code not in (401, 403), f"{method} {path}：{r.status_code}"


def test_payload_has_no_staff_id():
    """🔴 請款人只能從 token 解。

    schema 上一旦出現 `staff_id`，「登記一筆給別人」就成立了 —— 而且是**寫入**
    方向的越權，比讀取更難事後發現。這條釘的是那個欄位永遠不存在。
    """
    assert "staff_id" not in PettyExpensePayload.model_fields
    assert "payee" not in PettyExpensePayload.model_fields


# ── 別人的錢：兩個權限都要 ────────────────────────────────────────────
@pytest.mark.parametrize("path", FINANCE_PATHS)
def test_finance_views_reject_plain_employee(app_client, as_user, path):
    assert app_client.get(path, headers=as_user(modules=["me_finance"])
                          ).status_code == 403


@pytest.mark.parametrize("path", FINANCE_PATHS)
def test_finance_views_reject_approver_without_money_view(app_client, as_user, path):
    """🔴 只有審核權、沒有金額權 → 還是 403。

    審核畫面本身就是別人的金額，所以 `money_dep` 那一層不能因為「他是審核者」
    就跳過。反過來只有 money_view 沒有審核權的情況由下一條守。
    """
    assert app_client.get(path, headers=as_user(modules=["finance_approve"])
                          ).status_code == 403


@pytest.mark.parametrize("path", FINANCE_PATHS)
def test_finance_views_reject_money_view_without_approver(app_client, as_user, path):
    assert app_client.get(path, headers=as_user(modules=["money_view"])
                          ).status_code == 403


@pytest.mark.parametrize("path", FINANCE_PATHS)
def test_finance_views_allow_both(app_client, as_user, path):
    r = app_client.get(path, headers=as_user(
        modules=["money_view", "finance_approve"]))
    assert r.status_code not in (401, 403), f"{path} 兩權齊備仍被擋：{r.status_code}"


# ── 抹除層不准把「自己的錢」抹掉 ───────────────────────────────────────
# ── 代為登記：財務端管理所有人的零用金 ────────────────────────────────
DELEGATE = [
    ("get", "/api/v1/crm/petty/staff-options", None),
    ("get", "/api/v1/crm/petty/staff/__probe__", None),
    ("post", "/api/v1/crm/petty/staff/__probe__/expenses",
     {"actual": 100, "summary": "咖啡"}),
    ("post", "/api/v1/crm/petty/staff/__probe__/submit", {}),
]


@pytest.mark.parametrize("method,path,body", DELEGATE)
def test_delegate_rejects_plain_employee(app_client, as_user, method, path, body):
    """🔴 代為登記＝替別人記帳。只有審核者能做，一般員工連讀都不行。"""
    kw = {"headers": as_user(modules=["me_finance"])}
    if body is not None:
        kw["json"] = body
    assert getattr(app_client, method)(path, **kw).status_code == 403


@pytest.mark.parametrize("method,path,body", DELEGATE)
def test_delegate_needs_both_grants(app_client, as_user, method, path, body):
    for mods in (["finance_approve"], ["money_view"]):
        kw = {"headers": as_user(modules=mods)}
        if body is not None:
            kw["json"] = body
        assert getattr(app_client, method)(path, **kw).status_code == 403, mods


@pytest.mark.parametrize("method,path,body", DELEGATE)
def test_delegate_allows_approver(app_client, as_user, method, path, body):
    kw = {"headers": as_user(modules=["money_view", "finance_approve"])}
    if body is not None:
        kw["json"] = body
    r = getattr(app_client, method)(path, **kw)
    assert r.status_code not in (401, 403), f"{method} {path}：{r.status_code}"


def test_delegate_is_a_separate_endpoint_not_an_optional_field():
    """🔴 代管走**路徑參數**，own-scope 的 schema 仍然沒有 staff_id。

    低阻力的寫法是給 `/petty/expenses` 加一個可選的 staff_id、沒帶就當自己 ——
    那會讓「漏檢一次守衛」直接等於「任何人都能替別人記帳」。分成兩組端點之後，
    守衛寫在路徑上，漏不掉。
    """
    assert "staff_id" not in PettyExpensePayload.model_fields
    src = (Path(__file__).resolve().parents[2]
           / "routers" / "crm" / "petty.py").read_text(encoding="utf-8")
    own = src.split("async def add_my_petty_expense")[1].split("\n@router")[0]
    assert "_my_staff(request)" in own and "staff.id" in own
    assert "body.staff_id" not in src, "own-scope 端點不准從 body 取 staff_id"


# ── 審核／匯款（P2）──────────────────────────────────────────────────
APPROVE_WRITES = [
    "/api/v1/crm/petty/claims/__probe__/approve",
    "/api/v1/crm/petty/claims/__probe__/reject",
    "/api/v1/crm/petty/claims/__probe__/pay",
    "/api/v1/crm/petty/claims/__probe__/lines/__x__/reject",
]


@pytest.mark.parametrize("path", APPROVE_WRITES)
def test_approval_writes_reject_anonymous(app_client, path):
    assert app_client.post(path).status_code == 401


@pytest.mark.parametrize("path", APPROVE_WRITES)
def test_approval_writes_reject_plain_employee(app_client, as_user, path):
    """🔴 送單的人不能自己核准自己的單。"""
    assert app_client.post(path, headers=as_user(modules=["me_finance"])
                           ).status_code == 403


@pytest.mark.parametrize("path", APPROVE_WRITES)
def test_approval_writes_need_both_grants(app_client, as_user, path):
    assert app_client.post(path, headers=as_user(modules=["finance_approve"])
                           ).status_code == 403
    assert app_client.post(path, headers=as_user(modules=["money_view"])
                           ).status_code == 403


def test_submit_auto_approves_and_builds_aps():
    """🔴 owner 2026-08-17：**預設直接同意**，送出即成立、即產應付款。

    這條錨在 submit 的程式碼上，因為錯法很安靜：把 status 寫回「待審」而忘了
    也要拿掉 `_build_aps`，畫面上看起來一樣，只有「錢沒進匯款清冊」——
    而那要等到有人問「我的請款怎麼還沒下來」才會發現。
    """
    src = (Path(__file__).resolve().parents[2]
           / "routers" / "crm" / "petty.py").read_text(encoding="utf-8")
    # 錨在 `_submit_for` —— 送出的本體抽成函式後（本人送出／代為送出共用），
    # 錨在端點上會失明
    submit = src.split("async def _submit_for")[1].split("\n@router")[0]
    assert 'status="已核准"' in submit, "送出後的狀態不是已核准"
    assert "_build_aps(" in submit, "送出時沒有產應付款"
    assert "_assert_month_open(" in submit, "送出沒有檢查月結鎖帳"


def test_reject_clears_the_generated_aps():
    """🔴 退回一定要收掉應付款，否則帳上留下**幽靈負債**。

    單據回到本人草稿、應付帳款卻還掛著那筆錢 —— 月結與現金流預測都會多算，
    而且沒有任何畫面會顯示這個矛盾。三條退回路徑都必須經過 `_clear_aps`。
    """
    src = (Path(__file__).resolve().parents[2]
           / "routers" / "crm" / "petty.py").read_text(encoding="utf-8")
    for fn in ("reject_claim", "reject_claim_line"):
        body = src.split(f"async def {fn}")[1].split("\n@router")[0]
        assert "_clear_aps(" in body, f"{fn} 沒有撤掉應付款"
    # 已付款的不准撤（那筆錢真的出去了）
    clear = src.split("async def _clear_aps")[1].split("\n@router")[0]
    assert "已付款" in clear and "409" in clear


def test_line_reject_rebuilds_aps_instead_of_patching_them():
    """抽掉一行後**重建** AP，而不是就地改金額。

    就地改要處理「整個 (項目×月份) 桶消失」等分支，等於養出第二套 AP 邏輯；
    撤掉重建走的是與送出時同一段程式碼，不會漂。
    """
    src = (Path(__file__).resolve().parents[2]
           / "routers" / "crm" / "petty.py").read_text(encoding="utf-8")
    body = src.split("async def reject_claim_line")[1].split("\n@router")[0]
    assert "_clear_aps(" in body and "_build_aps(" in body


def test_ap_buckets_split_by_item_and_month():
    """🔴 應付款依「會計項目 × 認列月份」拆。

    合成一張的話會同時壞掉兩件事：科目軸被壓平成「零用金」（帳上看得到錢、
    看不出花在哪），而跨月的批次會把七月的錢認到八月（月度損益就歪了）。
    """
    from datetime import datetime

    from routers.crm.petty import _ap_buckets

    class R:
        def __init__(self, item, d, amt):
            self.item, self.actual = item, amt
            self.expense_date = datetime(2026, *d) if d else None

    rows = [R("行政", (7, 3), 100), R("行政", (7, 20), 50),
            R("行政", (8, 1), 30), R("專案雜支", (7, 9), 200),
            R("其他", None, 7)]
    b = _ap_buckets(rows)
    assert {k: sum(r.actual for r in v) for k, v in b.items()} == {
        ("行政", "2026-07"): 150,
        ("行政", "2026-08"): 30,
        ("專案雜支", "2026-07"): 200,
        ("其他", ""): 7,          # 沒日期的不猜月份，自成一桶
    }


def test_ap_category_is_the_item_not_the_word_petty_cash():
    """🔴 應付款的 category 不准寫死成「零用金」。

    種子裡 ('payment','零用金') 的 treatment 是 **transfer**（撥補備用金＝現金在
    帳戶間搬家），掛上去整批請款會從損益表消失。這條釘住那個字不出現在產生
    應付款的程式碼裡。
    """
    src = (Path(__file__).resolve().parents[2]
           / "routers" / "crm" / "petty.py").read_text(encoding="utf-8")
    # 錨在 `_build_aps` —— 產 AP 的地方只有這一處（送出／核准／逐行退回重建
    # 三條路都走它）。錨在某一支端點上的話，邏輯一被抽成函式測試就失明。
    build = src.split("async def _build_aps")[1].split("\nasync def ")[0]
    assert "category=item" in build.replace(" ", "")
    assert 'category="零用金"' not in src


def test_payment_source_mapping_covers_the_expense_items():
    """🔴 費用認列查的是 ('payment', category) —— 對映缺了就掉進「未歸類支出」。

    這條錨在種子上：source='cash' 有的費用項目，source='payment' 也要有，
    而且**科目要一致**（同一筆錢不管走收支還是走請款單都該落同一科目）。
    """
    from db.seed_finance import SEED_CATEGORY_MAP

    cash = {r["category_text"]: r["account_code"] for r in SEED_CATEGORY_MAP
            if r["source"] == "cash" and r["treatment"] == "direct_expense"}
    pay = {r["category_text"]: r["account_code"] for r in SEED_CATEGORY_MAP
           if r["source"] == "payment"}
    from routers.crm.petty import FALLBACK_ITEMS
    missing = [i for i in FALLBACK_ITEMS if i not in pay]
    assert not missing, f"這些零用金項目在 payment 側沒有科目對映：{missing}"
    drift = {i: (cash[i], pay[i]) for i in FALLBACK_ITEMS
             if i in cash and pay[i] != cash[i]}
    assert not drift, f"同一項目在 cash / payment 兩側科目不一致：{drift}"


# ── 專案頁的「支出」分頁（P3）────────────────────────────────────────
def test_every_lazy_tab_module_exists():
    """🔴 分頁的模組路徑打錯，只有在**有人點那顆鈕**時才會發現。

    這條錨在 `_LAZY_TABS` 那張表上，所以之後新增分頁自動受檢 —— 不是為「支出」
    手寫一條（那種測試跟它要防的疏漏同時失效）。
    """
    mods = re.findall(r'mod:\s*"(/[^"]+\.js)"', PROJECT_HTML)
    assert len(mods) >= 6, f"只掃到 {len(mods)} 個 lazy tab，掃描器八成壞了"
    missing = [m for m in mods if not (FRONTEND / m.lstrip("/")).exists()]
    assert not missing, f"這些分頁模組路徑指向不存在的檔案：{missing}"


def test_expense_tab_is_money_gated_in_the_page():
    """🔴 沒有金額權限的人**整顆鈕都不該出現**。

    畫出來然後點進去 403／一片空白，比不畫更糟：那看起來像壞掉，而不是像
    「你沒有權限」。這條要求每一個掛載點都被 `_CAN_MONEY` 包住。
    """
    mounts = re.findall(r'^\s*(.*?)_setupLazyTab\("expense"', PROJECT_HTML, re.M)
    assert mounts, "project.html 沒有掛「支出」分頁"
    ungated = [m for m in mounts if "_CAN_MONEY" not in m]
    assert not ungated, f"有 {len(ungated)} 個掛載點沒有 money_view 閘門"


def test_project_page_imports_stay_inside_the_closure():
    """支出元件住 tabs/proposals/ —— /project.html 的 import 閉包只准那裡與 js/shared。

    （閉包本身由 test_public_surface 逐檔驗；這條只釘「不要哪天順手搬去
    tabs/petty/」—— 那個目錄不在 MODULE_DIRS 裡，搬過去對外頁就 404。）
    """
    from core.public_assets import MODULE_DIRS
    assert "tabs/proposals" in MODULE_DIRS and "tabs/petty" not in MODULE_DIRS
    assert (FRONTEND / "tabs/proposals/expense-view.js").exists()


def test_both_hosts_share_one_fetch_contract():
    """🔴 零用金元件有**兩個宿主**（獨立頁 /petty-cash.html、CRM 財務子視圖）。

    元件把 body 當**物件**交出去，由宿主的 fetch 包裝 stringify（對齊
    `js/shared/utils.authFetch`）。任一邊改成自己 stringify，就變成雙重編碼，
    後端收到的是一個 JSON 字串而不是物件 → 422，而且只壞一個宿主。
    """
    view = (FRONTEND / "tabs" / "petty" / "petty-view.js").read_text(encoding="utf-8")
    send = view.split("async function send(")[1].split("\n}")[0]
    assert "JSON.stringify" not in send, "元件不該自己 stringify（宿主負責）"

    standalone = (FRONTEND / "petty-cash.html").read_text(encoding="utf-8")
    mfetch = standalone.split("async function mfetch(")[1].split("\n}")[0]
    assert "JSON.stringify(opts.body)" in mfetch, "獨立頁的 mfetch 沒有 stringify"

    sub = (FRONTEND / "tabs" / "finance" / "subviews" / "petty.js").read_text(encoding="utf-8")
    assert "mfetch: authFetch" in sub, "子視圖應直接接 authFetch（同一份合約）"
    # 上傳例外：authFetch 會補 JSON header 並 stringify FormData
    assert "bearerHeader()" in sub, "FormData 上傳不能走 authFetch"


def test_petty_subview_is_registered_in_the_finance_nav():
    """側欄有按鈕、subviews/ 有對應檔案 —— 少一邊就是點了沒反應。"""
    nav = (FRONTEND / "tabs" / "finance" / "finance.html").read_text(encoding="utf-8")
    names = re.findall(r'data-subview="([^"]+)"', nav)
    assert "petty" in names, "財務管理側欄沒有零用金"
    missing = [n for n in names
               if not (FRONTEND / "tabs" / "finance" / "subviews" / f"{n}.js").exists()]
    assert not missing, f"側欄按鈕指向不存在的子視圖：{missing}"


def test_own_scope_fields_are_not_redacted():
    """🔴 `_OWN_SCOPE` 與 `MONEY_FIELDS` 必須互斥。

    同時出現＝有人把它加進抹除名單卻沒發現另一張表已經說了「這要給本人看」。
    後果不是報錯而是**畫面上一片空白**，而空白看起來很像「還沒登記」。
    """
    both = sorted(set(_OWN_SCOPE) & MONEY_FIELDS)
    assert not both, f"這幾筆同時要抹又要給本人看：{both}"
