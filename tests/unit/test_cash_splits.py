# -*- coding: utf-8 -*-
"""收支拆項（「帳目一筆、內容拆裂」；owner 2026-08-31 規劃 v3）。

一筆母公司匯款（+350,436）同時裝著專案款、代墊回款、薪資。帳目維持一筆
（對帳 1↔1、收支明細一列都不動），拆裂放在 crm_cash_splits——這裡守三件事：

  1. 純規則（Σ 不變式、方向、代墊餘額）—— 直接算
  2. 載入層展開 —— **黃金等價**：拆項展開後的三表 ＝ 手動拆成 N 列的三表
     （你 Sheet 時代的記法就是後者；工具化不能改變任何一個數字）
  3. 守衛與讓位 —— 拆項父列的分類/金額/專案不准從別的門改
"""
import pytest

from core.crm_logic import advance_open_amount, split_amount_error, split_side
from services.finance_statements import explode_cash_splits
from tests.unit._srcscan import (code_only, flow_body, func_body, js_code_only,
                                 js_func_body, migration_sql, repo_src)

SPLITS = "routers/crm/cash_splits.py"
CASH = "routers/crm/cash.py"
STMT = "routers/api_finance_stmt.py"


# ── 1. 純規則 ───────────────────────────────────────────────────

def test_the_side_is_unambiguous_or_nothing():
    """兩側都有值（同列又收又付）不開放拆項 —— Σ 沒有明確的分母。"""
    assert split_side(350436, None) == "deposit"
    assert split_side(0, 5000) == "expense"
    assert split_side(100, 100) == ""
    assert split_side(None, None) == ""


def test_the_sum_must_match_to_the_dollar():
    """🔴 差一塊都不能存 —— 帳才對得回銀行。訊息要講出差多少。"""
    assert split_amount_error(350436, [250000, 62000, 38436]) == ""
    err = split_amount_error(350436, [250000, 62000])
    assert "38,436" in err.replace("+", "")      # 差額要說出來
    assert split_amount_error(100, []) != ""
    assert split_amount_error(100, [100, 0]) != "", "0 元拆項是沒有意義的殼"
    assert split_amount_error(100, [200, -100]) != "", "負數拆項會讓兩側互相掩護"


def test_the_advance_balance_never_goes_negative():
    assert advance_open_amount(5000, 3000) == 2000
    assert advance_open_amount(5000, 5000) == 0
    assert advance_open_amount(5000, 9999) == 0, "超沖是寫入端要擋的，餘額不出負數"


# ── 2. 載入層展開：黃金等價 ─────────────────────────────────────

def _entry(**kw):
    base = {"id": "E1", "entry_date": "2026-08-13", "deposit": None,
            "expense": None, "bank_fee": None, "claim": None, "status": None,
            "category": None, "summary": "新網自轉", "invoice_id": None,
            "bank_memo": "", "note": "", "advance_payment_id": None,
            "payment_request_id": None, "bank_account_id": "A"}
    base.update(kw)
    return base


