"""
Tests for the AI Teaching Chat feature — permission-gated, and the
OpenAI call is always mocked (never a real network call).
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.ai_teaching_chat_service import ai_teaching_chat_service
    from core.instruction_service import instruction_service
    from core.knowledge_manager import knowledge_manager

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    ai_teaching_chat_service.ensure_schema()
    instruction_service.ensure_schema()
    knowledge_manager.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (2, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 1, NULL, 'active')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, 2, ?, 'active')",
            (owner_role_id,),
        )
        conn.commit()

    from main import app
    from backend.services.auth_service import get_current_user

    state = {"user_id": 1, "is_super_admin": False}

    async def _override():
        return {"id": state["user_id"], "email": "agent@test.local", "is_super_admin": state["is_super_admin"], "active_company_id": COMPANY_ID}
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


def _mock_openai_response(reply="Got it.", instruction=None):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"output_text": f'{{"reply": "{reply}", "instruction": {("null" if instruction is None else chr(34) + instruction + chr(34))}}}'}
    return response


def test_regular_employee_without_permission_is_forbidden(client_and_db):
    client, _state = client_and_db
    resp = client.get("/api/ai-teaching-chat")
    assert resp.status_code == 403


def test_owner_always_has_access(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2
    resp = client.get("/api/ai-teaching-chat")
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


def test_super_admin_always_has_access(client_and_db):
    client, state = client_and_db
    state["is_super_admin"] = True
    resp = client.get("/api/ai-teaching-chat")
    assert resp.status_code == 200


def test_send_message_saves_instruction_when_extracted(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2  # owner, has access
    with patch("backend.services.ai_teaching_chat_service.AITeachingChatService._post_to_openai", return_value=_mock_openai_response(reply="Sure thing!", instruction="Always greet in Arabic first")):
        resp = client.post("/api/ai-teaching-chat", json={"text": "Always greet customers in Arabic first"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instruction_saved"] is True
    assert body["assistant_message"]["text"] == "Sure thing!"

    instructions = client.get("/api/instructions").json()["instructions"]
    assert any(item["text"] == "Always greet in Arabic first" for item in instructions)


def test_send_message_without_instruction_does_not_create_one(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2
    with patch("backend.services.ai_teaching_chat_service.AITeachingChatService._post_to_openai", return_value=_mock_openai_response(reply="Sure, happy to chat!", instruction=None)):
        resp = client.post("/api/ai-teaching-chat", json={"text": "How are you today?"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["instruction_saved"] is False

    instructions = client.get("/api/instructions").json()["instructions"]
    assert instructions == []


def test_send_message_degrades_gracefully_on_openai_failure(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2
    with patch("backend.services.ai_teaching_chat_service.AITeachingChatService._post_to_openai", side_effect=RuntimeError("network down")):
        resp = client.post("/api/ai-teaching-chat", json={"text": "Test message"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instruction_saved"] is False
    assert body["error"] is not None
    assert body["assistant_message"]["text"]


def test_send_rejects_empty_message(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2
    resp = client.post("/api/ai-teaching-chat", json={"text": "   "})
    assert resp.status_code == 400 or resp.status_code == 422


def test_test_reply_requires_permission(client_and_db):
    client, _state = client_and_db
    resp = client.post("/api/ai-teaching-chat/test", json={"message": "hi"})
    assert resp.status_code == 403


def test_test_reply_runs_real_pipeline_and_persists_nothing(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2

    with patch(
        "core.ai_router.ai_router.call_openai",
        return_value={"reply": "You can reach us 9-5.", "department": "information", "language": "en", "confidence": 0.9},
    ) as mock_call:
        resp = client.post(
            "/api/ai-teaching-chat/test",
            json={"message": "What are your hours?", "channel": "website", "department": "sales"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "You can reach us 9-5."
    assert body["department_detected"] == "information"
    assert isinstance(body["knowledge_used"], list)
    assert isinstance(body["instructions_used"], list)
    mock_call.assert_called_once()

    # Nothing should be persisted anywhere from a test run.
    from database.database import db
    with db.connect() as conn:
        conv_count = conn.execute("SELECT COUNT(*) AS c FROM conversations WHERE company_id = 1").fetchone()["c"]
    assert conv_count == 0


def test_test_reply_returns_502_when_ai_unavailable(client_and_db):
    client, state = client_and_db
    state["user_id"] = 2

    with patch("core.ai_router.ai_router.call_openai", side_effect=Exception("network down")):
        resp = client.post("/api/ai-teaching-chat/test", json={"message": "hi"})

    assert resp.status_code == 502
