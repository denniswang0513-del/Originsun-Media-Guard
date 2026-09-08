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
from tests.unit._srcscan import js_code_only, js_func_body


import pytest

from core.money import MONEY_FIELDS, _OWN_SCOPE
from core.schemas import PettyExpensePayload

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
PROJECT_HTML = (FRONTEND / "project.html").read_text(encoding="utf-8")
PETTY_SRC = (REPO / "routers" / "crm" / "petty.py").read_text(encoding="utf-8")
PETTY_VIEW = (FRONTEND / "tabs" / "petty" / "petty-view.js").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """petty.py 裡某個 endpoint/函式的本文切片（源碼錨定測試共用）。"""
    return PETTY_SRC.split(f"async def {name}")[1].split("\n@router")[0]


# 權限矩陣：三把鑰匙各自單獨都**不夠**（財務視角要 money_view + finance_approve）
LACKING = (["me_finance"], ["money_view"], ["finance_approve"])

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
@pytest.mark.parametrize("mods", LACKING)
@pytest.mark.parametrize("path", FINANCE_PATHS)
def test_finance_views_reject_insufficient_grants(app_client, as_user, path, mods):
    """🔴 三把鑰匙各自單獨都 403 —— 含「只有審核權沒金額權」：審核畫面本身就是
    別人的金額，`money_dep` 那層不能因為他是審核者就跳過。"""
    assert app_client.get(path, headers=as_user(modules=mods)).status_code == 403


@pytest.mark.parametrize("path", FINANCE_PATHS)
def test_finance_views_allow_both(app_client, as_user, path):
    r = app_client.get(path, headers=as_user(
        modules=["money_view", "finance_approve"]))
    assert r.status_code not in (401, 403), f"{path} 兩權齊備仍被擋：{r.status_code}"


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
    for mods in LACKING:
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


def test_ledger_endpoints_need_both_grants(app_client, as_user):
    for mods in LACKING:
        h = as_user(modules=mods)
        assert app_client.get("/api/v1/crm/petty/entries", headers=h).status_code == 403
        assert app_client.patch("/api/v1/crm/petty/entries/__probe__", headers=h,
                                json={"item": "行政"}).status_code == 403


def test_ledger_patch_has_a_field_whitelist():
    """🔴 就地編輯只准改分類與敘述，不准改 `status` / `claim_id`。

    帳冊頁的每一格都直接打 PATCH，白名單漏一個欄位＝畫面上多一個可以把單據
    「改成已付款」的洞。這條錨在白名單常數上。
    """
    body = PETTY_SRC.split("async def patch_petty_entry")[1].split("\n@router")[0]
    assert "allowed = {" in body
    for forbidden in ("status", "claim_id", "receipt_url"):
        assert f'"{forbidden}"' not in body.split("allowed = {")[1].split("}")[0], forbidden
    # 已產應付款的列不准改分類（科目與認列月份已入帳）
    assert "reimbursement_id == exp.claim_id" in body and "409" in body


def test_ledger_shows_new_drafts_too():
    """🔴 帳冊的判準是「有 item」，不是「有 claim_id」。

    用 claim_id 當判準的話，剛在這一頁新增的草稿（還沒送出、沒有 claim_id）
    會看不見 —— 使用者按了「新增」、資料真的寫進去了、畫面卻沒動。那種
    「成功但看起來失敗」比報錯更難查。舊的專案雜支沒有 item，照樣擋在外面。
    """
    body = PETTY_SRC.split("async def petty_entries")[1].split("\n@router")[0]
    assert "CrmProjectExpense.item.isnot(None)" in body
    assert "CrmProjectExpense.claim_id.isnot(None)" not in body