def test_exploded_statements_equal_the_hand_split_ones():
    """🔴 這是整個功能的驗收標準：拆項展開餵進 build_pnl 的結果，必須跟
    「手動拆成 N 列」（Sheet 時代的記法）**逐字相同** —— 收入、成本、每一個
    百分比。工具化改變任何一個數字，就是把對的帳改錯。"""
    from core.finance_logic import build_pnl
    cat_map = {("cash", "公司_專案"): {"treatment": "direct_income", "account_id": "4100"},
               ("cash", "公司_代墊"): {"treatment": "transfer", "account_id": "1300"},
               ("cash", "公司_薪水"): {"treatment": "direct_income", "account_id": "4200"}}
    accounts = {"4100": {"name": "營收", "pnl_group": "營業收入"},
                "4200": {"name": "薪資收入", "pnl_group": "營業收入"},
                "1300": {"name": "其他應收", "pnl_group": None}}
    months = ["2026-08"]
    # 一筆 350,436 拆三項
    lump = [_entry(deposit=350436)]
    splits = {"E1": [
        {"id": "S1", "amount": 250000, "category": "公司_專案", "note": "", "project_id": "P1"},
        {"id": "S2", "amount": 62000, "category": "公司_專案", "note": "", "project_id": "P2"},
        {"id": "S3", "amount": 38436, "category": "公司_代墊", "note": "", "project_id": None},
    ]}
    hand = [_entry(id="H1", deposit=250000, category="公司_專案"),
            _entry(id="H2", deposit=62000, category="公司_專案"),
            _entry(id="H3", deposit=38436, category="公司_代墊")]

    kw = dict(cat_map=cat_map, accounts=accounts, invoices=[], payments=[],
              equipment=[], loan_payments=[])
    a = build_pnl(months, cash_entries=explode_cash_splits(lump, splits), **kw)
    b = build_pnl(months, cash_entries=hand, **kw)
    assert a == b, "拆項展開與手動 N 列的損益不同 —— 展開規則改壞了"
    # 而且代墊那份**不在**營收裡（transfer 不進損益）
    assert a["revenue"]["total"] == 312000


def test_cash_flow_is_conserved_per_account():
    """展開前後每個帳戶的淨流一致 —— 銀行餘額與對帳工作台都靠這條。"""
    from core.finance_logic import cash_entry_flow
    lump = [_entry(deposit=350436, bank_fee=30)]
    splits = {"E1": [{"id": "S1", "amount": 350000, "category": "公司_專案",
                      "note": "", "project_id": None},
                     {"id": "S2", "amount": 436, "category": "公司_代墊",
                      "note": "", "project_id": None}]}
    out = explode_cash_splits(lump, splits)
    assert sum(cash_entry_flow(e) for e in out) == cash_entry_flow(lump[0])


def test_the_bank_fee_is_counted_exactly_once():
    """🔴 匯費掛第一個虛擬列。放每列一份 → bank_fee_total 重複計。"""
    lump = [_entry(deposit=1000, bank_fee=30)]
    splits = {"E1": [{"id": "S1", "amount": 600, "category": "a", "note": "", "project_id": None},
                     {"id": "S2", "amount": 400, "category": "b", "note": "", "project_id": None}]}
    out = explode_cash_splits(lump, splits)
    assert [int(e.get("bank_fee") or 0) for e in out] == [30, 0]


def test_a_sum_mismatch_surfaces_as_an_unclassified_residual():
    """🔴 資料繞過寫入端（直接改 DB、舊版匯入）造成 Σ 漂移時，差額補一個
    category=None 的殘項 → 落進「未歸類」誠實外顯。靜默吞掉的話報表憑空
    少一塊錢，而且不會有人知道。"""
    lump = [_entry(deposit=1000)]
    splits = {"E1": [{"id": "S1", "amount": 700, "category": "a", "note": "", "project_id": None}]}
    out = explode_cash_splits(lump, splits)
    assert len(out) == 2
    rest = out[-1]
    assert rest["deposit"] == 300 and rest["category"] is None
    assert rest["id"] == "E1#rest"


def test_parent_level_links_are_not_inherited_by_the_virtual_rows():
    """invoice_id／advance_payment_id／payment_request_id 屬於父列整筆 ——
    複製 N 份會讓對應的沖銷邏輯把同一筆連結算 N 次。"""
    lump = [_entry(deposit=1000, invoice_id="INV", advance_payment_id="ADV",
                   payment_request_id="PR")]
    splits = {"E1": [{"id": "S1", "amount": 1000, "category": "a", "note": "", "project_id": None}]}
    v = explode_cash_splits(lump, splits)[0]
    assert v["invoice_id"] is None and v["advance_payment_id"] is None \
        and v["payment_request_id"] is None


