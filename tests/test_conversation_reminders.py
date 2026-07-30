"""
Real tests for the conversation reminder / follow-up feature.

Run with: python3 -m pytest tests/test_conversation_reminders.py -v
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

COMPANY_ID = 1
CUSTOMER_ID = "555111222"


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.notification_service import notification_service
    from backend.services.message_status_service import message_status_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    notification_service.ensure_schema()
    message_status_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active')"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name) VALUES (1, 'Test Co')")
        conn.execute(
            "INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')"
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


def _future_iso(minutes=5):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _past_iso(minutes=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def test_set_reminder_on_conversation(client_and_db):
    client = client_and_db
    resp = client.post(
        f"/conversations/telegram/{CUSTOMER_ID}/reminder",
        json={"reminder_at": _future_iso(), "note": "Follow up about the IPTV renewal"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reminder_note"] == "Follow up about the IPTV renewal"


def test_clear_reminder(client_and_db):
    client = client_and_db
    client.post(
        f"/conversations/telegram/{CUSTOMER_ID}/reminder",
        json={"reminder_at": _future_iso(), "note": "test"},
    )
    resp = client.delete(f"/conversations/telegram/{CUSTOMER_ID}/reminder")
    assert resp.status_code == 200
    assert resp.json()["reminder_at"] is None


def test_due_reminder_fires_and_creates_notification(client_and_db):
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.notification_service import notification_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="Overdue follow-up", actor_user_id=1,
    )

    fired = conversation_control_service.check_due_reminders()
    assert len(fired) == 1
    assert fired[0]["reminder_note"] == "Overdue follow-up"

    # Simulating what main.py's reminder_worker does with the fired result:
    notification_service.create(
        company_id=COMPANY_ID, notification_type="conversation_reminder",
        title="Follow up", body="Overdue follow-up", channel="telegram",
        external_user_id=CUSTOMER_ID, conversation_id=fired[0]["id"], severity="info",
    )
    notifications = notification_service.list_for_user(company_id=COMPANY_ID, user_id=1)
    matching = [n for n in notifications if n["notification_type"] == "conversation_reminder"]
    assert len(matching) == 1


def test_due_reminder_only_fires_once(client_and_db):
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="test", actor_user_id=1,
    )

    first_check = conversation_control_service.check_due_reminders()
    second_check = conversation_control_service.check_due_reminders()
    assert len(first_check) == 1
    assert len(second_check) == 0


def test_future_reminder_does_not_fire_yet(client_and_db):
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_future_iso(minutes=60), note="not yet", actor_user_id=1,
    )

    fired = conversation_control_service.check_due_reminders()
    assert len(fired) == 0


def test_setting_a_new_reminder_resets_notified_flag(client_and_db):
    """If a reminder already fired and the employee sets a new one, it
    should be able to fire again — not be silently suppressed by the
    old notified flag."""
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="first", actor_user_id=1,
    )
    conversation_control_service.check_due_reminders()

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="second", actor_user_id=1,
    )
    fired = conversation_control_service.check_due_reminders()
    assert len(fired) == 1
    assert fired[0]["reminder_note"] == "second"


# ---------------------------------------------------------------------
# Auto follow-up (auto-send) tests
# ---------------------------------------------------------------------

AUTO_SEND_TEXT = "Hey! Just checking in about the IPTV renewal."


def test_auto_send_requires_message_text_at_service_level(client_and_db):
    from backend.services.conversation_control_service import conversation_control_service

    with pytest.raises(ValueError):
        conversation_control_service.set_reminder(
            company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
            reminder_at=_future_iso(), note="test", actor_user_id=1,
            auto_send=True, message_text="   ",
        )


def test_auto_send_requires_message_text_at_api_level(client_and_db):
    client = client_and_db
    resp = client.post(
        f"/conversations/telegram/{CUSTOMER_ID}/reminder",
        json={"reminder_at": _future_iso(), "note": "test", "auto_send": True},
    )
    assert resp.status_code == 422, resp.text


def test_api_accepts_auto_send_and_message_text(client_and_db):
    client = client_and_db
    resp = client.post(
        f"/conversations/telegram/{CUSTOMER_ID}/reminder",
        json={
            "reminder_at": _future_iso(),
            "note": "test",
            "auto_send": True,
            "message_text": AUTO_SEND_TEXT,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert bool(body["reminder_auto_send"]) is True
    assert body["reminder_message_text"] == AUTO_SEND_TEXT


def test_due_auto_send_reminder_sends_message_and_records_provider_id(client_and_db):
    """No new customer message since the reminder was armed and the
    conversation is still AI-handled (not human-owned) -> the
    pre-authored text should actually go out, exactly as written."""
    from backend.services.conversation_control_service import conversation_control_service
    from backend.services.message_status_service import message_status_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="Follow up", actor_user_id=1,
        auto_send=True, message_text=AUTO_SEND_TEXT,
    )

    fake_response = {"ok": True, "response": {"result": {"message_id": 987654}}}
    with patch(
        "backend.services.conversation_control_service.send_telegram_text",
        return_value=fake_response,
    ) as mock_send:
        fired = conversation_control_service.check_due_reminders()

    assert len(fired) == 1
    assert fired[0]["auto_send_requested"] is True
    assert fired[0]["auto_send_status"] == "sent"
    assert fired[0]["auto_send_skip_reason"] is None

    mock_send.assert_called_once_with(recipient_id=CUSTOMER_ID, text=AUTO_SEND_TEXT)

    statuses = message_status_service.get_statuses(
        channel="telegram", provider_message_ids=["987654"]
    )
    assert statuses.get("987654") == "sent"


def test_due_auto_send_reminder_skipped_when_customer_already_replied(client_and_db):
    """The whole point of a follow-up is 'the customer went quiet' — if
    they already sent a new message after the reminder was armed, the
    stale pre-written text must NOT go out."""
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="Follow up", actor_user_id=1,
        auto_send=True, message_text=AUTO_SEND_TEXT,
    )

    # Customer sends a new inbound message AFTER the reminder was armed.
    conversation_control_service.record_customer_message(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
    )

    with patch(
        "backend.services.conversation_control_service.send_telegram_text",
    ) as mock_send:
        fired = conversation_control_service.check_due_reminders()

    assert len(fired) == 1
    assert fired[0]["auto_send_requested"] is True
    assert fired[0]["auto_send_status"] == "skipped"
    assert fired[0]["auto_send_skip_reason"] == "customer_already_replied"
    mock_send.assert_not_called()


def test_due_auto_send_reminder_skipped_when_conversation_is_human_owned(client_and_db):
    """A human took over this conversation before the reminder fired —
    an automated send would step on whatever they're doing. Flips the
    ownership columns directly (rather than via set_ai_mode, which pulls
    in company_settings_service — unrelated to what's under test here)
    to isolate exactly the handled_by_ai/ai_enabled check check_due_
    reminders performs."""
    from database.database import db
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="Follow up", actor_user_id=1,
        auto_send=True, message_text=AUTO_SEND_TEXT,
    )

    with db.connect() as conn:
        conn.execute(
            "UPDATE conversations SET handled_by_ai = 0, ai_enabled = 0 "
            "WHERE company_id = ? AND channel = 'telegram' AND external_user_id = ?",
            (COMPANY_ID, CUSTOMER_ID),
        )
        conn.commit()

    with patch(
        "backend.services.conversation_control_service.send_telegram_text",
    ) as mock_send:
        fired = conversation_control_service.check_due_reminders()

    assert len(fired) == 1
    assert fired[0]["auto_send_requested"] is True
    assert fired[0]["auto_send_status"] == "skipped"
    assert fired[0]["auto_send_skip_reason"] == "human_owned"
    mock_send.assert_not_called()


def test_auto_send_reminder_never_double_sends(client_and_db):
    """check_due_reminders() runs on a 30s timer — calling it twice in a
    row (e.g. a slow send plus the next tick) must never send twice."""
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="Follow up", actor_user_id=1,
        auto_send=True, message_text=AUTO_SEND_TEXT,
    )

    fake_response = {"ok": True, "response": {"result": {"message_id": 111222}}}
    with patch(
        "backend.services.conversation_control_service.send_telegram_text",
        return_value=fake_response,
    ) as mock_send:
        first = conversation_control_service.check_due_reminders()
        second = conversation_control_service.check_due_reminders()

    assert len(first) == 1
    assert first[0]["auto_send_status"] == "sent"
    assert len(second) == 0
    mock_send.assert_called_once()


def test_clear_reminder_resets_auto_send_fields(client_and_db):
    """Clearing a reminder must reset auto_send/message_text so a
    previously-armed reminder can't fire (with the old auto-send state)
    after being cleared and re-set without auto-send."""
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="Follow up", actor_user_id=1,
        auto_send=True, message_text=AUTO_SEND_TEXT,
    )

    cleared = conversation_control_service.clear_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
    )
    assert not cleared["reminder_auto_send"]
    assert cleared["reminder_message_text"] is None

    conversation_control_service.set_reminder(
        company_id=COMPANY_ID, channel="telegram", external_user_id=CUSTOMER_ID,
        reminder_at=_past_iso(), note="No auto-send this time", actor_user_id=1,
    )

    with patch(
        "backend.services.conversation_control_service.send_telegram_text",
    ) as mock_send:
        fired = conversation_control_service.check_due_reminders()

    assert len(fired) == 1
    assert fired[0]["auto_send_requested"] is False
    assert fired[0]["auto_send_status"] == "not_requested"
    mock_send.assert_not_called()
