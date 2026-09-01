# -*- coding: utf-8 -*-
"""連結私帳（owner 2026-08-29「費用要給王士源的，直接在私帳建立專案、同步收入」）。

公司把後期發給我做：公司那邊記成本、我這邊記收入。兩本帳各記各的 ——
不是重複計帳，也不是換帳本（那是隔壁的「推送至私帳」）。

生產庫實查（2026-08-29）：16 案 / 68 行 / 1,149,286 的成本行掛給我，
其中在私帳有分身的＝0。這顆按鈕要補的就是這 115 萬。
"""
import re
from types import SimpleNamespace as NS

from core.ledger_project import (MIRROR_SOURCE, mirror_amount, mirror_detail,
                                 mirror_lines)
from tests.unit._srcscan import (code_only, func_body, js_code_only,
                                 js_func_body, repo_src)

ME = "me-staff-id"
OTHER = "someone-else"


def _line(item, actual=None, est=None, who=ME, est_who=None, phase="後期製作"):
    return NS(item_name=item, phase=phase, actual_amount=actual,
              estimated_amount=est, actual_staff_id=who, estimated_staff_id=est_who)


def test_only_my_lines_count():
    """認人只認**實際**派給誰 —— 預估掛我、實際換人做的不是我的收入。"""
    got = mirror_lines([_line("剪輯", 20000),
                        _line("導演", 30000, who=OTHER),
                        _line("調光", 8000, who=None, est_who=ME)], ME)
    assert got["total"] == 28000
    assert got["split"] == {"剪輯": 20000, "調光": 8000}


def test_actual_wins_over_estimate():
    """實際優先、沒填才用預估（跟 CRM 其他地方同口徑）。"""
    assert mirror_amount(_line("剪輯", 18000, 20000)) == 18000
    assert mirror_amount(_line("剪輯", None, 20000)) == 20000
    assert mirror_amount(_line("剪輯")) == 0


def test_unfilled_lines_are_dropped():
    """🔴 兩欄都空＝這工項還沒發生，不能記成收入 ——
    把估算當收入記進去，私帳的營收就會領先現實（生產庫的
    「2026 泛亞工程空拍專案」正是這種，合計 None）。"""
    got = mirror_lines([_line("專案轉檔11月"), _line("剪輯", 0)], ME)
    assert got == {"lines": [], "split": {}, "total": 0}


def test_same_item_twice_is_summed():
    """🔴 同名工項要相加 —— 生產庫的 OMRON 那案就有兩行導演、兩行腳本。
    dict 直接指派會讓後面那行吃掉前面那行（少算一筆收入）。"""
    got = mirror_lines([_line("導演", 10000, phase="前期製作"),
                        _line("導演", 20000, phase="前期製作")], ME)
    assert got["split"] == {"導演": 30000}
    assert got["total"] == 30000 and len(got["lines"]) == 2


def test_mirror_detail_is_income_only():
    """鏡射案只帶收入分項與案源，成本欄全 0 —— 委外／代開費用是**我自己的**
    成本，公司管不著，建立時不猜。"""
    d = mirror_detail({"剪輯": 20000})
    assert d["split"] == {"剪輯": 20000}
    assert d["source"] == MIRROR_SOURCE == "源日"
    assert d["outsource"] == 0 and d["invoice_fee"] == 0 and d["misc"] == 0


def test_source_is_cash_receipt():
    """源日＝現金收款：不抽代辦費、不算源頭代扣。內部轉單本來就沒有那些
    （手動建的 109 個歷史案也都是這個值）。"""
    from core.ledger_project import apply_source_fee, client_wire
    d = mirror_detail({"剪輯": 46000})
    assert apply_source_fee(46000, d) == d          # 不動任何費用欄
    assert client_wire(46000, d) == 46000           # 公司匯給我的＝全額


