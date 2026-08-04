"""Regression tests for the Calls API company-scoping and RBAC gate.

The Calls API (backend/api/routes/calls.py) manages a company's manual
call log (direction/outcome/duration/notes, linked to customer
profiles). It must enforce:

  1. Multi-tenant isolation: a user in company A can never list, read,
     update or delete a call that belongs to company B, and a call can
     never be linked to another company's customer.
  2. RBAC: viewing requires "calls.view"; creating, editing and deleting
     require "calls.manage".
  3. Validation: a call needs a called_at time and either a customer or
     a phone number; invalid direction/outcome codes and negative
     durations are rejected.

These tests also cover the optimistic-concurrency guard (stale
`expected_updated_at` -> 409).

Run with: python3 -m pytest tests/test_calls_company_scoping.py -v
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


def _make_customer(db, company_id, name):
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (company_id, display_name, status)
            VALUES (?, ?, 'active')
            """,
            (company_id, name),
        )
        customer_id = cursor.lastrowid
        conn.commit()
    return customer_id


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _call_payload(**overrides):
    payload = {
        "phone_number": "+9611234567",
        "direction": "outbound",
        "outcome": "answered",
        "duration_seconds": 180,
        "notes": "Discussed renewal",
        "called_at": "2026-08-04T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/calls").status_code == 401
        assert client.get("/api/calls/1").status_code == 401
        assert client.post("/api/calls", json=_call_payload()).status_code == 401
        assert client.put("/api/calls/1", json={"notes": "x"}).status_code == 401
        assert client.delete("/api/calls/1").status_code == 401


def test_view_without_permission_is_403(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        assert client.get("/api/calls", headers=_headers(token)).status_code == 403


def test_manage_permission_required_for_writes(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    _make_role(db, company, "viewer", "Viewer", ["calls.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")

    with TestClient(app) as client:
        created = client.post(
            "/api/calls", headers=_headers(owner_token), json=_call_payload()
        )
        assert created.status_code == 201
        call_id = created.json()["id"]

        assert client.get("/api/calls", headers=_headers(viewer_token)).status_code == 200
        assert (
            client.post(
                "/api/calls", headers=_headers(viewer_token), json=_call_payload()
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/calls/{call_id}",
                headers=_headers(viewer_token),
                json={"notes": "Hacked"},
            ).status_code
            == 403
        )
        assert (
            client.delete(f"/api/calls/{call_id}", headers=_headers(viewer_token)).status_code
            == 403
        )


def test_calls_are_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/calls", headers=_headers(token_a), json=_call_payload(notes="A secret call")
        )
        assert created_a.status_code == 201
        call_id_a = created_a.json()["id"]

        listed_b = client.get("/api/calls", headers=_headers(token_b))
        ids_b = {item["id"] for item in listed_b.json()["items"]}
        assert call_id_a not in ids_b

        assert (
            client.get(f"/api/calls/{call_id_a}", headers=_headers(token_b)).status_code
            == 404
        )
        assert (
            client.put(
                f"/api/calls/{call_id_a}",
                headers=_headers(token_b),
                json={"notes": "Stolen"},
            ).status_code
            == 404
        )
        assert (
            client.delete(f"/api/calls/{call_id_a}", headers=_headers(token_b)).status_code
            == 404
        )


def test_call_cannot_reference_other_companys_customer(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")
    customer_b = _make_customer(db, company_b, "Company B Customer")

    _, token_a = _make_user(db, auth_service, company_a, "ownera2@test.local")

    with TestClient(app) as client:
        cross = client.post(
            "/api/calls",
            headers=_headers(token_a),
            json=_call_payload(customer_id=customer_b, phone_number=None),
        )
        assert cross.status_code == 422


def test_owner_can_log_and_list_calls(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    customer = _make_customer(db, company, "Sara Client")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/calls",
            headers=_headers(token),
            json=_call_payload(customer_id=customer),
        )
        assert created.status_code == 201
        body = created.json()
        assert body["customer_name"] == "Sara Client"
        assert body["direction"] == "outbound"
        assert body["outcome"] == "answered"

        listed = client.get("/api/calls", headers=_headers(token))
        assert listed.status_code == 200
        assert any(item["id"] == body["id"] for item in listed.json()["items"])


def test_validation_rules(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner3@test.local")

    with TestClient(app) as client:
        # Neither customer nor phone number.
        neither = client.post(
            "/api/calls",
            headers=_headers(token),
            json=_call_payload(phone_number=None),
        )
        assert neither.status_code == 422

        # Invalid outcome code.
        bad_outcome = client.post(
            "/api/calls",
            headers=_headers(token),
            json=_call_payload(outcome="teleported"),
        )
        assert bad_outcome.status_code == 422

        # Negative duration (rejected at the pydantic layer via ge=0).
        negative = client.post(
            "/api/calls",
            headers=_headers(token),
            json=_call_payload(duration_seconds=-5),
        )
        assert negative.status_code == 422


def test_stale_update_is_rejected_with_409(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner4@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/calls", headers=_headers(token), json=_call_payload()
        )
        call_id = created.json()["id"]
        original_updated_at = created.json()["updated_at"]

        first_edit = client.put(
            f"/api/calls/{call_id}",
            headers=_headers(token),
            json={"notes": "Edited once", "expected_updated_at": original_updated_at},
        )
        assert first_edit.status_code == 200

        stale_edit = client.put(
            f"/api/calls/{call_id}",
            headers=_headers(token),
            json={"notes": "Edited stale", "expected_updated_at": original_updated_at},
        )
        assert stale_edit.status_code == 409
        body = stale_edit.json()["detail"]
        assert body["current"]["notes"] == "Edited once"
