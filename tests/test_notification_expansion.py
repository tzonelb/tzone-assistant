"""
Tests for the Notification Center expansion: comments, post published,
tasks (assigned/completed/due), and appointments (created/reminder) now
raise bell notifications in addition to customer messages.

Run with: python -m pytest tests/test_notification_expansion.py -v
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

COMPANY_ID = 1
USER_ID = 1


@pytest.fixture()
def env():
    from database.database import db
    from backend.services.auth_service import auth_service
    from backend.services.customer_service import customer_service
    from backend.services.task_service import task_service
    from backend.services.appointment_service import appointment_service
    from backend.services.scheduled_post_service import scheduled_post_service
    from backend.services.comment_service import comment_service
    from backend.services.notification_service import notification_service

    tmp_db_path = tempfile.mktemp(suffix=".db")
    original = db.db_path
    db.db_path = Path(tmp_db_path)

    db.create_tables()
    auth_service.create_tables()
    customer_service.ensure_schema()
    task_service.ensure_schema()
    appointment_service.ensure_schema()
    scheduled_post_service.ensure_schema()
    comment_service.ensure_schema()
    notification_service.ensure_schema()

    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, full_name, status, is_super_admin) "
            "VALUES (1, 'agent@test.local', 'Agent', 'active', 0)"
        )
        conn.execute("INSERT OR IGNORE INTO companies (id, name, slug, workspace_id) VALUES (1, 'Test Co', 'test-co', 1)")
        conn.execute("INSERT OR IGNORE INTO company_users (company_id, user_id, status) VALUES (1, 1, 'active')")
        conn.commit()

    yield

    db.db_path = original
    import gc
    gc.collect()
    for _ in range(5):
        try:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)
            break
        except PermissionError:
            time.sleep(0.1)


def _types_for_user():
    from backend.services.notification_service import notification_service
    items = notification_service.list_for_user(company_id=COMPANY_ID, user_id=USER_ID)
    return {i["notification_type"] for i in items}


def test_task_assigned_and_completed_notify(env):
    from backend.services.task_service import task_service

    task = task_service.create_task(
        company_id=COMPANY_ID, title="Call the supplier", assigned_user_id=USER_ID,
    )
    assert "task_assigned" in _types_for_user()

    task_service.update_task(company_id=COMPANY_ID, task_id=task["id"], values={"status": "done"})
    assert "task_completed" in _types_for_user()


def test_task_due_scan_notifies_once(env):
    from backend.services.task_service import task_service

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    task_service.create_task(
        company_id=COMPANY_ID, title="Overdue thing", assigned_user_id=USER_ID, due_at=past,
    )
    assert task_service.scan_due_tasks() >= 1
    # Second scan must not create a duplicate (dedupe_key).
    task_service.scan_due_tasks()
    from backend.services.notification_service import notification_service
    due = [i for i in notification_service.list_for_user(company_id=COMPANY_ID, user_id=USER_ID)
           if i["notification_type"] == "task_due"]
    assert len(due) == 1


def test_due_alert_rearms_when_due_at_changes(env):
    """After a task's due alert fires, rescheduling it must clear the claim so
    the new due date alerts again (not silently suppressed forever)."""
    from backend.services.task_service import task_service
    from database.database import db

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    task = task_service.create_task(
        company_id=COMPANY_ID, title="Rearm me", assigned_user_id=USER_ID, due_at=past,
    )
    task_service.scan_due_tasks()
    with db.connect() as conn:
        assert conn.execute("SELECT due_notified_at FROM tasks WHERE id = ?", (task["id"],)).fetchone()[0] is not None

    # Reschedule to a new (also-past, for the test) time → claim cleared.
    newpast = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    task_service.update_task(company_id=COMPANY_ID, task_id=task["id"], values={"due_at": newpast})
    with db.connect() as conn:
        assert conn.execute("SELECT due_notified_at FROM tasks WHERE id = ?", (task["id"],)).fetchone()[0] is None
    # And it fires again.
    assert task_service.scan_due_tasks() >= 1


def test_appointment_created_and_reminder_notify(env):
    from backend.services.appointment_service import appointment_service

    soon = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    appointment_service.create_appointment(
        company_id=COMPANY_ID, title="Haircut", scheduled_at=soon, employee_user_id=USER_ID,
    )
    types = _types_for_user()
    assert "appointment_created" in types

    assert appointment_service.scan_upcoming_reminders() >= 1
    assert "appointment_reminder" in _types_for_user()


def test_new_comment_notifies_deduped(env):
    from backend.services.comment_service import comment_service
    from backend.services.notification_service import notification_service

    comment_service._upsert_comment(
        company_id=COMPANY_ID, channel_account_id=1, channel="instagram_direct",
        post_external_id="p1", comment_external_id="c1", parent_comment_external_id=None,
        author_name="A Customer", author_external_id="55", text="Is this available?",
        platform_created_at=None, is_from_business=0,
    )
    # Re-ingesting the same comment (a re-sync) must not notify again.
    comment_service._upsert_comment(
        company_id=COMPANY_ID, channel_account_id=1, channel="instagram_direct",
        post_external_id="p1", comment_external_id="c1", parent_comment_external_id=None,
        author_name="A Customer", author_external_id="55", text="Is this available?",
        platform_created_at=None, is_from_business=0,
    )
    comments = [i for i in notification_service.list_for_user(company_id=COMPANY_ID, user_id=USER_ID)
                if i["notification_type"] == "new_comment"]
    assert len(comments) == 1

    # Our own reply must NOT raise a notification.
    comment_service._upsert_comment(
        company_id=COMPANY_ID, channel_account_id=1, channel="instagram_direct",
        post_external_id="p1", comment_external_id="c2", parent_comment_external_id="c1",
        author_name="You", author_external_id=None, text="Yes!",
        platform_created_at=None, is_from_business=1,
    )
    comments_after = [i for i in notification_service.list_for_user(company_id=COMPANY_ID, user_id=USER_ID)
                      if i["notification_type"] == "new_comment"]
    assert len(comments_after) == 1
