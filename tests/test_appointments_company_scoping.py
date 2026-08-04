"""Regression tests for the Appointments API company-scoping, RBAC gate,
and double-booking guard.

The Appointments API (backend/api/routes/appointments.py) manages a
company's booking calendar. It must enforce three audited properties:

  1. Multi-tenant isolation: a user in company A can never list, read,
     update or delete an appointment that belongs to company B, even with
     a guessable sequential id.
  2. RBAC: viewing (list/get) requires "appointments.view"; creating,
     editing and deleting requires "appointments.manage".
  3. Double-booking guard: two "scheduled" appointments for the same
     assignee must not overlap in time.

These tests also cover the optimistic-concurrency guard on update: a stale
`expected_updated_at` token is rejected with 409.

Run with: python3 -m pytest tests/test_appointments_company_scoping.py -v
"""
import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_env():
    """Point the shared db singleton at a throwaway SQLite file per test."""
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    company_settings_service.ensure_schema()

    yield db, auth_service

    db.db_path = original_path

    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _make_company(db, name, slug):
    with db.connect() as conn:
        cursor = conn.execute(
            "INSERT INTO workspaces (name, slug, status) VALUES (?, ?, 'active')",
            (f"{name} workspace", f"{slug}-ws"),
        )
        workspace_id = cursor.lastrowid

        cursor = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (?, ?, ?, 'active')
            """,
            (workspace_id, name, slug),
        )
        company_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, 'Owner', 'owner', 'Full access', 1)
            """,
            (company_id,),
        )
        conn.commit()

    return company_id


