"""
Real tests for Saved Replies — company-scoped reusable reply snippets
that employees insert/manage from within the conversation screen.

Run with: python3 -m pytest tests/test_saved_replies.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.saved_reply_service import saved_reply_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    saved_reply_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 1, ?, 'active')",
            (owner_role_id,),
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

    app.dependency_overrides.clear()
    db.db_path = original_db_path
    import gc
    gc.collect()
    for _attempt in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def test_create_and_list_saved_reply(client_and_db):
    client = client_and_db
    create_resp = client.post(
        "/api/saved-replies", json={"title": "Greeting", "body": "Hi! How can I help you today?"},
    )
    assert create_resp.status_code == 200, create_resp.text

    list_resp = client.get("/api/saved-replies")
    assert list_resp.status_code == 200
    titles = [r["title"] for r in list_resp.json()["replies"]]
    assert "Greeting" in titles


def test_create_requires_title_and_body(client_and_db):
    client = client_and_db
    resp = client.post("/api/saved-replies", json={"title": "  ", "body": "text"})
    assert resp.status_code == 400


def test_update_saved_reply(client_and_db):
    client = client_and_db
    create_resp = client.post("/api/saved-replies", json={"title": "Old", "body": "old body"})
    reply_id = create_resp.json()["id"]

    update_resp = client.patch(f"/api/saved-replies/{reply_id}", json={"title": "New title"})
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["title"] == "New title"
    assert body["body"] == "old body"  # untouched field kept


def test_delete_saved_reply(client_and_db):
    client = client_and_db
    create_resp = client.post("/api/saved-replies", json={"title": "Temp", "body": "delete me"})
    reply_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/api/saved-replies/{reply_id}")
    assert delete_resp.status_code == 200

    list_resp = client.get("/api/saved-replies")
    ids = [r["id"] for r in list_resp.json()["replies"]]
    assert reply_id not in ids


def test_delete_nonexistent_reply_returns_404(client_and_db):
    client = client_and_db
    resp = client.delete("/api/saved-replies/99999")
    assert resp.status_code == 404


def test_saved_replies_are_scoped_per_company(client_and_db):
    from database.database import db
    from backend.services.saved_reply_service import saved_reply_service

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    saved_reply_service.create(company_id=2, title="Other Co Reply", body="not mine", actor_user_id=None)

    client = client_and_db
    resp = client.get("/api/saved-replies")
    titles = [r["title"] for r in resp.json()["replies"]]
    assert "Other Co Reply" not in titles


def test_saved_reply_supports_department_scoping(client_and_db):
    client = client_and_db
    client.post("/api/saved-replies", json={"title": "Sales Pitch", "body": "text", "department": "Sales"})
    client.post("/api/saved-replies", json={"title": "General Greeting", "body": "hi"})

    resp = client.get("/api/saved-replies", params={"department": "Sales"})
    titles = [r["title"] for r in resp.json()["replies"]]
    assert titles == ["Sales Pitch"]


def test_plain_employee_cannot_create_or_manage_saved_replies(client_and_db):
    from main import app
    from backend.services.auth_service import get_current_user
    from database.database import db

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'employee@test.local', 'Employee', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 2, 'active')")
        conn.commit()

    client = client_and_db

    async def _override_employee():
        return {"id": 2, "email": "employee@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override_employee

    try:
        create_resp = client.post("/api/saved-replies", json={"title": "Nope", "body": "text"})
        assert create_resp.status_code == 403

        list_resp = client.get("/api/saved-replies")
        assert list_resp.status_code == 200
        assert list_resp.json()["can_manage"] is False
    finally:
        async def _override_owner():
            return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
        app.dependency_overrides[get_current_user] = _override_owner