def test_overview_lists_everyone_with_history_not_just_debtors():
    """🔴 帳戶總覽的判準是「曾經有過單據」，不是「現在還欠他錢」。

    只列未結的話這頁就是匯款清冊的複本，而 owner 要的是全貌（誰用過、誰沒綁
    帳號、歷史付了多少）。這條錨在查詢條件上：`HAVING` 那一段若退回成
    「只看未結」，斷言會紅。
    """
    body = PETTY_SRC.split("async def petty_accounts")[1].split("\n@router")[0]
    assert "bound_user" in body, "帳戶總覽沒有綁定狀態"
    assert "User.staff_id" in body, "綁定狀態沒有去 users 撈"
    assert "paid_total" in body and "rows" in body, "缺歷史累計"
    # 沒有紀錄也沒有備用金也沒欠款的人才跳過 —— 不是「沒有未結就跳過」
    assert "if not a and not float_amt and not owe" in body
    # 🔴 那個 continue 必須在補預設值之前 —— 補完 `a` 永遠是 dict、條件永遠不成立，
    # 149 個人會全部湧進匯款清冊（2026-08-17 踩過，生產上真的變成 150 列）
    skip_at = body.index("if not a and not float_amt and not owe")
    default_at = body.index('a = a or {"draft"')
    assert skip_at < default_at, "跳過條件被排在補預設值之後 → 永遠不會跳過"


def test_delegate_is_a_separate_endpoint_not_an_optional_field():
    """🔴 代管走**路徑參數**，own-scope 的 schema 仍然沒有 staff_id。

    低阻力的寫法是給 `/petty/expenses` 加一個可選的 staff_id、沒帶就當自己 ——
    那會讓「漏檢一次守衛」直接等於「任何人都能替別人記帳」。分成兩組端點之後，
    守衛寫在路徑上，漏不掉。
    """
    assert "staff_id" not in PettyExpensePayload.model_fields
    own = PETTY_SRC.split("async def add_my_petty_expense")[1].split("\n@router")[0]
    assert "_my_staff(request)" in own and "staff.id" in own
    assert "body.staff_id" not in PETTY_SRC, "own-scope 端點不准從 body 取 staff_id"


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
    # 錨在 `_submit_for` —— 送出的本體抽成函式後（本人送出／代為送出共用），
    # 錨在端點上會失明
    submit = PETTY_SRC.split("async def _submit_for")[1].split("\n@router")[0]
    assert 'status="已核准"' in submit, "送出後的狀態不是已核准"
    assert "_build_aps(" in submit, "送出時沒有產應付款"
    assert "_assert_month_open(" in submit, "送出沒有檢查月結鎖帳"


def test_reject_clears_the_generated_aps():
    """🔴 退回一定要收掉應付款，否則帳上留下**幽靈負債**。

    單據回到本人草稿、應付帳款卻還掛著那筆錢 —— 月結與現金流預測都會多算，
    而且沒有任何畫面會顯示這個矛盾。三條退回路徑都必須經過 `_clear_aps`。
    """
    for fn in ("reject_claim", "reject_claim_line"):
        body = PETTY_SRC.split(f"async def {fn}")[1].split("\n@router")[0]
        assert "_clear_aps(" in body, f"{fn} 沒有撤掉應付款"
    # 已付款的不准撤（那筆錢真的出去了）
    clear = PETTY_SRC.split("async def _clear_aps")[1].split("\n@router")[0]
    assert "已付款" in clear and "409" in clear


def test_line_reject_rebuilds_aps_instead_of_patching_them():
    """抽掉一行後**重建** AP，而不是就地改金額。

    就地改要處理「整個 (項目×月份) 桶消失」等分支，等於養出第二套 AP 邏輯；
    撤掉重建走的是與送出時同一段程式碼，不會漂。
    """
    body = PETTY_SRC.split("async def reject_claim_line")[1].split("\n@router")[0]
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
    # 錨在 `_build_aps` —— 產 AP 的地方只有這一處（送出／核准／逐行退回重建
    # 三條路都走它）。錨在某一支端點上的話，邏輯一被抽成函式測試就失明。
    build = PETTY_SRC.split("async def _build_aps")[1].split("\nasync def ")[0]
    assert "category=item" in build.replace(" ", "")
    assert 'category="零用金"' not in PETTY_SRC


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


