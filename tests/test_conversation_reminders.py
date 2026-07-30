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

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    conversation_control_service.ensure_schema()
    notification_service.ensure_schema()

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