# ── 端點守衛（讀原始碼斷言）──
# 🔴 一律經過 `code_only`：不剝註解的話，「這個函式**不可以**碰 p.contract_amount」
# 這種說明文字自己就會讓 not-in 斷言變綠（_srcscan 檔頭記的那個坑）。
def _src():
    return repo_src("routers/crm/projects.py")


def _fn(src: str, name: str) -> str:
    return code_only(func_body(src, f"async def {name}("))


def test_both_endpoints_are_gated_on_the_private_ledger():
    """只有私帳 full scope 的帳號能按（跟搬帳本同一道門）。"""
    src = _src()
    for name in ("check_project_mirror", "mirror_project_to_mine"):
        fn = _fn(src, name)
        assert 'require_entity(request, "mine", level="full")' in fn, name


def test_who_am_i_comes_from_the_single_resolver():
    """🔴 「我」＝帳號綁的 crm_staff，從 `core.identity` 現查（不寫死人名、
    也不信 JWT 裡的舊值 —— admin 重綁後要立刻生效）。"""
    src = code_only(_src())
    assert "resolve_current_staff" in src
    assert "999be75c" not in src, "不可以把人的 staff id 寫死在程式裡"


def test_linking_twice_is_blocked():
    """雙擊／兩個分頁／重送 —— 一案只能有一個分身，否則收入記兩次。

    ⚠ 擋法在 2026-09-01 換了：從前是「已連結就整個擋掉」，那讓 CRM 後來改的
    費用再也同步不過去。現在改由 `target_id` 解成已連結那一案來守（見
    `test_resyncing_a_linked_case_never_creates_a_second_mirror`），這裡守的是
    「查得到既有分身」與「該擋的時候真的回 409」。
    """
    src = _src()
    assert "source_project_id == project_id" in src
    fn = _fn(src, "mirror_project_to_mine")
    assert "_mirror_blocked_reason" in fn and "409" in fn


def test_linking_existing_keeps_my_own_costs():
    """🔴 連結既有案只覆蓋收入那半邊（split + contract_amount）。
    整包寫回去會把他在私帳填的委外／代開費用洗成 0 ——
    那是 v2.0 就咬過的「整包 model_dump 寫回」同一顆雷。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    seg = fn.split("if target_id:")[1]
    assert "keep = norm_detail(t.ledger_detail)" in seg
    # 只換掉收入那半邊（split/source）；keep 這個 dict 的其他鍵原封帶著走
    assert 'keep["split"], keep["source"] = merged, MIRROR_SOURCE' in seg
    assert "t.ledger_detail = keep" in seg


def test_mine_cache_is_invalidated():
    """新建的是私帳案 —— is_mine_project 的 60 秒 id-set 快取要清，
    否則這一分鐘內它還被當成母公司的（同 move-ledger 的理由）。"""
    assert "invalidate_mine_projects()" in _fn(_src(), "mirror_project_to_mine")


def test_the_parent_project_is_not_touched():
    """母公司那一列的**錢與歸屬**一個欄位都不動 —— 它跟客戶的合約與成本
    都還在原地。唯一准寫的是連結欄 `mine_link_id`（2026-09-01 owner
    「可以多筆專案連結到一筆私帳」後移到來源側的，見下方多對一那兩條）。
    只要有人往 `p.` 寫別的東西，這條就會亮。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    body = fn.split("async with factory() as session:")[1]
    for bad in ("p.entity =", "p.contract_amount =", "p.crm_pushed =",
                "p.ledger_detail =", "p.amount_received =", "p.client_id ="):
        assert bad not in body, bad
    # 寫進來源列的就只有連結欄（+ 它自己的時間戳）
    written = {m for m in re.findall(r"p\.(\w+) =", body)}
    assert written <= {"mine_link_id", "updated_at"}, written


