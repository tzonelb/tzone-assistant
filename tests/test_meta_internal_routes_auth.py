"""
Real test for an adversarial-audit finding: channels/meta/debug.py,
channels/meta/tester.py and channels/meta/logs.py defined fully
unauthenticated FastAPI routes. tester.py's POST /test/meta-payload fed
arbitrary caller-supplied payloads straight into the real production
message pipeline, and logs.py's GET/DELETE /logs/meta read and could wipe
real customer message logs -- with zero auth.

These routers are only wired onto the legacy backend/main.py (see
docs/DECISION_LOG.md D-001), not the real entrypoint root main.py, so
they are not reachable in production today. This test guards against the
routers ever being safe to mistakenly wire in (or backend/main.py ever
being mistakenly deployed) by asserting every route requires
authentication, and the destructive DELETE /logs/meta additionally
requires super admin.

Run with: python3 -m pytest tests/test_meta_internal_routes_auth.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.auth_service import get_current_user
from channels.meta import debug as meta_debug
from channels.meta import tester as meta_tester
from channels.meta import logs as meta_logs


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(meta_debug.router)
    app.include_router(meta_tester.router)
    app.include_router(meta_logs.router)
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Keep the destructive DELETE route from ever touching a real log
    # file, no matter what the auth checks decide.
    fake_log = tmp_path / "meta_messages.log"
    monkeypatch.setattr(meta_logs, "LOG_FILE", fake_log)

    # tester.py routes call straight into the real production message
    # pipeline; stub it out so authorized-path assertions don't need a
    # full database.
    monkeypatch.setattr(
        meta_tester, "process_meta_payload", lambda payload: {"status": "ok", "payload": payload}
    )

    app = _build_app()
    return TestClient(app), fake_log


REGULAR_USER = {"id": 101, "email": "employee@test.local", "is_super_admin": False}
SUPER_ADMIN_USER = {"id": 999, "email": "admin@test.local", "is_super_admin": True}

UNAUTHENTICATED_REQUESTS = [
    ("GET", "/debug/meta", None),
    ("POST", "/debug/meta", {}),
    ("POST", "/test/meta-payload", {"payload": {}}),
    ("POST", "/test/meta-message", None),
    ("GET", "/logs/meta", None),
    ("DELETE", "/logs/meta", None),
]


@pytest.mark.parametrize("method,path,body", UNAUTHENTICATED_REQUESTS)
def test_routes_reject_unauthenticated_requests(client, method, path, body):
    test_client, _ = client
    response = test_client.request(method, path, json=body)
    assert response.status_code == 401, (
        f"{method} {path} should require authentication but returned {response.status_code}"
    )


def test_routes_reject_bad_scheme(client):
    test_client, _ = client
    response = test_client.get("/logs/meta", headers={"Authorization": "Basic notabearer"})
    assert response.status_code == 401


def test_authenticated_regular_employee_can_use_read_routes(client):
    test_client, app_client_pair = client
    app = test_client.app
    app.dependency_overrides[get_current_user] = lambda: REGULAR_USER
    try:
        assert test_client.get("/debug/meta").status_code == 200
        assert test_client.post("/debug/meta", json={"a": 1}).status_code == 200
        assert test_client.post("/test/meta-payload", json={"payload": {}}).status_code == 200
        assert test_client.post("/test/meta-message").status_code == 200
        assert test_client.get("/logs/meta").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_delete_logs_forbidden_for_regular_employee(client):
    test_client, fake_log = client
    fake_log.parent.mkdir(parents=True, exist_ok=True)
    fake_log.write_text('{"customer": "real message text"}\n', encoding="utf-8")

    app = test_client.app
    app.dependency_overrides[get_current_user] = lambda: REGULAR_USER
    try:
        response = test_client.delete("/logs/meta")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()

    # Log content must survive an unauthorized wipe attempt.
    assert fake_log.read_text(encoding="utf-8") != ""


def test_delete_logs_allowed_for_super_admin(client):
    test_client, fake_log = client
    fake_log.parent.mkdir(parents=True, exist_ok=True)
    fake_log.write_text('{"customer": "real message text"}\n', encoding="utf-8")

    app = test_client.app
    app.dependency_overrides[get_current_user] = lambda: SUPER_ADMIN_USER
    try:
        response = test_client.delete("/logs/meta")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()

    assert fake_log.read_text(encoding="utf-8") == ""
