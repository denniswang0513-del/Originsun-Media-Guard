# -*- coding: utf-8 -*-
"""登記餘額（owner 2026-09-17「先讓我登記我的帳戶資產，明細我後面補」）。

釘的是規矩：
- 餘額的規則只有 core.finance_logic.derive_balance 一份；六個算餘額的地方都經 api_finance._balances_by_account。
- 登記過：餘額 = 登記餘額 + 基準日之後的流水；基準日當天與之前的明細只當歷史（補了不動今天的數字）。
- 沒登記：老公式（期初＋全部流水），一個數字都不變。
- 桌機 nav 私帳限定、手機 #register 隱藏路由、按鈕純文字沒有 emoji。
"""
import re

from core.finance_logic import bank_balances_asof, day_key, derive_balance
from tests.unit._srcscan import func_body, js_code_only, migration_sql, models_src, repo_src

_EMOJI = re.compile("[\U0001F300-\U0001FAFF]|[✓✔✗]")


# ── 純規則 ──────────────────────────────────────────────────────────
def test_no_anchor_is_the_old_formula():
    """沒登記過的帳戶：期初＋全部流水，unfilled 是 None（不是 0 —— 畫面要分得出「沒登記」與「補齊了」）。"""
    r = derive_balance(700_069, None, None, flow_all=-38_200, flow_after=-38_200)
    assert r == {"balance": 661_869, "unfilled": None}
    # 只有其中一欄有值也當沒登記（兩欄一起有或一起空）
    assert derive_balance(100, 999, None, 5, 5)["balance"] == 105
    assert derive_balance(100, None, "2026-09-17", 5, 5)["balance"] == 105


def test_anchor_starts_from_registered_balance_and_old_entries_become_history():
    """登記 1,482,235（帳上算到基準日是 1,520,435）：餘額＝登記數；unfilled＝−38,200＝還沒補的明細。
    之後補一筆基準日**之前**的支出 −38,200：flow_all 變了、flow_after 沒變 → 餘額不動、unfilled 歸 0。
    再記一筆基準日**之後**的 +10,000 → 餘額才動。"""
    r = derive_balance(700_069, 1_482_235, "2026-09-17", flow_all=820_366, flow_after=0)
    assert r == {"balance": 1_482_235, "unfilled": -38_200}
    filled = derive_balance(700_069, 1_482_235, "2026-09-17", flow_all=820_366 - 38_200, flow_after=0)
    assert filled == {"balance": 1_482_235, "unfilled": 0}, "補舊明細：今天的餘額一毛都不動"
    later = derive_balance(700_069, 1_482_235, "2026-09-17", flow_all=820_366 - 38_200 + 10_000, flow_after=10_000)
    assert later["balance"] == 1_492_235 and later["unfilled"] == 0


def test_until_before_or_on_anchor_day_falls_back_to_old_formula():
    """對帳單期初／月底餘額：那個時點還沒登記 → 老公式；基準日之後的時點才用登記數。"""
    kw = dict(opening=100, anchor_balance=500, anchor_date="2026-09-17", flow_all=40, flow_after=10)
    assert derive_balance(**kw, until="2026-09-01")["balance"] == 140, "登記日之前：期初＋流水"
    assert derive_balance(**kw, until="2026-09-17")["balance"] == 140, "登記日當天（until 是開區間）還是老公式"
    assert derive_balance(**kw, until="2026-10-01")["balance"] == 510, "登記日之後：登記數＋之後的流水"


def test_day_key_compares_naive_aware_and_strings_alike():
    from datetime import datetime, timezone
    assert day_key(datetime(2026, 9, 17, 8, tzinfo=timezone.utc)) == "2026-09-17"
    assert day_key(datetime(2026, 9, 17)) == "2026-09-17" == day_key("2026-09-17T00:00:00")
    assert day_key(None) == "" and day_key("") == ""


