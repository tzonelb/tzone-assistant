"""Regression tests for the Tasks API company-scoping and RBAC gate.

The Tasks API (backend/api/routes/tasks.py) manages tasks/follow-ups/
payments/services/internal cases assigned to team members. It must enforce
two audited properties:

  1. Multi-tenant isolation: a user in company A can never list, read,
     update or delete a task that belongs to company B, even with a
     guessable sequential id. A task also can never be created assigned to a
     user, or linked to a customer, from a different company.
  2. RBAC: viewing (list/get) requires "tasks.view"; creating, editing and
     deleting requires "tasks.manage". A user whose role lacks the required
     code gets 403.

These tests also cover the optimistic-concurrency guard on update: a stale
`expected_updated_at` token is rejected with 409 so two editors can't
silently overwrite each other.

Run with: python3 -m pytest tests/test_tasks_company_scoping.py -v
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
    # The tasks and customers tables both live in the central schema
    # (database.py's _create_platform_tables), already created above by
    # db.create_tables() -- this module does not need the separate
    # customer_service.ensure_schema() (that call, and the module-level
    # `customer_service = CustomerService()` singleton it sits behind, run
    # eagerly at import time against whatever db.db_path was *before* this
    # fixture swaps it, which is a pre-existing landmine in that service --
    # unrelated to tasks, so it is avoided here rather than worked around).
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


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _task_payload(title="Follow up with client", **overrides):
    payload = {"title": title, "description": "Call about renewal."}
    payload.update(overrides)
    return payload


def test_unauthenticated_requests_are_rejected(fresh_env):
    db, _auth = fresh_env
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/tasks").status_code == 401
        assert client.get("/api/tasks/1").status_code == 401
        assert client.post("/api/tasks", json=_task_payload()).status_code == 401
        assert client.put("/api/tasks/1", json={"title": "x"}).status_code == 401
        assert client.delete("/api/tasks/1").status_code == 401


def test_view_without_permission_is_403(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    # Role with no permissions at all -> cannot even view.
    _make_role(db, company, "guest", "Guest", [])
    _, token = _make_user(db, auth_service, company, "guest@test.local", role_code="guest")

    with TestClient(app) as client:
        assert client.get("/api/tasks", headers=_headers(token)).status_code == 403
        assert (
            client.get("/api/tasks/assignable-users", headers=_headers(token)).status_code
            == 403
        )


def test_manage_permission_required_to_create(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    # Role can view tasks but has no tasks.manage.
    _make_role(db, company, "viewer", "Viewer", ["tasks.view"])
    _, token = _make_user(db, auth_service, company, "viewer@test.local", role_code="viewer")

    with TestClient(app) as client:
        # Viewing is allowed for this role...
        assert client.get("/api/tasks", headers=_headers(token)).status_code == 200
        # ...but creating is not.
        create = client.post(
            "/api/tasks", headers=_headers(token), json=_task_payload()
        )
        assert create.status_code == 403


def test_manage_permission_required_to_edit_and_delete(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, owner_token = _make_user(db, auth_service, company, "owner@test.local")
    _make_role(db, company, "viewer", "Viewer", ["tasks.view"])
    _, viewer_token = _make_user(db, auth_service, company, "viewer2@test.local", role_code="viewer")

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks", headers=_headers(owner_token), json=_task_payload()
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        edit = client.put(
            f"/api/tasks/{task_id}",
            headers=_headers(viewer_token),
            json={"title": "Hacked title"},
        )
        assert edit.status_code == 403

        delete = client.delete(
            f"/api/tasks/{task_id}", headers=_headers(viewer_token)
        )
        assert delete.status_code == 403

    with db.connect() as conn:
        row = conn.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["title"] == "Follow up with client"


def test_owner_can_create_and_list(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner2@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks", headers=_headers(token), json=_task_payload("Renew IPTV subscription")
        )
        assert created.status_code == 201
        body = created.json()
        assert body["title"] == "Renew IPTV subscription"
        assert body["status"] == "open"
        assert body["priority"] == "normal"

        listed = client.get("/api/tasks", headers=_headers(token))
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
            "/api/tasks", headers=_headers(token_a), json=_task_payload("Company A task")
        ).json()
        created_b = client.post(
            "/api/tasks", headers=_headers(token_b), json=_task_payload("Company B task")
        ).json()

        response = client.get("/api/tasks", headers=_headers(token_a))

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["items"]}
    assert created_a["id"] in ids
    assert created_b["id"] not in ids


def test_get_cross_company_returns_404(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera2@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb2@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/tasks", headers=_headers(token_a), json=_task_payload()
        ).json()

        response = client.get(f"/api/tasks/{created_a['id']}", headers=_headers(token_b))

    assert response.status_code == 404


def test_update_cross_company_returns_404(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera3@test.local")
    _, token_b = _make_user(db, auth_service, company_b, "ownerb3@test.local")

    with TestClient(app) as client:
        created_a = client.post(
            "/api/tasks", headers=_headers(token_a), json=_task_payload()
        ).json()

        response = client.put(
            f"/api/tasks/{created_a['id']}",
            headers=_headers(token_b),
            json={"title": "hacked"},
        )
        assert response.status_code == 404

        delete_response = client.delete(
            f"/api/tasks/{created_a['id']}", headers=_headers(token_b)
        )
        assert delete_response.status_code == 404

    with db.connect() as conn:
        row = conn.execute(
            "SELECT title FROM tasks WHERE id = ?", (created_a["id"],)
        ).fetchone()
    assert row["title"] == "Follow up with client"


def test_cannot_assign_task_to_user_from_another_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera4@test.local")
    outsider_id, _ = _make_user(db, auth_service, company_b, "outsider@test.local")

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            headers=_headers(token_a),
            json=_task_payload(assignee_user_id=outsider_id),
        )

    assert response.status_code == 422


def test_cannot_link_task_to_customer_from_another_company(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company_a = _make_company(db, "Company A", "company-a")
    company_b = _make_company(db, "Company B", "company-b")

    _, token_a = _make_user(db, auth_service, company_a, "ownera5@test.local")

    now = "2026-01-01T00:00:00+00:00"
    with db.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO customers (
                company_id, display_name, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company_b, "Bob B", now, now, now, now),
        )
        conn.commit()
        other_customer_id = int(cursor.lastrowid)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            headers=_headers(token_a),
            json=_task_payload(related_customer_id=other_customer_id),
        )

    assert response.status_code == 422


def test_stale_update_conflicts_with_409(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner3@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks", headers=_headers(token), json=_task_payload()
        ).json()
        task_id = created["id"]
        stale_token = created["updated_at"]

        first = client.put(
            f"/api/tasks/{task_id}",
            headers=_headers(token),
            json={"status": "in_progress", "expected_updated_at": stale_token},
        )
        assert first.status_code == 200

        second = client.put(
            f"/api/tasks/{task_id}",
            headers=_headers(token),
            json={"status": "done", "expected_updated_at": stale_token},
        )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert isinstance(detail, dict)
    assert detail.get("current", {}).get("status") == "in_progress"


def test_invalid_status_is_rejected(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner4@test.local")

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            headers=_headers(token),
            json=_task_payload(status="not-a-real-status"),
        )

    assert response.status_code == 422


def test_delete_removes_task(fresh_env):
    db, auth_service = fresh_env
    from main import app

    company = _make_company(db, "Company A", "company-a")
    _, token = _make_user(db, auth_service, company, "owner5@test.local")

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks", headers=_headers(token), json=_task_payload()
        ).json()

        deleted = client.delete(f"/api/tasks/{created['id']}", headers=_headers(token))
        assert deleted.status_code == 200

        missing = client.get(f"/api/tasks/{created['id']}", headers=_headers(token))
        assert missing.status_code == 404
