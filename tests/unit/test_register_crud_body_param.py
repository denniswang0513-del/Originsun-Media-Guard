"""Regression: register_crud closure-bound schema must resolve as Body, not query.

Bug: ``async def _create(req: create_schema, ...)`` combined with
``from __future__ import annotations`` (top of _common.py) stringified the
annotation. FastAPI then evaluated ``'create_schema'`` against module globals
where it isn't visible — it's a closure variable of ``register_crud()``. The
fallback path treats unresolvable annotations as primitive query params, so
PUT/POST requests carrying a JSON body got rejected with::

    {"type":"missing","loc":["query","req"],"msg":"Field required"}

The fix: drop the type annotation, set ``__annotations__["req"]`` post-hoc
with the actual class object (skips string evaluation entirely), and use
``Body(...)`` as the default to lock body-source semantics.

This test calls register_crud with real closures + sends a real JSON PUT,
asserting 200 OK and a body roundtrip. Reverting _common.py to the buggy
``req: create_schema`` form makes this fail with 422 query missing.
"""
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from routers.website._common import register_crud
import routers.website._common as _common


class _SamplePostUpdate(BaseModel):
    title: str = ""
    body: list = []


class _SamplePostCreate(BaseModel):
    title: str
    slug: str = ""


def _build_app(monkeypatch):
    """Build a minimal FastAPI app with register_crud, bypassing auth/db."""
    async def _noop_session():
        yield None

    monkeypatch.setattr(_common, "admin_session", _noop_session)

    state = {"calls": []}

    async def fake_list(session):
        return [{"id": 1, "title": "stub"}]

    async def fake_create(session, data):
        state["calls"].append(("create", data))
        return {"id": 99, **data}

    async def fake_update(session, item_id, data):
        state["calls"].append(("update", item_id, data))
        return {"id": item_id, **data}

    async def fake_delete(session, item_id):
        state["calls"].append(("delete", item_id))
        return True

    app = FastAPI()
    router = APIRouter(prefix="/api/website/admin")
    register_crud(
        router, prefix="posts", name="Post",
        list_fn=fake_list, create_fn=fake_create,
        update_fn=fake_update, delete_fn=fake_delete,
        create_schema=_SamplePostCreate, update_schema=_SamplePostUpdate,
    )
    app.include_router(router)
    return app, state


def test_put_accepts_json_body_not_query(monkeypatch):
    """PUT /posts/{id} with JSON body must be accepted as Body, not Query.

    Pre-fix: req's annotation resolves to string 'update_schema' which
    FastAPI can't find → falls back to query param → 422 missing.
    """
    app, state = _build_app(monkeypatch)
    client = TestClient(app)
    resp = client.put("/api/website/admin/posts/11", json={"title": "edited"})
    assert resp.status_code == 200, (
        f"PUT must accept JSON body. Got {resp.status_code}: {resp.json()}"
    )
    assert resp.json() == {"id": 11, "title": "edited"}
    assert state["calls"] == [("update", 11, {"title": "edited"})]


def test_post_accepts_json_body_not_query(monkeypatch):
    """POST /posts with JSON body — same regression as PUT."""
    app, _ = _build_app(monkeypatch)
    client = TestClient(app)
    resp = client.post("/api/website/admin/posts", json={"title": "new", "slug": "s"})
    assert resp.status_code == 201, (
        f"POST must accept JSON body. Got {resp.status_code}: {resp.json()}"
    )
    assert resp.json()["title"] == "new"


def test_endpoint_dependant_classifies_req_as_body(monkeypatch):
    """Direct check that FastAPI's dependency analyzer puts ``req`` in
    body_params, not query_params. If this test fails, the fix has been
    reverted at the source level."""
    app, _ = _build_app(monkeypatch)
    target = next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/website/admin/posts/{item_id}"
        and "PUT" in getattr(r, "methods", set())
    )
    body_names = {p.name for p in target.dependant.body_params}
    query_names = {p.name for p in target.dependant.query_params}
    assert "req" in body_names, (
        f"`req` must be a body param. body={body_names}, query={query_names}"
    )
    assert "req" not in query_names, (
        f"`req` leaked into query params (the bug). body={body_names}, query={query_names}"
    )