def test_statement_month_end_balance_honours_the_anchor():
    """三表的月底餘額：登記月之前的月份老公式；登記月起從登記數起算、只加基準日之後的流水。"""
    banks = [{"id": "a", "name": "富邦", "opening_balance": 1000, "anchor_balance": 5000, "anchor_date": "2026-09-17"}]
    ents = [
        {"bank_account_id": "a", "entry_date": "2026-08-10", "deposit": 300},   # 基準日前
        {"bank_account_id": "a", "entry_date": "2026-09-17", "expense": 50},    # 基準日當天：算歷史
        {"bank_account_id": "a", "entry_date": "2026-09-20", "deposit": 200},   # 基準日後
        {"bank_account_id": "a", "entry_date": "2026-10-03", "expense": 20},
    ]
    amt = lambda m: bank_balances_asof(banks, ents, m)[0]["amount"]  # noqa: E731
    assert amt("2026-08") == 1300, "登記月之前：期初＋流水"
    assert amt("2026-09") == 5200, "登記月：登記數＋基準日之後（不含當天）的流水"
    assert amt("2026-10") == 5180
    plain = [{"id": "a", "name": "富邦", "opening_balance": 1000}]
    assert bank_balances_asof(plain, ents, "2026-10")[0]["amount"] == 1430, "沒登記：一個數字都不變"


# ── 欄位與唯一規則 ───────────────────────────────────────────────────
def test_columns_exist_in_model_and_startup_migration():
    m = models_src("class BankAccount(Base):")
    assert "anchor_balance = Column(Integer, nullable=True)" in m
    assert "anchor_date = Column(DateTime(timezone=True), nullable=True)" in m
    sql = migration_sql()
    assert "ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS anchor_balance INTEGER" in sql
    assert "ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS anchor_date TIMESTAMP WITH TIME ZONE" in sql


def test_every_balance_site_goes_through_the_single_helper():
    """六個算餘額的地方都經 _balances_by_account；誰自己寫「期初＋流水」就會漏掉基準點。"""
    for rel in ("routers/api_finance_assets.py", "routers/api_fortress.py", "routers/api_finance_stmt.py",
                "routers/api_balance_register.py"):
        src = repo_src(rel)
        assert "_balances_by_account(" in src, rel
        assert "opening_balance or 0) +" not in src, f"{rel} 自己算了一次餘額"
    fin = repo_src("routers/api_finance.py")
    assert fin.count("_balances_by_account(") >= 3, "帳戶清單、對帳月底餘額都要經它"
    assert "_flow_sums_by_account" not in fin, "舊的聚合器留著就是第二份規則"
    assert "derive_balance(" in fin
    stm = repo_src("services/finance_statements.py")
    assert '"anchor_balance", "anchor_date"' in stm, "三表的帳戶投影要帶基準點，不然 bank_balances_asof 看不到"


def test_register_router_is_mounted_on_master_and_office_and_only_writes_db():
    assert "'api_balance_register'" in repo_src("main.py")
    assert '"api_balance_register"' in repo_src("main_office.py"), "手機在 NAS 上也要能登記"
    src = repo_src("routers/api_balance_register.py")
    assert "save_settings" not in src, "NAS 的 settings.json 是唯讀副本：這支只能寫 DB（信用卡走 /card-summary）"
    assert '@router.get("/balance-register")' in src and '@router.put("/balance-register")' in src
    assert "REGISTER_KINDS = (\"bank\", \"cash\")" in src, "信用卡與股東往來不能登記餘額"
    assert "BankReconciliation(" in src, "每次登記留一筆對帳紀錄當歷史"
    sch = repo_src("core/schemas/_finance.py")
    assert "balance: Optional[int] = None" in sch, "balance=null ＝ 取消登記"


def test_bank_dict_exposes_anchor_and_list_exposes_unfilled():
    fin = repo_src("routers/api_finance.py")
    assert '"anchor_balance": b.anchor_balance,' in fin
    assert 'd["unfilled"] = r["unfilled"]' in fin