def test_new_mirror_row_goes_through_the_single_create_path():
    """建立走 `new_ledger_project`（帳本新增專案的單一正本）——
    自己 insert 一份 CrmProject 就會漏掉 amount_receivable 初始化，
    那案永遠不進應收帳款（owner 2026-08-26 實測過的坑）。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    assert "new_ledger_project(" in fn
    # 連結既有那條路換掉了營收 → 應收要一起重算（同一支 resync_receivable）
    assert "resync_receivable(t, keep)" in fn
    # `new_ledger_project` 自己有沒有初始化應收，由 test_project_entity_wall
    # 的 test_new_and_edited_cases_land_in_receivable 擁有 —— 這裡再抄一份，
    # 就是兩個檔案要一起改


def test_button_only_shows_for_parent_projects():
    """已經搬到私帳的案子沒有「公司付給我」這回事 —— 按鈕不畫。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-detail.js"))
    seg = js.split("actions.innerHTML")[1].split("crm-detail-close")[0]
    assert "_mine && _toMine" in seg and "proj-mirror-mine" in seg


def test_the_mirrored_badge_is_hidden_from_people_without_the_private_ledger():
    """🔴 「已連結私帳」標籤等於在說「owner 的私帳有這一案」—— 對沒有
    finance_mine 的人不能出現（owner 2026-08-30：「沒有權限看到私帳的帳號
    不能看到私帳才有的內容」）。分身本身是私帳的列，hide_mine_projects
    那條線也該罩到它。"""
    fn = _fn(_src(), "list_projects")
    assert "mirrored_ids = set()" in fn
    assert "show_mine = not _hide_mine(request)" in fn
    # 一次撈成集合，不逐列查（409 案的清單）
    assert "CrmProject.source_project_id.isnot(None)" in fn
    # 🔴 新的連結記在來源側（mine_link_id），那半邊**也要**被同一條線罩住 ——
    # 只把 mirrored_ids 清空是不夠的，`is_mirrored` 光看 mine_link_id 就會回 True。
    assert "show_mine and is_mirrored(p, mirrored_ids)" in fn
    row = code_only(repo_src("routers/crm/projects.py")).split(
        "def _to_project_dict(")[1].split("\ndef ")[0]
    assert '"mirrored": bool(mirrored),' in row


def test_the_badge_also_shows_for_the_second_project_sharing_one_mine_case():
    """🔴 owner 2026-09-01 回報：「南山人壽謝經理」用「加進去」連上去之後
    沒有「已連結私帳」標籤，同一個私帳案的「王經理」卻有。

    原因是判定只查了**私帳案回指的** `source_project_id` —— 那一欄只裝得下
    第一個來源，第二個之後連上去的案只有自己身上的 `mine_link_id`。判定要
    兩種形狀都認，而且**只有一份**（`is_mirrored`），不要在清單與詳情各寫一遍。
    """
    from routers.crm.projects import is_mirrored
    first = NS(id="p1", mine_link_id=None)      # 舊資料：只有私帳那側指回來
    second = NS(id="p2", mine_link_id="mine1")  # 新連結：記在來源這一側
    plain = NS(id="p3", mine_link_id=None)
    assert is_mirrored(first, {"p1"})
    assert is_mirrored(second)                  # 不必靠 legacy 集合
    assert is_mirrored(second, {"p1"})
    assert not is_mirrored(plain, {"p1"})
    # 詳情那條路要的是**連到哪一案**（id），不是「有沒有連」——
    # 兩種形狀都認得的規則在那裡是「先看來源側，查不到才退回舊查法」。
    body = code_only(func_body(_src(), "async def _mirror_preview("))
    assert "if p.mine_link_id:" in body
    assert "CrmProject.source_project_id == project_id" in body


