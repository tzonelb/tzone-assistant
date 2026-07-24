"""
Real test for sending a manual reply through Telegram from the dashboard
(POST /conversations/telegram/{user_id}/reply). Mocks the outbound HTTP
call to Telegram's API and isolates file-based conversation storage to a
temp dir — no real network calls, no writes into the real data/ folder.

Run with: python3 -m pytest tests/test_telegram_manual_reply.py -v
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
CHANNEL = "telegram"
CUSTOMER_ID = "999888777"
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

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) "
            "VALUES (?, 'telegram_emp@test.local', 'Telegram Employee', 'active')",
            (EMPLOYEE_ID,),
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')")
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, ?, 'active')",
            (EMPLOYEE_ID,),
        )
        conn.commit()

    # Employee takes over the conversation first (manual reply requires
    # ownership + human handling, matching Messenger's exact rule).
    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id=CUSTOMER_ID,
        handled_by_ai=False, actor_user_id=EMPLOYEE_ID,
    )

    from main import app
    from backend.services.auth_service import get_current_user

    async def _override():
        return {
            "id": EMPLOYEE_ID,
            "email": "telegram_emp@test.local",
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


def test_telegram_reply_sends_via_bot_and_records_correct_provider(client_and_db):
    client = client_and_db

    fake_telegram_response = MagicMock()
    fake_telegram_response.json.return_value = {
        "ok": True,
        "result": {"message_id": 4242},
    }

    with patch("channels.telegram.sender.config.TELEGRAM_BOT_TOKEN", "fake-token-for-tests"), \
         patch("channels.telegram.sender.requests.post", return_value=fake_telegram_response) as mock_post:
        resp = client.post(
            f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply",
            json={"text": "Your IPTV subscription has been renewed."},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["message"]["metadata"]["provider"] == "telegram"
    assert body["message"]["metadata"]["provider_message_id"] == 4242

    # Confirm the bot API was actually called with the right chat/text.
    called_url = mock_post.call_args.args[0]
    called_payload = mock_post.call_args.kwargs["json"]
    assert "sendMessage" in called_url
    assert called_payload["chat_id"] == CUSTOMER_ID
    assert called_payload["text"] == "Your IPTV subscription has been renewed."


def test_telegram_channel_is_accepted_by_validation(client_and_db):
    """Regression guard: telegram used to be rejected with 400 before
    this integration — SUPPORTED_CHANNELS must include it."""
    client = client_and_db
    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    with patch("channels.telegram.sender.config.TELEGRAM_BOT_TOKEN", "fake-token-for-tests"), \
         patch("channels.telegram.sender.requests.post", return_value=fake_response):
        resp = client.post(
            f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply",
            json={"text": "hi"},
        )
    assert resp.status_code == 200, resp.text
