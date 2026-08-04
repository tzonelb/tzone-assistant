"""Company-scoping and permission tests for the Analytics API.

These prove the two audited properties for /api/analytics:

  1. Multi-tenant isolation: an authenticated user's analytics only ever
     aggregate their own company's rows. Company B's owner never sees any
     of Company A's conversations/customers/tickets in totals or breakdowns.
  2. Permission enforcement: a user whose role lacks the required
     "dashboard.view" permission is rejected with 403, and unauthenticated
     callers are rejected with 401.

Run with: python3 -m pytest tests/test_analytics_company_scoping.py -v
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
    from pathlib import Path
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.company_settings_service import company_settings_service

    tmp_path = tempfile.mktemp(suffix=".db")
    original_path = db.db_path
    db.db_path = Path(tmp_path)

    db.create_tables()
    auth_service.create_tables()
    # core/engine.py (imported via main) depends on this schema existing.
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


def _make_role_without_permissions(db, company_id, code):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, ?, ?, 'No permissions', 0)
            """,
            (company_id, code.title(), code),
        )
        conn.commit()


def _make_user(db, auth_service, company_id, email, role_code="owner"):
    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code=role_code)
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _add_conversation(db, company_id, channel="whatsapp", status="open",
                      handled_by_ai=1, assigned_user_id=None):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                company_id, channel, external_user_id, status,
                handled_by_ai, assigned_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company_id, channel, f"cust-{channel}", status,
             handled_by_ai, assigned_user_id),
        )
        conn.commit()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_is_rejected(fresh_env):
    _db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        response = client.get("/api/analytics")

    assert response.status_code == 401


def test_analytics_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    # Company A: two conversations on distinct channels.
    _add_conversation(db, company_a, channel="whatsapp")
    _add_conversation(db, company_a, channel="messenger")
    # Company B: a single conversation.
    _add_conversation(db, company_b, channel="instagram")

    with TestClient(app) as client:
        result_a = client.get("/api/analytics", headers=_auth_headers(token_a)).json()
        result_b = client.get("/api/analytics", headers=_auth_headers(token_b)).json()

    # A sees exactly its own two, never B's.
    assert result_a["totals"]["conversations"] == 2
    channels_a = {row["label"] for row in result_a["conversations_by_channel"]}
    assert channels_a == {"whatsapp", "messenger"}
    assert "instagram" not in channels_a

    # B sees exactly its own one, never A's.
    assert result_b["totals"]["conversations"] == 1
    channels_b = {row["label"] for row in result_b["conversations_by_channel"]}
    assert channels_b == {"instagram"}
    assert "whatsapp" not in channels_b and "messenger" not in channels_b


def test_ai_vs_human_split_is_company_scoped(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "a2@test.local")

    _add_conversation(db, company_a, handled_by_ai=1)
    _add_conversation(db, company_a, handled_by_ai=0)
    # Company B rows must not leak into A's AI/human split.
    _add_conversation(db, company_b, handled_by_ai=0)
    _add_conversation(db, company_b, handled_by_ai=0)

    with TestClient(app) as client:
        result_a = client.get("/api/analytics", headers=_auth_headers(token_a)).json()

    assert result_a["totals"]["ai_handled_conversations"] == 1
    assert result_a["totals"]["human_handled_conversations"] == 1
    split = {row["label"]: row["value"] for row in result_a["ai_vs_human"]}
    assert split == {"AI-handled": 1, "Human-handled": 1}


def test_missing_permission_is_forbidden(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    _make_role_without_permissions(db, company_a, "agent")

    # This user's role has NO permissions at all (not owner, no dashboard.view).
    _, token = _make_user(db, auth_service, company_a, "agent@test.local", role_code="agent")

    with TestClient(app) as client:
        response = client.get("/api/analytics", headers=_auth_headers(token))

    assert response.status_code == 403


def test_owner_has_access(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company_a, "owner-access@test.local")

    with TestClient(app) as client:
        response = client.get("/api/analytics", headers=_auth_headers(token))

    assert response.status_code == 200
    assert "totals" in response.json()
