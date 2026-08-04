"""Regression tests for the company-scoped Knowledge / AI Teaching HTTP API.

These prove the two audited properties for backend/api/routes/knowledge.py
(the endpoints the AI Teaching management page is built on):

  1. Multi-tenant isolation: an authenticated user only ever sees and can
     only ever mutate their own company's knowledge_items rows. Company A's
     owner must never see, edit or delete Company B's FAQ rows, even when the
     external ids collide across companies.

  2. Permission enforcement: reads require "knowledge.view" and writes
     require "knowledge.manage". A user assigned to a role holding neither
     code is rejected with 403 on every route, and unauthenticated requests
     are rejected with 401.

The companion tests/test_knowledge_company_scoping.py already covers the
knowledge_manager fallback logic; this file covers the router + auth layer.

Run with: python3 -m pytest tests/test_knowledge_api_scoping.py -v
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
    # core/engine.py depends on the company_settings schema being present.
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


def _make_owner(db, auth_service, company_id, email):
    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code="owner")
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _make_no_permission_user(db, auth_service, company_id, email):
    """A user in a role that holds neither knowledge.view nor knowledge.manage
    (in fact no permissions at all)."""
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO roles (company_id, name, code, description, is_system)
            VALUES (?, 'Agent', 'agent', 'No knowledge access', 0)
            """,
            (company_id,),
        )
        conn.commit()

    user_id = auth_service.create_user(
        email=email, password="a-strong-password", full_name=email
    )
    auth_service.assign_user_to_company(user_id, company_id, role_code="agent")
    session = auth_service.create_session(user_id, company_id=company_id)
    return user_id, session["access_token"]


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _sample_faq(faq_id, title_en="Opening hours"):
    return {
        "id": faq_id,
        "title_en": title_en,
        "title_ar": "ساعات العمل",
        "body_en": "We are open 9 to 5.",
        "body_ar": "نعمل من 9 إلى 5.",
        "category": "General",
        "enabled": True,
    }


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

def test_unauthenticated_list_all_is_rejected(fresh_env):
    from main import app

    with TestClient(app) as client:
        assert client.get("/knowledge/faqs").status_code == 401


def test_unauthenticated_write_is_rejected(fresh_env):
    from main import app

    with TestClient(app) as client:
        response = client.post(
            "/knowledge/information/faqs", json=_sample_faq("faq-1")
        )
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Permission enforcement
# --------------------------------------------------------------------------

def test_view_permission_required_for_read(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_no_permission_user(db, auth_service, company, "agent@a.local")

    with TestClient(app) as client:
        assert (
            client.get("/knowledge/faqs", headers=_auth_headers(token)).status_code
            == 403
        )
        assert (
            client.get(
                "/knowledge/information/faqs", headers=_auth_headers(token)
            ).status_code
            == 403
        )


def test_manage_permission_required_for_write(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_no_permission_user(db, auth_service, company, "agent2@a.local")

    with TestClient(app) as client:
        create = client.post(
            "/knowledge/information/faqs",
            headers=_auth_headers(token),
            json=_sample_faq("faq-x"),
        )
        assert create.status_code == 403

        delete = client.delete(
            "/knowledge/information/faqs/faq-x", headers=_auth_headers(token)
        )
        assert delete.status_code == 403


# --------------------------------------------------------------------------
# Company scoping
# --------------------------------------------------------------------------

def test_list_all_is_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_owner(db, auth_service, company_a, "owner-a@test.local")
    _, token_b = _make_owner(db, auth_service, company_b, "owner-b@test.local")

    with TestClient(app) as client:
        client.post(
            "/knowledge/sales/faqs",
            headers=_auth_headers(token_a),
            json=_sample_faq("shared-id", "A-only answer"),
        )
        client.post(
            "/knowledge/sales/faqs",
            headers=_auth_headers(token_b),
            json=_sample_faq("shared-id", "B-only answer"),
        )

        list_a = client.get("/knowledge/faqs", headers=_auth_headers(token_a)).json()
        list_b = client.get("/knowledge/faqs", headers=_auth_headers(token_b)).json()

    titles_a = {row["title_en"] for row in list_a}
    titles_b = {row["title_en"] for row in list_b}

    # Same external id in both companies, but each owner sees only their own.
    assert titles_a == {"A-only answer"}
    assert titles_b == {"B-only answer"}


def test_cross_company_get_returns_404(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_owner(db, auth_service, company_a, "owner-a2@test.local")
    _, token_b = _make_owner(db, auth_service, company_b, "owner-b2@test.local")

    with TestClient(app) as client:
        client.post(
            "/knowledge/sales/faqs",
            headers=_auth_headers(token_a),
            json=_sample_faq("a-secret"),
        )
        # Company B must not be able to read Company A's row by its id.
        response = client.get(
            "/knowledge/sales/faqs/a-secret", headers=_auth_headers(token_b)
        )

    assert response.status_code == 404


def test_cross_company_delete_does_not_touch_other_company(fresh_env):
    db, auth_service = fresh_env
    from main import app
    from core.knowledge_manager import knowledge_manager

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_owner(db, auth_service, company_a, "owner-a3@test.local")
    _, token_b = _make_owner(db, auth_service, company_b, "owner-b3@test.local")

    with TestClient(app) as client:
        client.post(
            "/knowledge/sales/faqs",
            headers=_auth_headers(token_a),
            json=_sample_faq("shared-id", "A answer"),
        )
        client.post(
            "/knowledge/sales/faqs",
            headers=_auth_headers(token_b),
            json=_sample_faq("shared-id", "B answer"),
        )

        # Company B deletes ITS row with the shared external id.
        deleted = client.delete(
            "/knowledge/sales/faqs/shared-id", headers=_auth_headers(token_b)
        )
        assert deleted.status_code == 200

    # Company A's identically-keyed row must still be intact.
    a_rows = knowledge_manager.list_all_faqs(company_a)
    b_rows = knowledge_manager.list_all_faqs(company_b)
    assert {row["title_en"] for row in a_rows} == {"A answer"}
    assert b_rows == []


def test_create_and_edit_roundtrip_same_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_owner(db, auth_service, company, "owner-rt@test.local")

    with TestClient(app) as client:
        headers = _auth_headers(token)
        client.post(
            "/knowledge/information/faqs",
            headers=headers,
            json=_sample_faq("faq-rt", "Original"),
        )

        # Edit: same id, moved to a new department and disabled.
        edited = _sample_faq("faq-rt", "Updated")
        edited["enabled"] = False
        client.post("/knowledge/sales/faqs", headers=headers, json=edited)

        rows = client.get("/knowledge/faqs", headers=headers).json()

    # Still a single row (updated in place, not duplicated), now under sales.
    assert len(rows) == 1
    row = rows[0]
    assert row["title_en"] == "Updated"
    assert row["department"] == "sales"
    assert row["enabled"] is False
