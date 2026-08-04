"""Regression tests for the Customers API company-scoping and RBAC gate.

The Customers API (backend/api/routes/customers.py) exposes customer PII
(phone/email/notes) captured from conversations. It must enforce two audited
properties:

  1. Multi-tenant isolation: a user in company A can never list, read or
     mutate a customer that belongs to company B, even with a guessable
     sequential id.
  2. RBAC: viewing requires "conversations.view"; editing requires
     "users.manage". A user whose role lacks the required code gets 403.

These tests also cover the optimistic-concurrency guard on update: a stale
`expected_updated_at` token is rejected with 409 so two editors can't
silently overwrite each other.

Run with: python3 -m pytest tests/test_customer_company_scoping.py -v
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
    from backend.services.customer_service import customer_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    # core/engine.py reads company settings at import/use time, and the
    # customers tables live in the customer service's own schema.
    company_settings_service.ensure_schema()
    customer_service.ensure_schema()

    yield db, auth_service, customer_service

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
    """Create a non-owner role for a company and grant it a set of codes."""
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


def _make_customer(db, company_id, display_name, phone=None, email=None):
    now = "2026-01-01T00:00:00+00:00"
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, phone, email,
                first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, display_name, phone, email, now, now, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth, _svc = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/customers").status_code == 401
        assert client.get("/api/customers/1").status_code == 401
        assert client.put("/api/customers/1", json={"phone": "x"}).status_code == 401


def test_list_is_scoped_to_caller_company(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")

    cust_a = _make_customer(db, company_a, "Alice A", phone="111")
    cust_b = _make_customer(db, company_b, "Bob B", phone="222")

    with TestClient(app) as client:
        response = client.get("/api/customers", headers=_headers(token_a))

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["items"]}
    assert cust_a in ids
    assert cust_b not in ids


def test_get_cross_company_returns_404(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")
    cust_a = _make_customer(db, company_a, "Alice A")

    with TestClient(app) as client:
        response = client.get(f"/api/customers/{cust_a}", headers=_headers(token_b))

    assert response.status_code == 404


def test_update_cross_company_returns_404(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_b = _make_user(db, auth_service, company_b, "ownerb2@test.local")
    cust_a = _make_customer(db, company_a, "Alice A", phone="111")

    with TestClient(app) as client:
        response = client.put(
            f"/api/customers/{cust_a}",
            headers=_headers(token_b),
            json={"phone": "hacked"},
        )

    assert response.status_code == 404
    # The victim record must be untouched.
    with db.connect() as conn:
        row = conn.execute(
            "SELECT phone FROM customers WHERE id = ?", (cust_a,)
        ).fetchone()
    assert row["phone"] == "111"


def test_view_without_permission_is_403(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    # Role with no permissions at all -> cannot even view.
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")
    _make_customer(db, company, "Alice A")

    with TestClient(app) as client:
        assert client.get("/api/customers", headers=_headers(token)).status_code == 403


def test_edit_requires_manage_permission(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    # Agent can view conversations (=> view customers) but has no users.manage.
    _make_role(db, company, "agent", "Agent", ["conversations.view"])
    _, token = _make_user(db, auth_service, company, "agent@test.local", role_code="agent")
    cust = _make_customer(db, company, "Alice A", phone="111")

    with TestClient(app) as client:
        # Viewing is allowed for this role...
        assert client.get(f"/api/customers/{cust}", headers=_headers(token)).status_code == 200
        # ...but editing is not.
        edit = client.put(
            f"/api/customers/{cust}",
            headers=_headers(token),
            json={"phone": "999"},
        )

    assert edit.status_code == 403
    with db.connect() as conn:
        row = conn.execute("SELECT phone FROM customers WHERE id = ?", (cust,)).fetchone()
    assert row["phone"] == "111"


def test_owner_can_edit(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner@test.local")
    cust = _make_customer(db, company, "Alice A", phone="111")

    with TestClient(app) as client:
        response = client.put(
            f"/api/customers/{cust}",
            headers=_headers(token),
            json={"internal_name": "VIP client", "phone": "555"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["internal_name"] == "VIP client"
    assert body["phone"] == "555"


def test_stale_update_conflicts_with_409(fresh_env):
    db, auth_service, _svc = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")
    cust = _make_customer(db, company, "Alice A", phone="111")

    with TestClient(app) as client:
        loaded = client.get(f"/api/customers/{cust}", headers=_headers(token)).json()
        stale_token = loaded["updated_at"]

        # First edit moves the record forward (updated_at changes).
        first = client.put(
            f"/api/customers/{cust}",
            headers=_headers(token),
            json={"phone": "222", "expected_updated_at": stale_token},
        )
        assert first.status_code == 200

        # Second edit reusing the now-stale token must be rejected.
        second = client.put(
            f"/api/customers/{cust}",
            headers=_headers(token),
            json={"phone": "333", "expected_updated_at": stale_token},
        )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert isinstance(detail, dict)
    assert detail.get("current", {}).get("phone") == "222"
