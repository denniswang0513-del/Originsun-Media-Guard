# -*- coding: utf-8 -*-
"""備份三根掛在 CRM 專案上（owner 2026-09-10）。

三條不變式，壞任何一條都是靜默的資料事故：
  1. 存進 DB 的一律是 canonical UNC —— 存 `T:\\` 進去，沒掛那顆磁碟的機器就開不到
  2. 綁了專案就以 DB 為準 —— 唯讀只做在 UI 上的話，改個 DOM 就繞過去了
  3. 投影端點只給 id/name/三根 —— 備份頁在機隊上是免登入的，不能讓它看到錢
"""
import asyncio
import types

from core.drive_map import to_canonical
from routers.api_backup import _root_view
from routers.crm.projects import BACKUP_ROOT_FIELDS, normalize_backup_roots

NAS = r"\\192.168.1.132\Project_Longterm"


class TestToCanonical:
    def test_drive_letter_becomes_unc(self):
        assert to_canonical(r"T:\某案") == NAS + r"\某案"

    def test_drive_root_becomes_share_root(self):
        assert to_canonical("T:\\") == NAS

    def test_unc_passes_through(self):
        assert to_canonical(NAS) == NAS

    def test_local_disk_is_untouched(self):
        """讀卡槽/本機碟不在 map 內 —— 翻了反而變成不存在的 UNC。"""
        assert to_canonical(r"E:\卡匣") == r"E:\卡匣"

    def test_nas_container_view_becomes_unc(self, monkeypatch):
        """NAS 容器看到的是 /share/…，存進 DB 前要翻成 UNC，master 才讀得到。"""
        monkeypatch.setenv("NAS_LOCAL_SHARE_ROOT", "/share")
        assert to_canonical("/share/Archive/x") == r"\\192.168.1.132\Archive\x"

    def test_empty_stays_empty(self):
        assert to_canonical("") == ""


class TestNormalizeBackupRoots:
    def test_canonicalizes_every_root(self):
        data = {"backup_local_root": r"T:\a", "backup_nas_root": "T:/b",
                "backup_proxy_root": NAS + r"\c"}
        assert normalize_backup_roots(data) == []
        assert data["backup_local_root"] == NAS + r"\a"
        assert data["backup_proxy_root"] == NAS + r"\c"

    def test_trailing_slash_is_stripped_before_translating(self):
        data = {"backup_nas_root": "T:\\"}
        normalize_backup_roots(data)
        assert data["backup_nas_root"] == NAS

    def test_blank_becomes_none_not_empty_string(self):
        """DB 欄位 nullable —— 空字串跟 NULL 混用的話「有沒有設定」會有兩種答案。"""
        data = {"backup_local_root": "   ", "backup_nas_root": ""}
        normalize_backup_roots(data)
        assert data["backup_local_root"] is None
        assert data["backup_nas_root"] is None

    def test_only_touches_fields_that_were_sent(self):
        """逐格 autosave 只送 dirty 欄；沒送的不能被塞成 None（等於清空別人的設定）。"""
        data = {"name": "某案", "backup_nas_root": r"T:\x"}
        normalize_backup_roots(data)
        assert set(data) == {"name", "backup_nas_root"}

    def test_garbage_path_warns_but_does_not_block(self):
        """設定的人可能就在一台看不到那個 share 的機器上編輯 —— 擋下來反而逼人換機器。"""
        data = {"backup_local_root": r"C:\A_TEST_PROXYS:/黏在一起"}
        warnings = normalize_backup_roots(data)
        assert warnings and "backup_local_root" in warnings[0]
        assert data["backup_local_root"], "警告歸警告，值還是要存下去"

    def test_field_list_matches_the_schema(self):
        from core.schemas import CrmProjectPayload
        assert set(BACKUP_ROOT_FIELDS) <= set(CrmProjectPayload.model_fields)


