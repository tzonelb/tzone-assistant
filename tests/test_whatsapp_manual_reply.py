"""
Real test for sending a manual reply through WhatsApp from the
dashboard (POST /conversations/whatsapp/{user_id}/reply). Before this
fix, "whatsapp" was missing from manual_messages.py's SUPPORTED_CHANNELS
entirely, so an employee who took over a WhatsApp conversation and hit
Send got a 400 "Manual sending currently supports only Messenger,
Instagram, and Telegram" - despite WhatsApp being a fully working
inbound channel. Mirrors tests/test_telegram_manual_reply.py's pattern.

Run with: python3 -m pytest tests/test_whatsapp_manual_reply.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1
CHANNEL = "whatsapp"
CUSTOMER_ID = "96170123456"
EMPLOYEE_ID = 101


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
    from backend.services.message_status_service import message_status_service
    message_status_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) "
            "VALUES (?, 'whatsapp_emp@test.local', 'WhatsApp Employee', 'active')",
            (EMPLOYEE_ID,),
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')")
        conn.execute(
            "INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system) "
            "VALUES (1, 'Owner', 'owner', 'Full access', 1)"
        )
        owner_role_id = conn.execute(
            "SELECT id FROM roles WHERE company_id = 1 AND code = 'owner'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, role_id, status) VALUES (1, ?, ?, 'active')",
            (EMPLOYEE_ID, owner_role_id),
        )
        conn.commit()

    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=EMPLOYEE_ID,
    )

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {
            "id": EMPLOYEE_ID,
            "email": "whatsapp_emp@test.local",
            "is_super_admin": False,
            "active_company_id": COMPANY_ID,
        }
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


def test_whatsapp_channel_is_accepted_by_validation(client_and_db):
    """Regression guard: whatsapp used to be rejected with 400 before
    this fix - SUPPORTED_CHANNELS must include it."""
    client = client_and_db
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = '{"messages": [{"id": "wamid.ABC"}]}'
    fake_response.json.return_value = {"messages": [{"id": "wamid.ABC"}]}

    with patch("channels.whatsapp.sender.config.WHATSAPP_PHONE_NUMBER_ID", "999999999"), \
         patch("channels.whatsapp.sender.config.WHATSAPP_ACCESS_TOKEN", "fake-token"), \
         patch("channels.whatsapp.sender.httpx.post", return_value=fake_response) as mock_post:
        resp = client.post(
            f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply",
            json={"text": "Your order has shipped."},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["message"]["metadata"]["provider"] == "whatsapp"
    assert body["message"]["metadata"]["provider_message_id"] == "wamid.ABC"

    called_url = mock_post.call_args.args[0]
    called_payload = mock_post.call_args.kwargs["json"]
    assert "/messages" in called_url
    assert called_payload["to"] == CUSTOMER_ID
    assert called_payload["text"]["body"] == "Your order has shipped."


def test_whatsapp_send_failure_returns_502_not_a_silent_success(client_and_db):
    client = client_and_db
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.text = '{"error": {"message": "Session has expired."}}'
    fake_response.json.return_value = {"error": {"message": "Session has expired."}}

    with patch("channels.whatsapp.sender.config.WHATSAPP_PHONE_NUMBER_ID", "999999999"), \
         patch("channels.whatsapp.sender.config.WHATSAPP_ACCESS_TOKEN", "fake-token"), \
         patch("channels.whatsapp.sender.httpx.post", return_value=fake_response):
        resp = client.post(
            f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply",
            json={"text": "hi"},
        )

    assert resp.status_code == 502
    assert "Session has expired" in resp.json()["detail"]
