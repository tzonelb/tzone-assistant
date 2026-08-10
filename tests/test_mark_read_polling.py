"""
Real test for GET /conversations/{channel}/{user_id}'s mark_read query
param. Before this fix, every fetch of this endpoint unconditionally
cleared unread_count for the owning employee - including the 3-second
background poll ConversationDetailPage.jsx runs while a conversation is
open. That meant clicking "mark as unread" while still viewing the
conversation got silently undone by the very next poll tick.

Run with: python3 -m pytest tests/test_mark_read_polling.py -v
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
CHANNEL = "telegram"
CUSTOMER_ID = "mark-read-test-customer"
OWNER_ID = 1


@pytest.fixture()
def client_and_db(tmp_path):
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    import core.conversation_store as conversation_store

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    original_base_dir = conversation_store.BASE_DIR
    conversation_store.BASE_DIR = tmp_path / "conversations"
    conversation_store.BASE_DIR.mkdir(parents=True, exist_ok=True)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    company_settings_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'owner@test.local', 'Owner', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute("SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'").fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, ?, ?, 'active')",
            (OWNER_ID, owner_role_id),
        )
        conn.commit()

    # Owner owns the conversation and it's marked unread (simulating the
    # explicit "mark as unread" action).
    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=OWNER_ID,
    )
    conversation_control_service.update_workspace_state(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        actor_user_id=OWNER_ID, is_unread=True,
    )
    # A message must exist for the GET endpoint to find the conversation.
    import core.conversation_store as store
    store.save_conversation_message(company_id=COMPANY_ID, channel=CHANNEL, user_id=CUSTOMER_ID, direction="in", text="hi", metadata={})

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {"id": OWNER_ID, "email": "owner@test.local", "is_super_admin": False, "active_company_id": COMPANY_ID}
    app.dependency_overrides[get_current_user] = _override

    yield TestClient(app)

    app.dependency_overrides.clear()
    conversation_store.BASE_DIR = original_base_dir
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


def test_mark_read_false_does_not_clear_unread(client_and_db):
    from backend.services.conversation_control_service import conversation_control_service

    client = client_and_db
    resp = client.get(f"/conversations/{CHANNEL}/{CUSTOMER_ID}", params={"mark_read": "false"})
    assert resp.status_code == 200, resp.text

    state = conversation_control_service.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert state["unread_count"] > 0


def test_mark_read_default_clears_unread(client_and_db):
    from backend.services.conversation_control_service import conversation_control_service

    client = client_and_db
    resp = client.get(f"/conversations/{CHANNEL}/{CUSTOMER_ID}")
    assert resp.status_code == 200, resp.text

    state = conversation_control_service.get_state(company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID)
    assert state["unread_count"] == 0
