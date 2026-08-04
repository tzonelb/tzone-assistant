"""Real automated tests for POST /test/whatsapp/.

A prior attempt tried to resolve and pass a real company_id into
message_gateway.handle_text() but called it with a company_id keyword
argument that handle_text() did not accept at the time, so every call to
this endpoint crashed with TypeError -> 500.

These tests prove, against the actual FastAPI app (root main.py, per
docs/DECISION_LOG.md D-001):
  1. The endpoint succeeds end-to-end (200, not 500) for an authenticated
     user.
  2. The company_id resolved and used is the caller's own tenant company,
     not hardcoded to company 1 -- even when the caller's company has a
     different id.

Run with: python3 -m pytest tests/ -v
"""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def fresh_db():
    """Point the shared db singleton at a throwaway SQLite file per test.

    Mirrors tests/test_conversation_ownership.py's fresh_db fixture --
    core/engine.py depends on company_settings_service.ensure_schema()
    unconditionally, so it must be called here too.
    """
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.customer_service import customer_service
    from backend.services.diagnostics_service import diagnostics_service
    from backend.services.notification_service import notification_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()
    customer_service.ensure_schema()
    diagnostics_service.ensure_schema()
    notification_service.ensure_schema()

    yield db

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


def _make_company_user(db, *, workspace_slug, company_name, company_slug, email):
    """Create a workspace + company + user assigned only to that company.

    Returns (user_id, company_id).
    """
    with db.connect() as conn:
        workspace_id = conn.execute(
            "INSERT INTO workspaces (name, slug) VALUES (?, ?)",
            (workspace_slug, workspace_slug),
        ).lastrowid

        company_id = conn.execute(
            """
            INSERT INTO companies (workspace_id, name, slug, status)
            VALUES (?, ?, ?, 'active')
            """,
            (workspace_id, company_name, company_slug),
        ).lastrowid

        role_id = conn.execute(
            """
            INSERT INTO roles (company_id, name, code, is_system)
            VALUES (?, 'Owner', 'owner', 1)
            """,
            (company_id,),
        ).lastrowid

        user_id = conn.execute(
            """
            INSERT INTO users (email, password_hash, full_name, status, is_super_admin)
            VALUES (?, 'x', ?, 'active', 0)
            """,
            (email, email),
        ).lastrowid

        conn.execute(
            """
            INSERT INTO company_users (company_id, user_id, role_id, status)
            VALUES (?, ?, ?, 'active')
            """,
            (company_id, user_id, role_id),
        )

        conn.commit()

    return user_id, company_id


def test_whatsapp_test_endpoint_succeeds_and_resolves_real_company_id(fresh_db):
    from fastapi.testclient import TestClient
    from backend.services.auth_service import auth_service

    db = fresh_db

    # Company A gets created first (id 1) but is NOT the caller's company --
    # this is deliberate: config.DEFAULT_COMPANY_ID is 1, so if the endpoint
    # regressed to the hardcoded default, this test would still catch it.
    _make_company_user(
        db,
        workspace_slug="workspace-a",
        company_name="Company A",
        company_slug="company-a",
        email="usera@test.local",
    )

    user_id, company_id = _make_company_user(
        db,
        workspace_slug="workspace-b",
        company_name="Company B",
        company_slug="company-b",
        email="userb@test.local",
    )

    assert company_id != 1, "test setup must exercise a non-default company_id"

    session = auth_service.create_session(user_id=user_id, company_id=company_id)
    token = session["access_token"]

    import main

    with TestClient(main.app) as client:
        response = client.post(
            "/test/whatsapp/",
            json={"user_id": "test_customer_1", "message": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company_id"] == company_id
    assert body["company_id"] != 1
    assert "reply" in body


def test_whatsapp_test_endpoint_requires_authentication(fresh_db):
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        response = client.post(
            "/test/whatsapp/",
            json={"user_id": "test_customer_1", "message": "hi"},
        )

    assert response.status_code == 401