def test_entries_without_splits_pass_through_untouched():
    lump = [_entry()]
    assert explode_cash_splits(lump, {}) is lump, "沒有拆項就不要重建列表"
    assert explode_cash_splits(lump, {"OTHER": [{"id": "x", "amount": 1,
                                                 "category": "a", "note": "",
                                                 "project_id": None}]})[0] is lump[0]


def test_expense_side_splits_explode_on_the_expense_column():
    lump = [_entry(deposit=None, expense=5000)]
    splits = {"E1": [{"id": "S1", "amount": 3000, "category": "a", "note": "", "project_id": None},
                     {"id": "S2", "amount": 2000, "category": "b", "note": "", "project_id": None}]}
    out = explode_cash_splits(lump, splits)
    assert [e["expense"] for e in out] == [3000, 2000]
    assert all(e["deposit"] is None for e in out)


# ── 2.5 發票代開費（owner 2026-08-31：源日代開發票、扣完費用才匯）────
#
# 數學同收款匯費（recognize_receipt_fee）：拆項 amount 記**實匯淨額**、fee
# 外加。三個不變量：營收進毛額、費用記一次、淨流一塊不動。

def test_the_invoice_fee_grosses_revenue_and_keeps_net_flow():
    from core.finance_logic import cash_entry_flow
    lump = [_entry(deposit=350436)]
    splits = {"E1": [
        {"id": "S1", "amount": 57000, "fee": 3000, "category": "公司_專案",
         "note": "", "project_id": "P1"},
        {"id": "S2", "amount": 293436, "category": "公司_代墊", "note": "",
         "project_id": None},
    ]}
    out = explode_cash_splits(lump, splits)
    s1 = next(e for e in out if e["id"] == "S1")
    assert s1["deposit"] == 60000, "營收要進毛額（amount+fee）"
    assert int(s1["bank_fee"] or 0) == 3000, "代開費走 bank_fee 費用鏈"
    assert sum(cash_entry_flow(e) for e in out) == 350436, \
        "淨流不變 —— 銀行餘額鏈與對帳工作台都靠這條"
    assert not any(str(e["id"]).endswith("#rest") for e in out), \
        "Σ 殘項判定用淨額 —— fee 不能生出假殘項"


def test_the_parent_bank_fee_and_the_split_fee_stack_without_loss():
    """父列自己的匯費（掛第一列）＋拆項的代開費要疊加，不能互相蓋掉。"""
    from core.finance_logic import cash_entry_flow
    lump = [_entry(deposit=1000, bank_fee=30)]
    splits = {"E1": [{"id": "S1", "amount": 600, "fee": 50, "category": "公司_專案",
                      "note": "", "project_id": "P1"},
                     {"id": "S2", "amount": 400, "category": "b", "note": "",
                      "project_id": None}]}
    out = explode_cash_splits(lump, splits)
    assert [int(e.get("bank_fee") or 0) for e in out] == [80, 0]
    assert sum(cash_entry_flow(e) for e in out) == cash_entry_flow(lump[0])


def test_project_settlement_uses_the_gross_amount():
    """🔴 已收按毛額（amount+fee）記 —— 只記淨額的話，未收額永遠留一個
    費用大小的尾巴清不掉（這正是 fee 欄存在的理由）。"""
    from routers.crm.cash_splits import _project_deltas

    class S:                      # 替身：_project_deltas 只讀這三個欄
        def __init__(self, **kw):
            self.__dict__.update(kw)

    d = _project_deltas("deposit", 0, None,
                        [S(project_id="P1", amount=57000, fee=3000),
                         S(project_id=None, amount=1000, fee=0)])
    assert d == {"P1": 60000}


