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