class TestRootViewProjection:
    """🔴 這支端點在機隊上是 check_lan_or_logged_in（免登入），不能露錢。"""

    def _project(self, **kw):
        base = dict(id="p1", name="某案", backup_local_root=r"D:\work",
                    backup_nas_root=NAS, backup_proxy_root="")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_exposes_exactly_five_keys(self):
        assert set(_root_view(self._project())) == {
            "id", "name", "local_root", "nas_root", "proxy_root"}

    def test_money_never_leaks(self):
        p = self._project()
        for money in ("contract_amount", "amount_received", "amount_receivable",
                      "transfer_fee", "client_id", "entity", "notes"):
            setattr(p, money, 999999)
        view = _root_view(p)
        assert not ({"contract_amount", "amount_received", "amount_receivable",
                     "transfer_fee", "client_id", "entity", "notes"} & set(view))

    def test_unset_root_is_empty_string_not_none(self):
        assert _root_view(self._project())["proxy_root"] == ""

    def test_paths_are_translated_to_the_calling_machine(self, monkeypatch):
        """NAS 容器上要看到 /share/…，不是 Windows 的 UNC。"""
        monkeypatch.setenv("NAS_LOCAL_SHARE_ROOT", "/share")
        assert _root_view(self._project())["nas_root"] == "/share/Project_Longterm"


class TestProjectionVisibility:
    def _src(self):
        return open("routers/api_backup.py", encoding="utf-8").read()

    def test_mine_projects_are_listed_and_selectable(self):
        """owner 2026-09-10 拍板：私帳的案子一樣要備份，選不到等於功能對它失效。
        這是對 2026-08-28「連專案都看不到」的**局部例外**，只在這支投影成立 ——
        成立的前提是這支不帶錢（見 test_money_never_leaks）。"""
        src = self._src()
        assert "hide_mine_projects" not in src
        assert 'CrmProject.entity != "mine"' not in src
        assert "owner 2026-09-10 明確拍板" in src, "例外要留得下理由，否則下次會被當 bug 修掉"

    def test_the_guard_is_lan_not_the_crm_module_key(self):
        """備檔電腦沒人登入 —— 用 crm_projects 模組守它等於鎖死備份頁。"""
        src = self._src()
        assert src.count("check_lan_or_logged_in(request)") >= 3, "三支端點都要守"
        assert "check_admin_or_module" not in src


class TestSaveBackupRoots:
    """備份頁回存（owner 2026-09-10）：**只填空、不覆寫**。

    這條不是潔癖 —— 這支端點免登入，能力限縮在「補上空白」才安全。
    """

    def _src(self):
        return open("routers/api_backup.py", encoding="utf-8").read()

    def test_only_fills_blanks_never_overwrites(self):
        seg = self._src()[self._src().index("async def save_backup_roots"):]
        assert 'skipped.append(field)' in seg
        assert "不覆寫" in seg

    def test_it_reuses_the_one_normalizer(self):
        """兩條寫入路（專案頁 PUT 與備份頁回存）不能各算一次 canonical。"""
        seg = self._src()[self._src().index("async def save_backup_roots"):]
        assert "normalize_backup_roots" in seg
        assert "to_canonical" not in seg, "正規化只有一份，不要在這裡再長一份"

    def test_blank_fields_are_not_sent_as_none(self):
        """沒送的欄位不可以變成 None 去把專案上的設定清掉。"""
        seg = self._src()[self._src().index("async def save_backup_roots"):]
        assert "if str(getattr(req, field) or \"\").strip()" in seg

    def test_payload_fields_match_the_projection(self):
        from core.schemas import BackupRootsPayload
        assert set(BackupRootsPayload.model_fields) == {
            "local_root", "nas_root", "proxy_root"}


# ── enqueue 端：綁了專案就以 DB 為準 ────────────────────────────────

class _FakeSession:
    def __init__(self, project):
        self._project = project

    async def get(self, _model, _pid):
        return self._project

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _install_db(monkeypatch, project):
    import core.state as state
    import db.session
    monkeypatch.setattr(state, "db_online", True)
    monkeypatch.setattr(db.session, "get_session_factory",
                        lambda: (lambda: _FakeSession(project)))


def _req(**kw):
    base = dict(project_id="p1", local_root=r"D:\手打", nas_root=r"D:\手打nas",
                proxy_root=r"D:\手打proxy")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _apply(request, task_type="backup"):
    from core.worker import _apply_project_roots
    return asyncio.run(_apply_project_roots(request, task_type))