def test_the_fee_survives_the_full_round_trip():
    """🔴 重編輯的 initial 就是 _split_to_dict 的輸出 —— fee 沒跟著出去的話，
    使用者按一次「儲存拆項」代開費就靜默歸零（advances 同一款教訓）。
    載入層與 migration 也各要有它，少一處就是三表少算或開機缺欄。"""
    # 🔴 序列化的欄位集合要**蓋過** schema：少一欄就是使用者重存時靜默丟資料
    # （advances 與 fee 都踩過）。釘欄位集合而不是某一行的寫法。
    from core.schemas import CashSplitItem
    ser = code_only(func_body(repo_src(SPLITS), "def _split_to_dict("))
    for field in CashSplitItem.model_fields:
        assert f'"{field}"' in ser, f"_split_to_dict 沒有把 {field} 送出去"
    assert '"fee": int(_s.fee or 0)' in repo_src("services/finance_statements.py")
    assert "crm_cash_splits ADD COLUMN IF NOT EXISTS fee" in migration_sql()
    ed = js_func_body(js_code_only(repo_src("frontend/js/shared/cash-split-editor.js")),
                      "export async function openCashSplitEditor(")
    for field in ("fee", "project_name", "advances"):
        assert field in ed, f"編輯器沒有處理 {field}（讀回來或送回去少一邊都是丟資料）"
    # 🔴 project_name 同款：未收案清單只有還在等錢的案 —— 結清的案查不到名字，
    # 那列會顯示成一串 raw id（和平行動者案，owner 以為連結壞掉）。
    assert '"project_name"' in ser
    cbjs = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "_splitProjNames" in cbjs, "拆項父列的專案格要把拆項連的案名秀回來"


def test_the_split_row_still_fills_all_three_taxonomy_cells():
    """🔴 收支列表是 **flex**，欄寬由 `nth-child(N)` 給 —— 拆項列若只吐一個
    格子（原本想用 `grid-column:span 3` 併成一格，那在 flex 底下無效），
    後面每一欄的 nth-child 全部前移兩格：銀行資訊跑到附註欄、專案名跑到
    銀行資訊欄（owner 2026-09-01 截圖）。所以拆項分支也要吐滿三個 div。
    """
    src = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "grid-column" not in src, \
        "這個列表是 flex，grid-column 不會生效 —— 要吐滿格子數"
    # 🔴 js_func_body 切到下一個**頂層宣告** —— `split("\nfunction ")` 只擋得住
    # 下一個剛好是 function 的情形，過切會把整個檔案算進來（_srcscan 檔頭那個坑）
    row = js_func_body(src, "function _rowHtml(")
    # 兩個分支的格子數要一樣多，否則整排錯位（數 <div 的開頭）
    branch = row.split("${e.split_count ?")[1].split("`}")[0]
    yes, no = branch.split('` : `')
    assert yes.count("<div") == no.count("<div") == 3, \
        f"拆項/未拆兩個分支的格子數不同：{yes.count('<div')} vs {no.count('<div')}"


def test_the_fee_is_rejected_outside_deposit_side_project_splits():
    """代開費＝源日先扣走的**專案款** —— 支出側或沒掛專案的拆項寫 fee
    是分類錯誤，寫入端要 422 而不是默默存起來變成幽靈費用。"""
    body = code_only(func_body(repo_src(SPLITS), "async def _apply_splits("))
    assert "it.fee" in body and "代開費" in func_body(repo_src(SPLITS),
                                                  "async def _apply_splits(")
    assert 'side != "deposit"' in body


# ── 3. 守衛與讓位（掃原始碼）────────────────────────────────────

def test_the_writer_is_single_and_enforces_the_trinity():
    """寫入端唯一（_apply_splits），三個正本都走既有的那份：
    Σ 規則（crm_logic）、鏡射（cash._sync_taxonomy）、專案已收
    （cash._sync_mine_project_received）。"""
    src = code_only(repo_src(SPLITS))
    body = func_body(repo_src(SPLITS), "async def _apply_splits(")
    assert "split_amount_error(" in body
    assert "_sync_taxonomy(session, s," in body, "鏡射不准在這裡自己拼"
    assert "_sync_mine_project_received" in src, "專案已收要走增量制那份正本"
    # 讓位：父列的分類與專案清空
    assert "e.taxonomy_node_id = None" in body
    assert "e.project_id = None" in body


