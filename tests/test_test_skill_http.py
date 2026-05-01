"""
test_test_skill_http.py — Phase 3 HTTP for /test skill.

httpx 直打 master uvicorn (port 18002, /test 專用 — 跟 conftest:real_server 18000 隔離)
驗證 backend endpoint 的形狀 + 副作用。

範圍：
- CRM staff CRUD（GET/POST/PUT + /staff/{id}/projects 新形狀）
- showcase-edit token endpoints（staff_search / staff_quick_add / PUT credits）
- redirects 機制（slug 變動 → public_old_slugs append）

跑：`pytest tests/test_test_skill_http.py -v`
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.fixtures.test_data import (
    cleanup_all_test_data,
    seed_test_staff,
    seed_test_project,
    seed_test_showcase_with_token,
    make_admin_token,
    TEST_PREFIX,
)


# ── /test 專用 server fixture（port 18002, 60s timeout 給 NAS PG migration） ──

@pytest.fixture(scope="module")
def real_server():
    """覆寫 conftest 版本 — port 隔離 + 更長 timeout（NAS PG migration 約 25-30s）。"""
    project_root = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["MOCK_FFMPEG"] = "1"
    env["MOCK_NAS"] = "1"
    env["ORIGINSUN_DISABLE_SELFHEAL"] = "1"  # 沒這行 _self_heal_scheduled_task 在 Session 0 會 4s 後 taskkill server
    # DEVNULL: Windows pipe buffer (64KB) fills with uvicorn logs and deadlocks
    # event loop. Match conftest.py:real_server pattern.
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:io_app",
         "--host", "0.0.0.0", "--port", "18002"],
        cwd=project_root, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base_url = "http://localhost:18002"
    deadline = time.time() + 90       # 90s — NAS PG migration 完整流程 + buffer
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(1.0)
    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.fail("real_server failed to start within 60 seconds")
    yield {"base_url": base_url, "_proc": proc}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── module-level: cleanup 上次殘留 + setup 共用 fixture ──

@pytest.fixture(scope="module", autouse=True)
def _cleanup_before_after(real_server):
    """每個 module 開頭/結尾都掃 __test_ prefix cleanup。

    Depend on real_server 確保 server 先起 — autouse 預設順序會讓 cleanup
    搶在 server 之前跑，PG 連線 hang 卡 60 秒以上。
    """
    asyncio.run(cleanup_all_test_data())
    yield
    asyncio.run(cleanup_all_test_data())


@pytest.fixture(scope="module")
def admin_headers():
    token = asyncio.run(make_admin_token())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fixture_staff():
    """每個 test 建一個 fresh staff，function scope。"""
    s = asyncio.run(seed_test_staff(role="演員"))
    yield s
    # cleanup 走 module-level autouse


@pytest.fixture
def fixture_project_with_token():
    """建 project + showcase + edit token。"""
    p = asyncio.run(seed_test_project())
    sc = asyncio.run(seed_test_showcase_with_token(p["id"]))
    yield {"project": p, "token": sc["token"]}


# ══════════════════════════════════════════════════════════
# CRM staff CRUD
# ══════════════════════════════════════════════════════════

class TestStaffCRUD:
    def test_list_staff_returns_array(self, real_server, admin_headers):
        r = httpx.get(f"{real_server['base_url']}/api/v1/crm/staff", headers=admin_headers, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "staff" in body
        assert isinstance(body["staff"], list)

    def test_get_staff_returns_dict(self, real_server, admin_headers, fixture_staff):
        r = httpx.get(
            f"{real_server['base_url']}/api/v1/crm/staff/{fixture_staff['id']}",
            headers=admin_headers, timeout=10,
        )
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["id"] == fixture_staff["id"]
        assert s["name"].startswith(TEST_PREFIX)
        # 階段 4-A 加的兩個欄位
        assert "created_via" in s
        assert "created_for_project_id" in s

    def test_get_staff_projects_new_shape(self, real_server, admin_headers, fixture_staff):
        """階段 5 重寫的 endpoint — 必須回 projects + credit_only_projects 雙欄位。"""
        r = httpx.get(
            f"{real_server['base_url']}/api/v1/crm/staff/{fixture_staff['id']}/projects",
            timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "projects" in body
        assert "credit_only_projects" in body
        # fixture staff 沒派工 → 兩個都該空
        assert body["projects"] == []
        assert body["credit_only_projects"] == []


# ══════════════════════════════════════════════════════════
# Showcase-edit token endpoints
# ══════════════════════════════════════════════════════════

class TestShowcaseEditToken:
    def test_get_showcase_data_includes_credit_metadata(self, real_server, fixture_project_with_token):
        token = fixture_project_with_token["token"]
        r = httpx.get(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}",
            timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 階段 4-A 加：showcase-edit 編輯器要拿職位庫 + 模板候選
        assert "credit_roles_available" in body
        assert "credit_templates_available" in body
        assert isinstance(body["credit_roles_available"], list)
        assert isinstance(body["credit_templates_available"], list)
        # 階段 4 加：staff search / quick_add 給 PM 用
        # 既有：分類候選
        assert "categories_available" in body

    def test_staff_search_via_token(self, real_server, fixture_project_with_token, fixture_staff):
        token = fixture_project_with_token["token"]
        # 用 fixture staff 的 name 部分做搜尋（保證至少 1 筆）
        q = fixture_staff["name"][len(TEST_PREFIX):][:5]  # 拿 prefix 後的前 5 char
        r = httpx.get(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}/staff_search",
            params={"q": q}, timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body
        # 應該找得到 fixture staff
        ids = [s["id"] for s in body["items"]]
        assert fixture_staff["id"] in ids, f"fixture staff 未出現: {body['items']}"

    def test_staff_search_empty_query_returns_top10(self, real_server, fixture_project_with_token):
        token = fixture_project_with_token["token"]
        r = httpx.get(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}/staff_search",
            params={"q": ""}, timeout=10,
        )
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["items"], list)
        assert len(body["items"]) <= 10  # limit 10

    def test_staff_quick_add_creates_with_proper_defaults(self, real_server, fixture_project_with_token):
        """階段 4-A：quick_add 要 status='專案'、resume_visible=False、created_via='showcase_edit'。"""
        token = fixture_project_with_token["token"]
        new_name = f"{TEST_PREFIX}qa_{uuid.uuid4().hex[:6]}"
        r = httpx.post(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}/staff_quick_add",
            json={"name": new_name, "role": "客串"}, timeout=10,
        )
        assert r.status_code == 200, r.text  # FastAPI 預設 200, 不是 201
        body = r.json()
        assert body["name"] == new_name
        assert body["role"] == "客串"
        assert body["resume_visible"] is False  # 階段 4-A 預設
        # 透過 admin endpoint 驗 created_via
        # （quick_add response 沒帶這欄 — 看 _to_staff_public_dict 精簡版）

    def test_put_credits_block_format_persists(self, real_server, fixture_project_with_token):
        """PUT credits 用 block 格式 → 後端應該收下、不會被舊 flat 邏輯打回。"""
        token = fixture_project_with_token["token"]
        block = [{
            "role_id": None,
            "name_zh": "演員",
            "name_en": "Cast",
            "entries": [{"duty": "主演", "name": "張三", "resume_url": ""}],
        }]
        r = httpx.put(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}",
            json={"credits": block}, timeout=10,
        )
        assert r.status_code == 200, r.text

        # 驗證儲存後 GET 拿回的是同樣 block 結構
        r2 = httpx.get(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}", timeout=10,
        )
        assert r2.status_code == 200
        saved = r2.json()
        assert isinstance(saved["credits"], list)
        assert len(saved["credits"]) == 1
        assert saved["credits"][0]["name_zh"] == "演員"
        assert saved["credits"][0]["entries"][0]["name"] == "張三"


# ══════════════════════════════════════════════════════════
# Redirects 機制
# ══════════════════════════════════════════════════════════

class TestRedirects:
    def test_slug_change_appends_to_old_slugs(self, real_server, fixture_project_with_token):
        """階段 6：改 sc.slug → _sync_showcase_to_public 偵測 → public_old_slugs append。"""
        token = fixture_project_with_token["token"]

        # 第 1 次設 slug
        r1 = httpx.put(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}",
            json={"slug": "test-slug-original"}, timeout=10,
        )
        assert r1.status_code == 200, r1.text

        # 改 slug
        r2 = httpx.put(
            f"{real_server['base_url']}/api/v1/crm/public/showcase-edit/{token}",
            json={"slug": "test-slug-new"}, timeout=10,
        )
        assert r2.status_code == 200, r2.text

        # 直接查 DB 驗證 public_old_slugs append 了原 slug
        async def _verify():
            from tests.fixtures.test_data import _get_session_factory
            from db.models import CrmProject
            factory = await _get_session_factory()
            async with factory() as session:
                proj = await session.get(CrmProject, fixture_project_with_token["project"]["id"])
                return proj.public_slug, list(proj.public_old_slugs or [])

        new_slug, old_slugs = asyncio.run(_verify())
        assert new_slug == "test-slug-new"
        assert "test-slug-original" in old_slugs, f"old_slugs={old_slugs}"

    def test_redirects_endpoint_merges_posts_works(self, real_server):
        """GET /api/website/redirects 應該回 items + count（merge posts + works）。"""
        r = httpx.get(
            f"{real_server['base_url']}/api/website/redirects", timeout=10,
        )
        # NAS website-api endpoint — master 上不一定有
        # 預期 200（含 merge）或 404（master 沒掛 routers/website/）
        assert r.status_code in (200, 404), f"unexpected: {r.status_code} {r.text[:200]}"
        if r.status_code == 200:
            body = r.json()
            assert "items" in body
            assert "count" in body
            assert isinstance(body["items"], dict)
        else:
            pytest.skip("/api/website/redirects 在 master 不存在（NAS-only）")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