PETTY_HOSTS = ("petty-cash.html", "tabs/finance/subviews/petty.js", "m/views/petty.js")


def test_all_hosts_share_one_fetch_contract():
    """🔴 零用金元件有**三個宿主**（獨立頁 /petty-cash.html、CRM 財務子視圖、手機 CRM 分頁）。

    元件把 body 當**物件**交出去，由 fetch 包裝 stringify（對齊
    `js/shared/utils.authFetch`）。元件自己 stringify 就變成雙重編碼，後端收到的
    是一個 JSON 字串而不是物件 → 422。

    fetch 出口只有元件自己的預設宿主一份（DEFAULT_HOST＝authFetch；上傳走
    bearerHeader() 讓瀏覽器補 multipart boundary）。三個宿主都不再各設一次
    `window.__petty` —— 第三個宿主出現時三份 shim 就漂了。
    """
    send = PETTY_VIEW.split("async function send(")[1].split("\n}")[0]
    assert "JSON.stringify" not in send, "元件不該自己 stringify（宿主負責）"

    head = PETTY_VIEW.split("async function get(")[0]
    assert "const DEFAULT_HOST = {" in head and "mfetch: authFetch" in head, "元件沒有自己的預設宿主"
    assert "bearerHeader()" in head, "FormData 上傳不能走 authFetch"
    assert "window.__petty || DEFAULT_HOST" in head, "沒有 shim 時要退回預設宿主"
    for rel in PETTY_HOSTS:
        src = js_code_only((FRONTEND / rel).read_text(encoding="utf-8"))
        assert "window.__petty" not in src, f"{rel} 又長出自己的 fetch shim"
        assert "async function mfetch(" not in src, f"{rel} 不准再有本地 mfetch 複本"


def test_binding_a_project_also_attaches_a_cost_group():
    """🔴 綁專案時要一併落到成本子表。

    CRM 專案詳情的雜支區塊是**照子表分組**畫的 —— `cost_group_id` 是 NULL 的列
    只活在扁平清單裡，於是「綁了專案、專案頁上卻看不到那筆錢」（owner
    2026-08-17 回報，生產上有 2 列這樣）。四條會設 project_id 的路徑都要走
    `_resolve_target_group`（與 CRM 建立雜支同一支）。
    """
    assert "_attach_cost_group" in PETTY_SRC
    patch = PETTY_SRC.split("async def patch_petty_entry")[1].split("\n@router")[0]
    assert "_resolve_target_group" in patch, "PATCH 綁專案沒有落子表"
    bind = PETTY_SRC.split("async def petty_bind_label")[1].split("\n@router")[0]
    assert "_resolve_target_group" in bind, "一次綁整個標籤沒有落子表"
    # 解除專案時也要把子表清掉，否則會留下「沒有專案卻掛在某子表下」的孤兒
    assert "exp.cost_group_id = None" in patch


def test_only_project_misc_can_link_a_project():
    """🔴 只有「專案雜支」可以連結專案（owner 2026-08-17）。

    行政／設備耗材／業務推廣是公司層級支出 —— 掛到專案上會讓那個案子的毛利
    多算一筆不屬於它的錢，而且從報表上看不出來是誤掛。

    三個地方都要遵守同一份 `PROJECT_LINK_ITEMS`：建立、PATCH、未歸戶標籤。
    前端也讀同一份（經 `/petty/options` 的 `project_link_items`），不各寫一份。
    """
    from routers.crm.petty import PROJECT_LINK_ITEMS
    assert "專案雜支" in PROJECT_LINK_ITEMS
    assert "行政" not in PROJECT_LINK_ITEMS

    # 不變量收斂在 `_enforce_project_link` —— **賦值後查最終狀態**，不是賦值前
    # 預測（預測式守衛漏過 PUT 整支、以及 PATCH {"item": ""} 讓連結留在 NULL
    # 項目上）。四條會動到 project_id/item 的寫入路徑都必須經過它。
    helper = PETTY_SRC.split("def _enforce_project_link")[1][:900]
    assert "409" in helper and "不開放連結專案" in helper
    for fn in ("add_my_petty_expense", "add_petty_expense_for",
               "update_my_petty_expense", "patch_petty_entry"):
        body = PETTY_SRC.split(f"async def {fn}")[1].split("\n@router")[0]
        assert "_enforce_project_link" in body, f"{fn} 沒過不變量"
    # 解除連結要**回報**（靜默清掉的話，專案毛利自己少一筆而沒人知道為什麼）
    patch = PETTY_SRC.split("async def patch_petty_entry")[1].split("\n@router")[0]
    assert '"unlinked": unlinked' in patch

    assert "project_link_items" in PETTY_VIEW, "前端沒讀後端那份規則"
    # fallback 清單只准存在一份（wireProjectGate/linkableSet 收斂點）
    assert PETTY_VIEW.count('["專案雜支"]') == 1, "fallback 清單被複製了"


