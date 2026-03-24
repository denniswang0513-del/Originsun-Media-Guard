"""
Smoke tests — verify the server is alive and core endpoints respond.
Uses real_server fixture (port 18000).
"""
import pytest
import httpx

pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def client(real_server):
    with httpx.Client(base_url=real_server["base_url"], timeout=10.0) as c:
        yield c


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200, f"Health failed: {r.text}"


def test_version(client):
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    assert "version" in r.json(), f"Response: {r.json()}"


def test_settings(client):
    r = client.get("/api/settings/load")
    assert r.status_code == 200
    assert isinstance(r.json(), dict), f"Not a dict: {r.json()}"


def test_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "originsun" in r.text.lower(), f"Start: {r.text[:200]}"


def test_socketio_endpoint(client):
    r = client.get("/socket.io/?EIO=4&transport=polling")
    assert r.status_code != 404, f"Socket.IO not found: {r.status_code}"
