"""
Real tests for AI Instructions — company-scoped behavioral rules,
distinct from Knowledge (facts). Covers CRUD, ordering, isolation, and
the wiring into the actual AI prompt.

Run with: python3 -m pytest tests/test_instructions.py -v
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
    from core.instruction_service import instruction_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    instruction_service.ensure_schema()

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


def test_create_and_list_instruction(client_and_db):
    client = client_and_db
    resp = client.post("/api/instructions", json={"text": "Don't share prices"})
    assert resp.status_code == 200, resp.text

    list_resp = client.get("/api/instructions")
    texts = [i["text"] for i in list_resp.json()["instructions"]]
    assert "Don't share prices" in texts


def test_create_requires_text(client_and_db):
    client = client_and_db
    resp = client.post("/api/instructions", json={"text": "   "})
    assert resp.status_code == 400


def test_new_instructions_append_in_order(client_and_db):
    client = client_and_db
    client.post("/api/instructions", json={"text": "First rule"})
    client.post("/api/instructions", json={"text": "Second rule"})
    client.post("/api/instructions", json={"text": "Third rule"})

    resp = client.get("/api/instructions")
    texts = [i["text"] for i in resp.json()["instructions"]]
    assert texts == ["First rule", "Second rule", "Third rule"]


def test_update_instruction(client_and_db):
    client = client_and_db
    create_resp = client.post("/api/instructions", json={"text": "Old rule"})
    instruction_id = create_resp.json()["id"]

    update_resp = client.patch(f"/api/instructions/{instruction_id}", json={"text": "New rule"})
    assert update_resp.status_code == 200
    assert update_resp.json()["text"] == "New rule"


def test_delete_instruction(client_and_db):
    client = client_and_db
    create_resp = client.post("/api/instructions", json={"text": "Temp rule"})
    instruction_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/api/instructions/{instruction_id}")
    assert delete_resp.status_code == 200

    list_resp = client.get("/api/instructions")
    ids = [i["id"] for i in list_resp.json()["instructions"]]
    assert instruction_id not in ids


def test_reorder_instructions(client_and_db):
    client = client_and_db
    r1 = client.post("/api/instructions", json={"text": "A"}).json()
    r2 = client.post("/api/instructions", json={"text": "B"}).json()
    r3 = client.post("/api/instructions", json={"text": "C"}).json()

    resp = client.post("/api/instructions/reorder", json={"ordered_ids": [r3["id"], r1["id"], r2["id"]]})
    assert resp.status_code == 200
    texts = [i["text"] for i in resp.json()["instructions"]]
    assert texts == ["C", "A", "B"]


def test_instructions_are_isolated_per_company(client_and_db):
    from database.database import db
    from core.instruction_service import instruction_service

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()
    instruction_service.create(company_id=2, text="Other company's rule")

    client = client_and_db
    resp = client.get("/api/instructions")
    texts = [i["text"] for i in resp.json()["instructions"]]
    assert "Other company's rule" not in texts


def test_list_texts_for_ai_returns_ordered_strings_only():
    """This is what actually gets threaded into the AI prompt — plain
    ordered strings, no metadata."""
    from database.database import db
    from core.instruction_service import instruction_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    instruction_service.ensure_schema()

    try:
        with db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (5, 'Co', 'co', 1)")
            conn.commit()
        instruction_service.create(company_id=5, text="Use emojis when appropriate")
        instruction_service.create(company_id=5, text="Don't send follow-up messages")

        texts = instruction_service.list_texts_for_ai(5)
        assert texts == ["Use emojis when appropriate", "Don't send follow-up messages"]
        assert instruction_service.list_texts_for_ai(None) == []
        assert instruction_service.list_texts_for_ai(999) == []
    finally:
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


def test_instruction_tags_scope_matching():
    """Instructions can now be scoped by any tag (channel, department,
    or custom) — an instruction with no tags still applies everywhere."""
    from database.database import db
    from core.instruction_service import instruction_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    instruction_service.ensure_schema()

    try:
        with db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (6, 'Co', 'co', 1)")
            conn.commit()

        instruction_service.create(company_id=6, text="Always be polite")  # no tags -> everywhere
        instruction_service.create(company_id=6, text="WhatsApp: keep replies under 100 chars", tags=["whatsapp"])
        instruction_service.create(company_id=6, text="Sales dept: always mention the discount", tags=["sales"])

        whatsapp_texts = instruction_service.list_texts_for_ai(6, context_tags=["whatsapp"])
        assert "Always be polite" in whatsapp_texts
        assert "WhatsApp: keep replies under 100 chars" in whatsapp_texts
        assert "Sales dept: always mention the discount" not in whatsapp_texts

        telegram_texts = instruction_service.list_texts_for_ai(6, context_tags=["telegram"])
        assert "Always be polite" in telegram_texts
        assert "WhatsApp: keep replies under 100 chars" not in telegram_texts
    finally:
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


def test_ai_router_includes_instructions_in_prompt():
    """Confirms instructions actually reach the prompt sent to the AI —
    not just stored, but genuinely wired into the system."""
    from unittest.mock import patch, MagicMock
    from core.ai_router import ai_router

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "choices": [{"message": {"content": '{"reply": "ok", "department": "information", "intent": "x", "topic": "x", "language": "en", "confidence": 0.9, "buttons": [], "needs_human": false, "notes": ""}'}}]
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.ai_router.config.AI_ENABLED", True), \
         patch("core.ai_router.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.ai_router.httpx.Client", return_value=mock_client):
        ai_router.route(
            message="hi", channel="messenger", user_id="cust-1",
            company_id=1, instructions=["Never mention competitors", "Always be polite"],
        )

    sent_kwargs = mock_client.__enter__.return_value.post.call_args.kwargs
    sent_text = str(sent_kwargs.get("json") or {})
    assert "Never mention competitors" in sent_text
    assert "Always be polite" in sent_text
