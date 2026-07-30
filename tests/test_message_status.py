"""
Real tests for message delivery status tracking (sent/delivered/read
ticks). Covers the service directly plus the WhatsApp/Messenger
webhook status-event handling.

Run with: python3 -m pytest tests/test_message_status.py -v
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture()
def fresh_db():
    from database.database import db
    from backend.services.message_status_service import message_status_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    message_status_service.ensure_schema()

    yield message_status_service

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


def test_record_sent_then_lookup(fresh_db):
    service = fresh_db
    service.record_sent(channel="messenger", provider_message_id="mid-1", company_id=1, recipient_id="cust-1")
    statuses = service.get_statuses(channel="messenger", provider_message_ids=["mid-1"])
    assert statuses["mid-1"] == "sent"


def test_status_upgrades_sent_to_delivered_to_read(fresh_db):
    service = fresh_db
    service.record_sent(channel="whatsapp", provider_message_id="wamid-1", recipient_id="cust-2")
    service.update_status(channel="whatsapp", provider_message_id="wamid-1", status="delivered")
    service.update_status(channel="whatsapp", provider_message_id="wamid-1", status="read")

    statuses = service.get_statuses(channel="whatsapp", provider_message_ids=["wamid-1"])
    assert statuses["wamid-1"] == "read"


def test_status_never_downgrades(fresh_db):
    """A late 'delivered' webhook arriving after we already know a
    message was 'read' should not roll the status backwards."""
    service = fresh_db
    service.record_sent(channel="whatsapp", provider_message_id="wamid-2", recipient_id="cust-3")
    service.update_status(channel="whatsapp", provider_message_id="wamid-2", status="read")
    service.update_status(channel="whatsapp", provider_message_id="wamid-2", status="delivered")

    statuses = service.get_statuses(channel="whatsapp", provider_message_ids=["wamid-2"])
    assert statuses["wamid-2"] == "read"


def test_mark_read_by_watermark_updates_all_undelivered_for_recipient(fresh_db):
    service = fresh_db
    service.record_sent(channel="messenger", provider_message_id="mid-a", recipient_id="cust-4")
    service.record_sent(channel="messenger", provider_message_id="mid-b", recipient_id="cust-4")
    service.record_sent(channel="messenger", provider_message_id="mid-other-cust", recipient_id="cust-5")

    service.mark_read_by_watermark(channel="messenger", recipient_id="cust-4", watermark=1234567890)

    statuses = service.get_statuses(channel="messenger", provider_message_ids=["mid-a", "mid-b", "mid-other-cust"])
    assert statuses["mid-a"] == "read"
    assert statuses["mid-b"] == "read"
    assert statuses["mid-other-cust"] == "sent"  # different recipient, untouched


def test_get_statuses_with_empty_list_returns_empty(fresh_db):
    service = fresh_db
    assert service.get_statuses(channel="telegram", provider_message_ids=[]) == {}


def test_get_statuses_ignores_unknown_ids(fresh_db):
    service = fresh_db
    service.record_sent(channel="telegram", provider_message_id="123", recipient_id="cust-6")
    statuses = service.get_statuses(channel="telegram", provider_message_ids=["123", "999-unknown"])
    assert statuses == {"123": "sent"}


def test_whatsapp_webhook_status_event_updates_status():
    """Full path: a WhatsApp status webhook payload updates the ticks
    without touching the normal message-receiving flow."""
    from fastapi.testclient import TestClient
    from database.database import db
    from backend.services.message_status_service import message_status_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)
    db.create_tables()
    message_status_service.ensure_schema()
    message_status_service.record_sent(channel="whatsapp", provider_message_id="wamid.ABC123", recipient_id="96170000000")

    try:
        from main import app
        with TestClient(app) as client:
            resp = client.post("/webhook/whatsapp/", json={
                "entry": [{"changes": [{"value": {
                    "metadata": {"phone_number_id": "999999999"},
                    "statuses": [{"id": "wamid.ABC123", "status": "delivered"}],
                }}]}]
            })
        assert resp.status_code == 200
        statuses = message_status_service.get_statuses(channel="whatsapp", provider_message_ids=["wamid.ABC123"])
        assert statuses["wamid.ABC123"] == "delivered"
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