def test_the_mirror_does_not_show_up_next_to_its_parent():
    """🔴 連結之後同一個案名會在專案管理出現兩列（母公司那列標「已連結私帳」、
    分身那列標「私帳」）—— 看起來像重複建案。owner 2026-08-30：「已經連結的
    就不用重複出現了，只出現 crm 的就好」。

    主體是母公司那一列（案子、客戶、派工都在它身上）；分身只是收入的鏡射，
    它該出現的地方是財務管理 › 執行專案（那支是另一個端點）。"""
    fn = _fn(_src(), "list_projects")
    assert "CrmProject.source_project_id.is_(None)" in fn


# ── 連結到「已經填過工項」的私帳案：三個處理方式（owner 2026-08-30）──

def test_three_modes_are_offered_and_validated():
    """「跳出幾個選擇讓我決定要怎麼做」—— 四種（add 是多對一那次加的），
    而且後端要驗值域
    （前端傳錯字不能靜靜當成 overwrite 把人家的工項洗掉）。"""
    from routers.crm.projects import MIRROR_MODES
    assert MIRROR_MODES == ("overwrite", "add", "keep", "import")
    fn = _fn(_src(), "mirror_project_to_mine")
    assert "mode not in MIRROR_MODES" in fn and "422" in fn


def test_keep_mode_touches_no_money():
    """「保留私帳」＝只建立連結。金額一毛不動 —— 那份數字可能是他照實際
    請款填的，比 CRM 的成本行準。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    seg = fn.split('elif mode in ("overwrite", "add"):')[1].split("p.mine_link_id")[0]
    # 只有 overwrite/add 那條動錢；keep 落在所有分支之外，只走到連結那兩行
    assert "t.contract_amount = " in seg and "resync_receivable" in seg
    assert "t.source_project_id = project_id" in fn


def test_import_mode_writes_into_crm_and_leaves_the_private_ledger_alone():
    """「從私帳匯入」＝反過來，私帳是正本。寫的是母公司的成本行，
    私帳那一列除了連結之外一個數字都不碰。"""
    fn = _fn(_src(), "mirror_project_to_mine")
    seg = fn.split('if mode == "import":')[1].split("elif")[0]
    assert "_import_split_to_cost_lines(" in seg
    for bad in ("t.contract_amount", "t.ledger_detail", "resync_receivable"):
        assert bad not in seg, bad


def test_import_only_touches_lines_assigned_to_me():
    """🔴 只碰 `actual_staff_id` 是我的那幾行。掛給別人的同名行一律不動 ——
    「導演」那種工項本來就可能同時有兩個人，覆蓋掉就是改到別人的錢。"""
    body = code_only(func_body(repo_src("routers/crm/projects.py"),
                               "async def _import_split_to_cost_lines("))
    assert '(r.actual_staff_id or "") == staff_id' in body
    assert "actual_staff_id=staff_id" in body, "新增的行也要掛給我"
    assert 'phase="後期製作"' in body


def test_the_conflict_dialog_shows_both_sides_before_asking():
    """🔴 只給三顆按鈕不給數字，等於要他憑印象賭一把 —— 視窗要把兩邊的工項
    並排列出來。私帳那份可能是照實際請款填的，也可能是舊估算，沒有哪一邊
    先天是對的。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-core.js"))
    fn = js_func_body(js, "function _pmmDrawConflict(")
    assert "私帳現有" in fn and "CRM 成本行" in fn
    for mode in ("overwrite", "keep", "import"):
        assert f"btn('{mode}'" in fn, mode
    assert "opt.split" in fn, "對照要用那一案自己的工項"


