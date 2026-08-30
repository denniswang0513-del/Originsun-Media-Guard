# -*- coding: utf-8 -*-
"""收支分類樹：正本內容、路徑 → 三欄鏡射、扁平轉樹，與寫入端的順序不變式。

不碰 DB —— 這裡釘的是規則，種子與回填的實際效果在 dev 上驗。
"""
from core.cash_taxonomy import mirror_from_path, split_category
from core.cash_tree import build_tree, flatten
from db.seed_cash_taxonomy import CANONICAL, HOUSE_GROUPS, canonical_paths
from tests.unit._srcscan import finance_src


class _N:
    """假的 ORM 節點（build_tree 只讀這四個屬性）。"""
    def __init__(self, nid, parent_id, name, depth):
        self.id, self.parent_id, self.name, self.depth = nid, parent_id, name, depth


# ── 鏡射：只有前三層進 category/item/sub_item ──────────────────────
def test_mirror_keeps_first_three_levels_only():
    deep = ["家用", "變動支出", "醫療保健", "乳癌治療", "台北馬偕"]
    assert mirror_from_path(deep) == ("家用_變動支出", "變動支出", "醫療保健")
    # 三層以內原樣
    assert mirror_from_path(["公司", "專案", "已收帳款"]) == ("公司_專案", "專案", "已收帳款")
    assert mirror_from_path(["公司", "代墊"]) == ("公司_代墊", "代墊", "")
    assert mirror_from_path(["信用卡"]) == ("信用卡", "", "")
    assert mirror_from_path([]) == ("", "", "")
    assert mirror_from_path(None) == ("", "", "")


def test_mirror_round_trips_with_split_category():
    """鏡射出來的 category 一定切得回原本的前兩層 —— 兩邊規則不能各走各的。"""
    for path in canonical_paths():
        cat, item, _sub = mirror_from_path(list(path))
        book, item2 = split_category(cat)
        assert book == path[0]
        assert item2 == item
        assert item == (path[1] if len(path) > 1 else "")


# ── 正本內容（對著 owner 的 Sheet `索引` 分頁與帳上實際資料）─────────
def test_canonical_has_five_books():
    assert list(CANONICAL) == ["公司", "個人", "家用", "轉匯與定存", "信用卡"]


def test_company_project_third_level_is_the_money_role():
    """`公司▸專案` 的第三層＝款別（Sheet 裝不下才溢出到旁邊那欄的 610 筆）。

    🔴 不是專案名 —— 專案走 crm_cash_entries.project_id，不在樹裡再存一次。
    """
    assert list(CANONICAL["公司"]["專案"]) == ["已收帳款", "已付稅款", "委外已付"]


def test_medical_branch_goes_five_levels_deep():
    """唯一長到第五層的一支。深度因分支而異，正是不用固定欄位數的理由。"""
    hosp = CANONICAL["家用"]["變動支出"]["醫療保健"]["乳癌治療"]
    assert "台北馬偕" in hosp and "內湖禾馨" in hosp
    deepest = max(len(p) for p in canonical_paths())
    assert deepest == 5


def test_personal_and_household_share_the_same_eight_groups():
    """個人／家用同名的 8 組出生時內容一致（帳上印證共用一份清單）。"""
    for grp, subs in HOUSE_GROUPS.items():
        assert list(CANONICAL["個人"][grp]) == list(subs), grp
        assert list(CANONICAL["家用"][grp]) == list(subs) or grp == "變動支出"


def test_household_variable_only_differs_by_the_deep_branch():
    """家用▸變動支出 跟共用那份的差別**只有**醫療保健長出下一層。"""
    shared, house = HOUSE_GROUPS["變動支出"], CANONICAL["家用"]["變動支出"]
    assert list(shared) == list(house)              # 同一批子項目、同一個順序
    assert all(not v for k, v in house.items() if k != "醫療保健")
    assert house["醫療保健"] and not shared["醫療保健"]   # 共用那份沒被改到


def test_house_groups_not_mutated_by_deep_branch():
    """🔴 深支是 deepcopy 出來的 —— 直接改會連個人那 8 組一起改到。"""
    assert HOUSE_GROUPS["變動支出"]["醫療保健"] == {}


# ── 扁平 → 樹 ──────────────────────────────────────────────────────
def test_build_tree_fills_full_path():
    nodes = [_N("a", "", "家用", 1), _N("b", "a", "變動支出", 2),
             _N("c", "b", "醫療保健", 3), _N("d", "c", "乳癌治療", 4)]
    flat = {n["id"]: n for n in flatten(build_tree(nodes))}
    assert flat["d"]["path"] == ["家用", "變動支出", "醫療保健", "乳癌治療"]
    assert flat["a"]["path"] == ["家用"]