def _make_role(db, company_id, code, name, permission_codes):
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, ?, ?, '', 0)
            """,
            (company_id, name, code),
        )
        role_id = cursor.lastrowid
        for permission_code in permission_codes:
            row = conn.execute(
                "SELECT id FROM permissions WHERE code = ?", (permission_code,)
            ).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_id, row["id"]),
                )
        conn.commit()
    return role_id


def _make_user(db, auth_service, company_id, email, role_code="owner"):
    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code=role_code)
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _appointment_payload(title="Installation visit", **overrides):
    payload = {
        "title": title,
        "starts_at": "2026-09-01T10:00:00+00:00",
        "ends_at": "2026-09-01T11:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/appointments").status_code == 401
        assert client.get("/api/appointments/1").status_code == 401
        assert (
            client.post("/api/appointments", json=_appointment_payload()).status_code
            == 401
        )
        assert client.put("/api/appointments/1", json={"title": "x"}).status_code == 401
        assert client.delete("/api/appointments/1").status_code == 401


def test_view_without_permission_is_403(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        assert client.get("/api/appointments", headers=_headers(token)).status_code == 403
        assert (
            client.get("/api/appointments/assignable-users", headers=_headers(token)).status_code
            == 403
        )


def test_manage_permission_required_to_create(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "viewer", "Viewer", ["appointments.view"])
    _, token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")

    with TestClient(app) as client:
        assert client.get("/api/appointments", headers=_headers(token)).status_code == 200
        create = client.post(
            "/api/appointments", headers=_headers(token), json=_appointment_payload()
        )
        assert create.status_code == 403


def test_manage_permission_required_to_edit_and_delete(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    _make_role(db, company, "viewer", "Viewer", ["appointments.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer2@test.local", role_code="viewer")

    with TestClient(app) as client:
        created = client.post(
            "/api/appointments", headers=_headers(owner_token), json=_appointment_payload()
        )
        assert created.status_code == 201
        appointment_id = created.json()["id"]

        edit = client.put(
            f"/api/appointments/{appointment_id}",
            headers=_headers(viewer_token),
            json={"title": "Hacked title"},
        )
        assert edit.status_code == 403

        delete = client.delete(
            f"/api/appointments/{appointment_id}", headers=_headers(viewer_token)
        )
        assert delete.status_code == 403

    with db.connect() as conn:
        row = conn.execute(
            "SELECT title FROM appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
    assert row["title"] == "Installation visit"


def test_owner_can_create_and_list(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/appointments", headers=_headers(token), json=_appointment_payload("Site visit")
        )
        assert created.status_code == 201
        body = created.json()
        assert body["title"] == "Site visit"
        assert body["status"] == "scheduled"

        listed = client.get("/api/appointments", headers=_headers(token))
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(item["id"] == body["id"] for item in items)


def test_list_is_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/appointments", headers=_headers(token_a), json=_appointment_payload("Company A Visit")
        )
        assert created_a.status_code == 201
        appointment_id_a = created_a.json()["id"]

        listed_b = client.get("/api/appointments", headers=_headers(token_b))
        ids_b = {item["id"] for item in listed_b.json()["items"]}
        assert appointment_id_a not in ids_b

        get_cross = client.get(
            f"/api/appointments/{appointment_id_a}", headers=_headers(token_b)
        )
        assert get_cross.status_code == 404

        edit_cross = client.put(
            f"/api/appointments/{appointment_id_a}",
            headers=_headers(token_b),
            json={"title": "Stolen"},
        )
        assert edit_cross.status_code == 404

        delete_cross = client.delete(
            f"/api/appointments/{appointment_id_a}", headers=_headers(token_b)
        )
        assert delete_cross.status_code == 404

    with db.connect() as conn:
        row = conn.execute(
            "SELECT title FROM appointments WHERE id = ?", (appointment_id_a,)
        ).fetchone()
    assert row["title"] == "Company A Visit"


def test_stale_update_is_rejected_with_409(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner3@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/appointments", headers=_headers(token), json=_appointment_payload("Stale Test")
        )
        appointment_id = created.json()["id"]
        original_updated_at = created.json()["updated_at"]

        first_edit = client.put(
            f"/api/appointments/{appointment_id}",
            headers=_headers(token),
            json={"location": "Branch A", "expected_updated_at": original_updated_at},
        )
        assert first_edit.status_code == 200

        stale_edit = client.put(
            f"/api/appointments/{appointment_id}",
            headers=_headers(token),
            json={"location": "Branch B", "expected_updated_at": original_updated_at},
        )
        assert stale_edit.status_code == 409
        body = stale_edit.json()["detail"]
        assert body["current"]["location"] == "Branch A"


def test_overlapping_appointment_for_same_assignee_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    user_id, token = _make_user(db, auth_service, company, "owner4@test.local")

    with TestClient(app) as client:
        first = client.post(
            "/api/appointments",
            headers=_headers(token),
            json=_appointment_payload(
                "First visit",
                assignee_user_id=user_id,
                starts_at="2026-09-01T10:00:00+00:00",
                ends_at="2026-09-01T11:00:00+00:00",
            ),
        )
        assert first.status_code == 201

        # Overlaps the first appointment's 10:00-11:00 window.
        overlapping = client.post(
            "/api/appointments",
            headers=_headers(token),
            json=_appointment_payload(
                "Overlapping visit",
                assignee_user_id=user_id,
                starts_at="2026-09-01T10:30:00+00:00",
                ends_at="2026-09-01T11:30:00+00:00",
            ),
        )
        assert overlapping.status_code == 409

        # Back-to-back (starts exactly when the first ends) does NOT
        # overlap -- the boundary is exclusive.
        back_to_back = client.post(
            "/api/appointments",
            headers=_headers(token),
            json=_appointment_payload(
                "Back to back visit",
                assignee_user_id=user_id,
                starts_at="2026-09-01T11:00:00+00:00",
                ends_at="2026-09-01T12:00:00+00:00",
            ),
        )
        assert back_to_back.status_code == 201

        # A cancelled appointment never conflicts, even at the exact same
        # time as an active one.
        cancelled_conflict = client.post(
            "/api/appointments",
            headers=_headers(token),
            json=_appointment_payload(
                "Cancelled slot reuse",
                assignee_user_id=user_id,
                starts_at="2026-09-01T10:00:00+00:00",
                ends_at="2026-09-01T11:00:00+00:00",
                status="cancelled",
            ),
        )
        assert cancelled_conflict.status_code == 201


def test_invalid_status_is_rejected_with_422(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner5@test.local")

    with TestClient(app) as client:
        response = client.post(
            "/api/appointments",
            headers=_headers(token),
            json=_appointment_payload("Bad Status", status="not_a_real_status"),
        )
        assert response.status_code == 422
