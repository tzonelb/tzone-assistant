"""Regression tests for the Scheduler API company-scoping, RBAC gate,
and post-approval status workflow.

The Scheduler API (backend/api/routes/scheduler.py) manages a company's
social post drafting/approval/publishing workflow. It must enforce:

  1. Multi-tenant isolation: a user in company A can never list, read,
     update, transition or delete a post that belongs to company B.
  2. RBAC: viewing requires "scheduler.view"; creating, editing,
     approving, publishing, cancelling and deleting require
     "scheduler.manage".
  3. Status workflow: draft -> scheduled (approve; requires
     scheduled_at) -> published (manual confirmation) or cancelled.
     Illegal transitions (e.g. draft -> published directly, editing a
     published post) are rejected with 422.

These tests also cover the optimistic-concurrency guard (stale
`expected_updated_at` -> 409).

Run with: python3 -m pytest tests/test_scheduler_company_scoping.py -v
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


def _post_payload(title="Eid promotion", **overrides):
    payload = {
        "title": title,
        "content": "Special offer this week only!",
        "channel": "facebook",
        "scheduled_at": "2026-09-01T10:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/scheduler").status_code == 401
        assert client.get("/api/scheduler/1").status_code == 401
        assert client.post("/api/scheduler", json=_post_payload()).status_code == 401
        assert client.put("/api/scheduler/1", json={"title": "x"}).status_code == 401
        assert (
            client.post("/api/scheduler/1/status", json={"status": "scheduled"}).status_code
            == 401
        )
        assert client.delete("/api/scheduler/1").status_code == 401


def test_view_without_permission_is_403(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        assert client.get("/api/scheduler", headers=_headers(token)).status_code == 403


def test_manage_permission_required_for_writes(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    _make_role(db, company, "viewer", "Viewer", ["scheduler.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")

    with TestClient(app) as client:
        created = client.post(
            "/api/scheduler", headers=_headers(owner_token), json=_post_payload()
        )
        assert created.status_code == 201
        post_id = created.json()["id"]

        assert client.get("/api/scheduler", headers=_headers(viewer_token)).status_code == 200
        assert (
            client.post(
                "/api/scheduler", headers=_headers(viewer_token), json=_post_payload()
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/scheduler/{post_id}",
                headers=_headers(viewer_token),
                json={"title": "Hacked"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/scheduler/{post_id}/status",
                headers=_headers(viewer_token),
                json={"status": "scheduled"},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/scheduler/{post_id}", headers=_headers(viewer_token)
            ).status_code
            == 403
        )


def test_list_is_scoped_to_caller_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/scheduler", headers=_headers(token_a), json=_post_payload("Company A Post")
        )
        assert created_a.status_code == 201
        post_id_a = created_a.json()["id"]

        listed_b = client.get("/api/scheduler", headers=_headers(token_b))
        ids_b = {item["id"] for item in listed_b.json()["items"]}
        assert post_id_a not in ids_b

        assert (
            client.get(f"/api/scheduler/{post_id_a}", headers=_headers(token_b)).status_code
            == 404
        )
        assert (
            client.put(
                f"/api/scheduler/{post_id_a}",
                headers=_headers(token_b),
                json={"title": "Stolen"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/scheduler/{post_id_a}/status",
                headers=_headers(token_b),
                json={"status": "scheduled"},
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/scheduler/{post_id_a}", headers=_headers(token_b)
            ).status_code
            == 404
        )


def test_status_workflow_happy_path(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    user_id, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/scheduler", headers=_headers(token), json=_post_payload()
        )
        assert created.status_code == 201
        body = created.json()
        post_id = body["id"]
        assert body["status"] == "draft"

        approved = client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "scheduled"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "scheduled"
        assert approved.json()["approved_by"] == user_id

        published = client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "published"},
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"
        assert published.json()["published_at"]


def test_illegal_transitions_are_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner3@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/scheduler", headers=_headers(token), json=_post_payload()
        )
        post_id = created.json()["id"]

        # draft -> published directly is illegal (must be approved first).
        direct_publish = client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "published"},
        )
        assert direct_publish.status_code == 422

        # Approving a post with no scheduled_at is rejected.
        no_time = client.post(
            "/api/scheduler",
            headers=_headers(token),
            json=_post_payload("No time", scheduled_at=None),
        )
        no_time_id = no_time.json()["id"]
        approve_no_time = client.post(
            f"/api/scheduler/{no_time_id}/status",
            headers=_headers(token),
            json={"status": "scheduled"},
        )
        assert approve_no_time.status_code == 422

        # Editing a published post is rejected.
        client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "scheduled"},
        )
        client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "published"},
        )
        edit_published = client.put(
            f"/api/scheduler/{post_id}",
            headers=_headers(token),
            json={"title": "Too late"},
        )
        assert edit_published.status_code == 422

        # A published post cannot transition anywhere.
        cancel_published = client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "cancelled"},
        )
        assert cancel_published.status_code == 422


def test_stale_update_is_rejected_with_409(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner4@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/scheduler", headers=_headers(token), json=_post_payload("Stale Test")
        )
        post_id = created.json()["id"]
        original_updated_at = created.json()["updated_at"]

        first_edit = client.put(
            f"/api/scheduler/{post_id}",
            headers=_headers(token),
            json={"title": "Edited once", "expected_updated_at": original_updated_at},
        )
        assert first_edit.status_code == 200

        stale_edit = client.put(
            f"/api/scheduler/{post_id}",
            headers=_headers(token),
            json={"title": "Edited stale", "expected_updated_at": original_updated_at},
        )
        assert stale_edit.status_code == 409
        body = stale_edit.json()["detail"]
        assert body["current"]["title"] == "Edited once"

        # The status endpoint honors the same concurrency token.
        stale_status = client.post(
            f"/api/scheduler/{post_id}/status",
            headers=_headers(token),
            json={"status": "scheduled", "expected_updated_at": original_updated_at},
        )
        assert stale_status.status_code == 409
