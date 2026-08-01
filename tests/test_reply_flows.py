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
    from backend.services.reply_flow_service import reply_flow_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    reply_flow_service.ensure_schema()

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


def test_create_and_list_flow(client_and_db):
    client = client_and_db
    resp = client.post("/api/reply-flows", json={"name": "Sales WhatsApp", "channel": "whatsapp", "department": "Sales"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["nodes"] == []

    list_resp = client.get("/api/reply-flows")
    names = [f["name"] for f in list_resp.json()["flows"]]
    assert "Sales WhatsApp" in names


def test_create_requires_name(client_and_db):
    resp = client_and_db.post("/api/reply-flows", json={"name": "  "})
    assert resp.status_code == 400


def test_update_saves_nodes_and_edges(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()

    nodes = [
        {"id": "n1", "type": "custom", "position": {"x": 0, "y": 0}, "data": {"nodeType": "greeting", "label": "Greeting"}},
        {"id": "n2", "type": "custom", "position": {"x": 200, "y": 0}, "data": {"nodeType": "ai_knowledge_plus", "label": "AI+Knowledge"}},
    ]
    edges = [{"id": "e1", "source": "n1", "target": "n2"}]
    resp = client.patch(f"/api/reply-flows/{flow['id']}", json={"nodes": nodes, "edges": edges})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["nodes"]) == 2
    assert body["edges"][0]["source"] == "n1"


def test_update_rejects_invalid_node_type(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    bad_nodes = [{"id": "n1", "data": {"nodeType": "not_a_real_type"}}]
    resp = client.patch(f"/api/reply-flows/{flow['id']}", json={"nodes": bad_nodes})
    assert resp.status_code == 400


def test_update_status_transitions(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    resp = client.patch(f"/api/reply-flows/{flow['id']}", json={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    bad = client.patch(f"/api/reply-flows/{flow['id']}", json={"status": "not_a_status"})
    assert bad.status_code == 400


def test_delete_flow(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Temp"}).json()
    resp = client.delete(f"/api/reply-flows/{flow['id']}")
    assert resp.status_code == 200

    get_resp = client.get(f"/api/reply-flows/{flow['id']}")
    assert get_resp.status_code == 404


def test_duplicate_flow_copies_graph(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Original"}).json()
    nodes = [{"id": "n1", "data": {"nodeType": "greeting"}}]
    client.patch(f"/api/reply-flows/{flow['id']}", json={"nodes": nodes})

    dup = client.post(f"/api/reply-flows/{flow['id']}/duplicate")
    assert dup.status_code == 200, dup.text
    body = dup.json()
    assert body["name"] == "Original (copy)"
    assert body["status"] == "draft"
    assert len(body["nodes"]) == 1


def test_flows_isolated_per_company(client_and_db):
    from database.database import db
    from backend.services.reply_flow_service import reply_flow_service

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()
    reply_flow_service.create(company_id=2, name="Other Co Flow", actor_user_id=None)

    resp = client_and_db.get("/api/reply-flows")
    names = [f["name"] for f in resp.json()["flows"]]
    assert "Other Co Flow" not in names


def test_plain_employee_cannot_manage_flows(client_and_db):
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
        resp = client.post("/api/reply-flows", json={"name": "Nope"})
        assert resp.status_code == 403
        # But listing/viewing (read-only) stays open to any company member.
        list_resp = client.get("/api/reply-flows")
        assert list_resp.status_code == 200
    finally:
        async def _override_owner():
            return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
        app.dependency_overrides[get_current_user] = _override_owner