def test_the_endpoint_has_the_full_guard_stack():
    """check_logged_in → 列的帳本（_mine_or_admin_write）→ 鎖月。跟收支明細
    其他寫入端同一套，一個都不能少。"""
    body = code_only(func_body(repo_src(SPLITS),
                               "async def replace_cash_entry_splits("))
    assert "check_logged_in(request)" in body
    assert "_mine_or_admin_write(request, e.entity)" in body
    assert "_assert_month_open(session, e.entry_date" in body


def test_over_settling_an_advance_is_rejected():
    """代墊那列只剩 2,000 未回款就不能沖 5,000 —— 超沖讓 1300 變負。"""
    body = code_only(func_body(repo_src(SPLITS), "async def _apply_splits("))
    assert "advance_open_amount(" in body and "超沖" in func_body(
        repo_src(SPLITS), "async def _apply_splits(")


def test_a_split_parent_cannot_be_edited_around_the_splits():
    """🔴 三個旁門都要鎖：單筆編輯（金額/分類/專案）、批次分類、規則自動分類。
    留一個就等於 Σ 不變式旁邊長出第二份答案。"""
    upd = func_body(repo_src(CASH), "async def update_cash_entry(")
    assert '"deposit", "expense", "category"' in upd and "409" in upd
    batch = func_body(repo_src(CASH), "async def batch_set_taxonomy(")
    assert "_split_parents" in batch and "409" in batch
    rules = code_only(func_body(repo_src(STMT),
                                "async def apply_rules_to_unclassified("))
    assert "entry_has_splits_subq()" in rules


def test_deleting_the_parent_cascades_and_reverses_the_project_deltas():
    body = func_body(repo_src(CASH), "async def delete_cash_entry(")
    # teardown 只有 cash_splits 一份（remove_entry_splits：連刪＋專案已收沖回）
    assert "remove_entry_splits(session, e)" in body
    assert "advance_entry_id == entry_id" in body, "被沖銷的代墊列刪除時，指向它的連結要清"
    rm = code_only(func_body(repo_src(SPLITS), "async def remove_entry_splits("))
    assert "_shift_project_received" in rm and "_drop_splits" in rm


def test_the_statement_import_routes_splits_through_the_one_writer():
    """匯入的拆項也走 _apply_splits（唯一寫入端），而且要在本列的
    分類/專案/發票處理**之前** continue —— 讓位規則兩條路一致。"""
    body = code_only(flow_body(repo_src(STMT), "async def apply_bank_statement("))
    assert "_apply_splits(session, ce, r.splits" in body
    i = body.index("_apply_splits(session, ce, r.splits")
    assert "continue" in body[i:i + 400], "拆項列沒有跳過本列的分類/發票處理"


def test_list_filters_can_still_find_split_money():
    """**每一個**分類形狀的篩選（節點/類別/子項/book/item/專案）都要問拆項 ——
    走同一個述詞入口 split_category_pred；「未分類」快篩要排除拆項父列。

    🔴 母公司沒有樹，category/book/item 平面篩選是它的主要入口 —— 第一版只
    補了 node/project 兩個，母公司拆過的錢在平面篩選裡人間蒸發（/simplify
    層次審查）。"""
    body = code_only(func_body(repo_src(CASH), "async def list_cash_entries("))
    assert body.count("split_category_pred(") >= 5,         "分類形狀的篩選有的沒問拆項（node/category/sub_item/book/item/project 六處）"
    assert "entry_has_splits_subq()" in body, "未分類快篩沒排除拆項父列"


def test_the_project_rollup_includes_split_costs():
    body = code_only(func_body(repo_src("routers/api_finance_projects.py"),
                               "async def _rollups("))
    assert "CrmCashSplit.project_id" in body
    assert "CrmCashEntry.expense > 0" in body, "收入側走 amount_received，不重複算"


