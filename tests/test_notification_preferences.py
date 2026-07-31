"""
Tests for per-user notification preferences (backlog item 15).

Covers:
- defaults returned when a user has never set preferences
- update round-trips through the service and API
- notification_service.create honours a "none" new_message preference
  while still creating an ai_escalation notification for the same user

Run with: python3 -m pytest tests/test_notification_preferences.py -v
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
USER_ID = 1


@pytest.fixture()
def client_and_db():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.notification_service import notification_service
    from backend.services.notification_preference_service import notification_preference_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original_db_path = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    notification_service.ensure_schema()
    notification_preference_service.ensure_schema()

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


def test_defaults_returned_when_unset(client_and_db):
    client = client_and_db
    resp = client.get("/api/notification-preferences")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notify_new_message"] == "all"
    assert body["notify_ai_escalation"] is True
    assert body["notify_mentions"] is True
    assert body["notify_tasks"] is True


def test_update_round_trips(client_and_db):
    client = client_and_db
    put = client.put(
        "/api/notification-preferences",
        json={"notify_new_message": "none", "notify_tasks": False},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["notify_new_message"] == "none"
    assert body["notify_tasks"] is False
    # Untouched fields keep their defaults.
    assert body["notify_ai_escalation"] is True
    assert body["notify_mentions"] is True

    # Persisted for the next read.
    again = client.get("/api/notification-preferences").json()
    assert again["notify_new_message"] == "none"
    assert again["notify_tasks"] is False


def test_service_defaults_and_should_notify(client_and_db):
    from backend.services.notification_preference_service import notification_preference_service

    prefs = notification_preference_service.get_for_user(user_id=USER_ID, company_id=COMPANY_ID)
    assert prefs == {
        "notify_new_message": "all",
        "notify_ai_escalation": True,
        "notify_mentions": True,
        "notify_tasks": True,
    }
    # Everything on by default.
    assert notification_preference_service.should_notify(
        user_id=USER_ID, company_id=COMPANY_ID, notification_type="customer_message"
    ) is True
    assert notification_preference_service.should_notify(
        user_id=USER_ID, company_id=COMPANY_ID, notification_type="ai_escalation"
    ) is True


def test_create_respects_none_new_message_but_allows_escalation(client_and_db):
    from backend.services.notification_service import notification_service
    from backend.services.notification_preference_service import notification_preference_service

    # User opts out of new-message pings.
    notification_preference_service.update_for_user(
        user_id=USER_ID, company_id=COMPANY_ID, notify_new_message="none"
    )

    suppressed = notification_service.create(
        company_id=COMPANY_ID,
        notification_type="new_message",
        title="New message",
        recipient_user_id=USER_ID,
    )
    assert suppressed.get("skipped") is True
    assert "id" not in suppressed

    # Escalation still comes through (default on).
    escalation = notification_service.create(
        company_id=COMPANY_ID,
        notification_type="ai_escalation",
        title="AI needs a human",
        recipient_user_id=USER_ID,
    )
    assert "id" in escalation
    assert escalation.get("skipped") is None

    # Only the escalation landed in the recipient's list.
    stored = notification_service.list_for_user(company_id=COMPANY_ID, user_id=USER_ID)
    types = [n["notification_type"] for n in stored]
    assert "ai_escalation" in types
    assert "new_message" not in types


def test_escalation_can_be_muted(client_and_db):
    from backend.services.notification_service import notification_service
    from backend.services.notification_preference_service import notification_preference_service

    notification_preference_service.update_for_user(
        user_id=USER_ID, company_id=COMPANY_ID, notify_ai_escalation=False
    )
    result = notification_service.create(
        company_id=COMPANY_ID,
        notification_type="ai_escalation",
        title="AI needs a human",
        recipient_user_id=USER_ID,
    )
    assert result.get("skipped") is True


def test_broadcast_notifications_are_not_suppressed(client_and_db):
    from backend.services.notification_service import notification_service
    from backend.services.notification_preference_service import notification_preference_service

    # Even with new_message off, a company-wide broadcast (no recipient)
    # is still created — there is no single recipient whose pref applies.
    notification_preference_service.update_for_user(
        user_id=USER_ID, company_id=COMPANY_ID, notify_new_message="none"
    )
    result = notification_service.create(
        company_id=COMPANY_ID,
        notification_type="customer_message",
        title="New WhatsApp message",
        channel="whatsapp",
    )
    assert "id" in result