class TestApplyProjectRoots:
    def test_all_three_are_overwritten(self, monkeypatch):
        _install_db(monkeypatch, types.SimpleNamespace(
            name="某案", backup_local_root=r"E:\L", backup_nas_root=r"E:\N",
            backup_proxy_root=r"E:\P"))
        r = _req()
        note = _apply(r)
        assert (r.local_root, r.nas_root, r.proxy_root) == (r"E:\L", r"E:\N", r"E:\P")
        assert "某案" in note

    def test_a_blank_root_on_the_project_does_not_clear_the_typed_one(self, monkeypatch):
        """半設定的專案（只填了 NAS）不該把使用者手填的本機路徑清成空字串。"""
        _install_db(monkeypatch, types.SimpleNamespace(
            name="某案", backup_local_root="", backup_nas_root=r"E:\N",
            backup_proxy_root=None))
        r = _req()
        _apply(r)
        assert r.local_root == r"D:\手打"
        assert r.nas_root == r"E:\N"
        assert r.proxy_root == r"D:\手打proxy"

    def test_project_with_no_roots_at_all_says_so(self, monkeypatch):
        _install_db(monkeypatch, types.SimpleNamespace(
            name="某案", backup_local_root="", backup_nas_root="",
            backup_proxy_root=""))
        r = _req()
        note = _apply(r)
        assert "尚未設定" in note
        assert r.local_root == r"D:\手打"

    def test_missing_project_falls_back_instead_of_blowing_up(self, monkeypatch):
        _install_db(monkeypatch, None)
        r = _req()
        assert "找不到專案" in _apply(r)
        assert r.local_root == r"D:\手打"

    def test_no_project_id_is_a_noop(self, monkeypatch):
        _install_db(monkeypatch, types.SimpleNamespace(
            name="x", backup_local_root=r"E:\L", backup_nas_root=r"E:\N",
            backup_proxy_root=r"E:\P"))
        r = _req(project_id="")
        assert _apply(r) == ""
        assert r.local_root == r"D:\手打"

    def test_db_offline_does_not_block_the_job(self, monkeypatch):
        """機隊 agent 本來就可能斷線；這裡再擋一次等於備份全停。"""
        import core.state as state
        monkeypatch.setattr(state, "db_online", False)
        r = _req()
        assert _apply(r) == ""
        assert r.local_root == r"D:\手打"

    def test_other_task_types_are_untouched(self, monkeypatch):
        _install_db(monkeypatch, types.SimpleNamespace(
            name="x", backup_local_root=r"E:\L", backup_nas_root="",
            backup_proxy_root=""))
        r = _req()
        assert _apply(r, task_type="transcode") == ""
        assert r.local_root == r"D:\手打"


def test_backup_request_accepts_project_id():
    from core.schemas import BackupRequest
    req = BackupRequest(project_name="x", local_root="a", nas_root="b",
                        proxy_root="c", cards=[("A001", r"E:\card")])
    assert req.project_id == "", "選填，臨時任務不必綁專案"


def test_the_two_folder_concepts_stay_documented():
    """folder_path（工作資料夾完整路徑）跟 backup_*_root（根）是不同東西。
    註解掉了以後一定有人把它們合併。"""
    src = open("db/models/_crm.py", encoding="utf-8").read()
    block = src[src.index("folder_path = Column"):src.index("description = Column")]
    assert "folder_path" in block and "backup_local_root" in block
    assert "canonical UNC" in block


# ── 專案清單＝跟專案工時同一份（owner 2026-09-11）────────────────────

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeQuerySession:
    """`list_options` 會 execute 兩次：先專案列、再每案最後一筆工時的日期。"""

    def __init__(self, projects, last_ts=()):
        self._answers = [projects, last_ts]

    async def execute(self, _stmt):
        return _Rows(self._answers.pop(0))


def _p(pid, name, *, status="製作", entity="parent", mine_link_id=None,
       source_project_id=None, client="客", start=None, updated=None, extra=()):
    from datetime import date
    return (pid, name, client, start or date(2025, 3, 1), None, date(2025, 1, 1),
            status, entity, mine_link_id, source_project_id, updated, *extra)


def _options(projects, last_ts=(), **kw):
    from services.project_picker import list_options
    return asyncio.run(list_options(_FakeQuerySession(list(projects), list(last_ts)), **kw))