# ── 畫面 ─────────────────────────────────────────────────────────────
def test_desktop_nav_is_mine_only_and_subview_uses_the_endpoint():
    html = repo_src("frontend/tabs/finance/finance.html")
    m = re.search(r'<button class="([^"]*)" data-subview="register">', html)
    assert m, "finance.html 要有 data-subview=register 的 nav 鈕"
    assert {"fin-nav-mine-ok", "fin-nav-mine-only"} <= set(m.group(1).split()), "只在私帳出現"
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/register.js"))
    assert "export default async function render(" in js
    assert "finFetchMine('/balance-register')" in js and "method: 'PUT'" in js
    assert "balance: null" in js, "取消登記送 null"
    assert "'/card-summary'" in js and "derive_opening_from" in js, "信用卡走既有那支"
    bank = js_code_only(repo_src("frontend/tabs/finance/subviews/banking.js"))
    assert "_anchorLine(a)" in bank and "a.anchor_balance" in bank, "銀行卡片要看得到登記過的數字"


def test_mobile_register_is_a_hidden_route_reached_from_overview():
    ledger = repo_src("frontend/m/ledger.js")
    assert "register: '登記餘額'" in ledger and "register: 'overview'" in ledger
    assert "register: registerView" in ledger
    ov = js_code_only(repo_src("frontend/m/views/ledger-overview.js"))
    assert 'data-go="register"' in ov and 'data-go="fortress"' in ov
    assert "go.dataset.go" in ov, "隱藏路由的入口用同一個 data-go 委派"
    view = repo_src("frontend/m/views/ledger-register.js")
    code = js_code_only(view)
    assert "'/api/v1/finance/balance-register?entity=mine'" in code
    assert "todayLocal()" in code and "toISOString" not in code
    assert "markStale('overview', 'assets')" in code, "登記後總覽的堡壘卡與資產頁要重抓"
    assert not _EMOJI.search(view), "按鈕純文字，沒有 emoji"
    css = repo_src("frontend/m/m.css")
    assert ".rg-row" in css and ".rg-go" in css


def test_holdings_can_be_registered_share_by_share():
    """owner「登記的還有證券的股數等」：GET 每家券商帶 holdings[]，PUT 收 holdings[]{id, shares, last_price, manual_value, cost_total}，
    先套持股再算未拆明細；給了股數或現價且算得出股數 × 現價，就清掉手填市值。"""
    src = repo_src("routers/api_balance_register.py")
    assert 'g["holdings"].append(' in src and "value = _holding_value(h, fx)" in src and '"value_twd": value' in src
    assert "for hl in lines:" in func_body(src, "async def _register_holdings(")
    put = func_body(src, "async def put_balance_register(")
    assert put.index("await _register_holdings(") < put.index("await _register_brokers("), "先套持股再算未拆明細（brokers 是去重後的清單）"
    assert "h.manual_value = None" in src and "h.price_at = datetime.now()" in src
    sch = repo_src("core/schemas/_finance.py")
    assert "class BalanceRegisterHolding(BaseModel):" in sch and "holdings: List[BalanceRegisterHolding] = []" in sch
    desk = js_code_only(repo_src("frontend/tabs/finance/subviews/register.js"))
    # owner「證券我只需要更改單位數」：畫面每檔只有股數一格（後端仍收 last_price／cost_total，給別的入口用）
    assert 'data-holding="${esc(h.id)}"' in desk and 'data-k="shares"' in desk
    assert 'data-k="last_price"' not in desk and 'data-k="cost_total"' not in desk
    assert "JSON.stringify({ date, accounts, holdings, brokers })" in desk
    mob = js_code_only(repo_src("frontend/m/views/ledger-register.js"))
    # 手機 mfetch 自己會 stringify（BUG-1）：body 傳物件
    assert 'data-holding="${esc(h.id)}"' in mob and "body: { date, accounts, holdings, brokers }" in mob
    assert 'data-k="last_price"' not in mob
    assert ":scope > .m-form > input.rg-in" in mob, "券商總市值那格不能把持股的格子一起讀走"



