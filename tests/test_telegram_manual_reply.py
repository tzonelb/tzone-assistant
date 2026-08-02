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
    from backend.services.message_status_service import message_status_service
    message_status_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) "
            "VALUES (?, 'telegram_emp@test.local', 'Telegram Employee', 'active')",
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


def test_telegram_media_reply_sends_photo_and_saves_media_metadata(client_and_db):
    client = client_and_db

    fake_telegram_response = MagicMock()
    fake_telegram_response.json.return_value = {"ok": True, "result": {"message_id": 777}}

    with patch("channels.telegram.sender.config.TELEGRAM_BOT_TOKEN", "fake-token-for-tests"), \
         patch("channels.telegram.sender.requests.post", return_value=fake_telegram_response) as mock_post:
        resp = client.post(
            f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply-media",
            json={"media_url": "https://cdn.example/photo.jpg", "media_type": "image", "caption": "Here's the invoice"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["message"]["metadata"]["provider"] == "telegram"
    assert body["message"]["metadata"]["media_url"] == "https://cdn.example/photo.jpg"
    assert body["message"]["metadata"]["media_type"] == "image"
    assert body["message"]["text"] == "Here's the invoice"

    called_url = mock_post.call_args.args[0]
    called_payload = mock_post.call_args.kwargs["json"]
    assert "sendPhoto" in called_url
    assert called_payload["photo"] == "https://cdn.example/photo.jpg"


def test_telegram_media_reply_sends_document(client_and_db):
    client = client_and_db

    fake_telegram_response = MagicMock()
    fake_telegram_response.json.return_value = {"ok": True, "result": {"message_id": 778}}

    with patch("channels.telegram.sender.config.TELEGRAM_BOT_TOKEN", "fake-token-for-tests"), \
         patch("channels.telegram.sender.requests.post", return_value=fake_telegram_response) as mock_post:
        resp = client.post(
            f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply-media",
            json={"media_url": "https://cdn.example/invoice.pdf", "media_type": "document"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message"]["metadata"]["media_type"] == "document"

    called_url = mock_post.call_args.args[0]
    called_payload = mock_post.call_args.kwargs["json"]
    assert "sendDocument" in called_url
    assert called_payload["document"] == "https://cdn.example/invoice.pdf"


def test_media_reply_requires_ownership_like_text_reply(client_and_db):
    """The media endpoint must go through the same _prepare_reply gate as
    text - AI still handling the conversation blocks it with 409."""
    from backend.services.conversation_control_service import conversation_control_service

    client = client_and_db
    conversation_control_service.set_ai_mode(
        company_id=COMPANY_ID, channel=CHANNEL, external_user_id="ai-still-handling",
        handled_by_ai=True, actor_user_id=EMPLOYEE_ID,
    )
    resp = client.post(
        f"/conversations/{CHANNEL}/ai-still-handling/reply-media",
        json={"media_url": "https://cdn.example/photo.jpg", "media_type": "image"},
    )
    assert resp.status_code == 409


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


def test_reading_conversation_after_a_reply_does_not_500(client_and_db):
    """Regression guard: GET /conversations/{channel}/{user_id} crashed
    with NameError (message_status_service used but never imported)
    the moment a conversation had an outbound message with a
    provider_message_id in it — i.e. right after any real reply was
    sent. py_compile did not catch this (it's a runtime-only
    NameError), so this test exists specifically to exercise that
    code path end-to-end."""
    client = client_and_db
    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": True, "result": {"message_id": 555}}
    with patch("channels.telegram.sender.config.TELEGRAM_BOT_TOKEN", "fake-token-for-tests"), \
         patch("channels.telegram.sender.requests.post", return_value=fake_response):
        client.post(f"/conversations/{CHANNEL}/{CUSTOMER_ID}/reply", json={"text": "hello"})

    resp = client.get(f"/conversations/{CHANNEL}/{CUSTOMER_ID}")
    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    assert any(m.get("delivery_status") for m in messages)
