"""
Real tests for WhatsApp's unified pipeline integration — the same
conversation_control_service + AI-batching pipeline Messenger and
Telegram already use, instead of the old standalone core.engine path.

Run with: python3 -m pytest tests/test_whatsapp_incoming.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
CUSTOMER_ID = "96170123456"
LEGACY_PHONE_NUMBER_ID = "999999999"


@pytest.fixture()
def fresh_env(tmp_path, monkeypatch):
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.company_settings_service import company_settings_service
    from backend.services.notification_service import notification_service
    from backend.services.customer_service import customer_service
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
    notification_service.ensure_schema()
    customer_service.ensure_schema()

    monkeypatch.setattr("config.settings.config.WHATSAPP_PHONE_NUMBER_ID", LEGACY_PHONE_NUMBER_ID)

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.commit()

    yield

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


def test_incoming_whatsapp_message_creates_conversation_state(fresh_env):
    from channels.whatsapp.processor import process_whatsapp_message
    from backend.services.conversation_control_service import conversation_control_service

    process_whatsapp_message(
        user_id=CUSTOMER_ID, text="Hello, need help", recipient_phone_number_id=LEGACY_PHONE_NUMBER_ID,
        customer_name="Jean Test",
    )

    state = conversation_control_service.get_state(
        company_id=COMPANY_ID, channel="whatsapp", external_user_id=CUSTOMER_ID,
    )
    assert state is not None


def test_incoming_whatsapp_message_creates_notification(fresh_env):
    from channels.whatsapp.processor import process_whatsapp_message
    from backend.services.notification_service import notification_service

    process_whatsapp_message(
        user_id=CUSTOMER_ID, text="Hello", recipient_phone_number_id=LEGACY_PHONE_NUMBER_ID,
    )

    notifications = notification_service.list_for_user(company_id=COMPANY_ID, user_id=1)
    matching = [n for n in notifications if n["channel"] == "whatsapp" and n["external_user_id"] == CUSTOMER_ID]
    assert len(matching) == 1


def test_incoming_whatsapp_message_queues_ai_reply(fresh_env):
    from channels.whatsapp.processor import process_whatsapp_message

    result = process_whatsapp_message(
        user_id=CUSTOMER_ID, text="Hi", recipient_phone_number_id=LEGACY_PHONE_NUMBER_ID,
    )
    assert result["queue_result"]["queued"] is True
    assert result["company_id"] == COMPANY_ID


def test_whatsapp_message_resolves_correct_company_by_connected_number(fresh_env):
    from database.database import db
    from channels.whatsapp.processor import process_whatsapp_message
    from backend.services.channel_account_service import channel_account_service

    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (2, 'Other Co', 'other-co', 1)")
        conn.commit()

    fake_response = MagicMock()
    fake_response.json.return_value = {"display_phone_number": "+96170000000", "verified_name": "Other Co Support"}
    with patch("backend.services.channel_account_service.requests.get", return_value=fake_response):
        channel_account_service.connect_whatsapp(
            company_id=2, phone_number_id="555000111", access_token="tok",
        )

    result = process_whatsapp_message(
        user_id=CUSTOMER_ID, text="Hi from other co customer", recipient_phone_number_id="555000111",
    )
    assert result["company_id"] == 2


def test_whatsapp_webhook_ignores_unknown_phone_number(fresh_env):
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        resp = client.post("/webhook/whatsapp/", json={
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "123456123"},
                "messages": [{"type": "text", "from": CUSTOMER_ID, "text": {"body": "hi"}}],
            }}]}]
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_whatsapp_webhook_accepts_legacy_configured_number(fresh_env):
    from fastapi.testclient import TestClient
    from main import app

    with patch("channels.whatsapp.processor.schedule_smart_reply", return_value={"queued": True}):
        with TestClient(app) as client:
            resp = client.post("/webhook/whatsapp/", json={
                "entry": [{"changes": [{"value": {
                    "metadata": {"phone_number_id": LEGACY_PHONE_NUMBER_ID},
                    "messages": [{"type": "text", "from": CUSTOMER_ID, "text": {"body": "hi there"}}],
                    "contacts": [{"profile": {"name": "Jean"}}],
                }}]}]
            })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "received"
