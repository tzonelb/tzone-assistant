"""Regression tests for the tickets API authentication / company-scoping fix.

Before this fix, backend/api/routes/tickets.py had no Depends(get_current_user)
on any route:

  - GET  /tickets/            called db.get_tickets() with no company_id,
                               which returns every company's tickets to
                               anyone who could reach the URL, unauthenticated.
  - GET  /tickets/{ticket_id} called db.get_ticket(ticket_id), which had no
                               company_id parameter at all, so any ticket ID
                               from any company was readable by anyone.
  - POST /tickets/            was unauthenticated and TicketCreate had no
                               company_id field, so every ticket silently
                               landed in config.DEFAULT_COMPANY_ID regardless
                               of who (if anyone) made the request.

These tests prove: (1) unauthenticated requests are rejected, (2) an
authenticated user only ever sees their own company's tickets, never
another company's, and (3) ticket creation always uses the caller's
resolved company_id, never a client-supplied one.

Run with: python3 -m pytest tests/test_ticket_company_scoping.py -v
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
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors tests/test_conversation_ownership.py's fresh_db fixture.
    """
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()

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


def _make_authenticated_user(db, auth_service, company_id, email):
    user_id = auth_service.create_user(
        email=email,
        password="a-strong-password",
        full_name=email,
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code="owner")
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_list_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    with TestClient(app) as client:
        response = client.get("/tickets/")

    assert response.status_code == 401


def test_unauthenticated_get_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    with TestClient(app) as client:
        response = client.get("/tickets/1")

    assert response.status_code == 401


def test_unauthenticated_create_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    with TestClient(app) as client:
        response = client.post(
            "/tickets/",
            json={"platform": "whatsapp", "user_id": "u1"},
        )

    assert response.status_code == 401


def test_list_tickets_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_authenticated_user(db, auth_service, company_a, "ownera@test.local")

    ticket_a = db.create_ticket({
        "company_id": company_a, "platform": "whatsapp", "user_id": "customer-a",
    })
    ticket_b = db.create_ticket({
        "company_id": company_b, "platform": "whatsapp", "user_id": "customer-b",
    })

    with TestClient(app) as client:
        response = client.get("/tickets/", headers=_auth_headers(token_a))

    assert response.status_code == 200
    returned_ids = {row["id"] for row in response.json()}
    assert ticket_a in returned_ids
    assert ticket_b not in returned_ids


def test_get_ticket_cross_company_returns_404(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_b = _make_authenticated_user(db, auth_service, company_b, "ownerb@test.local")

    ticket_a = db.create_ticket({
        "company_id": company_a, "platform": "whatsapp", "user_id": "customer-a",
    })

    with TestClient(app) as client:
        # Company B's owner must not be able to read Company A's ticket,
        # even though the ticket ID is guessable/sequential.
        response = client.get(f"/tickets/{ticket_a}", headers=_auth_headers(token_b))

    assert response.status_code == 404


def test_get_ticket_same_company_succeeds(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    _, token_a = _make_authenticated_user(db, auth_service, company_a, "ownera2@test.local")

    ticket_a = db.create_ticket({
        "company_id": company_a, "platform": "whatsapp", "user_id": "customer-a",
    })

    with TestClient(app) as client:
        response = client.get(f"/tickets/{ticket_a}", headers=_auth_headers(token_a))

    assert response.status_code == 200
    assert response.json()["id"] == ticket_a


def test_create_ticket_ignores_client_supplied_company_id(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_authenticated_user(db, auth_service, company_a, "ownera3@test.local")

    with TestClient(app) as client:
        response = client.post(
            "/tickets/",
            headers=_auth_headers(token_a),
            json={
                "platform": "whatsapp",
                "user_id": "customer-a",
                # An attacker-controlled body trying to plant a ticket in
                # another company. TicketCreate has no company_id field, so
                # this must be silently ignored by pydantic, and the route
                # must use the caller's resolved company_id regardless.
                "company_id": company_b,
            },
        )

    assert response.status_code == 200
    ticket_id = response.json()["ticket_id"]

    # The ticket must be visible when scoped to the caller's real company...
    assert db.get_ticket(ticket_id, company_id=company_a) is not None
    # ...and must NOT have landed in the company the client tried to inject.
    assert db.get_ticket(ticket_id, company_id=company_b) is None


def test_company_a_user_cannot_list_into_company_b_scope(fresh_env):
    """End-to-end: two companies, each creates a ticket through the API,
    and neither owner ever sees the other's ticket in list or detail."""
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_authenticated_user(db, auth_service, company_a, "a@test.local")
    _, token_b = _make_authenticated_user(db, auth_service, company_b, "b@test.local")

    with TestClient(app) as client:
        create_a = client.post(
            "/tickets/",
            headers=_auth_headers(token_a),
            json={"platform": "whatsapp", "user_id": "customer-a"},
        )
        create_b = client.post(
            "/tickets/",
            headers=_auth_headers(token_b),
            json={"platform": "whatsapp", "user_id": "customer-b"},
        )

        ticket_a = create_a.json()["ticket_id"]
        ticket_b = create_b.json()["ticket_id"]

        list_a = client.get("/tickets/", headers=_auth_headers(token_a)).json()
        list_b = client.get("/tickets/", headers=_auth_headers(token_b)).json()

        ids_a = {row["id"] for row in list_a}
        ids_b = {row["id"] for row in list_b}

    assert ticket_a in ids_a and ticket_a not in ids_b
    assert ticket_b in ids_b and ticket_b not in ids_a