def test_cost_group_endpoint_needs_both_grants(app_client, as_user):
    for mods in LACKING:
        assert app_client.get("/api/v1/crm/petty/project-groups/__probe__",
                              headers=as_user(modules=mods)).status_code == 403


def test_ledger_asks_which_cost_group_when_there_are_several():
    """🔴 專案有多張成本子表時要問使用者掛哪一張（owner 2026-08-17）。

    預設落主表不會算錯錢，但「這筆算哪一天的拍攝」只有人知道 —— 默默落主表
    等於幫使用者做了一個他看不到的決定。只有一張時不問（沒得選）。
    """
    assert "_pickCostGroup" in PETTY_VIEW
    fn = PETTY_VIEW.split("const _pickCostGroup")[1].split("\n    };")[0]
    assert "groups.length <= 1" in fn, "只有一張子表時不該問"
    assert "petty/project-groups/" in fn
    # 取消要放棄整個動作，不是默默落主表
    assert "resolve(null)" in fn or "done(null)" in fn


def test_item_owner_mapping_needs_both_grants(app_client, as_user):
    for mods in LACKING:
        h = as_user(modules=mods)
        assert app_client.get("/api/v1/crm/petty/item-owners",
                              headers=h).status_code == 403
        assert app_client.put("/api/v1/crm/petty/item-owners", headers=h,
                              json={"mapping": {}}).status_code == 403


def test_item_owner_mapping_only_fills_unassigned():
    """🔴 「套用到既有」只補 `owner_staff_id IS NULL` 的列。

    手動改過的例外、已結清的歷史都不能被一鍵覆蓋 —— 否則每按一次設定就把人家
    整理好的歸屬洗掉一次，而且沒有任何提示。
    """
    body = PETTY_SRC.split("async def set_item_owners")[1].split("\n@router")[0]
    assert "owner_staff_id.is_(None)" in body


def test_new_expense_applies_the_item_owner_rule():
    """建立單據時就套用對映 —— 規則設定一次，不必每筆挑（帳冊因此不用開那一欄）。"""
    body = PETTY_SRC.split("def _new_expense")[1].split("\ndef ")[0]
    assert "_item_owners()" in body and "owner_staff_id=" in body
    # 帳冊不再有逐列的歸屬欄，改成工具列的設定按鈕
    assert 'data-f="owner_staff_id"' not in PETTY_VIEW
    assert 'id="lg-owners"' in PETTY_VIEW


def test_project_picker_is_one_shared_popover_not_300_selects():
    """🔴 專案有 238 個 —— 每列各長一份下拉＝七萬個 option（載入卡住的原因）。

    2026-09-04 起改成**共用一個浮層**（js/shared/project-pop，跟工作日誌同一個；分「進行中／已結案」）：
    選項只在記憶體一份，列裡是 input 不是 select（回頭改成 select 就會把 238 個選項再乘上 300）。
    """
    assert "<datalist" not in js_code_only(PETTY_VIEW)
    assert 'import { attachProjectPop } from "../../js/shared/project-pop.js"' in PETTY_VIEW
    assert "data-lazy" not in PETTY_VIEW
    assert '<input data-f="project_id" data-proj-pick' in PETTY_VIEW
    assert '<select data-no-search data-f="project_id"' not in PETTY_VIEW