class TestSharedProjectList:
    """owner 2026-09-11：「綁定專案的列彆方式，和專案工時的專案列表相同」
    ＋「不篩 連專案工時也不篩」。規則一份，住在 services/project_picker。"""

    def test_closed_projects_are_listed_and_flagged(self):
        """🔴 不篩狀態：備份最常發生在結案之後（歸檔、補檔、客戶回頭要素材）。
        分組交給 closed 旗標，不是把案子從清單裡拿掉。"""
        opts = _options([_p("a", "進行中案"), _p("b", "結案案", status="結案"),
                         _p("c", "歸檔案", status="歸檔"), _p("d", "未成案", status="未成案")])
        assert {o["name"] for o in opts} == {"進行中案", "結案案", "歸檔案", "未成案"}
        assert {o["name"]: o["closed"] for o in opts} == {
            "進行中案": False, "結案案": True, "歸檔案": True, "未成案": True}

    def test_proposal_stage_counts_as_active(self):
        assert _options([_p("a", "提案中", status="提案")])[0]["closed"] is False

    def test_linked_ledger_pair_is_listed_once_keeping_the_mine_row(self):
        """母私帳連結的兩筆是同一個案（工時對映只認私帳）。"""
        opts = _options([_p("m", "某案", entity="parent", mine_link_id="s"),
                         _p("s", "某案", entity="mine", source_project_id="m")])
        assert [o["id"] for o in opts] == ["s"]

    def test_the_row_that_actually_has_the_backup_roots_wins(self):
        """🔴 三根多半設在母帳那筆（私帳案對沒有 finance_mine 的人整個看不見，
        設定的人打不開）。一律留私帳的話備份頁只選得到那個空殼分身：畫面說
        「還沒設定」、`_apply_project_roots` 用那個 id 反查也讀不到，於是靜默
        退回手動填的路徑 —— 綁定對這個案等於失效，而且沒有任何徵兆。"""
        cols = ("backup_local_root", "backup_nas_root", "backup_proxy_root")
        pair = [_p("m", "某案", entity="parent", mine_link_id="s",
                   extra=(NAS + r"\x", "", "")),
                _p("s", "某案", entity="mine", source_project_id="m",
                   extra=("", None, ""))]
        assert [o["id"] for o in _options(pair, extra=cols)] == ["m"]
        # 沒點名 extra（＝工時那條路）規則原封不動：留私帳那筆
        assert [o["id"] for o in _options(pair)] == ["s"]

    def test_the_mine_row_still_wins_when_it_has_roots_of_its_own(self):
        cols = ("backup_local_root", "backup_nas_root", "backup_proxy_root")
        pair = [_p("m", "某案", entity="parent", mine_link_id="s",
                   extra=(NAS + r"\x", "", "")),
                _p("s", "某案", entity="mine", source_project_id="m",
                   extra=(NAS + r"\y", "", ""))]
        assert [o["id"] for o in _options(pair, extra=cols)] == ["s"]

    def test_neither_side_has_roots_keeps_the_mine_row(self):
        cols = ("backup_local_root", "backup_nas_root", "backup_proxy_root")
        pair = [_p("m", "某案", entity="parent", mine_link_id="s", extra=("", "", "")),
                _p("s", "某案", entity="mine", source_project_id="m", extra=("", "", ""))]
        assert [o["id"] for o in _options(pair, extra=cols)] == ["s"]

    def test_recently_touched_comes_first(self):
        from datetime import date
        opts = _options([_p("old", "很久沒動", updated=date(2024, 1, 1)),
                         _p("new", "最近有動", updated=date(2026, 9, 1))])
        assert [o["name"] for o in opts] == ["最近有動", "很久沒動"]

    def test_a_recent_timesheet_row_counts_as_touched(self):
        """專案本身沒動、但有人上週填了工時 —— 那也是「最近有動」。"""
        from datetime import date
        opts = _options([_p("a", "有工時", updated=date(2024, 1, 1)),
                         _p("b", "都沒動", updated=date(2024, 6, 1))],
                        last_ts=[("a", date(2026, 9, 1))])
        assert [o["name"] for o in opts] == ["有工時", "都沒動"]

    def test_label_is_year_client_name(self):
        assert _options([_p("a", "某案", client="源日")])[0]["label"] == "2025 源日 某案"

    def test_extra_columns_come_through_untouched(self):
        """備份頁要三根；`extra` 是呼叫端點名的白名單，值不加工。"""
        opts = _options([_p("a", "某案", extra=(NAS + r"\x", None, ""))],
                        extra=("backup_local_root", "backup_nas_root", "backup_proxy_root"))
        assert opts[0]["backup_local_root"] == NAS + r"\x"
        assert opts[0]["backup_nas_root"] is None

    def test_timesheets_and_backup_share_the_one_rule(self):
        """兩邊各寫一份的話，改了一邊另一邊就靜默地不一樣了。"""
        ts = open("services/timesheet_manual.py", encoding="utf-8").read()
        assert "from services.project_picker import list_options" in ts
        assert "CrmProject.status.in_" not in ts, "工時那份也不篩狀態（owner 原話）"
        bk = open("routers/api_backup.py", encoding="utf-8").read()
        assert "from services.project_picker import list_options" in bk
        assert "CrmProject.name.ilike" not in bk, "清單規則不要在這裡長第二份"

    def test_no_status_filter_anywhere_in_the_rule(self):
        src = open("services/project_picker.py", encoding="utf-8").read()
        assert "status.in_" not in src and "!= \"結案\"" not in src
        assert "不看狀態" in src, "拿掉篩選的理由要留著，否則下次會被當 bug 加回去"


