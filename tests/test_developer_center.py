"""Tests for the Developer Center console -- diagnostics for a Super Admin.

Untested until now: three routes existed with nothing exercising them over
HTTP, only `get_current_user` and `diagnostics_service` covered separately.
The property worth pinning is the one every Super Admin surface in this
platform shares -- see `test_platform_admin.py`'s own framing -- an
administrator manages the platform but must not read a company's customer
data by accident. `super_admin_context` gates on `is_super_admin`, but it
still resolves an ordinary company id from the caller's own session the same
way every other router does, so an ordinary employee token must be refused
here just as firmly as an anonymous one.
"""

from __future__ import annotations

import sys

import pytest


PASSWORD = "PlatformAdminPass1"
EMPLOYEE_PASSWORD = "EmployeePass12345"


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.auth  # noqa: F401
    import backend.api.routes.developer_center  # noqa: F401
    import backend.services.auth_service  # noqa: F401
    import backend.services.diagnostics_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.auth_service",
        "backend.services.diagnostics_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import auth, developer_center

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(developer_center.router)

    return TestClient(app)


def _employ(platform, company, user_id: int, role_code: str = "owner") -> None:
    from backend.services.auth_service import auth_service
    from database.manager import utc_now_iso

    with platform["manager"].control() as conn:
        role = conn.execute(
            "SELECT id FROM roles WHERE company_id = ? AND code = ?",
            (company["id"], role_code),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (company["id"], user_id, int(role["id"]), utc_now_iso()),
        )
        conn.commit()


def _login(client, company, email, password):
    return client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": password},
    )


def _super_admin(client, service, platform, alpha):
    """A user who is both an employee of alpha and flagged is_super_admin --
    the only shape `super_admin_context` actually admits, since resolving a
    company still goes through the caller's own `active_company_id`."""
    user_id = service.create_user(
        email="root@platform.example.com",
        password=PASSWORD,
        full_name="Platform Root",
        is_super_admin=True,
    )
    _employ(platform, alpha, user_id)

    response = _login(client, alpha, "root@platform.example.com", PASSWORD)
    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _ordinary_employee(client, service, platform, alpha, role_code="agent"):
    user_id = service.create_user(
        email="agent@alpha.example.com",
        password=EMPLOYEE_PASSWORD,
        full_name="Ordinary Employee",
    )
    _employ(platform, alpha, user_id, role_code=role_code)

    response = _login(client, alpha, "agent@alpha.example.com", EMPLOYEE_PASSWORD)
    assert response.status_code == 200, response.text

    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------- the gate


def test_an_ordinary_employee_is_refused(client, service, platform, alpha):
    headers = _ordinary_employee(client, service, platform, alpha)

    response = client.get("/api/developer-center/summary", headers=headers)

    assert response.status_code == 403


def test_an_unauthenticated_request_is_refused(client):
    response = client.get("/api/developer-center/summary")

    assert response.status_code in (401, 403)


def test_a_super_admin_reaches_every_route(client, service, platform, alpha):
    headers = _super_admin(client, service, platform, alpha)

    summary = client.get("/api/developer-center/summary", headers=headers)
    assert summary.status_code == 200, summary.text

    events = client.get("/api/developer-center/events", headers=headers)
    assert events.status_code == 200, events.text

    cleanup = client.post("/api/developer-center/cleanup", headers=headers)
    assert cleanup.status_code == 200, cleanup.text
    assert cleanup.json()["retention_days"] == 14


# --------------------------------------------------------------- the data


def test_the_summary_reflects_recorded_events(client, service, platform, alpha):
    from backend.services.diagnostics_service import diagnostics_service

    diagnostics_service.record(
        company_id=alpha["id"],
        event_type="ai_reply_error",
        channel="messenger",
        severity="error",
        status="failed",
    )

    headers = _super_admin(client, service, platform, alpha)

    summary = client.get("/api/developer-center/summary", headers=headers)

    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["total_events"] >= 1
    assert body["errors"] >= 1


def test_events_can_be_filtered_by_severity(client, service, platform, alpha):
    from backend.services.diagnostics_service import diagnostics_service

    diagnostics_service.record(
        company_id=alpha["id"], event_type="one", severity="info"
    )
    diagnostics_service.record(
        company_id=alpha["id"], event_type="two", severity="error"
    )

    headers = _super_admin(client, service, platform, alpha)

    response = client.get(
        "/api/developer-center/events",
        params={"severity": "error"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    events = response.json()["items"] if isinstance(response.json(), dict) else response.json()
    assert all(item["severity"] == "error" for item in events)
    assert any(item["event_type"] == "two" for item in events)


def test_cleanup_removes_events_older_than_retention(client, service, platform, alpha):
    from backend.services.diagnostics_service import diagnostics_service

    with platform["manager"].tenant(alpha["id"]) as conn:
        conn.execute(
            """
            INSERT INTO diagnostic_events (
                company_id, channel, external_user_id, event_type,
                severity, status, duration_ms, data_json, created_at
            ) VALUES (?, NULL, NULL, 'ancient', 'info', NULL, NULL, '{}', '2000-01-01T00:00:00+00:00')
            """,
            (alpha["id"],),
        )
        conn.commit()

    diagnostics_service.record(company_id=alpha["id"], event_type="fresh")

    headers = _super_admin(client, service, platform, alpha)

    response = client.post(
        "/api/developer-center/cleanup",
        params={"retention_days": 1},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["deleted"] >= 1

    with platform["manager"].tenant(alpha["id"]) as conn:
        remaining = {
            row["event_type"]
            for row in conn.execute(
                "SELECT event_type FROM diagnostic_events WHERE company_id = ?",
                (alpha["id"],),
            ).fetchall()
        }

    assert "ancient" not in remaining
    assert "fresh" in remaining
