# -*- coding: utf-8 -*-
"""案源規則（owner 2026-08-25）：源日＝現金收款；代開發票＝營收×服務費率的
代辦費（預設 8%，191 個歷史案實證全部 8.00%；逐案可調）。正本在
core.ledger_project.apply_source_fee —— 前端只是同一條式子的即時預覽。"""
from tests.unit._srcscan import finance_src
from core.ledger_project import (DEFAULT_FEE_PCT, SOURCES, apply_source_fee,
                                 norm_detail)


def test_norm_detail_keeps_source_and_custom_fee():
    d = norm_detail({"source": "代開發票", "fee_pct": 10, "misc": 5})
    assert d["source"] == "代開發票" and d["fee_pct"] == 10 and d["misc"] == 5


def test_norm_detail_drops_default_fee_and_unknown_source():
    """費率＝預設值不落盤（免得預設哪天要調，402 筆都被舊值釘死）；
    案源走白名單（亂字串不進 meta）。"""
    d = norm_detail({"source": "路邊撿的", "fee_pct": DEFAULT_FEE_PCT})
    assert "source" not in d and "fee_pct" not in d
    assert set(SOURCES) == {"自接", "源日", "代開發票", "執行業務所得"}


def test_agency_fee_is_contract_times_pct():
    d = apply_source_fee(105462, norm_detail({"source": "代開發票"}))
    assert d["invoice_fee"] == 8437          # 思沙龍 EP02 的實際數字
    d = apply_source_fee(80000, norm_detail({"source": "代開發票", "fee_pct": 10}))
    assert d["invoice_fee"] == 8000          # 逐案調率


def test_other_sources_never_touch_the_fee():
    """🔴 自動規則只管代開發票 —— 歷史案（自接＋手填代辦費）不可被回溯改寫。"""
    d = norm_detail({"source": "自接", "invoice_fee": 1512})
    assert apply_source_fee(18900, dict(d))["invoice_fee"] == 1512
    d2 = norm_detail({"source": "源日", "invoice_fee": 0})
    assert apply_source_fee(50000, dict(d2))["invoice_fee"] == 0