class TestPickerProjection:
    """清單那支的白名單（比單筆多了浮層分組要的四格，仍然不帶錢）。"""

    def _opt(self, **kw):
        base = dict(id="p1", name="某案", client="源日", year="2025", closed=True,
                    label="2025 源日 某案", backup_local_root=r"D:\work",
                    backup_nas_root=NAS, backup_proxy_root=None)
        base.update(kw)
        return base

    def test_exposes_exactly_the_nine_display_keys(self):
        from routers.api_backup import _picker_view
        assert set(_picker_view(self._opt())) == {
            "id", "name", "client", "year", "closed", "label",
            "local_root", "nas_root", "proxy_root"}

    def test_money_never_leaks(self):
        """這支端點免登入（備檔電腦沒人登入）—— client_id／entity／金額一律不上。"""
        from routers.api_backup import _picker_view
        never = {"contract_amount", "amount_received", "amount_receivable",
                 "transfer_fee", "client_id", "entity", "notes"}
        view = _picker_view(self._opt(**{k: 999999 for k in never}))
        assert not (never & set(view))

    def test_paths_are_translated_to_the_calling_machine(self, monkeypatch):
        from routers.api_backup import _picker_view
        monkeypatch.setenv("NAS_LOCAL_SHARE_ROOT", "/share")
        v = _picker_view(self._opt())
        assert v["nas_root"] == "/share/Project_Longterm"
        assert v["proxy_root"] == "", "沒設定的根是空字串，不是 None"


def test_backup_page_uses_the_shared_typing_popup():
    """跟專案工時同一支元件（js/shared/project-pop.js）：分兩段、選了記 data-pid。"""
    html = open("frontend/tabs/backup/backup.html", encoding="utf-8").read()
    js = open("frontend/tabs/backup/backup.js", encoding="utf-8").read()
    assert 'id="bk_project_pick"' in html and "data-proj-pick" in html
    assert "<select id=\"bk_project_sel\"" not in html, "原生 select 分不了組"
    assert "attachProjectPop(" in js
    assert "dataset.pid" in js, "綁的是 id，不是輸入框裡那串字"