def test_works_list_hides_private_ledger_projects_that_are_linked_to_a_crm_project():
    """owner 2026-09-17「官網私帳與 crm 如果有連結時，出現 crm 的資料就可以了」：
    同一件案子在母帳與私帳各一列時，作品清單只留母帳那列。兩種連結形狀都要認（mine_link_id／source_project_id），
    沒連結的私帳案照列；公開清單的共用基底也套同一個條件。"""
    from tests.unit._srcscan import func_body
    src = repo_src("services/website/project_service.py")
    cond = func_body(src, "def _not_a_linked_mine_project(published_only: bool = False):")
    assert "P.mine_link_id.isnot(None)" in cond and "CrmProject.source_project_id.is_(None)" in cond
    assert 'CrmProject.entity != "mine"' in cond, "母帳案一律列；只有私帳案才看連結"
    # 2026-09-17 調整：母帳那邊要真的有東西才不列（母帳案存在；公開清單還要母帳作品已公開）；子查詢用 aliased 免被 correlate
    assert "P.id.in_(parent)" in cond and "CrmProject.source_project_id.notin_(parent)" in cond and "aliased(CrmProject)" in cond
    assert "S.published.is_(True)" in cond and ".correlate(None)" in cond
    assert "_not_a_linked_mine_project()" in func_body(src, "async def list_admin_projects(")
    assert "_not_a_linked_mine_project(published_only=True)" in func_body(src, "def _published_works_base():")


def test_mobile_views_pass_objects_to_mfetch_not_strings():
    """🔴 BUG-1（polish 2026-09-17）：shell.js 的 mfetch 會自己 JSON.stringify(opts.body)。登記頁與月報頁又先
    stringify 一次 → 送出去的是 JSON **字串字面值** → pydantic 422「Input should be a valid dictionary」——
    手機按「儲存有填的」與「重新產生本月」永遠失敗。桌機的 finFetch 相反（原樣丟給 fetch），兩邊慣例不同。"""
    for rel in ("frontend/m/views/ledger-register.js", "frontend/m/views/ledger-report.js", "frontend/m/views/ledger-fortress.js"):
        code = js_code_only(repo_src(rel))
        assert "body: JSON.stringify(" not in code, f"{rel}：手機 mfetch 的 body 要傳物件"


def test_day_key_uses_local_day_for_aware_datetimes():
    """BUG-5（polish 2026-09-17）：台北 10/1 00:00 存進 timestamptz 讀回是 9/30 16:00Z → day_key 要回 10/01，
    不然登記日等於對帳月底那天時 derive_balance 會誤判成「那時已登記」（規則說 until 不晚於登記日要走老公式）。"""
    from datetime import datetime, timezone
    from core.finance_logic import derive_balance
    anchor = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)      # ＝ 台北 2026-10-01 00:00
    assert day_key(anchor) == "2026-10-01"
    r = derive_balance(100, 500, anchor, flow_all=40, flow_after=0, until=datetime(2026, 10, 1))
    assert r["balance"] == 140, "對帳 2026-09 的月底：登記日（10/1）不早於 until（10/1）→ 老公式"


def test_report_month_window_is_naive_like_the_cash_entries():
    """BUG-5：月報本月視窗不能用 UTC-aware 邊界（明細存的是 naive 本地 00:00，1 日的會掉到上個月）。"""
    from tests.unit._srcscan import func_body
    rpt = repo_src("routers/api_monthly_report.py")
    assert "def _month_window" not in rpt and "timezone.utc" not in rpt, "月報不自己切月份視窗，也不用 UTC-aware 邊界"
    w = func_body(repo_src("routers/api_finance.py"), "def _month_window(month: str) -> tuple:")
    assert "timezone" not in w and 'strptime(month + "-01", "%Y-%m-%d")' in w


def test_register_and_generate_reject_the_edge_inputs():
    """BUG-6（polish 2026-09-17）：對帳月份用 strftime；同券商去重；未拆明細不能是負的；月報只能產生本月。"""
    from tests.unit._srcscan import func_body
    reg = repo_src("routers/api_balance_register.py")
    assert 'month = day.strftime("%Y-%m")' in reg and "payload.date[:7]" not in reg
    assert "brokers = list({(b.broker or \"\").strip(): b for b in payload.brokers}.values())" in reg
    assert "if gap < 0:" in reg and "還少，先改那家的持股股數或更新報價" in reg
    gen = func_body(repo_src("routers/api_monthly_report.py"), "async def generate_report(")
    assert "if month != this_month:" in gen and "月報只能用現在的數字產生本月" in gen
