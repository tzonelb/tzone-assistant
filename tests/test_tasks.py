"""
Real tests for the Tasks module: a company's internal task list —
title, description, assignee, due date, priority, status, optionally
linked to a customer.

Run with: python -m pytest tests/test_tasks.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.customer_service import customer_service
    from backend.services.task_service import task_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    customer_service.ensure_schema()
    task_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')"
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    from fastapi.testclient import TestClient
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


def _make_task(client, **overrides):
    payload = {"title": "Follow up with customer"}
    payload.update(overrides)
    resp = client.post("/api/tasks", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_task_defaults_to_open_status(client_and_db):
    client = client_and_db
    task = _make_task(client)
    assert task["status"] == "open"
    assert task["priority"] == "normal"
    assert task["completed_at"] is None


def test_create_task_rejects_empty_title(client_and_db):
    client = client_and_db
    resp = client.post("/api/tasks", json={"title": "   "})
    assert resp.status_code == 400


def test_create_task_rejects_invalid_priority(client_and_db):
    client = client_and_db
    resp = client.post("/api/tasks", json={"title": "Task", "priority": "super-urgent"})
    assert resp.status_code == 400


def test_create_task_rejects_assignee_outside_company(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (99, 'outsider@test.local', 'Outsider', 'active', 0)"
        )
        conn.commit()

    client = client_and_db
    resp = client.post("/api/tasks", json={"title": "Task", "assigned_user_id": 99})
    assert resp.status_code == 400


def test_create_task_with_bad_customer_id_404s(client_and_db):
    client = client_and_db
    resp = client.post("/api/tasks", json={"title": "Task", "customer_id": 99999})
    assert resp.status_code == 404


def test_create_task_links_to_customer(client_and_db):
    from backend.services.customer_service import customer_service
    customer = customer_service.create_customer(company_id=COMPANY_ID, display_name="Rami")

    client = client_and_db
    task = _make_task(client, customer_id=customer["id"])
    assert task["customer_id"] == customer["id"]
    assert task["customer_name"] == "Rami"


def test_list_filters_by_status(client_and_db):
    client = client_and_db
    a = _make_task(client, title="A")
    b = _make_task(client, title="B")
    client.put(f"/api/tasks/{a['id']}", json={"status": "done"})

    resp = client.get("/api/tasks", params={"status": "done"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]

    resp_open = client.get("/api/tasks", params={"status": "open"})
    open_ids = {item["id"] for item in resp_open.json()["items"]}
    assert open_ids == {b["id"]}


def test_list_filters_by_assigned_user_id(client_and_db):
    client = client_and_db
    a = _make_task(client, title="A", assigned_user_id=1)
    _make_task(client, title="B")

    resp = client.get("/api/tasks", params={"assigned_user_id": 1})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == a["id"]
    assert items[0]["assigned_user_name"] == "Agent"


def test_update_status_to_done_sets_completed_at(client_and_db):
    client = client_and_db
    task = _make_task(client)
    assert task["completed_at"] is None

    resp = client.put(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["completed_at"] is not None


def test_update_status_away_from_done_clears_completed_at(client_and_db):
    client = client_and_db
    task = _make_task(client)
    client.put(f"/api/tasks/{task['id']}", json={"status": "done"})

    resp = client.put(f"/api/tasks/{task['id']}", json={"status": "open"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["completed_at"] is None


def test_update_rejects_invalid_status(client_and_db):
    client = client_and_db
    task = _make_task(client)
    resp = client.put(f"/api/tasks/{task['id']}", json={"status": "not-a-real-status"})
    assert resp.status_code == 400


def test_delete_task_removes_it(client_and_db):
    client = client_and_db
    task = _make_task(client)

    resp = client.delete(f"/api/tasks/{task['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    get_resp = client.get(f"/api/tasks/{task['id']}")
    assert get_resp.status_code == 404


def test_tasks_are_isolated_per_company(client_and_db):
    from database.database import db
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    from backend.services.task_service import task_service
    other_task = task_service.create_task(company_id=2, title="Other Co Task")

    client = client_and_db
    list_resp = client.get("/api/tasks")
    assert all(item["id"] != other_task["id"] for item in list_resp.json()["items"])

    get_resp = client.get(f"/api/tasks/{other_task['id']}")
    assert get_resp.status_code == 404

    update_resp = client.put(f"/api/tasks/{other_task['id']}", json={"status": "done"})
    assert update_resp.status_code == 404

    delete_resp = client.delete(f"/api/tasks/{other_task['id']}")
    assert delete_resp.status_code == 404