def test_endpoints_apply_the_rule_on_write():
    """create 與 update 都要在寫入時套（前端的 disabled 欄位擋不住 API 呼叫）。

    掃描走 `_srcscan`（同檔其他測試也是）—— 手刻 read_text＋split 少了剝註解
    那一步，斷言會被「正好在說明這件事」的註解餵飽。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    src = repo_src("routers/api_finance_projects.py")
    for fn_name in ("create_ledger_project", "update_project_ledger"):
        fn = code_only(func_body(src, f"async def {fn_name}("))
        assert "apply_source_fee(" in fn, fn_name


def test_mine_link_rule_is_prefix_and_parent_is_list():
    from core.project_link import CASH_CATEGORIES, cash_can_link
    assert cash_can_link("mine", "公司_專案")
    assert cash_can_link("mine", "公司_專案支出")
    assert not cash_can_link("mine", "個人_生活")      # 掛上去污染專案毛利
    assert not cash_can_link("mine", "")
    for c in CASH_CATEGORIES:
        assert cash_can_link("parent", c)
    assert not cash_can_link("parent", "公司_專案")     # 兩本詞彙不互通
    assert not cash_can_link(None, "公司_專案")         # 預設＝母公司規則


def test_received_sync_is_incremental_and_mine_only():
    """🔴 增量制（±delta）不是重算 —— 歷史已收是匯入基準，重算會把老案洗掉。
    三個寫入端點都要掛；update 必須在 setattr 前抓舊值。只管 mine（母公司的
    已收走發票/分配那條既有流程，疊上去＝雙重驅動）。"""
    src = finance_src()   # 四個帳務檔串起來（2026-08-30 拆檔）
    helper = src.split("async def _sync_mine_project_received(")[1].split("\ndef ")[0]
    # mine-only 那半（安全半邊）收在共用前導 _get_mine_project，兩支 sync 都走它
    assert "_get_mine_project(session, project_id)" in helper
    pre = src.split("async def _get_mine_project(")[1].split("\nasync def ")[0]
    assert '!= "mine":' in pre.replace("'", '"') and "return None" in pre
    assert "amount_received or 0) + int(delta)" in helper
    for fn_name in ("create_cash_entry", "update_cash_entry", "delete_cash_entry"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_sync_mine_project_received(" in fn, fn_name
    upd = src.split("async def update_cash_entry(")[1].split("\n@router")[0]
    assert upd.index("_old_dep, _old_pid") < upd.index("for k, v in data.items()"), \
        "舊值要在 setattr 之前抓"


def test_agency_fee_composition():
    """稅金5% + 買發票 = 代辦費（owner 口述＋Sheet 實證 82,000→3,905/2,655/6,560）。
    三欄是**組成**不是加項 —— compute 只扣 invoice_fee，三個都扣＝同一筆錢
    扣兩次。"""
    from core.ledger_project import compute
    d = apply_source_fee(82000, norm_detail({"source": "代開發票"}))
    assert (d["invoice_fee"], d["tax_fee"], d["buy_invoice"]) == (6560, 3905, 2655)
    assert d["tax_fee"] + d["buy_invoice"] == d["invoice_fee"]
    net, _ = compute(82000, d)
    assert net == 82000 - 6560               # 只扣代辦費一次


def test_professional_income_withholding_auto():
    """執行業務所得（owner 2026-08-26「新增一個執行業務所得的項目自動算」）：
    源頭代扣＝所得扣繳 10%（單次稅額 ≤2,000 免扣）＋二代健保 2.11%
    （單次 <20,000 免扣）。42,000 → 4,200＋886＝5,086（典藏媒體顧問實帳）。
    「自接」自可選清單移除（歷史值仍有效，白名單保留）。"""
    from core.ledger_project import (SELECTABLE_SOURCES, SOURCES,
                                     apply_source_fee, withholding)
    assert withholding(42000) == 5086
    assert withholding(20000) == 422          # 稅 2,000 免扣；健保 422 照收
    assert withholding(19999) == 0            # 兩門檻皆未達
    assert withholding(100000) == 10000 + 2110
    d = apply_source_fee(42000, norm_detail({"source": "執行業務所得"}))
    assert d["personal_tax"] == 5086
    assert "自接" not in SELECTABLE_SOURCES and "自接" in SOURCES
    assert "執行業務所得" in SELECTABLE_SOURCES


def test_the_withholding_is_an_estimate_the_owner_can_override():
    """🔴 個人稅款的自動值是**試算不是規定**（owner 2026-09-01「一開始先試算，
    但有些狀況讓我可以調整 —— 有些客戶會拆單，所以我不用先繳」）。

    原本每次存檔都無條件覆寫：使用者改成 0 按儲存，數字自己跳回 5,086。
    `keep`＝這次真的送上來的欄位 —— 有送就尊重，沒送（例如新建案還沒有這一欄）
    照樣試算。
    """
    from core.ledger_project import apply_source_fee, withholding
    base = norm_detail({"source": "執行業務所得", "personal_tax": 0})
    kept = apply_source_fee(42000, dict(base), keep={"personal_tax"})
    assert kept["personal_tax"] == 0, "使用者刻意設的 0（拆單不用先繳）被覆寫了"
    assert apply_source_fee(42000, dict(base))["personal_tax"] == withholding(42000)

    # 🔴 旗標要落庫：只改別欄的那種存檔（不送稅款）不可以把人調好的值洗回去。
    # 這一條是 dev 端到端抓到的 —— 只看「這次有沒有送」的版本在這裡就破功。
    saved = norm_detail(kept)
    assert saved.get("manual") == ["personal_tax"], "norm_detail 把旗標洗掉了"
    later = apply_source_fee(42000, dict(saved), keep={"misc"})
    assert later["personal_tax"] == 0, "只改別欄時，人調好的稅款被重算了"
    # 改回試算值＝交還自動：之後營收變動會跟著重算
    back = apply_source_fee(42000, dict(saved) | {"personal_tax": withholding(42000)},
                            keep={"personal_tax"})
    assert "personal_tax" not in (back.get("manual") or [])
    assert apply_source_fee(50000, norm_detail(back))["personal_tax"] == withholding(50000)
    # 代辦費 2026-09-13 起也放寬（owner「代開費用……但我要改還是可以改」）：
    # 改過就凍住，稅金照算、買發票＝代辦費 − 稅金；改回試算值交還自動。
    agency = apply_source_fee(82000, norm_detail({"source": "代開發票",
                                                  "invoice_fee": 5000}),
                              keep={"invoice_fee"})
    assert agency["invoice_fee"] == 5000 and agency.get("manual") == ["invoice_fee"]
    assert (agency["tax_fee"], agency["buy_invoice"]) == (3905, 5000 - 3905)
    later = apply_source_fee(90000, norm_detail(agency), keep={"misc"})
    assert later["invoice_fee"] == 5000, "只改別欄時，人調好的代辦費被重算了"
    back = apply_source_fee(82000, dict(norm_detail(agency)) | {"invoice_fee": 6560}, keep={"invoice_fee"})
    assert "invoice_fee" not in (back.get("manual") or []) and back["buy_invoice"] == 2655
    # 沒送（新建案）照樣試算
    assert apply_source_fee(82000, norm_detail({"source": "代開發票"}))["invoice_fee"] == 6560


def test_fee_deducted_flag_moves_the_fee_between_withheld_and_full_wire():
    """「代辦費已扣除」（owner 2026-09-13）：預設（缺鍵）＝源頭代扣，應收＝營收 − 代辦費；
    取消＝全額會匯進來，應收＝營收。營收永遠是合約額（實收 net 照扣代辦費 —— 那是成本）。
    只在 False 時落庫、前端用 `!== false` 讀。"""
    from core.ledger_project import (FEE_DEDUCTED_KEY, client_wire, compute, expected_cash_in,
                                     fee_deducted, to_collect, withheld_total)
    d = apply_source_fee(82000, norm_detail({"source": "代開發票"}))
    assert fee_deducted(d) and FEE_DEDUCTED_KEY not in d
    assert expected_cash_in(82000, d) == client_wire(82000, d) == 82000 - 6560
    assert to_collect(82000, 82000 - 6560, d) == 0
    off = norm_detail(dict(d) | {FEE_DEDUCTED_KEY: False})
    assert off[FEE_DEDUCTED_KEY] is False and not fee_deducted(off)
    assert expected_cash_in(82000, off) == client_wire(82000, off) == 82000
    assert withheld_total(off) == 0 and withheld_total(d) == 6560
    assert to_collect(82000, 82000 - 6560, off) == 6560, "沒先扣的代辦費不能當成收齊"
    assert compute(82000, off)[0] == compute(82000, d)[0] == 82000 - 6560, "實收不受旗標影響"
    # True 不落庫（缺鍵＝True）；垃圾值不當 False
    assert FEE_DEDUCTED_KEY not in norm_detail(dict(d) | {FEE_DEDUCTED_KEY: True})
    assert FEE_DEDUCTED_KEY not in norm_detail(dict(d) | {FEE_DEDUCTED_KEY: 0})


def test_the_manual_flag_is_a_list_so_a_second_field_costs_one_string():
    """🔴 「這幾欄由人決定」存成**清單**不是每欄一個布林鍵。

    只有一個欄位在用的時候兩種寫法一樣長，但第二欄（`invoice_fee` 的手動覆寫
    遲早會來 —— owner 對代開費說過「預設帶出內容，但我可以細調」）在布林鍵那
    條路上要付：新鍵 ＋ `norm_detail` 一段保留邏輯 ＋ `apply_source_fee` 一個
    分支 ＋ 一份前端鏡射。清單只多一個字串。

    舊資料（`tax_manual: 1`）要能無痛讀進來 —— 生產庫裡有。
    """
    from core.ledger_project import MANUAL_FIELDS, apply_source_fee

    assert "personal_tax" in MANUAL_FIELDS
    old = norm_detail({"source": "執行業務所得", "personal_tax": 3000, "tax_manual": 1})
    assert old.get("manual") == ["personal_tax"], "舊的布林鍵沒有轉成清單"
    assert "tax_manual" not in old, "兩種形狀同時存在＝兩個真相"
    # 轉過來之後照樣不被自動值覆寫
    assert apply_source_fee(42000, dict(old))["personal_tax"] == 3000
    # 名單外的鍵不留（避免前端亂送把任意欄位凍住）
    assert "manual" not in norm_detail({"source": "執行業務所得", "manual": ["outsource"]})


def test_the_update_endpoint_passes_what_the_user_sent():
    """端點要把「使用者送了哪些欄」傳給規則 —— 少了它，前端怎麼改都存不進去。

    🔴 掃描一律走 `_srcscan`：手刻 `Path(...).read_text()` ＋ `split("async def")`
    會漏掉剝註解那一步，斷言就會被「正好在說明這件事」的註解餵飽
    （_srcscan 檔頭記的第一個坑）。
    """
    from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src
    fn = code_only(func_body(repo_src("routers/api_finance_projects.py"),
                             "async def update_project_ledger("))
    # keep＝這次**真的送了值**的欄位；`None` 是「沒送」不是值（寫入迴圈也跳過
    # 它）—— 一起算進去的話，送 `{"personal_tax": null}` 會把舊值凍住。
    assert "keep={k for k, v in data.items() if v is not None}" in fn
    # 前端：「人調過了沒」的答案只有一個來源 —— 後端落庫的 `manual` 清單。自己
    # 維護「上次試算值」那種模組狀態活不過面板重畫，還得每次 render 手動校準。
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/projects.js"))
    assert "det.manual" in js, "前端沒吃後端落庫的旗標，又在自己猜"
    assert "_lastProTax" not in js, "又長出第二個「人調過了沒」的答案"


def test_each_ledger_has_its_own_write_key():
    """🔴 兩本帳各有各的鑰匙，**都不是「管理員」**：

    · 私帳 → `finance_mine`（指名制）。owner＝lv1＋finance_mine，原本 CRM 寫入
      全是管理員限定 —— 帳本主人在生產連一筆帳都記不進去（真實帳號形狀實測
      403 才發現；先前測試全用管理員 token）。
    · 母帳 → 「財務管理」模組（`crm_invoices`）。2026-09-05 起不再要管理員 ——
      owner：「權限管理也沒有什麼 lv 幾，都在這裡管理就好」，而使用者管理上
      「管理員」的說明是**使用者 / 設定 / 發版**。
    """
    src = finance_src()   # 四個帳務檔串起來（2026-08-30 拆檔）
    helper = src.split("def _mine_or_admin_write(")[1].split("\ndef ")[0]
    assert 'require_entity(request, "mine", level="full")' in helper
    assert 'require_entity(request, "parent", level="full")' in helper      # 母帳＝帳務＋金額檢視（2026-09-08 第三批一把尺）
    # 建立/更新走 _entity_for_write 咽喉（守衛收在裡面 —— 逐端點明呼會忘，
    # 而且端點自己那行**擋不住**咽喉：2026-09-05 實測過）
    throat = src.split("def _entity_for_write(")[1].split("\ndef ")[0]
    assert "_mine_or_admin_write(request, ent)" in throat
    for fn_name in ("create_cash_entry", "update_cash_entry",
                    "create_payment", "update_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_entity_for_write" in fn, fn_name
    # 刪除沒有 payload、不走咽喉 —— 各自明呼
    for fn_name in ("delete_cash_entry", "delete_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_mine_or_admin_write" in fn, fn_name
    # 🔴 分配寫入（_write_allocs 兩側共用）與批次掛專案 —— 2026-08-26 生產實測
    # 補上的兩條漏網（owner 存「請款單分配」權限不足 ×3）。不走咽喉的寫入端點
    # 一律：check_logged_in → 載列 → 按列帳本驗 _mine_or_admin_write*
    alloc = src.split("async def _write_allocs(")[1].split("\n@router")[0]
    assert "_check_auth(" not in alloc and "_mine_or_admin_write(request, e.entity)" in alloc
    batch = src.split("async def batch_assign_project(")[1].split("\n@router")[0]
    assert "_check_auth(" not in batch and "_mine_or_admin_write_rows(request, rows)" in batch
    # batch_receive 2026-08-30 併入同一條規則：它原本 `_check_auth` ＋逐張
    # `session.get(CrmInvoice, iid)` 不看 entity＝可跨帳本改發票（見下面那支測試）
    for fn_name in ("batch_pay", "batch_unpay", "batch_update_month", "batch_receive"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_mine_or_admin_write_rows(request, rows)" in fn, fn_name
        assert "_check_auth(" not in fn, f"{fn_name}：Lv3-only 會把帳本主人擋在門外"
    # 發票的寫入 2026-09-05 起走模組守衛（細節在 test_invoice_write_permission.py）
    for fn_name in ("create_invoice", "purge_invoice_trash"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_check_finance_auth(request)" in fn, fn_name


def test_batch_receive_cannot_touch_the_other_ledger():
    """🔴 批次標記已收款要按列的帳本驗，不能只認 Lv3。

    原本的形狀：`_check_auth` 過了就逐張 `session.get(CrmInvoice, iid)` 直接改。
    id 是前端從**已按帳本過濾**的清單帶回來的，所以畫面上摸不到別本帳的發票 ——
    但端點本身沒有任何一道防線，手打一組 id 就改得到，回應只有一個 updated 數字，
    不噴錯、不留痕。私帳現在 0 張發票所以還沒出事，這是還沒到期的帳。
    """
    fn = finance_src().split("async def batch_receive(")[1].split("\n@router")[0]
    # 先把會變動的列載齊 → 驗 → 才寫（權限不足時一筆都不動）
    assert fn.index("_mine_or_admin_write_rows(request, rows)") < fn.index(
        "_mark_invoice_received("), "守衛要在寫入之前，不能邊改邊驗"


def test_a_mixed_batch_needs_write_rights_on_every_ledger_it_touches():
    """批次守衛取**聯集**：涉及哪幾本帳就要有哪幾本的權限。

    舊版是「全私帳 → mine full；混到母公司 → Lv3」。Lv3 不隱含 finance_mine
    （生產上除 owner 外每個管理員都是這種），所以往 ids 裡混一張母公司發票，
    整批就從 Lv3 那道門進來、把私帳那幾列一起改掉。
    """
    fn = finance_src().split("def _mine_or_admin_write_rows(")[1].split("\n\n\n")[0]
    assert "for ent in {" in fn and "_mine_or_admin_write(request, ent)" in fn, \
        "要逐本驗，不是把整批壓成單一 target_entity"
    assert 'else "parent"' not in fn.split('or "parent") for r in rows}')[-1], \
        "混帳本不可以再降級成只驗母公司"


def test_outsource_sync_is_incremental_and_scoped():
    src = finance_src()   # 四個帳務檔串起來（2026-08-30 拆檔）
    helper = src.split("async def _apply_outsource(")[1].split("\ndef ")[0]
    # 🔴 帶硬連結的請款單（逐案損益的一鍵請款建的）不累加 —— 那筆錢已經由
    # apply_crm_costs 從 CRM 成本行算過一次（甲案是相加制）
    assert '!= "專案外包" or linked:\n        return' in helper.replace("'", '"')
    assert "sign * amount" in helper, "增量制：加與減走同一條，不是重算"
    for fn_name in ("create_payment", "update_payment", "delete_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_apply_outsource" in fn, fn_name