class TestListEndpointFiltering:
    """`/backup-roots` 的 `q`／`limit`（特徵測試：把現在的行為釘住）。

    這兩個參數 2026-09-11 起改成在清單**之後**做（規則本身在 project_picker）——
    留著是為了 Cloudflare 那一輪「新 html ＋ 舊 js」，舊分頁會送 `limit=200`。
    """

    def _call(self, monkeypatch, opts, *, boom=False, offline=False, **kw):
        import routers.api_backup as ab
        import services.project_picker as picker

        monkeypatch.setattr(ab, "check_lan_or_logged_in", lambda _r: None)

        async def _fake_options(session, extra=()):
            if boom:
                raise RuntimeError("DB 斷了")
            return [dict(o) for o in opts]

        monkeypatch.setattr(picker, "list_options", _fake_options)

        async def _factory():
            return None if offline else (lambda: _FakeSession(None))

        monkeypatch.setattr(ab, "_factory", _factory)
        return asyncio.run(ab.list_backup_roots(None, **kw))

    def _opts(self, n):
        return [{"id": f"p{i}", "name": f"案{i}", "client": "源日", "year": "2026",
                 "closed": False, "label": f"2026 源日 案{i}",
                 "backup_local_root": "", "backup_nas_root": "", "backup_proxy_root": ""}
                for i in range(n)]

    def test_no_limit_means_the_whole_list(self, monkeypatch):
        """浮層自己在前端篩，所以清單要是完整的（截斷＝有些案永遠選不到）。"""
        out = self._call(monkeypatch, self._opts(300))
        assert out["status"] == "ok" and len(out["projects"]) == 300

    def test_old_cached_page_asking_for_200_still_works(self, monkeypatch):
        out = self._call(monkeypatch, self._opts(300), limit=200)
        assert len(out["projects"]) == 200
        assert set(out["projects"][0]) >= {"id", "name", "local_root"}, "舊 js 認得的欄位還在"

    def test_limit_is_clamped_to_the_safety_valve(self, monkeypatch):
        from routers.api_backup import _PROJECTION_LIMIT
        assert _PROJECTION_LIMIT >= 646, "生產 2026-09-11 有 646 案，別再退回會截斷的數字"
        out = self._call(monkeypatch, self._opts(_PROJECTION_LIMIT + 100), limit=999999)
        assert len(out["projects"]) == _PROJECTION_LIMIT

    def test_q_matches_name_and_label_case_insensitively(self, monkeypatch):
        opts = self._opts(2)
        opts[0]["name"] = "ZZ 測試"
        opts[0]["label"] = "2026 源日 ZZ 測試"
        assert [p["name"] for p in self._call(monkeypatch, opts, q="zz")["projects"]] == ["ZZ 測試"]
        assert [p["name"] for p in self._call(monkeypatch, opts, q="源日")["projects"]] == ["ZZ 測試", "案1"]

    def test_q_with_no_hit_is_an_empty_list_not_an_error(self, monkeypatch):
        out = self._call(monkeypatch, self._opts(2), q="不存在的案")
        assert out["status"] == "ok" and out["projects"] == []

    def test_db_offline_returns_200_and_never_blocks_dispatch(self, monkeypatch):
        out = self._call(monkeypatch, [], offline=True)
        assert out == {"status": "db_offline", "projects": []}

    def test_a_query_blowing_up_degrades_instead_of_500(self, monkeypatch):
        """讀不到專案清單不是派工的阻礙 —— 前端據此退回手動輸入三根。"""
        out = self._call(monkeypatch, [], boom=True)
        assert out["status"] == "db_offline" and out["projects"] == []
        assert "DB 斷了" in out["detail"]


class TestProjectOptionsRecentNames:
    """工時補登下拉＝共用清單 ＋ 該員最近填過的案名（特徵測試）。"""

    def _call(self, projects, recent, staff_name=None):
        from services.timesheet_manual import project_options
        sess = _FakeQuerySession(list(projects), [])
        sess._answers.append(list(recent))          # 第三次 execute＝最近填過的案名
        return asyncio.run(project_options(sess, staff_name))

    def test_recent_names_are_appended_without_an_id(self):
        """Sheet 打進來、專案表裡沒有的案名也要選得到（id=None，存的時候走名稱對映）。"""
        from datetime import date
        out = self._call([_p("a", "有建檔的案")], [("只在工時裡的案", date(2026, 9, 1))], staff_name="王")
        assert [(o["name"], o["id"], o["closed"]) for o in out] == [
            ("有建檔的案", "a", False), ("只在工時裡的案", None, False)]

    def test_a_name_already_in_the_list_is_not_duplicated(self):
        from datetime import date
        out = self._call([_p("a", "同一個案")], [("同一個案", date(2026, 9, 1))], staff_name="王")
        assert [o["id"] for o in out] == ["a"]

    def test_without_a_staff_name_it_is_just_the_shared_list(self):
        out = self._call([_p("a", "某案")], [])
        assert [o["id"] for o in out] == ["a"]


def test_saving_a_root_back_merges_into_the_cached_row():
    """🔴 回存端點回的是單筆投影（id／name／三根）—— 直接覆蓋會把浮層分組要的
    closed／label 弄不見，那個案就會跑錯組、副標也不見了，而且完全沒有徵兆。"""
    js = open("frontend/tabs/backup/backup.js", encoding="utf-8").read()
    assert "_bkProjects[i] = { ..._bkProjects[i], ...data.project }" in js