def test_typo_in_project_does_not_silently_unbind():
    """打錯專案名要擋下來並還原 —— 靜默當成「不歸專案」會無聲抹掉歸屬。"""
    body = PETTY_VIEW.split('if (f === "project_id" && el.tagName === "INPUT")')[1][:500]
    assert "找不到專案" in body and "dataset.was" in body


def test_petty_subview_is_registered_in_the_finance_nav():
    """側欄有按鈕、subviews/ 有對應檔案 —— 少一邊就是點了沒反應。"""
    nav = (FRONTEND / "tabs" / "finance" / "finance.html").read_text(encoding="utf-8")
    names = re.findall(r'data-subview="([^"]+)"', nav)
    assert "petty" in names, "財務管理側欄沒有零用金"
    missing = [n for n in names
               if not (FRONTEND / "tabs" / "finance" / "subviews" / f"{n}.js").exists()]
    assert not missing, f"側欄按鈕指向不存在的子視圖：{missing}"


# ── 抹除層不准把「自己的錢」抹掉 ───────────────────────────────────────
def test_own_scope_fields_are_not_redacted():
    """🔴 `_OWN_SCOPE` 與 `MONEY_FIELDS` 必須互斥。

    同時出現＝有人把它加進抹除名單卻沒發現另一張表已經說了「這要給本人看」。
    後果不是報錯而是**畫面上一片空白**，而空白看起來很像「還沒登記」。
    """
    both = sorted(set(_OWN_SCOPE) & MONEY_FIELDS)
    assert not both, f"這幾筆同時要抹又要給本人看：{both}"


def test_ledger_marks_which_cost_group_a_bound_row_landed_on():
    """🔴 綁了專案的列要看得出掛在哪張成本子表，沒掛的要看得出來。

    `cost_group_id` 是 NULL 的列**綁了專案卻不會出現在專案頁的雜支裡**
    （2026-08-17 踩過）—— 那是資料層的靜默失敗，畫面上兩者長得一模一樣。
    帳冊的紅色 i 是唯一看得出來的地方，所以三件事都要在：
    後端回子表名稱、前端分兩種狀態、沒綁專案的列不長出這個記號。
    """
    body = _fn("petty_entries")
    assert "cost_group_name" in body, "帳冊沒回成本子表名稱"
    assert "CrmProjectCostGroup.id.in_(gids)" in body, "子表名稱不是批次撈的（N+1）"
    hint = PETTY_VIEW.split("const groupHint")[1].split("const projCell")[0]
    assert "if (!e.project_id) return \"\"" in hint, "沒綁專案的列不該有記號"
    assert "lg-i warn" in hint and "cost_group_name" in hint, "缺少「沒掛子表」的警示狀態"


def test_project_year_never_falls_back_to_created_at():
    """🔴 沒填日期的專案就留白，不准拿建立日充數。

    生產庫 238 個專案裡 216 個沒有任何自己的日期（start/shoot/completion 全空），
    而其中 217 個是 2026-05 那次匯入建立的。退到 `created_at` ＝ 幫一整批舊案子
    蓋上「2026」，既是假資料，又剛好讓「打 2025 找 2025 的案子」濾不到。
    """
    year = PETTY_SRC.split("def _project_year")[1].split("\n@router")[0]
    assert "p.created_at" not in year, "年份退到建立日了（那是匯入日不是專案年份）"
    assert "p.start_date, p.shoot_date, p.completion_date" in year
    opts = _fn("petty_options")
    assert "Client.short_name" in opts and "outerjoin(Client" in opts, "選項沒帶客戶"


