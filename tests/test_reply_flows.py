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
    from backend.services.department_service import department_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    department_service.ensure_schema()
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


def _add_department(name="Sales"):
    from backend.services.department_service import department_service
    department_service.create(company_id=COMPANY_ID, name=name)


def test_create_and_list_flow(client_and_db):
    client = client_and_db
    _add_department("Sales")
    resp = client.post(
        "/api/reply-flows",
        json={"name": "Sales WhatsApp", "channels": ["whatsapp"], "departments": ["Sales"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["nodes"] == []
    assert body["channels"] == ["whatsapp"]
    assert body["departments"] == ["Sales"]

    list_resp = client.get("/api/reply-flows")
    names = [f["name"] for f in list_resp.json()["flows"]]
    assert "Sales WhatsApp" in names


def test_create_rejects_invalid_channel(client_and_db):
    resp = client_and_db.post("/api/reply-flows", json={"name": "Flow", "channels": ["fax"]})
    assert resp.status_code == 400


def test_create_rejects_unregistered_department(client_and_db):
    resp = client_and_db.post("/api/reply-flows", json={"name": "Flow", "departments": ["Ghost Dept"]})
    assert resp.status_code == 400


def test_reply_modes_saved_and_validated(client_and_db):
    client = client_and_db
    resp = client.post("/api/reply-flows", json={"name": "Flow", "reply_modes": ["ai_direct", "human_handoff"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply_modes"] == ["ai_direct", "human_handoff"]

    bad = client.post("/api/reply-flows", json={"name": "Flow 2", "reply_modes": ["not_a_mode"]})
    assert bad.status_code == 400


def test_generate_from_text_builds_real_graph(client_and_db):
    from unittest.mock import patch, MagicMock

    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "AI Written Flow"}).json()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "output_text": (
            '{"nodes": ['
            '{"id": "a", "nodeType": "greeting", "label": "Say hi", "config": {"text": "Hi there!"}},'
            '{"id": "b", "nodeType": "ai_direct", "label": "Answer", "config": {"instructions": "Help them out"}}'
            '], "edges": [{"source": "a", "target": "b"}]}'
        )
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("backend.services.reply_flow_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("backend.services.reply_flow_service.httpx.Client", return_value=mock_client):
        resp = client.post(f"/api/reply-flows/{flow['id']}/generate-from-text", json={"text": "Greet the customer then let AI answer."})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["data"]["nodeType"] == "greeting"
    assert body["nodes"][0]["data"]["config"]["text"] == "Hi there!"
    assert len(body["edges"]) == 1
    assert body["edges"][0]["source"] == body["nodes"][0]["id"]
    assert body["edges"][0]["target"] == body["nodes"][1]["id"]


def test_generate_from_text_requires_text(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    resp = client.post(f"/api/reply-flows/{flow['id']}/generate-from-text", json={"text": "  "})
    assert resp.status_code == 400


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


def test_update_rejects_node_with_missing_node_type(client_and_db):
    """A node with no data.nodeType at all (not just an invalid one) must
    still be rejected at save time — otherwise it silently reaches the
    execution engine as an unrecognized node type."""
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    bad_nodes = [{"id": "n1", "position": {"x": 0, "y": 0}, "data": {"label": "No type set"}}]
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
        # Reply Flows are admin-only end-to-end — even listing/viewing is blocked.
        list_resp = client.get("/api/reply-flows")
        assert list_resp.status_code == 403
    finally:
        async def _override_owner():
            return {"id": 1, "email": "agent@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
        app.dependency_overrides[get_current_user] = _override_owner


# -- trigger registry -------------------------------------------------------

def test_new_flow_defaults_to_new_conversation_trigger(client_and_db):
    """Every existing flow (and any flow that doesn't explicitly set a
    trigger) must default to new_conversation — today's exact implicit
    behavior — with zero config."""
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    assert flow["trigger_type"] == "new_conversation"
    assert flow["trigger_config"] == {}


def test_trigger_type_and_config_round_trip_through_update(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    resp = client.patch(
        f"/api/reply-flows/{flow['id']}",
        json={"trigger_type": "appointment_reminder", "trigger_config": {"minutes_before": 45}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trigger_type"] == "appointment_reminder"
    assert body["trigger_config"] == {"minutes_before": 45}

    # Persisted, not just echoed back — a fresh GET must show the same thing.
    get_resp = client.get(f"/api/reply-flows/{flow['id']}")
    assert get_resp.json()["trigger_type"] == "appointment_reminder"
    assert get_resp.json()["trigger_config"] == {"minutes_before": 45}


def test_invalid_trigger_type_rejected(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Flow"}).json()
    resp = client.patch(f"/api/reply-flows/{flow['id']}", json={"trigger_type": "not_a_real_trigger"})
    assert resp.status_code == 400


def test_create_accepts_trigger_type_directly(client_and_db):
    client = client_and_db
    resp = client.post(
        "/api/reply-flows",
        json={"name": "Closed Flow", "trigger_type": "conversation_closed"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["trigger_type"] == "conversation_closed"


def test_duplicate_flow_carries_over_trigger(client_and_db):
    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "Original", "trigger_type": "call_logged"}).json()
    dup = client.post(f"/api/reply-flows/{flow['id']}/duplicate").json()
    assert dup["trigger_type"] == "call_logged"


def test_trigger_types_endpoint_lists_the_first_real_batch(client_and_db):
    client = client_and_db
    resp = client.get("/api/reply-flows/trigger-types")
    assert resp.status_code == 200, resp.text
    keys = {item["key"] for item in resp.json()["trigger_types"]}
    assert keys == {
        "new_conversation", "conversation_closed", "appointment_created",
        "appointment_completed", "appointment_reminder", "call_logged", "task_completed",
        "customer_no_reply", "team_no_reply",
    }
    reminder = next(item for item in resp.json()["trigger_types"] if item["key"] == "appointment_reminder")
    assert reminder["config_fields"][0]["key"] == "minutes_before"
    no_reply = next(item for item in resp.json()["trigger_types"] if item["key"] == "customer_no_reply")
    assert no_reply["config_fields"][0]["key"] == "minutes_of_silence"


def test_generate_from_text_also_sets_trigger(client_and_db):
    from unittest.mock import patch, MagicMock

    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "AI Written Flow"}).json()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "output_text": (
            '{"trigger": {"type": "appointment_completed", "config": {}}, '
            '"nodes": [{"id": "a", "nodeType": "canned_reply", "label": "Ask for rating", '
            '"config": {"text": "Please rate us 1-5"}}], "edges": []}'
        )
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("backend.services.reply_flow_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("backend.services.reply_flow_service.httpx.Client", return_value=mock_client):
        resp = client.post(
            f"/api/reply-flows/{flow['id']}/generate-from-text",
            json={"text": "When an appointment finishes, ask the customer to rate 1 to 5."},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trigger_type"] == "appointment_completed"
    assert len(body["nodes"]) == 1


def test_generate_from_text_ignores_invalid_trigger_without_failing(client_and_db):
    from unittest.mock import patch, MagicMock

    client = client_and_db
    flow = client.post("/api/reply-flows", json={"name": "AI Written Flow"}).json()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "output_text": (
            '{"trigger": {"type": "not_a_real_trigger", "config": {}}, '
            '"nodes": [{"id": "a", "nodeType": "greeting", "label": "Hi", "config": {"text": "Hi!"}}], "edges": []}'
        )
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("backend.services.reply_flow_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("backend.services.reply_flow_service.httpx.Client", return_value=mock_client):
        resp = client.post(f"/api/reply-flows/{flow['id']}/generate-from-text", json={"text": "Greet the customer."})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trigger_type"] == "new_conversation"  # unchanged default, generation still succeeded
    assert len(body["nodes"]) == 1
