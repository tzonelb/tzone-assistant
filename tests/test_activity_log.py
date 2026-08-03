"""
Real tests for the company-wide Activity Log — the manager-facing trail
of task/customer/catalogue/broadcast/role actions the user asked for
directly ("صفحة ال log تبعت كل حدث ... يراجعها المدير").
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
    from backend.services.activity_log_service import activity_log_service
    from backend.services.task_service import task_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    activity_log_service.ensure_schema()
    task_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'employee@test.local', 'Employee', 'active', 0)"
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
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 2, 'active')")
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    state = {"user_id": 1}

    async def _override():
        return {"id": state["user_id"], "email": "test@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app), state

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


def test_creating_a_task_records_a_real_activity_entry(client_and_db):
    client, _state = client_and_db
    client.post("/api/tasks", json={"title": "Follow up with customer"})

    resp = client.get("/api/activity-log")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(item["action"] == "task_created" and "Follow up with customer" in item["description"] for item in body["items"])
    assert body["items"][0]["actor_name"] == "Owner"


def test_completing_a_task_records_a_status_change_entry(client_and_db):
    client, _state = client_and_db
    task = client.post("/api/tasks", json={"title": "X"}).json()
    client.put(f"/api/tasks/{task['id']}", json={"status": "done"})

    body = client.get("/api/activity-log").json()
    assert any(item["action"] == "task_status_changed" for item in body["items"])


def test_deleting_a_task_records_an_entry(client_and_db):
    client, _state = client_and_db
    task = client.post("/api/tasks", json={"title": "To delete"}).json()
    client.delete(f"/api/tasks/{task['id']}")

    body = client.get("/api/activity-log").json()
    assert any(item["action"] == "task_deleted" and "To delete" in item["description"] for item in body["items"])


def test_filter_by_action_type(client_and_db):
    client, _state = client_and_db
    task = client.post("/api/tasks", json={"title": "X"}).json()
    client.delete(f"/api/tasks/{task['id']}")

    body = client.get("/api/activity-log?action=task_deleted").json()
    assert all(item["action"] == "task_deleted" for item in body["items"])
    assert len(body["items"]) >= 1


def test_filter_by_actor(client_and_db):
    client, state = client_and_db
    client.post("/api/tasks", json={"title": "Owner's task"})
    state["user_id"] = 2
    client.post("/api/tasks", json={"title": "Employee's task"})
    state["user_id"] = 1  # only an admin/owner can view the log itself

    body = client.get("/api/activity-log?actor_user_id=2").json()
    assert all(item["actor_name"] == "Employee" for item in body["items"])
    assert any("Employee's task" in item["description"] for item in body["items"])


def test_activity_log_requires_admin_permission(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2
    resp = client.get("/api/activity-log")
    assert resp.status_code == 403


def test_activity_log_isolated_per_company(client_and_db):
    from database.database import db
    from backend.services.activity_log_service import activity_log_service

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    activity_log_service.record(
        company_id=2, actor_user_id=None, action="task_created", entity_type="task",
        entity_id=1, description="Other company's task",
    )

    client, _state = client_and_db
    body = client.get("/api/activity-log").json()
    assert all("Other company's task" != item["description"] for item in body["items"])