def test_every_project_picker_shares_one_label_builder():
    """專案顯示字串只准有一份 —— 三個挑選處（登記表單／帳冊／未歸戶綁定）。

    格式是「年份｜客戶｜專案名」，而帳冊是**反查**用的：使用者打進來的字串要能
    對回 id，所以任何一處自己組字串，就會有一個挑得到卻存不回去的下拉。
    """
    assert PETTY_VIEW.count("function projectLabels") == 1
    # 沒有人再自己把 opts.projects 攤成 <option>（那就是繞過標籤建構）
    assert "opts.projects.map(" not in PETTY_VIEW, "有下拉自己組專案選項，沒走共用建構"
    # <select> 挑選處（未歸戶綁定）走共用 options 建構；帳冊新增列 2026-09-04 起也是浮層
    assert "projectOptions(opts.projects, '<option value=\"\">選擇專案…" in PETTY_VIEW, "「選擇專案…」那個下拉沒走共用的標籤建構"
    # 浮層挑選處（登記表單／帳冊每列與新增列）：列由 projectRows 用同一份 labelOf 組——
    # 值與反查表同源 —— 自由文字必須反查得到 id，打錯不准靜默變公司支出
    assert PETTY_VIEW.count("projectRows(opts.projects,") == 2
    assert "const { labelOf, idOfLabel } = projectLabels(opts.projects);" in PETTY_VIEW
    assert "idOfLabel: formIdOfLabel } = projectLabels(opts.projects)" in PETTY_VIEW
    assert "formIdOfLabel[projLabel]" in PETTY_VIEW, "表單送出沒反查 id"


# ── 專案頁雜支區 × 零用金（2026-08-18 整頓）────────────────────────
COSTS_SRC = (REPO / "routers" / "crm" / "costs.py").read_text(encoding="utf-8")
COST_VIEW = (FRONTEND / "tabs" / "crm" / "crm-projects-cost.js").read_text(encoding="utf-8")
SHARED_SRC = (REPO / "routers" / "crm" / "_shared.py").read_text(encoding="utf-8")
PROJECTS_SRC = (REPO / "routers" / "crm" / "projects.py").read_text(encoding="utf-8")


def test_claimed_rows_are_immutable_from_project_page():
    """🔴 已進請款單（claim_id）的列：專案頁的改/刪三條路都要過 _guard_claimed。

    少一條 ＝ 有人能從專案頁改掉某張請款單 total_claim 的組成金額，
    員工看到的單子總額無聲地變成錯的。
    """
    assert "def _guard_claimed" in COSTS_SRC
    for fn in ("patch_project_expense", "update_project_expense", "delete_project_expense"):
        body = COSTS_SRC.split(f"async def {fn}")[1].split("\n@router")[0]
        assert "_guard_claimed(e)" in body, f"{fn} 沒擋 claim 列"
    # 整批路徑同一個政策：刪子表會 cascade 刪雜支、綁預支會動結算歸屬 ——
    # 逐列守衛擋得再嚴，整批路徑漏了就等於沒守
    dg = COSTS_SRC.split("async def delete_cost_group")[1].split("\n@router")[0]
    assert "claim_id.isnot(None)" in dg, "刪子表沒擋 claim 列（cascade 會整批消滅）"
    la = COSTS_SRC.split("async def link_expenses_to_advance")[1].split("\n@router")[0]
    assert "_guard_claimed(e)" in la, "綁預支沒擋 claim 列"
    # 零用金列的收款人是身分不是文字：後端也要擋（前端鎖只是鏡像不是正本）
    patch = COSTS_SRC.split("async def patch_project_expense")[1].split("\n@router")[0]
    assert '"payee" in data and e.staff_id' in patch
    # 前端同步上鎖：locked 列不給 inline edit、不給刪除鈕
    assert "const locked = !!e.claim_id;" in COST_VIEW
    assert "已進零用金請款單" in COST_VIEW