def test_build_tree_drops_orphans():
    """父節點不在（被停用/刪掉）→ 整支不出現，不給半截爬不回根的路徑。"""
    nodes = [_N("a", "", "家用", 1), _N("x", "missing", "孤兒", 2)]
    ids = {n["id"] for n in flatten(build_tree(nodes))}
    assert ids == {"a"}


# ── 階段 2：寫入端的兩個容易靜默出錯的地方（讀原始碼釘住）─────────────
_FIN = finance_src()


def test_create_decides_taxonomy_by_what_was_actually_sent():
    """🔴 建立時 data 是**整包**（每個欄位都在）。照它判「有沒有送
    taxonomy_node_id」，會把「只送 category 的建立」當成「明確送了空節點」，
    分類當場被清掉。所以建立必須另外算一份 exclude_unset。
    """
    create = _FIN[_FIN.index("async def create_cash_entry"):
                  _FIN.index("async def _sync_single_alloc")]
    assert "exclude_unset=True" in create
    assert "_sync_taxonomy(session, e, sent)" in create
    assert "_sync_taxonomy(session, e, data)" not in create


def test_taxonomy_sync_runs_before_project_link_check():
    """可掛專案的判定吃的是 category，而 category 是分類同步**推導出來的**。
    順序反了，新列會用改之前的類別去判能不能掛專案。"""
    for fn in ("async def create_cash_entry", "async def update_cash_entry"):
        body = _FIN[_FIN.index(fn):]
        body = body[:body.index("await session.commit()")]
        assert body.index("_sync_taxonomy") < body.index("_enforce_cash_project_link"), fn


def test_node_is_the_source_of_truth_not_the_client_columns():
    """送了節點就以節點為準 —— 前端送的 category 不算數（兩邊各算一份必漂）。"""
    fn = _FIN[_FIN.index("async def _sync_taxonomy"):
              _FIN.index("async def _assert_project_same_entity")]
    assert "mirror_from_path" in fn
    # 清空節點時三欄一起清，不留「有類別但不在樹上」的孤兒
    assert 'e.category = e.item = e.sub_item = ""' in fn


# ── 階段 3：改名是資料遷移，三件事要一起做 ─────────────────────────
def test_rename_migrates_entries_and_the_category_map():
    """節點改名／搬家 = 節點本身 + 收支三欄鏡射 + finance_category_map 的鍵。

    🔴 少做第三件，那批帳**從三表消失** —— 對映表帶著每個鍵的科目與 treatment
    （實測 38 個私帳複合鍵都在）。少做第二件，樹上是新名字、帳上還是舊 category。
    """
    fn = _FIN[_FIN.index("async def update_cash_taxonomy_node"):
              _FIN.index("async def delete_cash_taxonomy_node")]
    for frag in ("category_keys", "_rename_category_map", "remirror_subtree"):
        assert frag in fn, frag
    # 🔴 順序：先 flush 才重算路徑，否則 category_keys 拿到的是舊名字
    assert fn.index("session.flush()") < fn.index("after = await category_keys")
    assert fn.index("before = await category_keys") < fn.index("node.name = new_name")


def test_rename_refuses_to_merge_two_category_mappings():
    """新鍵已經有對映 → 擋下來。把兩個科目對映併成一個不是「改名」該做的事。"""
    fn = _FIN[_FIN.index("async def _rename_category_map"):
              _FIN.index("async def update_cash_taxonomy_node")]
    assert "status_code=409" in fn


def test_move_is_same_depth_only():
    """跨層搬會讓深度變 → 複合鍵憑空出現或消失，對映表就對不上了。"""
    fn = _FIN[_FIN.index("async def update_cash_taxonomy_node"):
              _FIN.index("async def delete_cash_taxonomy_node")]
    assert "tgt.depth if tgt else 0" in fn and "old_parent.depth if old_parent else 0" in fn
    assert "不能搬到自己底下" in fn


def test_delete_only_removes_empty_nodes():
    """有資料的節點只能停用 —— 刪掉會讓那些列指向一個爬不回根的孤兒 id。"""
    fn = _FIN[_FIN.index("async def delete_cash_taxonomy_node"):]
    assert "請改用「停用」" in fn
    assert "底下還有" in fn


def test_remirror_requires_flush_first():
    """把「要先 flush」寫進 remirror_subtree 的約定裡（呼叫端只會讀 docstring）。"""
    src = open("core/cash_tree.py", encoding="utf-8").read()
    fn = src[src.index("async def remirror_subtree"):src.index("async def category_keys")]
    assert "flush" in fn