# ── 4. 前端契約 ─────────────────────────────────────────────────

def test_both_mount_points_share_the_one_editor():
    """對帳單匯入與收支明細用同一份編輯器（js/shared）—— 各抄一份的教訓
    見 cash-tax-picker 檔頭。"""
    for f in ("frontend/tabs/finance/subviews/recon.js",
              "frontend/tabs/crm/crm-cashbook.js"):
        assert "cash-split-editor.js" in repo_src(f), f


def test_the_import_payload_carries_splits_as_a_list():
    """同 invoices 的教訓：後端 List 欄不收 null，寫成 null 整批 422。"""
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/recon.js"))
    assert "splits: x.splits || []" in js


def test_the_split_row_yields_its_classification_cells():
    """有拆項的列在兩個畫面都顯示「已拆 N 項」而不是分類編輯格 ——
    分類的正本在拆項，留著可點的格子等於邀請人改出第二份答案。
    badge 只有 splitBadgeHtml 一顆（兩畫面各刻一顆上線第一天樣式就漂了）。"""
    ed = repo_src("frontend/js/shared/cash-split-editor.js")
    assert "export function splitBadgeHtml" in ed and "已拆 ${Number(count)" in ed
    for f in ("frontend/tabs/finance/subviews/recon.js",
              "frontend/tabs/crm/crm-cashbook.js"):
        js = js_code_only(repo_src(f))
        assert "splitBadgeHtml(" in js, f
        # 抓的是 badge 的 span 標記（背景綠 pill），不是「已拆」二字 ——
        # toast 訊息也會講「已拆 N 項」，那不是第二顆 badge
        assert "background:#14351f" not in js, f"{f} 又自己刻了一顆 badge"
    assert "_cashSplitOpen" in js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))


def test_the_editor_enforces_the_sum_before_the_server_does():
    """編輯器的儲存鈕在 Σ ≠ 目標時 disabled —— 後端 422 是底線，
    但即時的「差 +38,436」才是人拆得完的介面。"""
    ed = repo_src("frontend/js/shared/cash-split-editor.js")
    assert "sum() === target" in ed
    assert "disabled" in ed and "差 " in ed


@pytest.mark.parametrize("frag", [
    "advances", "project_node_id", "advance_node_id"])
def test_the_outstanding_endpoint_feeds_the_picker(frag):
    """選單資料（未收案／未回款代墊／建議節點）由後端一次給齊。"""
    body = repo_src(SPLITS)
    assert frag in body
    ed = repo_src("frontend/js/shared/cash-split-editor.js")
    assert frag.replace("_id", "") in ed or frag in ed


# ── /simplify 審查修正的釘（2026-08-31）────────────────────────

def test_every_split_needs_a_classification():
    """🔴「未分類快篩排除拆項父列」的前提＝拆項一定分好了 —— 前提要在唯一
    寫入端成立。放行沒分類的拆項，那筆錢會從每個「找未分類」的工具眼前消失。"""
    body = code_only(func_body(repo_src(SPLITS), "async def _apply_splits("))
    assert "每個拆項都要有分類" in func_body(repo_src(SPLITS),
                                             "async def _apply_splits(")
    assert "it.taxonomy_node_id" in body and "it.category" in body


def test_splits_are_exclusive_with_other_row_intents_at_the_schema():
    """🔴 拆項列的發票/請款/匯費/源日請款要在 **schema** 擋（422），不能讓
    寫入端靜默跳過 —— 使用者在預覽勾好的東西無聲消失，正好發生在他特地去拆
    的那種複雜列上。前端的讓位清單要跟互斥集一致。"""
    sch = repo_src("core/schemas.py")
    assert "_splits_are_exclusive" in sch and "petty_claim" in         func_body(sch, "def _splits_are_exclusive(")
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/recon.js"))
    for cleared in ("r.payments = []", "r.payment_fee = null",
                    "r.petty_claim = false"):
        assert cleared in js, f"前端讓位清單少了 {cleared}（會整批 422）"