def test_placeholder_seeding_is_gone():
    """🔴 $0 佔位雜支列不准再種回來（清過 dev 9,354 / prod 193 列）。

    它們和真資料在畫面上無法區分，會把雜支區塞成「一堆列其實全空」。
    類別清單活在前端 EXPENSE_CATEGORIES，不需要種進資料庫。
    """
    for src_name, src in (("costs.py", COSTS_SRC), ("projects.py", PROJECTS_SRC),
                          ("_shared.py", SHARED_SRC)):
        assert "_seed_default_expenses" not in src, f"{src_name} 還在種佔位列"


def test_project_page_expense_ux_contract():
    """就地新增列存在且送 expense_date；日期欄消費日優先；雜支區不被成本項目挾持。"""
    # 就地新增：日期/類別/細項/金額/收款人 + POST 帶 expense_date
    assert "window._expQuickAdd" in COST_VIEW
    assert "expense_date: document.getElementById('exp-qa-date').value" in COST_VIEW
    # 消費日優先，登記日只是 fallback；填錯要能就地改（PATCH 端要走 _parse_day）
    assert "e.expense_date || e.created_at" in COST_VIEW
    assert "edCell('exp-col-date', 'expense_date'" in COST_VIEW
    patch = COSTS_SRC.split("async def patch_project_expense")[1].split("\n@router")[0]
    assert "_parse_day(val)" in patch, "PATCH 把日期字串直接 setattr 進 timestamptz 會炸"
    # 🔴 雜支區必須在「尚無項目」時照畫（只有零用金列的專案，錢不能整區消失）
    before_misc = COST_VIEW.split("行政雜支 section")[0]
    assert before_misc.rstrip().endswith("// ──"), "雜支區前的結構變了，確認它不在 else 裡"
    assert "html += '<div class=\"cost-table\">';" in COST_VIEW
    # 零用金列標示：pill + 收款人讀員工檔
    assert "exp-pill" in COST_VIEW and "e.staff_name || e.payee" in COST_VIEW


def test_receipts_root_setting():
    """收據根目錄可後台設定（owner 2026-08-19），四個接點缺一不可：
    設定正本 _receipts_root、兩條 fallback 儲存路徑、服務端 allow-list
    （換到 NAS 後新收據不在 uploads/，漏了會 403）、admin 專用讀寫端點。"""
    assert "def _receipts_root" in COSTS_SRC
    save = COSTS_SRC.split("async def _save_receipt")[1].split("\n@router")[0]
    assert save.count("_receipts_root()") == 2, "兩條 fallback 都要吃設定值"
    assert 'os.getcwd(), "uploads", "receipts"' not in save, "殘留硬編碼路徑"
    # 檔名/資料夾日期走 _fmt_day 台北歸一（面值 strftime 差一天，08-19 踩到）
    assert "_fmt_day(exp.expense_date or exp.created_at)" in save
    assert 'strftime("%Y' not in save, "檔名日期繞過 _fmt_day"
    serve = COSTS_SRC.split("async def serve_receipt")[1].split("\n@router")[0]
    assert "_receipts_root()" in serve, "換了根目錄，舊/新收據連結會 403"
    # 2026-09-08 第二批：GET 給審核者（財務分頁零用金子視圖第一支就打它），設定仍限管理員
    assert "check_admin_or_module(request, 'finance_approve')" in COSTS_SRC.split("async def get_receipts_root")[1].split("\n@router")[0]
    assert "check_admin(request)" in COSTS_SRC.split("async def set_receipts_root")[1].split("\n@router")[0]
    sub = (FRONTEND / "tabs" / "finance" / "subviews" / "petty.js").read_text(encoding="utf-8")
    assert "petty/receipts-root" in sub
    assert "/api/settings/load" not in sub, "不准走遮罩過的 settings 整包（會洗掉機密）"