def test_only_first_two_levels_have_category_keys():
    """第三層以後不進 category → 改它們的名字不該動到科目對映。"""
    src = open("core/cash_tree.py", encoding="utf-8").read()
    fn = src[src.index("async def category_keys"):]
    assert "1 <= len(p) <= 2" in fn


# ── 階段 4：Sheet 的完整分類路徑（回填與對帳共用一份規則）─────────────
def test_sheet_path_includes_the_overflow_columns():
    """Sheet 只有三個分類欄，第四、五層溢出到「款別」「專案標籤」。

    🔴 兩支腳本必須用**同一份**定義：對帳那支只看 H/I/J 的話，會把回填補進
    sub_item 的 610 筆款別判成「Sheet 空白、帳上有值」，`--apply` 一跑整批抹掉
    （2026-08-27 實測，差一步就發生）。
    """
    from scripts.reconcile_ledger_sheet import sheet_path

    row = {"book": "家用", "item": "變動支出", "sub": "醫療保健",
           "fund": "乳癌治療", "plabel": "台北馬偕"}
    assert sheet_path(row) == ["家用", "變動支出", "醫療保健", "乳癌治療", "台北馬偕"]
    # 專案標籤是真專案（不是就醫地點）→ 不進分類路徑，那是另一個軸
    assert sheet_path({**row, "plabel": "政大資訊系_2025"}) \
        == ["家用", "變動支出", "醫療保健", "乳癌治療"]
    # 公司▸專案：子項目欄是空的，款別直接當第三層
    assert sheet_path({"book": "公司", "item": "專案", "sub": "",
                       "fund": "已收帳款", "plabel": ""}) == ["公司", "專案", "已收帳款"]


def test_backfill_derives_target_from_the_sheet_not_the_current_node():
    """🔴 目標路徑從 Sheet 推。從「這一列現在掛在哪」往下走的話，第一次跑完它
    已經在深處，第二次再往下找同一個名字就找不到 —— 675 筆會被誤報成「樹上沒有
    那個節點」（實測踩到）。"""
    src = open("scripts/backfill_cash_depth.py", encoding="utf-8").read()
    body = src[src.index("        for s, e in pairs:"):src.index("        drop_label")]
    assert "full = sheet_path(s)" in body
    # 路徑→節點的解析走共用的 node_for_path（從根開始），不是從
    # e['taxonomy_node_id'] 往下爬，也不自己再抄一份 walk
    assert "node_for_path(child, full)" in body


def test_matcher_breaks_ties_by_classification():
    """真雙胞胎（同日同方向同額同摘要）隨便挑一隻＝一半被判成衝突，而且跨次
    不穩定。同分時優先挑分類也對得上的那一隻。"""
    src = open("scripts/reconcile_ledger_sheet.py", encoding="utf-8").read()
    fn = src[src.index("def match_sheet_to_db"):src.index("async def run(")]
    assert "max(avail, key=" in fn
    # 🔴 DB 查詢要有 ORDER BY：沒排序時更新過後實體順序會變，L4 那層就對調
    assert "ORDER BY entry_date, id" in src


def test_reconcile_writes_the_node_alongside_the_three_columns():
    """🔴 改分類的腳本要**同時**寫 taxonomy_node_id。只寫那三欄會留下「有類別、
    但不在樹上」的孤兒 —— 樹狀篩選看不到它，後台的影響筆數也算不到它。
    樹上沒有那條路徑就整列不寫（不自己造節點，同 backfill_cash_depth）。"""
    src = open("scripts/reconcile_ledger_sheet.py", encoding="utf-8").read()
    body = src[src.index("        n, no_node = 0, []"):src.index("        m = 0")]
    assert "taxonomy_node_id=$4" in body
    assert "node_for_path(child, names)" in body
    assert "no_node.append" in body and "continue" in body


def test_owner_sheet_fixes_are_recorded_not_silently_applied():
    """Sheet 我沒有寫入權，owner 的判定記在 SHEET_FIXES（同 SHEET_DATE_FIXES 的
    先例）—— 有據可查、重跑結果一致，他改好 Sheet 之後刪掉也不影響結果。"""
    from scripts.reconcile_ledger_sheet import SHEET_FIXES

    assert SHEET_FIXES[671] == {"fund": ""}, "支出列不可能是「已收帳款」"
    for row in (555, 1730, 3232, 3561):
        assert SHEET_FIXES[row] == {"item": "專案"}
    # 這條方向相反：Sheet 錯、帳上對（owner 2026-08-28 拍板）
    assert SHEET_FIXES[4357] == {"item": "生活", "sub": "交通"}
