"""Tests for the appointment calendar's HTTP layer.

`appointment_service` itself already carries a lot of care -- see its own
`_require_staff` docstring on why a staff member must belong to the company
being booked, the exact bug this once had. What was never exercised is the
router in front of it: the split between `appointments.view` and
`appointments.manage`, and that `_context` really does take the company from
the caller's session on every route rather than trusting a client-supplied
one, which is the whole reason `AppointmentCreateRequest` carries no
`company_id` field at all.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest


PASSWORD = "EmployeePass12345"


def _iso(hours_from_now: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).isoformat()


@pytest.fixture()
def service(platform, monkeypatch):
    import database.manager as manager_module

    import backend.api.routes.appointments  # noqa: F401
    import backend.api.routes.auth  # noqa: F401
    import backend.services.appointment_service  # noqa: F401
    import backend.services.auth_service  # noqa: F401

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
        "backend.services.appointment_service",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    from backend.services.auth_service import auth_service

    return auth_service


@pytest.fixture()
def client(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import appointments, auth

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(appointments.router)

    return TestClient(app)


def _employ(platform, company, user_id: int, role_code: str) -> int:
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

    return user_id


def _login(client, company, email, password=PASSWORD):
    response = client.post(
        "/api/auth/login",
        json={"company": company["name"], "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _employee(client, service, platform, company, email, role_code="agent"):
    user_id = service.create_user(email=email, password=PASSWORD, full_name="Employee")
    _employ(platform, company, user_id, role_code)
    return user_id, _login(client, company, email)


def _booking(staff_user_id: int, **overrides) -> dict:
    payload = {
        "staff_user_id": staff_user_id,
        "starts_at": _iso(24),
        "ends_at": _iso(25),
        "title": "Consultation",
    }
    payload.update(overrides)
    return payload


# ----------------------------------------------------------------- the gate


def test_a_view_only_role_can_read_but_not_book(client, service, platform, alpha):
    """`viewer` carries `appointments.view` but not `appointments.manage` --
    the split `view_context`/`manage_context` exists to enforce."""
    _, headers = _employee(
        client, service, platform, alpha, "reader@alpha.example.com", "viewer"
    )

    listed = client.get("/api/appointments", headers=headers)
    assert listed.status_code == 200, listed.text

    booked = client.post(
        "/api/appointments", headers=headers, json=_booking(1)
    )
    assert booked.status_code == 403, booked.text


def test_an_unauthenticated_request_is_refused(client):
    response = client.get("/api/appointments")

    assert response.status_code in (401, 403)


# --------------------------------------------------------------- booking


def test_a_booking_is_created_and_can_be_read_back(client, service, platform, alpha):
    staff_id, staff_headers = _employee(
        client, service, platform, alpha, "staff1@alpha.example.com"
    )
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager1@alpha.example.com", "manager"
    )

    created = client.post(
        "/api/appointments", headers=manager_headers, json=_booking(staff_id)
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["staff_user_id"] == staff_id
    assert body["staff_name"]

    fetched = client.get(
        f"/api/appointments/{body['id']}", headers=staff_headers
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == body["id"]


def test_double_booking_the_same_slot_is_a_conflict(client, service, platform, alpha):
    staff_id, _ = _employee(
        client, service, platform, alpha, "staff2@alpha.example.com"
    )
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager2@alpha.example.com", "manager"
    )

    first = client.post(
        "/api/appointments", headers=manager_headers, json=_booking(staff_id)
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/appointments", headers=manager_headers, json=_booking(staff_id)
    )

    assert second.status_code == 409, second.text


def test_booking_against_another_companys_employee_is_refused(
    client, service, platform, alpha, beta
):
    """`_require_staff` checks the staff id belongs to *this* company -- the
    exact defect its own docstring describes, restored as a route-level test."""
    beta_staff_id, _ = _employee(
        client, service, platform, beta, "staff3@beta.example.com"
    )
    _, alpha_manager_headers = _employee(
        client, service, platform, alpha, "manager3@alpha.example.com", "manager"
    )

    response = client.post(
        "/api/appointments",
        headers=alpha_manager_headers,
        json=_booking(beta_staff_id),
    )

    assert response.status_code == 400, response.text


def test_an_appointment_never_crosses_the_company_boundary(
    client, service, platform, alpha, beta
):
    staff_id, _ = _employee(
        client, service, platform, alpha, "staff4@alpha.example.com"
    )
    _, alpha_manager_headers = _employee(
        client, service, platform, alpha, "manager4@alpha.example.com", "manager"
    )
    created = client.post(
        "/api/appointments", headers=alpha_manager_headers, json=_booking(staff_id)
    )
    appointment_id = created.json()["id"]

    _, beta_headers = _employee(
        client, service, platform, beta, "peeker@beta.example.com", "manager"
    )

    response = client.get(
        f"/api/appointments/{appointment_id}", headers=beta_headers
    )

    assert response.status_code == 404


def test_rescheduling_moves_the_slot(client, service, platform, alpha):
    staff_id, _ = _employee(
        client, service, platform, alpha, "staff5@alpha.example.com"
    )
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager5@alpha.example.com", "manager"
    )
    created = client.post(
        "/api/appointments", headers=manager_headers, json=_booking(staff_id)
    ).json()

    new_start = _iso(48)
    new_end = _iso(49)

    response = client.patch(
        f"/api/appointments/{created['id']}/reschedule",
        headers=manager_headers,
        json={"starts_at": new_start, "ends_at": new_end},
    )

    assert response.status_code == 200, response.text
    # Stored to the second; compare on the date portion which survives the
    # service's own normalisation format.
    assert response.json()["starts_at"][:16] == new_start[:16]


def test_cancelling_frees_the_slot_for_a_new_booking(client, service, platform, alpha):
    staff_id, _ = _employee(
        client, service, platform, alpha, "staff6@alpha.example.com"
    )
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager6@alpha.example.com", "manager"
    )
    booking = _booking(staff_id)
    created = client.post(
        "/api/appointments", headers=manager_headers, json=booking
    ).json()

    cancelled = client.post(
        f"/api/appointments/{created['id']}/cancel",
        headers=manager_headers,
        json={"reason": "Customer asked to move it"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    # The same slot, same staff member, now bookable again.
    rebooked = client.post(
        "/api/appointments", headers=manager_headers, json=booking
    )
    assert rebooked.status_code == 201, rebooked.text


def test_marking_status_updates_it(client, service, platform, alpha):
    staff_id, _ = _employee(
        client, service, platform, alpha, "staff7@alpha.example.com"
    )
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager7@alpha.example.com", "manager"
    )
    created = client.post(
        "/api/appointments", headers=manager_headers, json=_booking(staff_id)
    ).json()

    response = client.patch(
        f"/api/appointments/{created['id']}/status",
        headers=manager_headers,
        json={"status": "completed"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"


# ------------------------------------------------------------- availability


def test_availability_rules_round_trip(client, service, platform, alpha):
    staff_id, _ = _employee(
        client, service, platform, alpha, "staff8@alpha.example.com"
    )
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager8@alpha.example.com", "manager"
    )

    created = client.post(
        "/api/appointments/availability",
        headers=manager_headers,
        json={
            "staff_user_id": staff_id,
            "weekday": 1,
            "start_time": "09:00",
            "end_time": "17:00",
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    listed = client.get(
        "/api/appointments/availability",
        headers=manager_headers,
        params={"staff_user_id": staff_id},
    )
    assert listed.status_code == 200, listed.text
    assert any(rule["id"] == rule_id for rule in listed.json()["items"])

    updated = client.put(
        f"/api/appointments/availability/{rule_id}",
        headers=manager_headers,
        json={"end_time": "18:00"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["end_time"] == "18:00"

    deleted = client.delete(
        f"/api/appointments/availability/{rule_id}", headers=manager_headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["success"] is True


def test_options_lists_this_companys_staff_only(client, service, platform, alpha, beta):
    _employee(client, service, platform, alpha, "onlyalpha@alpha.example.com")
    _employee(client, service, platform, beta, "onlybeta@beta.example.com")
    _, manager_headers = _employee(
        client, service, platform, alpha, "manager9@alpha.example.com", "manager"
    )

    response = client.get("/api/appointments/options", headers=manager_headers)

    assert response.status_code == 200, response.text
    staff_emails = {row["name"] for row in response.json()["staff"]}
    assert "onlybeta@beta.example.com" not in staff_emails