def test_me_petty_is_the_standalone_key():
    """零用金入口的鑰匙是獨立的 me_petty（owner 2026-08-19「請款要單獨控制」），
    不再搭 me_finance 便車。入口兩處（獨立頁 + my.html 卡）都要認同一把。"""
    from core.auth import ALL_MODULES
    assert "me_petty" in ALL_MODULES
    page = (FRONTEND / "petty-cash.html").read_text(encoding="utf-8")
    assert 'mods.includes("me_petty")' in page
    assert 'mods.includes("me_finance")' not in page, "獨立頁還在收舊鑰匙"
    my = (FRONTEND / "my.html").read_text(encoding="utf-8")
    assert 'ws.allowed.includes("me_petty")' in my
    auth = (REPO / "core" / "auth.py").read_text(encoding="utf-8")
    assert '"me_petty"' in auth.split("ME_MODULE_KEYS")[1][:400], "workspace allowed 沒帶新 key，卡片永遠不出現（正本 core.auth.ME_MODULE_KEYS）"


def test_dashboard_misc_estimate_source():
    """🔴 預估雜支的正本＝子表「雜支預算」加總（misc_budget_total），不是逐列
    estimated —— $0 佔位列退場後逐列數字恆為 0，預估剩餘會漏扣雜支
    （owner 2026-08-18 抓到的洞）。全部未設才退回 % 自動推算並標「自動」。
    """
    calc = (FRONTEND / "tabs" / "crm" / "crm-projects-calc.js").read_text(encoding="utf-8")
    assert "misc_budget_total" in calc
    assert "expense_estimated" not in calc.split("export function calcDashboard")[1], \
        "calcDashboard 又退回死掉的逐列 estimated"
    assert '"misc_budget_total": misc_budget_total' in COSTS_SRC  # financial-summary 有帶
    # 公式單一正本：calcDashboardParts 是唯一的衍生鏈，_fillDashGrid 只准委派
    # 不准自己算（2026-08-18 /simplify：兩份公式已經在 usagePct 改口徑時
    # 差點各改各的）
    assert "export function calcDashboardParts" in calc
    fill = js_func_body(COST_VIEW, "function _fillDashGrid")
    assert "calcDashboardParts(" in fill
    assert "Math.round" not in fill, "_fillDashGrid 又自己長出公式了"
    assert "_miscBudgetEdit" in COST_VIEW and "_miscPctModal" in COST_VIEW
    assert "cd-misc-diff" in COST_VIEW


def test_expense_dates_are_formatted_in_taipei():
    """🔴 timestamptz 讀取端一律走 _fmt_day（台北歸一）。

    寫入是 naive（PG session=Asia/Taipei → 存前一天 16:00Z）、asyncpg 讀回
    aware UTC —— 面值 strftime/isoformat 取日期就差一天。2026-08-18 在消費日
    實測踩到（送 08-01 回讀 07-31）。
    """
    assert "def _fmt_day" in SHARED_SRC and "day_iso(" in SHARED_SRC       # 委派 hr_logic 那一份
    # costs 的 _fmt_date 委派給 _fmt_day
    fmt = COSTS_SRC.split("def _fmt_date")[1].split("\ndef ")[0]
    assert "_fmt_day" in fmt and "isoformat" not in fmt
    # 年份也是同一型坑（timestamptz 面值取年份，台北 1/1 → 前一年）
    year_fn = PETTY_SRC.split("def _project_year")[1].split("async def")[0]
    assert "_fmt_day" in year_fn and ".year" not in year_fn
    # 整個 routers/crm 套件掃一輪：日期欄位不准再面值取日
    # （hr_logic → api_proposals → 消費日，同一個坑已經修三次了）
    for f in sorted((REPO / "routers" / "crm").glob("*.py")) + [REPO / "routers" / "api_me.py", REPO / "core" / "hr_logic.py"]:
        src = f.read_text(encoding="utf-8")
        assert "isoformat()[:10]" not in src, f"{f.name} 有面值取日期，該走 _fmt_day"
        for ln in src.splitlines():
            if ('strftime("%Y-%m-%d")' in ln
                    and "datetime.now()" not in ln and "_now()" not in ln):
                raise AssertionError(f"{f.name}: {ln.strip()} — 該走 _fmt_day")