def test_many_crm_projects_can_share_one_mine_project():
    """owner 2026-09-01「可以多筆專案連結到一筆私帳」。

    🔴 連結必須記在**來源那一側**（crm_projects.mine_link_id）。原本記在私帳案
    上（source_project_id 指回來源），一個欄位只裝得下一個來源 —— 第二個 CRM 案
    連上去就被 409「已經是別案的分身了」擋掉，而候選清單也把它濾掉了。
    """
    src = repo_src("routers/crm/projects.py")
    body = code_only(func_body(src, "async def mirror_project_to_mine("))
    assert "p.mine_link_id = t.id" in body, "連結沒記在來源側 —— 多對一表達不出來"
    assert "已經是別案的分身了" not in body, "還擋著第二個來源"
    # 私帳案那側只記第一個來源（清單的「這是分身」判定沿用它）
    assert "if not t.source_project_id:" in body
    # 候選清單不再排除已被連結的
    opts = code_only(func_body(src, "async def check_project_mirror("))
    assert "source_project_id.is_(None)" not in opts, "候選還在排除已連結的私帳案"
    assert "linked_count" in opts, "沒告訴前端這案已經承接幾筆"


def test_sharing_one_mine_project_adds_instead_of_overwriting():
    """多筆共用一個私帳案時，錢要**相加**不是覆蓋 —— 覆蓋會把前一個 CRM 案
    鏡射進來的收入洗掉，而那筆也是他該拿的。"""
    from routers.crm.projects import MIRROR_MODES
    assert "add" in MIRROR_MODES
    body = code_only(func_body(repo_src("routers/crm/projects.py"),
                               "async def mirror_project_to_mine("))
    assert 'mode in ("overwrite", "add")' in body
    assert "int(t.contract_amount or 0) + mir[\"total\"]" in body, "add 沒有累加合約金額"
    assert "merged[k] = int(merged.get(k, 0)) + int(v or 0)" in body, "同名工項沒有相加"
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    assert "'add', '加進去'" in js, "UI 沒有給「加進去」這個選項"


def test_already_linked_is_not_a_reason_to_block():
    """🔴 owner 2026-09-01「我 crm 有更新費用，但是私帳沒有連結過去」。

    連結是**連結當下的一次性複製** —— CRM 後來新增的成本行（實例：蟾蜍山｜
    煥民新村後補的「翻譯(士源代)」3,000）不會自己流過去。而「已經連結過」
    原本是擋人的第一條理由，於是連**手動**再同步一次的路都沒有：按鈕只會
    toast 一句「已經連結到 X 了」，私帳永遠停在舊數字。
    """
    from routers.crm.projects import _mirror_blocked_reason
    assert _mirror_blocked_reason(60000) == ""       # 已連結不再是理由
    assert "成本行沒有一筆掛給你" in _mirror_blocked_reason(0)
    # 呼叫端也不再餵 linked 進去（餵了會 TypeError，等於漏改一處就紅）
    src = repo_src("routers/crm/projects.py")
    assert "_mirror_blocked_reason(mir[\"total\"], linked)" not in src


def test_resyncing_a_linked_case_never_creates_a_second_mirror():
    """🔴 已連結的案子重新同步時，空的 target 必須解成**原本那一案**。

    落到建立分支就是在私帳多開第二個分身：同一筆錢算兩次，而且兩案同名同
    客戶，畫面上分不出來。前端預設不送 target（它只在「連結既有」時才送）。
    """
    body = code_only(func_body(repo_src("routers/crm/projects.py"),
                               "async def mirror_project_to_mine("))
    assert "target_id = target_id or linked.id" in body
    assert "if target_id != linked.id:" in body, "改連別案要擋下來，不能靜靜改指"


def test_the_relink_dialog_hides_the_create_option_and_defaults_to_overwrite():
    """重新同步的視窗不給「建立新專案」（那會多一個分身），而且「加進去」
    要退到後面 —— 同一案重新同步時加會讓同一筆錢算兩次。"""
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    fn = js_code_only(js_func_body(js, "window._projMirrorMine = async function (id) {"))
    assert "const relink = !!chk.linked;" in fn
    assert "relink ?" in fn and 'id="pmm-target"' in fn
    draw = js_code_only(js_func_body(js, "function _pmmDrawConflict("))
    assert "chk.linked ? '' : btn('add'" in draw, "重新同步還把「加進去」排在最前面"
    assert "'再加一次'" in draw