def test_the_other_readers_learned_about_splits():
    """🔴 /simplify 層次審查抓到的整類「沒跟上的讀取端」，一個一個釘住：
    改名重鏡射、分類樹刪除守衛與用量、月結快照分類彙總、未對映稽核。"""
    remirror = code_only(func_body(repo_src("core/cash_tree.py"),
                                   "async def remirror_subtree("))
    assert "CrmCashSplit" in remirror, "改名後拆項的鏡射不會重算 → 靜默落未歸類"
    tax = code_only(repo_src("routers/crm/taxonomy.py"))
    assert tax.count("CrmCashSplit") >= 2, "刪除守衛或用量沒看拆項"
    snap = code_only(func_body(repo_src("routers/api_cashflow.py"),
                               "async def _month_snapshot("))
    assert "CrmCashSplit" in snap, "月結快照的分類彙總把拆項支出整筆算成未分類（快照是永久的）"
    audit = code_only(func_body(repo_src("routers/api_finance.py"),
                                "async def list_unmapped_categories("))
    assert "CrmCashSplit.category" in audit, "沒對映的拆項類別不會出現在待辦"


def test_the_explode_side_rule_is_the_shared_one_and_ambiguous_rows_pass_through():
    """兩側都有值的列判不出分母 → 原樣通過不展開（自己 if/else 判的話另一側
    會被 None 掉，錢從報表上消失）。"""
    lump = [_entry(deposit=100, expense=100)]
    splits = {"E1": [{"id": "S1", "amount": 100, "category": "a",
                      "note": "", "project_id": None}]}
    out = explode_cash_splits(lump, splits)
    assert out == lump, "兩側都有值的列被展開了"
    body = code_only(func_body(repo_src("services/finance_statements.py"),
                               "def explode_cash_splits("))
    assert "split_side(" in body, "方向判定要走 crm_logic 唯一那份"


def test_virtual_rows_carry_the_parent_pk_and_writers_skip_them():
    """展開後的 id 是拆項 id、不是 DB 的列 —— 拿 inputs 的 id 回頭寫 DB 的
    消費端（轉存匯費認列）要看 parent_id 跳過，不能靜默 continue。"""
    lump = [_entry(deposit=1000)]
    splits = {"E1": [{"id": "S1", "amount": 1000, "category": "a",
                      "note": "", "project_id": None}]}
    assert explode_cash_splits(lump, splits)[0]["parent_id"] == "E1"
    fin = code_only(repo_src("routers/api_finance.py"))
    assert fin.count('not e.get("parent_id")') >= 2,         "transfer-pairs 與匯費認列都要濾掉虛擬列"


def test_the_edit_guard_compares_values_not_sent_keys():
    """🔴 編輯視窗每次都整包送 _FIELDS —— 按「鍵有沒有送」判的話，拆項父列
    連改個摘要都 409，使用者被迫解除拆項（代墊結清連結陪葬）。"""
    upd = func_body(repo_src(CASH), "async def update_cash_entry(")
    assert "_changed" in upd and "_norm(" in upd
    assert "getattr(e, k" in upd, "要跟列上的現值比，不是看 payload 有沒有那個鍵"


def test_reedit_keeps_the_advance_links():
    """🔴 _split_to_dict 一定要帶 advances —— 重編輯的 initial 就是這份 dict，
    少了它，按一次儲存代墊結清連結就整組被 replace 掉（靜默）。"""
    body = code_only(func_body(repo_src(SPLITS), "def _split_to_dict("))
    assert '"advances"' in body
    lst = code_only(func_body(repo_src(CASH), "async def list_cash_entries("))
    assert "load_advance_links_map(" in lst, "清單的 splits 沒帶 advances"
